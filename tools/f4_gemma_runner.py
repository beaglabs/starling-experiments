#!/usr/bin/env python3
"""F4 heterogeneous Gemma operator runner.

The model process only emits raw protocol-vocabulary completions. The canonical
measurement layer is the Zig replay engine, which independently validates and
applies every recorded interaction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import sys
import time
import urllib.error
import urllib.request

WORKER_COUNT = 5
FACT_COUNT = 5
FULL_MASK = (1 << FACT_COUNT) - 1
TYPED = "typed_unconstrained"
CONSTRAINED = "cfg_constrained"
BACKEND_ERROR = "__BACKEND_ERROR__"
RUNNER_VERSION = 4

POPULATIONS = ("deterministic_only", "mixed", "model_only")
TOPOLOGIES = ("ring", "grid")

PROTOCOL_SPEC = """You are one operator in a distributed coordination experiment.

The only valid interactions are:
CLAIM <facts>
QUERY EVIDENCE <fact>

Facts are A, B, C, D, and E.

CLAIM announces one or more facts you currently know to your topology
neighbors. Write multiple facts comma-separated with no spaces, for example:
CLAIM A,C

QUERY EVIDENCE asks all topology neighbors for one fact. Any neighbor that
knows the fact returns evidence through the deterministic runtime, for example:
QUERY EVIDENCE D

You may only CLAIM facts listed in your current knowledge. Claims of unknown
facts are rejected by the runtime.

Choose your interaction autonomously. Output exactly one interaction and
nothing else.
"""


def fact_bit(index: int) -> int:
    return 1 << index


def initial_knowledge(environment_seed: int) -> list[int]:
    offset = environment_seed % FACT_COUNT
    return [
        fact_bit((worker + offset) % FACT_COUNT)
        | fact_bit((worker + 1 + offset) % FACT_COUNT)
        for worker in range(WORKER_COUNT)
    ]


def generation_seed(sampling_seed: int, round_number: int, worker: int) -> int:
    mixed = (
        sampling_seed * 1_000_003
        + round_number * 101
        + worker
    ) & ((1 << 64) - 1)
    return mixed & 0x7FFF_FFFF


def grid_width(population: int) -> int:
    return math.ceil(math.sqrt(population))


def neighbors(topology: str, worker_index: int) -> tuple[int, ...]:
    if topology == "ring":
        left = (worker_index + WORKER_COUNT - 1) % WORKER_COUNT
        right = (worker_index + 1) % WORKER_COUNT
        return (left,) if left == right else (left, right)

    if topology != "grid":
        raise ValueError(f"unknown topology: {topology}")

    width = grid_width(WORKER_COUNT)
    row = worker_index // width
    col = worker_index % width
    result: list[int] = []

    if col > 0:
        result.append(worker_index - 1)
    if col + 1 < width and worker_index + 1 < WORKER_COUNT:
        candidate = worker_index + 1
        if candidate // width == row:
            result.append(candidate)
    if worker_index >= width:
        result.append(worker_index - width)
    if worker_index + width < WORKER_COUNT:
        result.append(worker_index + width)
    return tuple(result)


def operator_type(population: str, worker_index: int) -> str:
    if population == "deterministic_only":
        return "deterministic"
    if population == "model_only":
        return "model"
    if population == "mixed":
        # Collector + one non-collector are model-backed; the remainder use a
        # deterministic protocol operator.
        return "model" if worker_index in (0, 2) else "deterministic"
    raise ValueError(f"unknown population: {population}")


def mask_text(mask: int) -> str:
    labels = [
        chr(ord("A") + i)
        for i in range(FACT_COUNT)
        if mask & fact_bit(i)
    ]
    return ",".join(labels) if labels else "(none)"


def deterministic_completion(knowledge: int) -> str:
    return f"CLAIM {mask_text(knowledge)}"


def build_prompt(
    *,
    population: str,
    topology: str,
    worker: int,
    round_number: int,
    private_facts: int,
    current_knowledge: int,
) -> str:
    worker_index = worker - 1
    peer_text = ",".join(str(peer + 1) for peer in neighbors(topology, worker_index))
    return (
        PROTOCOL_SPEC
        + "\n"
        + f"Population arm: {population}\n"
        + f"Topology: {topology}\n"
        + f"Your identity: Worker {worker}\n"
        + f"Your neighbors: {peer_text}\n"
        + f"Round: {round_number}\n"
        + f"Your original private facts: {mask_text(private_facts)}\n"
        + f"Your current knowledge: {mask_text(current_knowledge)}\n"
        + "Collective objective: Worker 1 must learn the complete fact set A,B,C,D,E.\n"
        + "Choose your next interaction."
    )


def parse_fact(text: str) -> int | None:
    if len(text) != 1 or text < "A" or text > "E":
        return None
    return fact_bit(ord(text) - ord("A"))


def parse_action(completion: str) -> tuple[str, int] | None:
    text = completion.strip()
    parts = text.split(" ")

    if len(parts) == 2 and parts[0] == "CLAIM":
        mask = 0
        for fact in parts[1].split(","):
            bit = parse_fact(fact)
            if bit is None:
                return None
            mask |= bit
        return None if mask == 0 else ("claim", mask)

    if len(parts) == 3 and parts[0] == "QUERY" and parts[1] == "EVIDENCE":
        bit = parse_fact(parts[2])
        return None if bit is None else ("query_evidence", bit)

    return None


def apply_round(
    knowledge: list[int],
    completions: list[str],
    topology: str,
) -> list[int]:
    snapshot = list(knowledge)
    next_state = list(snapshot)

    for sender, completion in enumerate(completions):
        parsed = parse_action(completion)
        if parsed is None:
            continue
        kind, facts = parsed
        peers = neighbors(topology, sender)

        if kind == "claim":
            if facts & ~snapshot[sender]:
                continue
            for recipient in peers:
                next_state[recipient] |= facts
            continue

        for recipient in peers:
            if snapshot[recipient] & facts:
                next_state[sender] |= facts

    return next_state


def mode_order(environment_seed: int, sampling_seed: int) -> tuple[str, str]:
    if (environment_seed + sampling_seed) & 1:
        return CONSTRAINED, TYPED
    return TYPED, CONSTRAINED


def escape_completion(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )


def build_payload(
    *,
    model: str,
    prompt: str,
    seed: int,
    mode: str,
    grammar: str,
    temperature: float,
    top_p: float,
    top_k: int,
    max_tokens: int,
) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "seed": seed,
        "temperature": temperature,
        "top_p": top_p,
        "top_k": top_k,
        "max_tokens": max_tokens,
        "stream": False,
        "cache_prompt": False,
        "reasoning_effort": "none",
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if mode == CONSTRAINED:
        payload["grammar"] = grammar
    return payload


def request_json(
    url: str,
    payload: dict | None,
    api_key: str | None,
    timeout: float,
) -> dict:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def discover_model(base_url: str, api_key: str | None, timeout: float) -> str:
    data = request_json(
        f"{base_url.rstrip('/')}/v1/models",
        None,
        api_key,
        timeout,
    )
    models = data.get("data") or []
    if not models or not isinstance(models[0], dict) or not models[0].get("id"):
        raise RuntimeError("llama.cpp /v1/models returned no model id")
    return str(models[0]["id"])


def run_completion(
    *,
    base_url: str,
    api_key: str | None,
    timeout: float,
    payload: dict,
) -> tuple[str, int, int]:
    started = time.perf_counter_ns()
    data = request_json(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        payload,
        api_key,
        timeout,
    )
    latency_us = (time.perf_counter_ns() - started) // 1_000
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("response contains no choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str):
        raise RuntimeError("response choice has no string message.content")
    usage = data.get("usage") or {}
    completion_tokens = int(usage.get("completion_tokens") or 0)
    return content, completion_tokens, latency_us


def write_metadata(
    *,
    output_path: pathlib.Path,
    model: str,
    grammar: str,
    args: argparse.Namespace,
) -> None:
    run_count = (
        len(POPULATIONS)
        * len(TOPOLOGIES)
        * args.environments
        * args.sampling_seeds
        * 2
    )
    metadata = {
        "stage": "F4",
        "runner_version": RUNNER_VERSION,
        "record_schema_version": 1,
        "historical_runner_blob": "f84098b579fda8c472eac0f16e9e05e3d534bf16",
        "model": model,
        "base_url": args.base_url,
        "populations": list(POPULATIONS),
        "topologies": list(TOPOLOGIES),
        "mixed_model_workers": [1, 3],
        "first_environment_seed": args.first_environment_seed,
        "environments": args.environments,
        "first_sampling_seed": args.first_sampling_seed,
        "sampling_seeds": args.sampling_seeds,
        "population_runs": run_count,
        "worker_count": WORKER_COUNT,
        "fact_count": FACT_COUNT,
        "collector_worker": 1,
        "max_rounds": args.max_rounds,
        "temperature": args.temperature,
        "top_p": args.top_p,
        "top_k": args.top_k,
        "max_tokens": args.max_tokens,
        "cache_prompt": False,
        "reasoning_effort": "none",
        "prompt_spec_sha256": hashlib.sha256(
            PROTOCOL_SPEC.encode("utf-8")
        ).hexdigest(),
        "grammar_sha256": hashlib.sha256(grammar.encode("utf-8")).hexdigest(),
        "grammar_path": args.grammar,
        "endpoint": "/v1/chat/completions",
        "sampling_seed_and_environment_seed_independent": True,
    }
    pathlib.Path(str(output_path) + ".meta.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def self_test() -> None:
    assert initial_knowledge(0) == [3, 6, 12, 24, 17]
    assert generation_seed(0, 1, 1) == 102
    assert operator_type("mixed", 0) == "model"
    assert operator_type("mixed", 1) == "deterministic"
    assert operator_type("mixed", 2) == "model"
    assert neighbors("ring", 0) == (4, 1)
    assert neighbors("grid", 0) == (1, 3)
    assert parse_action("CLAIM A,C,E") == ("claim", 0b10101)
    assert parse_action("QUERY EVIDENCE D") == ("query_evidence", 0b01000)
    assert parse_action("I think CLAIM A") is None

    knowledge = initial_knowledge(0)
    for _ in range(4):
        completions = [deterministic_completion(mask) for mask in knowledge]
        knowledge = apply_round(knowledge, completions, "ring")
        if knowledge[0] == FULL_MASK:
            break
    assert knowledge[0] == FULL_MASK
    print("F4 runner self-test passed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8087")
    parser.add_argument("--model", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--grammar", default="grammars/f4_protocol.gbnf")
    parser.add_argument("--output", default="trials/f4-raw.tsv")
    parser.add_argument("--first-environment-seed", type=int, default=0)
    parser.add_argument("--environments", type=int, default=2)
    parser.add_argument("--first-sampling-seed", type=int, default=0)
    parser.add_argument("--sampling-seeds", type=int, default=2)
    parser.add_argument("--max-rounds", type=int, default=5)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--top-p", type=float, default=0.9)
    parser.add_argument("--top-k", type=int, default=40)
    parser.add_argument("--max-tokens", type=int, default=32)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.environments <= 0 or args.sampling_seeds <= 0 or args.max_rounds <= 0:
        raise SystemExit("environment, sampling-seed, and round counts must be positive")

    grammar = pathlib.Path(args.grammar).read_text(encoding="utf-8")
    output_path = pathlib.Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    population_runs = (
        len(POPULATIONS)
        * len(TOPOLOGIES)
        * args.environments
        * args.sampling_seeds
        * 2
    )
    if args.dry_run:
        print(f"{population_runs} population runs")
        print(
            "model calls depend on convergence; deterministic-only arm makes "
            "zero model requests"
        )
        return 0

    model = args.model or discover_model(args.base_url, args.api_key, args.timeout)
    write_metadata(
        output_path=output_path,
        model=model,
        grammar=grammar,
        args=args,
    )

    with output_path.open("w", encoding="utf-8", newline="") as handle:
        for population in POPULATIONS:
            for topology in TOPOLOGIES:
                for environment_seed in range(
                    args.first_environment_seed,
                    args.first_environment_seed + args.environments,
                ):
                    private = initial_knowledge(environment_seed)

                    for sampling_seed in range(
                        args.first_sampling_seed,
                        args.first_sampling_seed + args.sampling_seeds,
                    ):
                        for mode in mode_order(environment_seed, sampling_seed):
                            knowledge = list(private)

                            for round_number in range(1, args.max_rounds + 1):
                                snapshot = list(knowledge)
                                completions: list[str] = []

                                for worker_index in range(WORKER_COUNT):
                                    worker = worker_index + 1
                                    kind = operator_type(population, worker_index)

                                    if kind == "deterministic":
                                        completion = deterministic_completion(
                                            snapshot[worker_index]
                                        )
                                        tokens = 0
                                        latency_us = 0
                                        model_seed = 0
                                    else:
                                        model_seed = generation_seed(
                                            sampling_seed,
                                            round_number,
                                            worker,
                                        )
                                        prompt = build_prompt(
                                            population=population,
                                            topology=topology,
                                            worker=worker,
                                            round_number=round_number,
                                            private_facts=private[worker_index],
                                            current_knowledge=snapshot[worker_index],
                                        )
                                        payload = build_payload(
                                            model=model,
                                            prompt=prompt,
                                            seed=model_seed,
                                            mode=mode,
                                            grammar=grammar,
                                            temperature=args.temperature,
                                            top_p=args.top_p,
                                            top_k=args.top_k,
                                            max_tokens=args.max_tokens,
                                        )
                                        try:
                                            completion, tokens, latency_us = run_completion(
                                                base_url=args.base_url,
                                                api_key=args.api_key,
                                                timeout=args.timeout,
                                                payload=payload,
                                            )
                                        except (
                                            urllib.error.URLError,
                                            urllib.error.HTTPError,
                                            TimeoutError,
                                            RuntimeError,
                                            ValueError,
                                        ) as exc:
                                            print(
                                                "backend error "
                                                f"population={population} topology={topology} "
                                                f"env={environment_seed} sampling={sampling_seed} "
                                                f"mode={mode} round={round_number} worker={worker}: {exc}",
                                                file=sys.stderr,
                                            )
                                            completion = BACKEND_ERROR
                                            tokens = 0
                                            latency_us = 0

                                    handle.write(
                                        f"{population}\t{topology}\t{environment_seed}\t"
                                        f"{sampling_seed}\t{mode}\t{round_number}\t{worker}\t"
                                        f"{kind}\t{snapshot[worker_index]}\t{model_seed}\t"
                                        f"{tokens}\t{latency_us}\t"
                                        f"{escape_completion(completion)}\n"
                                    )
                                    handle.flush()
                                    completions.append(completion)

                                knowledge = apply_round(
                                    knowledge,
                                    completions,
                                    topology,
                                )
                                if knowledge[0] == FULL_MASK:
                                    break

                            print(
                                f"population={population} topology={topology} "
                                f"env={environment_seed} sampling={sampling_seed} "
                                f"mode={mode} rounds={round_number} "
                                f"collector={mask_text(knowledge[0])} "
                                f"success={knowledge[0] == FULL_MASK}",
                                file=sys.stderr,
                            )

    print(f"done: output={output_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
