#!/usr/bin/env python3
"""F2.1 synchronous-to-asynchronous gap evidence gate."""

from __future__ import annotations

import csv
import hashlib
import io
import pathlib
import shutil
import subprocess
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "zig-out" / "bin" / "run-f2-1"
TRIAL = ROOT / "trials" / "f2-gap.tsv"

EXPECTED_ROWS = 36
DECISION_BUDGET = 4096
POPULATION = 8


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
        if capture and proc.stderr:
            stderr = proc.stderr.decode(errors="replace")
            if stderr:
                print(
                    stderr,
                    end="" if stderr.endswith("\n") else "\n",
                    file=sys.stderr,
                )
        raise SystemExit(
            f"F2.1 engineering gate failed: {' '.join(args)} "
            f"exit={proc.returncode}"
        )
    return proc


def parse_tsv(data: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(data.decode()), delimiter="\t"))


def integer(row: dict[str, str], field: str) -> int:
    return int(row[field], 10)


def validate_rows(rows: list[dict[str, str]]) -> None:
    if len(rows) != EXPECTED_ROWS:
        raise SystemExit(
            f"expected {EXPECTED_ROWS} F2.1 rows; got {len(rows)}"
        )

    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = (row["profile"], row["topology"], row["seed"])
        if key in seen:
            raise SystemExit(f"duplicate F2.1 world: {key}")
        seen.add(key)

        if integer(row, "decision_budget") != DECISION_BUDGET:
            raise SystemExit(f"decision-budget drift: {key}")
        if integer(row, "violations") != 0:
            raise SystemExit(f"protocol violation in F2.1 world: {key}")
        if row["async_accounted"] != "yes":
            raise SystemExit(f"async envelope accounting failure: {key}")
        if row["async_communication_accounted"] != "yes":
            raise SystemExit(f"async communication accounting failure: {key}")
        if row["stage7c_parity"] == "no":
            raise SystemExit(
                f"derived async harness diverged from frozen Stage 7C: {key}"
            )
        if row["stage7c_parity"] not in {"yes", "budget_bound"}:
            raise SystemExit(
                f"unknown Stage 7C parity state for {key}: "
                f"{row['stage7c_parity']}"
            )

        sync_rounds = integer(row, "sync_rounds")
        sync_calls = integer(row, "sync_policy_calls")
        if sync_calls != sync_rounds * POPULATION:
            raise SystemExit(
                f"sync decision accounting mismatch for {key}: "
                f"calls={sync_calls} rounds={sync_rounds}"
            )
        if row["sync_success"] == "no" and sync_rounds != DECISION_BUDGET:
            raise SystemExit(
                f"sync failure censored before budget for {key}: "
                f"rounds={sync_rounds}"
            )

        async_min = integer(row, "async_min_decisions")
        async_max = integer(row, "async_max_decisions")
        async_calls = integer(row, "async_policy_ticks")
        if not (async_min * POPULATION <= async_calls <= async_max * POPULATION):
            raise SystemExit(
                f"async local-decision bounds inconsistent for {key}"
            )

        if row["async_success"] == "yes":
            if row["async_censored"] != "no":
                raise SystemExit(f"successful async row marked censored: {key}")
            if row["stage7c_parity"] != "yes":
                raise SystemExit(
                    f"successful async row lacks frozen Stage 7C parity: {key}"
                )
        else:
            if row["async_censored"] != "yes":
                raise SystemExit(
                    f"failed async row is not explicitly censored: {key}"
                )
            if async_min != DECISION_BUDGET or async_max != DECISION_BUDGET:
                raise SystemExit(
                    f"async failure did not consume equal per-operator budget: "
                    f"{key} min={async_min} max={async_max}"
                )
            if row["stage7c_parity"] != "budget_bound":
                raise SystemExit(
                    f"budget-censored row has invalid parity state: {key}"
                )


def main() -> int:
    if shutil.which("zig") is None:
        raise SystemExit("zig 0.16.0 is required on PATH")

    version = run("zig", "version", capture=True).stdout.decode().strip()
    if version != "0.16.0":
        raise SystemExit(f"zig 0.16.0 is required; found {version}")

    run("zig", "build", "test", timeout_s=600)
    run("zig", "build", "-Doptimize=ReleaseFast", timeout_s=600)
    if not BIN.exists():
        raise SystemExit("F2.1 release runner was not installed")

    validation = run(str(BIN), "validate", capture=True, timeout_s=180)
    validation_text = validation.stdout.decode()
    print(validation_text, end="" if validation_text.endswith("\n") else "\n")
    required_validation = (
        "rows: 36",
        "violations: 0",
        "stage7c_parity_failures: 0",
        "accounting_failures: 0",
        "communication_failures: 0",
        "invalid_censoring: 0",
    )
    for marker in required_validation:
        if marker not in validation_text:
            raise SystemExit(
                f"F2.1 validation summary missing required marker: {marker}"
            )

    first = run(str(BIN), "gap", capture=True, timeout_s=180).stdout
    second = run(str(BIN), "gap", capture=True, timeout_s=180).stdout
    if first != second:
        raise SystemExit(
            "F2.1 gap dataset is not byte-identical across replay"
        )

    rows = parse_tsv(first)
    validate_rows(rows)

    TRIAL.parent.mkdir(parents=True, exist_ok=True)
    TRIAL.write_bytes(first)
    digest = hashlib.sha256(first).hexdigest()

    print(f"F2.1 rows: {len(rows)}")
    print(f"F2.1 bytes: {len(first)}")
    print(f"F2.1 sha256: {digest}")
    print("F2.1 byte_identical_replay: yes")
    print(
        "F2.1 stage7c_parity_rows: "
        f"{sum(r['stage7c_parity'] == 'yes' for r in rows)}/{len(rows)}"
    )
    print(
        "F2.1 budget_bound_rows: "
        f"{sum(r['stage7c_parity'] == 'budget_bound' for r in rows)}"
    )
    print(
        "F2.1 sync_successes: "
        f"{sum(r['sync_success'] == 'yes' for r in rows)}/{len(rows)}"
    )
    print(
        "F2.1 async_successes: "
        f"{sum(r['async_success'] == 'yes' for r in rows)}/{len(rows)}"
    )
    print(
        "F2.1 async_censored: "
        f"{sum(r['async_censored'] == 'yes' for r in rows)}"
    )

    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["profile"]].append(row)

    for profile in sorted(grouped):
        profile_rows = grouped[profile]
        sync_success = sum(
            r["sync_success"] == "yes" for r in profile_rows
        )
        async_success = sum(
            r["async_success"] == "yes" for r in profile_rows
        )
        comm_delta = sum(
            integer(r, "communication_delta") for r in profile_rows
        )
        duplicate_delta = sum(
            integer(r, "duplicate_delta") for r in profile_rows
        )
        policy_delta = sum(
            integer(r, "policy_call_delta") for r in profile_rows
        )
        tick_delta = sum(
            integer(r, "tick_round_delta") for r in profile_rows
        )
        print(
            f"gap {profile}: "
            f"sync_success={sync_success}/{len(profile_rows)} "
            f"async_success={async_success}/{len(profile_rows)} "
            f"communication_delta={comm_delta} "
            f"duplicate_delta={duplicate_delta} "
            f"policy_call_delta={policy_delta} "
            f"tick_round_delta={tick_delta}"
        )

    print(
        "F2.1 PASS: paired synchronous/asynchronous gap dataset is "
        "deterministic, budget-matched, and Stage-7C-parity checked"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
