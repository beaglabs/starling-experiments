#!/usr/bin/env python3
"""F4 heterogeneous live-model evidence gate."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import pathlib
import shutil
import subprocess
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_RAW = ROOT / "trials" / "f4-raw.tsv"
SUMMARY = ROOT / "trials" / "f4-heterogeneous.tsv"

EXPECTED_HISTORICAL_BLOB = "f84098b579fda8c472eac0f16e9e05e3d534bf16"
MODES = ("typed_unconstrained", "cfg_constrained")
TOPOLOGIES = ("ring", "grid")
MIXES = ("mixed", "model_only")


def run(
    *args: str,
    capture: bool = False,
    timeout_s: int | None = None,
) -> subprocess.CompletedProcess[bytes]:
    print("+", " ".join(args), file=sys.stderr)
    proc = subprocess.run(
        args,
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        timeout=timeout_s,
    )
    if proc.returncode != 0:
        if capture and proc.stdout:
            sys.stdout.write(proc.stdout.decode(errors="replace"))
        if capture and proc.stderr:
            sys.stderr.write(proc.stderr.decode(errors="replace"))
        raise SystemExit(
            f"F4 engineering gate failed: {' '.join(args)} "
            f"exit={proc.returncode}"
        )
    return proc


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(8 * 1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def integer(row: dict[str, str], field: str) -> int:
    base = 16 if field == "trajectory_hash" else 10
    return int(row[field], base)


def expected_keys(plan: str) -> set[tuple[str, str, int, int, str, str]]:
    keys: set[tuple[str, str, int, int, str, str]] = set()

    if plan == "smoke":
        keys.add(
            (
                "deterministic_only",
                "ring",
                0,
                0,
                "deterministic",
                "deterministic",
            )
        )
        for mode in MODES:
            for controller in ("knowledge_or_stale", "always_refresh"):
                keys.add(("mixed", "ring", 0, 0, mode, controller))
            keys.add(
                (
                    "model_only",
                    "ring",
                    0,
                    0,
                    mode,
                    "knowledge_or_stale",
                )
            )
        return keys

    if plan != "canonical":
        raise SystemExit(f"unknown F4 plan in metadata: {plan}")

    for topology in TOPOLOGIES:
        for env in range(3):
            keys.add(
                (
                    "deterministic_only",
                    topology,
                    env,
                    0,
                    "deterministic",
                    "deterministic",
                )
            )

    for topology in TOPOLOGIES:
        for env in range(3):
            for sampling in range(3):
                for mix in MIXES:
                    for mode in MODES:
                        keys.add(
                            (
                                mix,
                                topology,
                                env,
                                sampling,
                                mode,
                                "knowledge_or_stale",
                            )
                        )

    for topology in TOPOLOGIES:
        for mix in MIXES:
            for mode in MODES:
                keys.add(
                    (
                        mix,
                        topology,
                        0,
                        0,
                        mode,
                        "always_refresh",
                    )
                )

    return keys


def validate_metadata(
    raw_path: pathlib.Path,
) -> dict:
    meta_path = pathlib.Path(str(raw_path) + ".meta.json")
    if not meta_path.is_file():
        raise SystemExit(f"F4 metadata sidecar missing: {meta_path}")

    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    required = {
        "stage": "F4",
        "runner_version": 1,
        "raw_schema_version": 1,
        "historical_stage3f0_blob": EXPECTED_HISTORICAL_BLOB,
        "worker_count": 5,
        "fact_count": 5,
        "collector_worker": 1,
        "mixed_model_workers": [2, 3],
        "bandwidth": 2,
        "max_rounds": 10,
        "max_tokens": 32,
        "cache_prompt": False,
        "reasoning_effort": "none",
        "environment_and_sampling_seeds_separate": True,
        "resume_supported": False,
    }
    for key, expected in required.items():
        if metadata.get(key) != expected:
            raise SystemExit(
                f"F4 metadata mismatch {key}: "
                f"{metadata.get(key)!r} != {expected!r}"
            )

    raw_sha = sha256_file(raw_path)
    if metadata.get("raw_sha256") != raw_sha:
        raise SystemExit(
            f"F4 raw SHA mismatch: {metadata.get('raw_sha256')} != {raw_sha}"
        )

    for key in (
        "weights_sha256",
        "runner_sha256",
        "prompt_spec_sha256",
        "grammar_sha256",
        "raw_sha256",
    ):
        value = metadata.get(key)
        if not isinstance(value, str) or len(value) != 64:
            raise SystemExit(f"invalid F4 metadata digest {key}: {value!r}")

    runner_sha = hashlib.sha256(
        (ROOT / "tools" / "f4_llama_cpp.py").read_bytes()
    ).hexdigest()
    if metadata.get("runner_sha256") != runner_sha:
        raise SystemExit(
            f"F4 runner SHA mismatch: {metadata.get('runner_sha256')} != "
            f"{runner_sha}"
        )

    grammar_sha = hashlib.sha256(
        (ROOT / "grammars" / "f4.gbnf").read_bytes()
    ).hexdigest()
    if metadata.get("grammar_sha256") != grammar_sha:
        raise SystemExit(
            f"F4 grammar SHA mismatch: {metadata.get('grammar_sha256')} != "
            f"{grammar_sha}"
        )

    return metadata


def parse_summary(data: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(data.decode()), delimiter="	"))


def validate_rows(
    rows: list[dict[str, str]],
    plan: str,
) -> None:
    expected = expected_keys(plan)
    actual = {
        (
            row["mix"],
            row["topology"],
            integer(row, "environment_seed"),
            integer(row, "sampling_seed"),
            row["mode"],
            row["controller"],
        )
        for row in rows
    }
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise SystemExit(
            f"F4 run matrix mismatch: missing={missing} extra={extra}"
        )
    if len(rows) != len(expected):
        raise SystemExit(
            f"F4 duplicate summary keys: rows={len(rows)} "
            f"unique={len(actual)}"
        )

    for row in rows:
        label = (
            f"{row['mix']}/{row['topology']}/env={row['environment_seed']}/"
            f"sampling={row['sampling_seed']}/{row['mode']}/"
            f"{row['controller']}"
        )

        if (
            integer(row, "communication_units")
            != integer(row, "control_units")
            + integer(row, "useful_deliveries")
            + integer(row, "duplicate_deliveries")
        ):
            raise SystemExit(f"F4 communication identity failed in {label}")

        if integer(row, "token_budget_violations") != 0:
            raise SystemExit(f"F4 token-budget violation in {label}")

        mix = row["mix"]
        rounds = integer(row, "rounds")
        model_calls = integer(row, "model_calls")
        reuse = integer(row, "cache_reuses")
        det = integer(row, "deterministic_decisions")
        protocol = integer(row, "protocol_actions")
        accepted = integer(row, "accepted_model_actions")
        invalid = integer(row, "invalid_actions")
        backend = integer(row, "backend_errors")
        semantic = integer(row, "semantic_rejections")

        if protocol != accepted + semantic:
            raise SystemExit(f"F4 protocol/semantic partition failed in {label}")
        if model_calls != protocol + invalid + backend:
            raise SystemExit(f"F4 model-call partition failed in {label}")

        model_per_round = {
            "deterministic_only": 0,
            "mixed": 2,
            "model_only": 5,
        }[mix]
        det_per_round = {
            "deterministic_only": 5,
            "mixed": 3,
            "model_only": 0,
        }[mix]

        if model_calls + reuse != rounds * model_per_round:
            raise SystemExit(
                f"F4 model decision accounting failed in {label}: "
                f"{model_calls}+{reuse} != {rounds}*{model_per_round}"
            )
        if det != rounds * det_per_round:
            raise SystemExit(
                f"F4 deterministic decision accounting failed in {label}: "
                f"{det} != {rounds}*{det_per_round}"
            )

        if mix == "deterministic_only":
            if row["success"] != "yes":
                raise SystemExit(f"F4 deterministic control failed in {label}")
            if (
                model_calls != 0
                or reuse != 0
                or invalid != 0
                or backend != 0
                or semantic != 0
            ):
                raise SystemExit(
                    f"F4 deterministic control contains model metrics in {label}"
                )

        if row["mode"] == "cfg_constrained" and invalid != 0:
            raise SystemExit(
                f"F4 constrained syntax produced invalid action in {label}"
            )

        if mix == "mixed" and row["success"] == "yes":
            if row["essential_reached_deterministic"] != "yes":
                raise SystemExit(
                    f"F4 mixed success lacks essential model->det transfer "
                    f"in {label}"
                )


def aggregate_rows(
    rows: list[dict[str, str]],
) -> dict[tuple[str, str, str, str], dict[str, int]]:
    groups: dict[tuple[str, str, str, str], dict[str, int]] = {}
    for row in rows:
        key = (
            row["mix"],
            row["topology"],
            row["mode"],
            row["controller"],
        )
        group = groups.setdefault(
            key,
            {
                "runs": 0,
                "successes": 0,
                "model_calls": 0,
                "cache_reuses": 0,
                "invalid": 0,
                "semantic": 0,
                "communication": 0,
                "useful": 0,
                "duplicate": 0,
            },
        )
        group["runs"] += 1
        group["successes"] += row["success"] == "yes"
        group["model_calls"] += integer(row, "model_calls")
        group["cache_reuses"] += integer(row, "cache_reuses")
        group["invalid"] += integer(row, "invalid_actions")
        group["semantic"] += integer(row, "semantic_rejections")
        group["communication"] += integer(row, "communication_units")
        group["useful"] += integer(row, "useful_deliveries")
        group["duplicate"] += integer(row, "duplicate_deliveries")
    return groups


def successful_diversity(
    rows: list[dict[str, str]],
) -> list[tuple[str, int, str, int, int]]:
    buckets: dict[
        tuple[str, int, str], list[dict[str, str]]
    ] = defaultdict(list)

    for row in rows:
        if (
            row["mix"] == "mixed"
            and row["controller"] == "knowledge_or_stale"
        ):
            buckets[
                (
                    row["topology"],
                    integer(row, "environment_seed"),
                    row["mode"],
                )
            ].append(row)

    result: list[tuple[str, int, str, int, int]] = []
    for (topology, env, mode), bucket in sorted(buckets.items()):
        successful = [row for row in bucket if row["success"] == "yes"]
        unique = {
            integer(row, "trajectory_hash")
            for row in successful
        }
        result.append((topology, env, mode, len(successful), len(unique)))
    return result


def canonical_outcome(rows: list[dict[str, str]]) -> str:
    # F4 validates heterogeneous operator neutrality if at least one decode
    # treatment achieves >= 2/3 success on the 9 state-aware mixed runs for
    # each topology, and at least one fixed environment exhibits multiple
    # successful trajectories across sampling seeds.
    topology_ok: dict[str, bool] = {}
    for topology in TOPOLOGIES:
        counts = []
        for mode in MODES:
            bucket = [
                row
                for row in rows
                if row["mix"] == "mixed"
                and row["topology"] == topology
                and row["mode"] == mode
                and row["controller"] == "knowledge_or_stale"
            ]
            if len(bucket) != 9:
                raise SystemExit(
                    f"F4 expected 9 state-aware mixed runs for "
                    f"{topology}/{mode}; found {len(bucket)}"
                )
            counts.append(sum(row["success"] == "yes" for row in bucket))
        topology_ok[topology] = max(counts) >= 6

    diversity = successful_diversity(rows)
    diversity_ok = any(
        successful >= 2 and unique >= 2
        for _, _, _, successful, unique in diversity
    )

    return (
        "PASS"
        if all(topology_ok.values()) and diversity_ok
        else "LIMITATION"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", default=str(DEFAULT_RAW))
    args = parser.parse_args()

    if shutil.which("zig") is None:
        raise SystemExit("zig 0.16.0 is required on PATH")

    version = run("zig", "version", capture=True).stdout.decode().strip()
    if version != "0.16.0":
        raise SystemExit(f"zig 0.16.0 is required; found {version}")

    run("zig", "build", "test", timeout_s=1800)

    validation = run(
        "zig",
        "build",
        "-Doptimize=ReleaseFast",
        "run-f4",
        "--",
        "validate",
        capture=True,
        timeout_s=300,
    )
    validation_text = validation.stdout.decode()
    print(
        validation_text,
        end="" if validation_text.endswith("
") else "
",
    )

    run("python3", "tools/f4_llama_cpp.py", "--self-test", timeout_s=300)
    run(
        "python3",
        "tools/f4_llama_cpp.py",
        "--plan",
        "smoke",
        "--dry-run",
        timeout_s=300,
    )
    run(
        "python3",
        "tools/f4_llama_cpp.py",
        "--plan",
        "canonical",
        "--dry-run",
        timeout_s=300,
    )

    raw_path = pathlib.Path(args.raw)
    if not raw_path.is_file():
        raise SystemExit(
            f"F4 raw trial missing: {raw_path}
"
            "Run tools/f4_llama_cpp.py with --plan smoke or --plan canonical."
        )

    metadata = validate_metadata(raw_path)
    plan = str(metadata["plan"])

    first = run(
        "zig",
        "build",
        "-Doptimize=ReleaseFast",
        "run-f4",
        "--",
        "replay",
        str(raw_path),
        capture=True,
        timeout_s=1800,
    ).stdout
    second = run(
        "zig",
        "build",
        "-Doptimize=ReleaseFast",
        "run-f4",
        "--",
        "replay",
        str(raw_path),
        capture=True,
        timeout_s=1800,
    ).stdout
    if first != second:
        raise SystemExit("F4 deterministic replay summary is not byte-identical")

    rows = parse_summary(first)
    validate_rows(rows, plan)

    backend_errors = sum(integer(row, "backend_errors") for row in rows)
    if backend_errors != 0:
        raise SystemExit(
            f"F4 backend/server failures observed: {backend_errors}; "
            "this is an engineering failure, not a scientific limitation"
        )

    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY.write_bytes(first)

    summary_sha = sha256_bytes(first)
    raw_sha = str(metadata["raw_sha256"])
    weights_sha = str(metadata["weights_sha256"])

    print(f"F4 plan: {plan}")
    print(f"F4 rows: {len(rows)}")
    print(f"F4 bytes: {len(first)}")
    print(f"F4 sha256: {summary_sha}")
    print(f"F4 raw_sha256: {raw_sha}")
    print(f"F4 weights_sha256: {weights_sha}")
    print("F4 byte_identical_replay: yes")
    print("F4 backend_errors: 0")
    print("F4 token_budget_violations: 0")

    groups = aggregate_rows(rows)
    for key in sorted(groups):
        group = groups[key]
        print(
            "group "
            f"mix={key[0]} topology={key[1]} mode={key[2]} "
            f"controller={key[3]} "
            f"successes={group['successes']}/{group['runs']} "
            f"model_calls={group['model_calls']} "
            f"cache_reuses={group['cache_reuses']} "
            f"invalid={group['invalid']} "
            f"semantic_rejections={group['semantic']} "
            f"communication={group['communication']} "
            f"useful={group['useful']} "
            f"duplicate={group['duplicate']}"
        )

    diversity = successful_diversity(rows)
    for topology, env, mode, successful, unique in diversity:
        print(
            "diversity "
            f"topology={topology} env={env} mode={mode} "
            f"successful={successful} unique={unique}"
        )

    if plan == "smoke":
        accepted = sum(integer(row, "accepted_model_actions") for row in rows)
        if accepted == 0:
            raise SystemExit(
                "F4 smoke produced no accepted model-backed protocol action"
            )
        print(
            "F4 SMOKE PASS: live model adapter, authoritative replay, "
            "and accounting are structurally valid"
        )
        return 0

    if plan != "canonical":
        raise SystemExit(f"unsupported F4 plan: {plan}")

    outcome = canonical_outcome(rows)
    print(
        f"F4 {outcome}: heterogeneous model-backed operator evidence complete"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
