#!/usr/bin/env python3
"""Stage 3F.0 controlled-emergence runner for llama.cpp.

Five model-backed workers coordinate over a deterministic ring to make Worker 1
recover all five distributed facts. Environment rotation and model sampling are
independent experimental factors. Zig independently replays the raw record and
is authoritative for outcome and communication metrics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
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
RUNNER_VERSION = 2

PROTOCOL_SPEC = """You are one operator in a five-worker distributed coordination experiment.

The only valid interactions are:
CLAIM <facts>
QUERY EVIDENCE <fact>

Facts are A, B, C, D, and E.

CLAIM announces one or more facts you currently know to both ring neighbors.
Write multiple facts comma-separated with no spaces, for example:
CLAIM A,C

QUERY EVIDENCE asks both ring neighbors for one fact. Any neighbor that knows
the fact returns evidence automatically through the deterministic runtime, for example:
QUERY EVIDENCE D

You may only CLAIM facts listed in your current knowledge. Claims of unknown
facts are rejected by the runtime.

Choose your interaction autonomously. Coordinate efficiently toward the
collective objective. Output exactly one interaction and nothing else.
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
        (sampling_seed * 1_000_003)
        + (round_number * 101)
        + worker
    ) & ((1 << 64) - 1)
    return mixed & 0x7FFF_FFFF


def ring_neighbors(worker_index: int) -> tuple[int, int]:
    return (
        (worker_index + WORKER_COUNT - 1) % WORKER_COUNT,
        (worker_index + 1) % WORKER_COUNT,
    )


def mask_text(mask: int) -> str:
    labels = [chr(ord("A") + i) for i in range(FACT_COUNT) if mask & fact_bit(i)]
    return ",".join(labels) if labels else "(none)"


def build_prompt(
    *,
    worker: int,
    round_number: int,
    private_facts: int,
    current_knowledge: int,
) -> str:
    left, right = ring_neighbors(worker - 1)
    return (
        PROTOCOL_SPEC
        + "\n"
        + f"Your identity: Worker {worker}\n"
        + f"Your ring neighbors: Worker {left + 1}, Worker {right + 1}\n"
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
        if mask == 0:
            return None
        return "claim", mask

    if len(parts) == 3 and parts[0] == "QUERY" and parts[1] == "EVIDENCE":
        bit = parse_fact(parts[2])
        if bit is None:
            return None
        return "query_evidence", bit

    return None


def apply_round(knowledge: list[int], completions: list[str]) -> list[int]:
    snapshot = list(knowledge)
    next_state = list(snapshot)

    for sender, completion in enumerate(completions):
        parsed = parse_action(completion)
        if parsed is None:
            continue

        kind, facts = parsed
        neighbors = ring_neighbors(sender)

        if kind == "claim":
            if facts & ~snapshot[sender]:
                continue
            for recipient in neighbors:
                next_state[recipient] |= facts
            continue

        for recipient in neighbors:
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


def request_json(url: str, payload: dict | None, api_key: str | None, timeout: float) -> dict:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def discover_model(base_url: str, api_key: str | None, timeout: float) -> str:
    data = request_json(f"{base_url.rstrip('/')}/v1/models", None, api_key, timeout)
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


def write_record(
    handle,
    *,
    environment_seed: int,
    sampling_seed: int,
    mode: str,
    round_number: int,
    worker: int,
    knowledge_before: int,
    model_seed: int,
    completion_tokens: int,
    latency_us: int,
    completion: str,
) -> None:
    handle.write(
        f"{environment_seed}\t{sampling_seed}\t{mode}\t{round_number}\t{worker}\t"
        f"{knowledge_before}\t{model_seed}\t{completion_tokens}\t{latency_us}\t"
        f"{escape_completion(completion)}\n"
    )
    handle.flush()


def write_metadata(
    *,
    output_path: pathlib.Path,
    model: str,
    grammar: str,
    args: argparse.Namespace,
) -> None:
    population_runs = args.environments * args.sampling_seeds * 2
    metadata = {
        "stage": "3F.0",
        "runner_version": RUNNER_VERSION,
        "record_schema_version": 2,
        "model": model,
        "base_url": args.base_url,
        "first_environment_seed": args.first_environment_seed,
        "environments": args.environments,
        "first_sampling_seed": args.first_sampling_seed,
        "sampling_seeds": args.sampling_seeds,
        "population_runs": population_runs,
        "worker_count": WORKER_COUNT,
        "fact_count": FACT_COUNT,
        "collector_worker": 1,
        "topology": "ring",
        "max_rounds": args.max_rounds,
        "max_generations": population_runs * args.max_rounds * WORKER_COUNT,
        "temperature": args.temperature,
        "top_p": args.top_p,
        "top_k": args.top_k,
        "max_tokens": args.max_tokens,
        "cache_prompt": False,
        "reasoning_effort": "none",
        "chat_template_kwargs": {"enable_thinking": False},
        "prompt_spec_sha256": hashlib.sha256(PROTOCOL_SPEC.encode("utf-8")).hexdigest(),
        "grammar_sha256": hashlib.sha256(grammar.encode("utf-8")).hexdigest(),
        "grammar_path": args.grammar,
        "endpoint": "/v1/chat/completions",
        "resume_supported": False,
    }
    pathlib.Path(str(output_path) + ".meta.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def self_test() -> None:
    assert initial_knowledge(0) == [3, 6, 12, 24, 17]
    assert initial_knowledge(5) == initial_knowledge(0)
    assert generation_seed(0, 1, 1) == 102
    assert generation_seed(7, 1, 1) == generation_seed(7, 1, 1)
    assert generation_seed(7, 1, 1) != generation_seed(8, 1, 1)
    assert parse_action("CLAIM A,C,E") == ("claim", 0b10101)
    assert parse_action("QUERY EVIDENCE D") == ("query_evidence", 0b01000)
    assert parse_action("I think CLAIM A") is None

    knowledge = initial_knowledge(0)
    round_one = [
        "CLAIM A,B",
        "CLAIM B,C",
        "CLAIM C,D",
        "CLAIM D,E",
        "CLAIM A,E",
    ]
    knowledge = apply_round(knowledge, round_one)
    assert knowledge == [23, 15, 30, 29, 27]
    knowledge = apply_round(
        knowledge,
        ["QUERY EVIDENCE D", "CLAIM D", "CLAIM C", "CLAIM D", "CLAIM E"],
    )
    assert knowledge[0] == FULL_MASK

    grammar = 'root ::= "CLAIM A"'
    prompt = build_prompt(
        worker=1,
        round_number=1,
        private_facts=3,
        current_knowledge=3,
    )
    typed = build_payload(
        model="m",
        prompt=prompt,
        seed=102,
        mode=TYPED,
        grammar=grammar,
        temperature=0.7,
        top_p=0.9,
        top_k=40,
        max_tokens=32,
    )
    constrained = build_payload(
        model="m",
        prompt=prompt,
        seed=102,
        mode=CONSTRAINED,
        grammar=grammar,
        temperature=0.7,
        top_p=0.9,
        top_k=40,
        max_tokens=32,
    )
    constrained_without_grammar = dict(constrained)
    assert constrained_without_grammar.pop("grammar") == grammar
    assert typed == constrained_without_grammar
    assert mode_order(0, 0) == (TYPED, CONSTRAINED)
    assert mode_order(0, 1) == (CONSTRAINED, TYPED)
    print("stage3f0 runner self-test passed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--model", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--grammar", default="grammars/stage3f0.gbnf")
    parser.add_argument("--output", default="trials/stage3f0-v2.tsv")
    parser.add_argument("--first-environment-seed", type=int, default=0)
    parser.add_argument("--environments", type=int, default=5)
    parser.add_argument("--first-sampling-seed", type=int, default=0)
    parser.add_argument("--sampling-seeds", type=int, default=4)
    parser.add_argument("--max-rounds", type=int, default=10)
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
    if args.environments <= 0:
        raise SystemExit("--environments must be > 0")
    if args.sampling_seeds <= 0:
        raise SystemExit("--sampling-seeds must be > 0")
    if args.max_rounds <= 0:
        raise SystemExit("--max-rounds must be > 0")

    grammar = pathlib.Path(args.grammar).read_text(encoding="utf-8")
    output_path = pathlib.Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    population_runs = args.environments * args.sampling_seeds * 2
    max_generations = population_runs * args.max_rounds * WORKER_COUNT
    if args.dry_run:
        print(f"{population_runs} population runs")
        print(f"up to {max_generations} model generations")
        return 0

    model = args.model or discover_model(args.base_url, args.api_key, args.timeout)
    write_metadata(output_path=output_path, model=model, grammar=grammar, args=args)

    print(
        f"Stage 3F.0 v2: model={model!r}, environments={args.environments}, "
        f"sampling_seeds={args.sampling_seeds}, population_runs={population_runs}, "
        f"max_rounds={args.max_rounds}, max_generations={max_generations}, "
        f"output={output_path}",
        file=sys.stderr,
    )

    emitted = 0
    with output_path.open("w", encoding="utf-8", newline="") as handle:
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
                            model_seed = generation_seed(
                                sampling_seed,
                                round_number,
                                worker,
                            )
                            prompt = build_prompt(
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
                                    f"backend error environment={environment_seed} "
                                    f"sampling={sampling_seed} mode={mode} "
                                    f"round={round_number} worker={worker}: {exc}",
                                    file=sys.stderr,
                                )
                                completion, tokens, latency_us = BACKEND_ERROR, 0, 0

                            write_record(
                                handle,
                                environment_seed=environment_seed,
                                sampling_seed=sampling_seed,
                                mode=mode,
                                round_number=round_number,
                                worker=worker,
                                knowledge_before=snapshot[worker_index],
                                model_seed=model_seed,
                                completion_tokens=tokens,
                                latency_us=latency_us,
                                completion=completion,
                            )
                            completions.append(completion)
                            emitted += 1

                        knowledge = apply_round(knowledge, completions)
                        if knowledge[0] == FULL_MASK:
                            break

                    print(
                        f"environment={environment_seed} sampling={sampling_seed} "
                        f"mode={mode} rounds={round_number} "
                        f"collector={mask_text(knowledge[0])} "
                        f"success={knowledge[0] == FULL_MASK}",
                        file=sys.stderr,
                    )

    print(f"done: emitted={emitted}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
