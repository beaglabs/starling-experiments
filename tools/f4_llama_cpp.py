#!/usr/bin/env python3
"""F4 heterogeneous deterministic/model-backed population runner.

Python owns only llama.cpp I/O and a mirror needed to construct the next prompt.
The Zig F4 replayer is authoritative for deterministic policy actions, state
transitions, success, accounting, and trajectory metrics.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import pathlib
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Iterable

WORKER_COUNT = 5
FACT_COUNT = 5
FULL_MASK = (1 << FACT_COUNT) - 1
BANDWIDTH = 2
MAX_ROUNDS = 10
MAX_FACTS = 2048
MIXED_MODEL_INDICES = {1, 2}

TYPED = "typed_unconstrained"
CONSTRAINED = "cfg_constrained"
DETERMINISTIC_MODE = "deterministic"
ALWAYS_REFRESH = "always_refresh"
STATE_AWARE = "knowledge_or_stale"
DETERMINISTIC_CONTROLLER = "deterministic"
BACKEND_ERROR = "__BACKEND_ERROR__"
RUNNER_VERSION = 1
RAW_SCHEMA_VERSION = 1
HISTORICAL_STAGE3F0_BLOB = "f84098b579fda8c472eac0f16e9e05e3d534bf16"
DEFAULT_WEIGHTS = "~/Downloads/models/gemma-4-E2B-it-Q4_K_M.gguf"

# Frozen Stage 7B theta51.
THETA51 = {
    "novelty_permille": 354,
    "exploration_permille": 141,
    "retry_permille": 0,
    "bandwidth_utilization_permille": 994,
}

PROTOCOL_SPEC = """You are one operator in a five-worker Starlings coordination experiment.

The only valid interactions are:
CLAIM <facts>
QUERY EVIDENCE <fact>

Facts are A, B, C, D, and E.

CLAIM sends one or more facts you currently know to every topology neighbor.
You may claim at most two facts per interaction. Write multiple facts
comma-separated with no spaces, for example:
CLAIM A,C

QUERY EVIDENCE asks every topology neighbor for one fact. A neighbor that knows
the requested fact returns deterministic evidence through the Starlings
runtime, for example:
QUERY EVIDENCE D

You may only CLAIM facts listed in your current knowledge. Unknown claims and
claims above the two-fact bandwidth are rejected by the deterministic runtime.

Choose your interaction autonomously. Coordinate efficiently toward the
collective objective. Output exactly one interaction and nothing else.
"""


@dataclass
class State:
    knowledge: int = 0
    sent: int = 0
    cursor: int = 0


@dataclass
class Action:
    kind: str
    facts: int = 0
    selected: int = 0
    query_fact: int = 0
    next_cursor: int = 0
    reset_sent: bool = False


@dataclass
class Cache:
    initialized: bool = False
    action: Action | None = None
    knowledge_at_refresh: int = 0


@dataclass(frozen=True)
class RunSpec:
    mix: str
    topology: str
    environment_seed: int
    sampling_seed: int
    mode: str
    controller: str


def fact_bit(index: int) -> int:
    return 1 << index


def mask_text(mask: int) -> str:
    values = [
        chr(ord("A") + i)
        for i in range(FACT_COUNT)
        if mask & fact_bit(i)
    ]
    return ",".join(values) if values else "(none)"


def initial_states(environment_seed: int) -> list[State]:
    offset = environment_seed % FACT_COUNT
    states = [State() for _ in range(WORKER_COUNT)]
    for worker in range(WORKER_COUNT):
        states[worker].knowledge = (
            fact_bit((worker + offset) % FACT_COUNT)
            | fact_bit((worker + 1 + offset) % FACT_COUNT)
        )
    return states


def essential_fact(environment_seed: int) -> int:
    return (2 + environment_seed % FACT_COUNT) % FACT_COUNT


def operator_kind(mix: str, operator_index: int) -> str:
    if mix == "deterministic_only":
        return "deterministic"
    if mix == "model_only":
        return "model"
    if mix == "mixed":
        return "model" if operator_index in MIXED_MODEL_INDICES else "deterministic"
    raise ValueError(f"unknown mix {mix}")


def topology_neighbors(topology: str, operator_index: int) -> list[int]:
    if topology == "ring":
        return [
            (operator_index + WORKER_COUNT - 1) % WORKER_COUNT,
            (operator_index + 1) % WORKER_COUNT,
        ]
    if topology == "grid":
        width = math.isqrt(WORKER_COUNT)
        if width * width < WORKER_COUNT:
            width += 1
        row = operator_index // width
        col = operator_index % width
        result: list[int] = []
        if col > 0:
            result.append(operator_index - 1)
        if col + 1 < width and operator_index + 1 < WORKER_COUNT:
            recipient = operator_index + 1
            if recipient // width == row:
                result.append(recipient)
        if operator_index >= width:
            result.append(operator_index - width)
        if operator_index + width < WORKER_COUNT:
            result.append(operator_index + width)
        return result
    raise ValueError(f"unknown topology {topology}")


def u64(value: int) -> int:
    return value & ((1 << 64) - 1)


def mix64(value: int) -> int:
    z = u64(value + 0x9E3779B97F4A7C15)
    z = u64((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9)
    z = u64((z ^ (z >> 27)) * 0x94D049BB133111EB)
    return z ^ (z >> 31)


def gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return a


def seeded_ranks(
    operator_index: int,
    round_number: int,
    environment_seed: int,
) -> list[int]:
    salt = u64(
        environment_seed
        ^ u64(operator_index * 0x9E3779B97F4A7C15)
        ^ u64(round_number * 0xBF58476D1CE4E5B9)
    )
    start = mix64(salt) % FACT_COUNT

    step = 1
    if FACT_COUNT > 2:
        step = 1 + (mix64(salt ^ 0x94D049BB133111EB) % (FACT_COUNT - 1))
        while gcd(step, FACT_COUNT) != 1:
            step += 1
            if step >= FACT_COUNT:
                step = 1

    ranks = [0] * FACT_COUNT
    for rank in range(FACT_COUNT):
        fact = (start + rank * step) % FACT_COUNT
        ranks[fact] = rank
    return ranks


def effective_bandwidth() -> int:
    numerator = BANDWIDTH * THETA51["bandwidth_utilization_permille"]
    return min(BANDWIDTH, max(1, (numerator + 999) // 1000))


def deterministic_action(
    state: State,
    operator_index: int,
    round_number: int,
    environment_seed: int,
) -> Action | None:
    if state.knowledge == 0:
        return None

    bandwidth = effective_bandwidth()
    has_unsent = bool(state.knowledge & ~state.sent & FULL_MASK)
    ranks = seeded_ranks(operator_index, round_number, environment_seed)

    cursor_start = state.cursor % FACT_COUNT
    novelty_scale = MAX_FACTS + 1
    exploration = THETA51["exploration_permille"]
    cursor_weight = 1000 - exploration

    candidates: list[tuple[int, int, int]] = []
    for fact in range(FACT_COUNT):
        bit = fact_bit(fact)
        if not state.knowledge & bit:
            continue

        was_sent = bool(state.sent & bit)
        if has_unsent and was_sent and THETA51["retry_permille"] == 0:
            continue

        cursor_rank = (fact + FACT_COUNT - cursor_start) % FACT_COUNT
        seeded_rank = ranks[fact]
        score = cursor_weight * cursor_rank + exploration * seeded_rank
        if not was_sent:
            score -= THETA51["novelty_permille"] * novelty_scale
        candidates.append((score, fact, cursor_rank))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1]))
    chosen = candidates[:bandwidth]
    mask = 0
    next_cursor = cursor_start
    worst_rank = -1
    for _, fact, cursor_rank in chosen:
        mask |= fact_bit(fact)
        if cursor_rank >= worst_rank:
            worst_rank = cursor_rank
            next_cursor = (fact + 1) % FACT_COUNT

    return Action(
        kind="claim",
        facts=mask,
        selected=len(chosen),
        next_cursor=next_cursor,
    )


def parse_fact(text: str) -> int | None:
    if len(text) != 1 or text < "A" or text > "E":
        return None
    return ord(text) - ord("A")


def parse_action(completion: str) -> Action | None:
    text = completion.strip()
    parts = text.split(" ")

    if len(parts) == 2 and parts[0] == "CLAIM":
        mask = 0
        for item in parts[1].split(","):
            fact = parse_fact(item)
            if fact is None:
                return None
            mask |= fact_bit(fact)
        selected = mask.bit_count()
        if selected == 0:
            return None
        return Action(kind="claim", facts=mask, selected=selected)

    if len(parts) == 3 and parts[0] == "QUERY" and parts[1] == "EVIDENCE":
        fact = parse_fact(parts[2])
        if fact is None:
            return None
        return Action(
            kind="query_evidence",
            query_fact=fact,
            selected=1,
        )

    return None


def validate_model_action(action: Action, state: State) -> bool:
    if action.kind == "claim":
        return (
            1 <= action.selected <= BANDWIDTH
            and action.selected == action.facts.bit_count()
            and not (action.facts & ~state.knowledge)
        )
    if action.kind == "query_evidence":
        return 0 <= action.query_fact < FACT_COUNT
    return False


def cached_action_is_stale(action: Action, state: State) -> bool:
    if action.kind == "claim":
        has_unsent = bool(state.knowledge & ~state.sent & FULL_MASK)
        return has_unsent and not (action.facts & ~state.sent)
    if action.kind == "query_evidence":
        return bool(state.knowledge & fact_bit(action.query_fact))
    return True


def should_refresh(controller: str, state: State, cache: Cache) -> bool:
    if controller == ALWAYS_REFRESH:
        return True
    if controller != STATE_AWARE:
        raise ValueError(f"invalid model controller {controller}")

    if not cache.initialized:
        return True
    if state.knowledge != cache.knowledge_at_refresh:
        return True
    if cache.action is None:
        return True
    if not validate_model_action(cache.action, state):
        return True
    if cached_action_is_stale(cache.action, state):
        return True
    return False


def canonical_action_text(action: Action | None) -> str:
    if action is None:
        return "NO_ACTION"
    if action.kind == "query_evidence":
        return f"QUERY EVIDENCE {chr(ord('A') + action.query_fact)}"
    facts = [
        chr(ord("A") + i)
        for i in range(FACT_COUNT)
        if action.facts & fact_bit(i)
    ]
    return "CLAIM " + ",".join(facts)


def apply_round(
    states: list[State],
    actions: list[Action | None],
    topology: str,
) -> list[State]:
    snapshot = copy.deepcopy(states)
    next_state = copy.deepcopy(snapshot)
    received = [0] * WORKER_COUNT

    for sender, action in enumerate(actions):
        if action is None:
            continue

        if action.kind == "claim":
            if action.reset_sent:
                next_state[sender].sent = 0
            next_state[sender].sent |= action.facts
            next_state[sender].cursor = action.next_cursor

            for recipient in topology_neighbors(topology, sender):
                received[recipient] |= action.facts
            continue

        fact_mask = fact_bit(action.query_fact)
        for responder in topology_neighbors(topology, sender):
            if snapshot[responder].knowledge & fact_mask:
                received[sender] |= fact_mask

    for worker in range(WORKER_COUNT):
        next_state[worker].knowledge |= received[worker]

    return next_state


def collector_solved(states: list[State]) -> bool:
    return states[0].knowledge == FULL_MASK


def generation_seed(
    sampling_seed: int,
    round_number: int,
    worker: int,
) -> int:
    mixed = (
        sampling_seed * 1_000_003
        + round_number * 101
        + worker
    ) & ((1 << 64) - 1)
    return mixed & 0x7FFF_FFFF


def build_prompt(
    *,
    worker: int,
    topology: str,
    round_number: int,
    state: State,
    private_facts: int,
) -> str:
    neighbors = topology_neighbors(topology, worker - 1)
    neighbor_text = ",".join(f"Worker {i + 1}" for i in neighbors)
    return (
        PROTOCOL_SPEC
        + "\n"
        + f"Your identity: Worker {worker}\n"
        + f"Topology: {topology}\n"
        + f"Your neighbors: {neighbor_text}\n"
        + f"Round: {round_number}\n"
        + f"Your original private facts: {mask_text(private_facts)}\n"
        + f"Your current knowledge: {mask_text(state.knowledge)}\n"
        + f"Facts you have already claimed: {mask_text(state.sent)}\n"
        + "Collective objective: Worker 1 must learn A,B,C,D,E.\n"
        + "Choose your next interaction."
    )


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


def discover_model(
    base_url: str,
    api_key: str | None,
    timeout: float,
) -> str:
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


def build_payload(
    *,
    model: str,
    prompt: str,
    seed: int,
    mode: str,
    grammar: str,
    args: argparse.Namespace,
) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "seed": seed,
        "temperature": args.temperature,
        "top_p": args.top_p,
        "top_k": args.top_k,
        "max_tokens": args.max_tokens,
        "stream": False,
        "cache_prompt": False,
        "reasoning_effort": "none",
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if mode == CONSTRAINED:
        payload["grammar"] = grammar
    return payload


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


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(8 * 1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def deterministic_specs(plan: str) -> list[RunSpec]:
    if plan == "smoke":
        environments = [0]
        topologies = ["ring"]
    elif plan == "canonical":
        environments = [0, 1, 2]
        topologies = ["ring", "grid"]
    else:
        raise ValueError(plan)

    return [
        RunSpec(
            mix="deterministic_only",
            topology=topology,
            environment_seed=environment_seed,
            sampling_seed=0,
            mode=DETERMINISTIC_MODE,
            controller=DETERMINISTIC_CONTROLLER,
        )
        for topology in topologies
        for environment_seed in environments
    ]


def model_specs(plan: str) -> list[RunSpec]:
    specs: list[RunSpec] = []

    if plan == "smoke":
        # Exercise both decoding modes and both inference controllers in the
        # mixed arm, plus both modes for state-aware model-only.
        for mode in mode_order(0, 0):
            for controller in (STATE_AWARE, ALWAYS_REFRESH):
                specs.append(
                    RunSpec(
                        mix="mixed",
                        topology="ring",
                        environment_seed=0,
                        sampling_seed=0,
                        mode=mode,
                        controller=controller,
                    )
                )
            specs.append(
                RunSpec(
                    mix="model_only",
                    topology="ring",
                    environment_seed=0,
                    sampling_seed=0,
                    mode=mode,
                    controller=STATE_AWARE,
                )
            )
        return specs

    if plan != "canonical":
        raise ValueError(plan)

    # Canonical state-aware matrix:
    # 3 environments × 3 sampling seeds × 2 topologies × 2 mixes × 2 modes.
    for topology in ("ring", "grid"):
        for environment_seed in range(3):
            for sampling_seed in range(3):
                for mix in ("mixed", "model_only"):
                    for mode in mode_order(environment_seed, sampling_seed):
                        specs.append(
                            RunSpec(
                                mix=mix,
                                topology=topology,
                                environment_seed=environment_seed,
                                sampling_seed=sampling_seed,
                                mode=mode,
                                controller=STATE_AWARE,
                            )
                        )

    # Matched always-refresh audit subset:
    # env=0, sampling=0 across both topologies/mixes/modes.
    for topology in ("ring", "grid"):
        for mix in ("mixed", "model_only"):
            for mode in mode_order(0, 0):
                specs.append(
                    RunSpec(
                        mix=mix,
                        topology=topology,
                        environment_seed=0,
                        sampling_seed=0,
                        mode=mode,
                        controller=ALWAYS_REFRESH,
                    )
                )

    return specs


def max_model_calls(specs: Iterable[RunSpec]) -> int:
    result = 0
    for spec in specs:
        if spec.mix == "mixed":
            result += 2 * MAX_ROUNDS
        elif spec.mix == "model_only":
            result += WORKER_COUNT * MAX_ROUNDS
    return result


def write_header(handle) -> None:
    handle.write(
        "mix\ttopology\tenvironment_seed\tsampling_seed\tmode\tcontroller\t"
        "round\tworker\toperator_kind\tsource\tknowledge_before\tsent_before\t"
        "cursor_before\tmodel_seed\tcompletion_tokens\tlatency_us\ttoken_budget\t"
        "completion\n"
    )


def write_record(
    handle,
    *,
    spec: RunSpec,
    round_number: int,
    worker: int,
    kind: str,
    source: str,
    state: State,
    model_seed: int,
    completion_tokens: int,
    latency_us: int,
    token_budget: int,
    completion: str,
) -> None:
    handle.write(
        f"{spec.mix}\t{spec.topology}\t{spec.environment_seed}\t"
        f"{spec.sampling_seed}\t{spec.mode}\t{spec.controller}\t"
        f"{round_number}\t{worker}\t{kind}\t{source}\t"
        f"{state.knowledge}\t{state.sent}\t{state.cursor}\t"
        f"{model_seed}\t{completion_tokens}\t{latency_us}\t"
        f"{token_budget}\t{escape_completion(completion)}\n"
    )
    handle.flush()


def run_population(
    *,
    spec: RunSpec,
    handle,
    model: str | None,
    grammar: str,
    args: argparse.Namespace,
) -> tuple[bool, int, int]:
    states = initial_states(spec.environment_seed)
    private = [state.knowledge for state in states]
    caches = [Cache() for _ in range(WORKER_COUNT)]

    model_calls = 0
    final_round = 0

    for round_number in range(1, MAX_ROUNDS + 1):
        snapshot = copy.deepcopy(states)
        actions: list[Action | None] = [None] * WORKER_COUNT

        for operator_index in range(WORKER_COUNT):
            worker = operator_index + 1
            kind = operator_kind(spec.mix, operator_index)
            state = snapshot[operator_index]

            if kind == "deterministic":
                action = deterministic_action(
                    state,
                    operator_index,
                    round_number,
                    spec.environment_seed,
                )
                completion = canonical_action_text(action)
                actions[operator_index] = action
                write_record(
                    handle,
                    spec=spec,
                    round_number=round_number,
                    worker=worker,
                    kind=kind,
                    source="deterministic",
                    state=state,
                    model_seed=0,
                    completion_tokens=0,
                    latency_us=0,
                    token_budget=args.max_tokens,
                    completion=completion,
                )
                continue

            refresh = should_refresh(
                spec.controller,
                state,
                caches[operator_index],
            )

            if not refresh:
                action = caches[operator_index].action
                assert action is not None
                completion = canonical_action_text(action)
                actions[operator_index] = action
                write_record(
                    handle,
                    spec=spec,
                    round_number=round_number,
                    worker=worker,
                    kind=kind,
                    source="cache",
                    state=state,
                    model_seed=0,
                    completion_tokens=0,
                    latency_us=0,
                    token_budget=args.max_tokens,
                    completion=completion,
                )
                continue

            if model is None:
                raise RuntimeError("model run requested without discovered model")

            model_seed = generation_seed(
                spec.sampling_seed,
                round_number,
                worker,
            )
            prompt = build_prompt(
                worker=worker,
                topology=spec.topology,
                round_number=round_number,
                state=state,
                private_facts=private[operator_index],
            )
            payload = build_payload(
                model=model,
                prompt=prompt,
                seed=model_seed,
                mode=spec.mode,
                grammar=grammar,
                args=args,
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
                    f"backend error mix={spec.mix} topology={spec.topology} "
                    f"env={spec.environment_seed} sampling={spec.sampling_seed} "
                    f"mode={spec.mode} controller={spec.controller} "
                    f"round={round_number} worker={worker}: {exc}",
                    file=sys.stderr,
                )
                completion, tokens, latency_us = BACKEND_ERROR, 0, 0

            model_calls += 1
            caches[operator_index].initialized = True
            caches[operator_index].knowledge_at_refresh = state.knowledge
            caches[operator_index].action = None

            action = None
            if completion != BACKEND_ERROR:
                parsed = parse_action(completion)
                if parsed is not None and validate_model_action(parsed, state):
                    action = parsed
                    caches[operator_index].action = parsed

            actions[operator_index] = action
            write_record(
                handle,
                spec=spec,
                round_number=round_number,
                worker=worker,
                kind=kind,
                source="model_call",
                state=state,
                model_seed=model_seed,
                completion_tokens=tokens,
                latency_us=latency_us,
                token_budget=args.max_tokens,
                completion=completion,
            )

        states = apply_round(states, actions, spec.topology)
        final_round = round_number

        if collector_solved(states):
            break

    success = collector_solved(states)
    print(
        f"mix={spec.mix} topology={spec.topology} "
        f"env={spec.environment_seed} sampling={spec.sampling_seed} "
        f"mode={spec.mode} controller={spec.controller} "
        f"rounds={final_round} collector={mask_text(states[0].knowledge)} "
        f"essential={chr(ord('A') + essential_fact(spec.environment_seed))} "
        f"model_calls={model_calls} success={success}",
        file=sys.stderr,
    )
    return success, final_round, model_calls


def write_metadata(
    *,
    output_path: pathlib.Path,
    model: str,
    weights_path: pathlib.Path,
    weights_sha256: str,
    grammar: str,
    specs: list[RunSpec],
    args: argparse.Namespace,
) -> None:
    raw_sha = sha256_file(output_path)
    metadata = {
        "stage": "F4",
        "runner_version": RUNNER_VERSION,
        "raw_schema_version": RAW_SCHEMA_VERSION,
        "historical_stage3f0_blob": HISTORICAL_STAGE3F0_BLOB,
        "model": model,
        "weights_path": str(weights_path),
        "weights_sha256": weights_sha256,
        "raw_sha256": raw_sha,
        "base_url": args.base_url,
        "plan": args.plan,
        "population_runs": len(specs),
        "worker_count": WORKER_COUNT,
        "fact_count": FACT_COUNT,
        "collector_worker": 1,
        "mixed_model_workers": [2, 3],
        "bandwidth": BANDWIDTH,
        "max_rounds": MAX_ROUNDS,
        "temperature": args.temperature,
        "top_p": args.top_p,
        "top_k": args.top_k,
        "max_tokens": args.max_tokens,
        "cache_prompt": False,
        "reasoning_effort": "none",
        "chat_template_kwargs": {"enable_thinking": False},
        "prompt_spec_sha256": hashlib.sha256(
            PROTOCOL_SPEC.encode("utf-8")
        ).hexdigest(),
        "grammar_sha256": hashlib.sha256(
            grammar.encode("utf-8")
        ).hexdigest(),
        "grammar_path": args.grammar,
        "endpoint": "/v1/chat/completions",
        "environment_and_sampling_seeds_separate": True,
        "resume_supported": False,
    }
    pathlib.Path(str(output_path) + ".meta.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def self_test() -> None:
    states = initial_states(0)
    assert [state.knowledge for state in states] == [3, 6, 12, 24, 17]
    assert essential_fact(0) == 2
    assert operator_kind("mixed", 0) == "deterministic"
    assert operator_kind("mixed", 1) == "model"
    assert operator_kind("mixed", 2) == "model"
    assert generation_seed(0, 1, 1) == 102
    assert generation_seed(7, 1, 1) != generation_seed(8, 1, 1)

    assert parse_action("CLAIM A,C") == Action(
        kind="claim",
        facts=0b00101,
        selected=2,
    )
    assert parse_action("QUERY EVIDENCE D") == Action(
        kind="query_evidence",
        query_fact=3,
        selected=1,
    )
    assert parse_action("I think CLAIM A") is None

    state = State(knowledge=0b00111)
    assert not validate_model_action(
        Action(kind="claim", facts=0b00111, selected=3),
        state,
    )

    cache = Cache()
    assert should_refresh(STATE_AWARE, state, cache)
    cache = Cache(
        initialized=True,
        action=Action(kind="claim", facts=0b00001, selected=1),
        knowledge_at_refresh=state.knowledge,
    )
    assert not should_refresh(STATE_AWARE, state, cache)
    state.sent |= 0b00001
    assert should_refresh(STATE_AWARE, state, cache)

    assert effective_bandwidth() == 2
    action = deterministic_action(initial_states(0)[0], 0, 1, 0)
    assert action is not None
    assert 1 <= action.selected <= BANDWIDTH

    specs = model_specs("canonical")
    assert len(specs) == 80
    assert len(deterministic_specs("canonical")) == 6
    assert max_model_calls(specs) == 2800

    print("F4 runner self-test passed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--model", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--grammar", default="grammars/f4.gbnf")
    parser.add_argument("--weights", default=DEFAULT_WEIGHTS)
    parser.add_argument("--output", default="trials/f4-raw.tsv")
    parser.add_argument(
        "--plan",
        choices=("smoke", "canonical"),
        default="smoke",
    )
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

    if args.max_tokens <= 0:
        raise SystemExit("--max-tokens must be > 0")
    if args.plan == "canonical" and args.max_tokens != 32:
        raise SystemExit("canonical F4 requires --max-tokens 32")

    grammar_path = pathlib.Path(args.grammar)
    grammar = grammar_path.read_text(encoding="utf-8")

    controls = deterministic_specs(args.plan)
    model_runs = model_specs(args.plan)
    specs = controls + model_runs

    if args.dry_run:
        print(f"plan: {args.plan}")
        print(f"deterministic population runs: {len(controls)}")
        print(f"model-backed population runs: {len(model_runs)}")
        print(f"total population runs: {len(specs)}")
        print(f"maximum live model calls: {max_model_calls(model_runs)}")
        print(f"max rounds: {MAX_ROUNDS}")
        print(f"workers: {WORKER_COUNT}")
        return 0

    weights_path = pathlib.Path(args.weights).expanduser().resolve()
    if not weights_path.is_file():
        raise SystemExit(f"weights file not found: {weights_path}")

    print(f"+ sha256 weights {weights_path}", file=sys.stderr)
    weights_sha256 = sha256_file(weights_path)

    model = args.model or discover_model(
        args.base_url,
        args.api_key,
        args.timeout,
    )

    output_path = pathlib.Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(
        f"F4: plan={args.plan} model={model!r} runs={len(specs)} "
        f"max_live_calls={max_model_calls(model_runs)} output={output_path}",
        file=sys.stderr,
    )

    with output_path.open("w", encoding="utf-8", newline="") as handle:
        write_header(handle)
        for spec in specs:
            run_population(
                spec=spec,
                handle=handle,
                model=model if spec.mix != "deterministic_only" else None,
                grammar=grammar,
                args=args,
            )

    write_metadata(
        output_path=output_path,
        model=model,
        weights_path=weights_path,
        weights_sha256=weights_sha256,
        grammar=grammar,
        specs=specs,
        args=args,
    )

    print(
        f"done: raw_sha256={sha256_file(output_path)} "
        f"weights_sha256={weights_sha256}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
