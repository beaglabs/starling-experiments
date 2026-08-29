#!/usr/bin/env python3
"""F1a canonical contested-environment evidence gate."""

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
EXPECTED_ROWS = 432
PROFILES = {
    "theta37",
    "theta51",
    "theta93",
    "round_robin",
    "seeded",
    "novel_first",
}
TOPOLOGIES = {"ring", "grid"}
FAULTS = {
    "no_fault",
    "loss_50",
    "loss_200",
    "duplication_250",
    "latency_jitter",
    "reordering",
    "partition",
    "crash_restart_persist",
    "crash_restart_reset",
    "stale_view",
    "queue_capacity",
    "combined",
}


def run(*args: str, capture: bool = False) -> subprocess.CompletedProcess[bytes]:
    print("+", " ".join(args), file=sys.stderr)
    return subprocess.run(
        args,
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE if capture else None,
    )


def capture_text(*args: str) -> str:
    return run(*args, capture=True).stdout.decode().strip()


def parse_matrix(data: bytes) -> list[dict[str, str]]:
    text = data.decode("utf-8")
    return list(csv.DictReader(io.StringIO(text), delimiter="\t"))


def int_field(row: dict[str, str], key: str) -> int:
    return int(row[key], 10)


def main() -> int:
    if shutil.which("zig") is None:
        raise SystemExit("zig 0.16.0 is required on PATH")

    version = capture_text("zig", "version")
    if version != "0.16.0":
        raise SystemExit(f"zig 0.16.0 is required; found {version}")

    run("zig", "build", "test")
    run("zig", "build", "-Doptimize=ReleaseFast", "run-f1a", "--", "validate")

    first = run(
        "zig",
        "build",
        "-Doptimize=ReleaseFast",
        "run-f1a",
        "--",
        "matrix",
        capture=True,
    ).stdout
    second = run(
        "zig",
        "build",
        "-Doptimize=ReleaseFast",
        "run-f1a",
        "--",
        "matrix",
        capture=True,
    ).stdout

    if first != second:
        raise SystemExit("F1a matrix replay is not byte-identical")

    rows = parse_matrix(first)
    if len(rows) != EXPECTED_ROWS:
        raise SystemExit(f"expected {EXPECTED_ROWS} rows; found {len(rows)}")

    if {row["profile"] for row in rows} != PROFILES:
        raise SystemExit("profile axis does not match frozen F1a set")
    if {row["topology"] for row in rows} != TOPOLOGIES:
        raise SystemExit("topology axis does not match frozen F1a set")
    if {row["fault"] for row in rows} != FAULTS:
        raise SystemExit("fault axis does not match frozen F1a set")
    if {row["world_seed"] for row in rows} != {"0", "1", "2"}:
        raise SystemExit("seed axis does not match frozen F1a set")

    failures: list[str] = []
    for index, row in enumerate(rows, start=1):
        prefix = (
            f"row {index} {row['profile']}/{row['topology']}/"
            f"{row['world_seed']}/{row['fault']}"
        )
        if row["envelope_accounted"] != "yes":
            failures.append(f"{prefix}: envelope accounting failed")
        if row["missing_accounted"] != "yes":
            failures.append(f"{prefix}: missing-fact accounting failed")
        if row["fully_accounted"] != "yes":
            failures.append(f"{prefix}: full accounting failed")
        if int_field(row, "unattributed") != 0:
            failures.append(f"{prefix}: unattributed missing facts")
        if int_field(row, "violations") != 0:
            failures.append(f"{prefix}: protocol violations")
        if row["fault"] == "no_fault" and row["success"] != "yes":
            failures.append(f"{prefix}: no-fault world did not converge")

    by_fault: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_fault[row["fault"]].append(row)

    def require_positive(fault: str, field: str) -> None:
        if sum(int_field(row, field) for row in by_fault[fault]) == 0:
            failures.append(f"{fault}: injected fault produced no {field} events")

    require_positive("loss_50", "dropped")
    require_positive("loss_200", "dropped")
    require_positive("duplication_250", "duplicate_copies")
    require_positive("reordering", "forced_reorder_schedules")
    require_positive("reordering", "reordered")
    require_positive("partition", "partitioned")
    require_positive("crash_restart_persist", "crashed")
    require_positive("crash_restart_reset", "crashed")
    require_positive("stale_view", "stale_observations")
    require_positive("queue_capacity", "queue_overflow")
    require_positive("combined", "dropped")
    require_positive("combined", "duplicate_copies")
    require_positive("combined", "partitioned")

    if failures:
        for failure in failures[:50]:
            print(f"FAIL: {failure}", file=sys.stderr)
        if len(failures) > 50:
            print(f"... {len(failures) - 50} more failures", file=sys.stderr)
        return 1

    trial = ROOT / "trials" / "f1a-fault-matrix.tsv"
    trial.parent.mkdir(parents=True, exist_ok=True)
    trial.write_bytes(first)

    digest = hashlib.sha256(first).hexdigest()
    print(f"F1a rows: {len(rows)}")
    print(f"F1a bytes: {len(first)}")
    print(f"F1a sha256: {digest}")
    print("byte_identical_replay: yes")
    print("envelope_accounting_failures: 0")
    print("missing_accounting_failures: 0")
    print("unattributed_missing: 0")
    print("violations: 0")

    by_profile: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_profile[row["profile"]].append(row)

    for profile in sorted(PROFILES):
        profile_rows = by_profile[profile]
        successes = sum(row["success"] == "yes" for row in profile_rows)
        missing = sum(int_field(row, "missing") for row in profile_rows)
        communication = sum(
            int_field(row, "communication_units") for row in profile_rows
        )
        print(
            f"profile {profile}: successes={successes}/{len(profile_rows)} "
            f"terminal_missing={missing} communication_units={communication}"
        )

    print("F1a PASS: contested matrix is deterministic and fully attributed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
