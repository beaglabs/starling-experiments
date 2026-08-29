#!/usr/bin/env python3
"""F3 local-inference-control evidence gate."""

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
TRIAL = ROOT / "trials" / "f3-inference-control.tsv"

EXPECTED_STAGE7B_BLOB = "e91f88b2ea2dafd6bd51113954ff03aee4330163"
EXPECTED_STAGE7B_REPORT = (
    "e3d27eec1f7bb78d5cabf869fc5172c3746a356f7f4cd9db4cc91f657e01ff2f"
)
EXPECTED_CANDIDATES = 134
EXPECTED_CORNER_CHECKS = 268
HARD_SPLITS = {
    "population_N_128",
    "density_F_over_N_4",
    "redundancy_R_4",
    "bandwidth_B_8",
    "topology_complete",
    "compound",
}


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
            f"F3 engineering gate failed: {' '.join(args)} "
            f"exit={proc.returncode}"
        )
    return proc


def run_f3(
    command: str,
    *,
    capture: bool = False,
    timeout_s: int = 1800,
) -> subprocess.CompletedProcess[bytes]:
    return run(
        "zig",
        "build",
        "-Doptimize=ReleaseFast",
        "run-f3",
        "--",
        command,
        capture=capture,
        timeout_s=timeout_s,
    )


def parse_tsv(data: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(data.decode()), delimiter="\t"))


def integer(row: dict[str, str], field: str) -> int:
    return int(row[field], 10)


def require_marker(text: str, marker: str) -> None:
    if marker not in text:
        raise SystemExit(f"F3 validation output missing marker: {marker}")


def validate_rows(rows: list[dict[str, str]]) -> dict[str, object]:
    if not rows:
        raise SystemExit("F3 search emitted no evidence rows")

    for row in rows:
        candidate = f"{row['phase']} id={row['id']}"
        if integer(row, "violations") != 0:
            raise SystemExit(f"F3 protocol violation in {candidate}")
        if row["inference_accounted"] != "yes":
            raise SystemExit(f"F3 inference accounting flag failed in {candidate}")

        computation = integer(row, "computation_sum")
        inference = integer(row, "inference_sum")
        reuse = integer(row, "cache_reuse_sum")
        if computation != inference + reuse:
            raise SystemExit(
                f"F3 inference identity failed in {candidate}: "
                f"{computation} != {inference} + {reuse}"
            )
        if inference > computation:
            raise SystemExit(f"F3 inference exceeds computation in {candidate}")
        if integer(row, "c") == 1000:
            if reuse != 0 or inference != computation:
                raise SystemExit(
                    f"F3 c=1000 row is not fully refreshed in {candidate}"
                )

    training = [row for row in rows if row["phase"] == "training"]
    if len(training) != EXPECTED_CANDIDATES:
        raise SystemExit(
            f"expected {EXPECTED_CANDIDATES} training rows; found {len(training)}"
        )
    training_ids = {integer(row, "id") for row in training}
    if training_ids != set(range(EXPECTED_CANDIDATES)):
        raise SystemExit("F3 training candidate IDs are not exactly 0..133")

    training_frontier = {
        integer(row, "id")
        for row in training
        if row["selected_frontier"] == "yes"
    }
    if not training_frontier:
        raise SystemExit("F3 training frontier is empty")

    validation = [row for row in rows if row["phase"] == "validation"]
    if not validation:
        raise SystemExit("F3 validation phase is empty")
    validation_by_id = {integer(row, "id"): row for row in validation}

    selected = [
        row
        for row in validation
        if row["selected_frontier"] == "yes"
    ]
    if not selected:
        raise SystemExit("F3 validation frontier is empty")

    selected_ids = {integer(row, "id") for row in selected}
    if not selected_ids.issubset(training_frontier):
        raise SystemExit(
            "F3 validation frontier contains candidate absent from training frontier"
        )

    twins = {
        integer(row, "id"): row
        for row in rows
        if row["phase"] == "validation_ungated_twin"
    }

    selected_gated = [
        row for row in selected if integer(row, "c") < 1000
    ]
    for row in selected_gated:
        candidate_id = integer(row, "id")
        if candidate_id not in twins:
            raise SystemExit(
                f"selected gated candidate {candidate_id} lacks ungated twin"
            )
        twin = twins[candidate_id]
        for field in ("n", "e", "r", "u"):
            if row[field] != twin[field]:
                raise SystemExit(
                    f"candidate {candidate_id} twin changed base theta field {field}"
                )
        if integer(twin, "c") != 1000:
            raise SystemExit(
                f"candidate {candidate_id} ungated twin does not have c=1000"
            )

    hard_rows = [row for row in rows if row["phase"] == "hard"]
    hard_by_id: dict[int, set[str]] = defaultdict(set)
    for row in hard_rows:
        hard_by_id[integer(row, "id")].add(row["split"])

    required_hard_ids = selected_ids | {0, 1, 2}
    for candidate_id in required_hard_ids:
        if hard_by_id[candidate_id] != HARD_SPLITS:
            raise SystemExit(
                f"F3 hard split coverage incomplete for id={candidate_id}: "
                f"{sorted(hard_by_id[candidate_id])}"
            )

    hard_frozen = [
        row for row in rows if row["phase"] == "hard_frozen_stage7b"
    ]
    if len(hard_frozen) != 18:
        raise SystemExit(
            f"expected 18 frozen Stage 7B hard rows; found {len(hard_frozen)}"
        )

    frozen_by_id: dict[int, set[str]] = defaultdict(set)
    for row in hard_frozen:
        frozen_by_id[integer(row, "id")].add(row["split"])
    if set(frozen_by_id) != {37, 51, 93}:
        raise SystemExit("hard frozen-family IDs do not match 37/51/93")
    for candidate_id in (37, 51, 93):
        if frozen_by_id[candidate_id] != HARD_SPLITS:
            raise SystemExit(
                f"frozen Stage 7B hard split coverage incomplete for id={candidate_id}"
            )

    improvements: list[tuple[dict[str, str], dict[str, str]]] = []
    for row in selected_gated:
        if integer(row, "failures") != 0:
            continue
        twin = twins[integer(row, "id")]
        if integer(twin, "failures") != 0:
            continue
        if integer(row, "inference_sum") < integer(twin, "inference_sum"):
            improvements.append((row, twin))

    return {
        "training_frontier": training_frontier,
        "selected": selected,
        "twins": twins,
        "improvements": improvements,
        "hard_frozen": hard_frozen,
    }


def delta(row: dict[str, str], twin: dict[str, str], field: str) -> int:
    return integer(row, field) - integer(twin, field)


def main() -> int:
    if shutil.which("zig") is None:
        raise SystemExit("zig 0.16.0 is required on PATH")

    version = run("zig", "version", capture=True).stdout.decode().strip()
    if version != "0.16.0":
        raise SystemExit(f"zig 0.16.0 is required; found {version}")

    run("zig", "build", "test", timeout_s=1800)

    validation = run_f3("validate", capture=True, timeout_s=300)
    validation_text = validation.stdout.decode()
    print(
        validation_text,
        end="" if validation_text.endswith("\n") else "\n",
    )
    for marker in (
        f"stage7b_historical_blob: {EXPECTED_STAGE7B_BLOB}",
        f"stage7b_report_sha256: {EXPECTED_STAGE7B_REPORT}",
        "candidate_count: 134",
        "expected_candidate_count: 134",
        "corner_candidate_count: 134",
        "invalid_theta: 0",
        "duplicate_theta: 0",
        "training_worlds: 48",
        "validation_worlds: 24",
    ):
        require_marker(validation_text, marker)

    corner = run_f3("corner", capture=True, timeout_s=1800)
    corner_text = corner.stdout.decode()
    print(corner_text, end="" if corner_text.endswith("\n") else "\n")
    for marker in (
        "candidates: 134",
        f"aggregate_checks: {EXPECTED_CORNER_CHECKS}",
        "mismatches: 0",
    ):
        require_marker(corner_text, marker)

    first = run_f3("search", capture=True, timeout_s=3600).stdout
    second = run_f3("search", capture=True, timeout_s=3600).stdout
    if first != second:
        raise SystemExit("F3 search evidence is not byte-identical across replay")

    rows = parse_tsv(first)
    structure = validate_rows(rows)

    TRIAL.parent.mkdir(parents=True, exist_ok=True)
    TRIAL.write_bytes(first)
    digest = hashlib.sha256(first).hexdigest()

    selected = structure["selected"]
    improvements = structure["improvements"]
    hard_frozen = structure["hard_frozen"]

    assert isinstance(selected, list)
    assert isinstance(improvements, list)
    assert isinstance(hard_frozen, list)

    print(f"F3 rows: {len(rows)}")
    print(f"F3 bytes: {len(first)}")
    print(f"F3 sha256: {digest}")
    print("F3 byte_identical_replay: yes")
    print("F3 violations: 0")
    print("F3 inference_accounting_failures: 0")
    print("F3 c1000_corner_mismatches: 0")
    print("F3 stage7b_anchor: PASS")

    print("validation frontier:")
    for row in selected:
        print(
            f"  id={row['id']} "
            f"theta=({row['n']},{row['e']},{row['r']},{row['u']},{row['c']}) "
            f"failures={row['failures']} "
            f"rounds={row['rounds_sum']} "
            f"communication={row['communication_sum']} "
            f"duplicates={row['duplicate_sum']} "
            f"computation={row['computation_sum']} "
            f"inference={row['inference_sum']} "
            f"reuse={row['cache_reuse_sum']}"
        )

    if improvements:
        print("gated validation improvements:")
        for row, twin in improvements:
            saved = (
                integer(twin, "inference_sum")
                - integer(row, "inference_sum")
            )
            resource_preserving = all(
                delta(row, twin, field) <= 0
                for field in (
                    "rounds_sum",
                    "communication_sum",
                    "duplicate_sum",
                    "computation_sum",
                )
            )
            print(
                f"  id={row['id']} c={row['c']} failures=0 "
                f"inference={row['inference_sum']} "
                f"ungated={twin['inference_sum']} saved={saved} "
                f"rounds_delta={delta(row, twin, 'rounds_sum')} "
                f"communication_delta={delta(row, twin, 'communication_sum')} "
                f"duplicate_delta={delta(row, twin, 'duplicate_sum')} "
                f"computation_delta={delta(row, twin, 'computation_sum')} "
                f"resource_preserving={'yes' if resource_preserving else 'no'}"
            )
        outcome = "PASS"
    else:
        print(
            "no selected gated theta preserved zero validation failures while "
            "strictly reducing inference units against its zero-failure twin"
        )
        outcome = "LIMITATION"

    by_frozen: dict[int, list[dict[str, str]]] = defaultdict(list)
    for row in hard_frozen:
        by_frozen[integer(row, "id")].append(row)
    for candidate_id in sorted(by_frozen):
        candidate_rows = by_frozen[candidate_id]
        failures = sum(integer(row, "failures") for row in candidate_rows)
        communication = sum(
            integer(row, "communication_sum") for row in candidate_rows
        )
        inference = sum(
            integer(row, "inference_sum") for row in candidate_rows
        )
        print(
            f"frozen id{candidate_id} hard: failures={failures} "
            f"communication={communication} inference={inference}"
        )

    print(f"F3 {outcome}: local inference-control evidence complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
