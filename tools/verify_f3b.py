#!/usr/bin/env python3
"""F3b state-aware local inference-control evidence gate."""

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
TRIAL = ROOT / "trials" / "f3b-state-aware.tsv"

EXPECTED_CANDIDATES = 15
EXPECTED_BASES = {37, 51, 93}
EXPECTED_CONTROLLERS = {
    "always_refresh",
    "knowledge_change",
    "knowledge_or_stale",
    "knowledge_or_stale_age4",
    "knowledge_or_stale_age8",
}
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
            f"F3b engineering gate failed: {' '.join(args)} "
            f"exit={proc.returncode}"
        )
    return proc


def run_f3b(
    command: str,
    *,
    capture: bool = False,
    timeout_s: int = 1800,
) -> subprocess.CompletedProcess[bytes]:
    return run(
        "zig",
        "build",
        "-Doptimize=ReleaseFast",
        "run-f3b",
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
        raise SystemExit(f"F3b output missing marker: {marker}")


def validate_accounting(row: dict[str, str]) -> None:
    label = (
        f"{row['phase']}/{row['split']} "
        f"base={row['base_id']} controller={row['controller']}"
    )

    if integer(row, "violations") != 0:
        raise SystemExit(f"F3b protocol violation in {label}")
    if row["inference_accounted"] != "yes":
        raise SystemExit(f"F3b inference accounting flag failed in {label}")
    if row["communication_accounted"] != "yes":
        raise SystemExit(
            f"F3b communication accounting flag failed in {label}"
        )

    computation = integer(row, "computation_sum")
    inference = integer(row, "inference_sum")
    reuse = integer(row, "cache_reuse_sum")
    if computation != inference + reuse:
        raise SystemExit(
            f"F3b inference identity failed in {label}: "
            f"{computation} != {inference} + {reuse}"
        )

    refresh_sum = sum(
        integer(row, field)
        for field in (
            "refresh_first",
            "refresh_always",
            "refresh_knowledge",
            "refresh_invalid_action",
            "refresh_stale_action",
            "refresh_age",
        )
    )
    if inference != refresh_sum:
        raise SystemExit(
            f"F3b refresh attribution failed in {label}: "
            f"inference={inference} reasons={refresh_sum}"
        )

    controller = row["controller"]
    if controller == "always_refresh":
        if reuse != 0:
            raise SystemExit(f"F3b baseline reused cache in {label}")
        if integer(row, "refresh_always") != inference:
            raise SystemExit(
                f"F3b baseline refresh attribution drifted in {label}"
            )
        for field in (
            "refresh_first",
            "refresh_knowledge",
            "refresh_invalid_action",
            "refresh_stale_action",
            "refresh_age",
        ):
            if integer(row, field) != 0:
                raise SystemExit(
                    f"F3b baseline has unexpected {field} in {label}"
                )
    elif integer(row, "refresh_always") != 0:
        raise SystemExit(
            f"F3b state-aware controller has always-refresh counts in {label}"
        )


def validate_structure(
    rows: list[dict[str, str]],
) -> dict[str, object]:
    if not rows:
        raise SystemExit("F3b search emitted no evidence rows")

    for row in rows:
        validate_accounting(row)

    training = [row for row in rows if row["phase"] == "training"]
    if len(training) != EXPECTED_CANDIDATES:
        raise SystemExit(
            f"expected {EXPECTED_CANDIDATES} training rows; "
            f"found {len(training)}"
        )

    by_pair = {
        (integer(row, "base_id"), row["controller"]): row
        for row in training
    }
    expected_pairs = {
        (base_id, controller)
        for base_id in EXPECTED_BASES
        for controller in EXPECTED_CONTROLLERS
    }
    if set(by_pair) != expected_pairs:
        missing = sorted(expected_pairs - set(by_pair))
        extra = sorted(set(by_pair) - expected_pairs)
        raise SystemExit(
            f"F3b paired matrix mismatch: missing={missing} extra={extra}"
        )

    training_frontier = {
        integer(row, "id")
        for row in training
        if row["selected_frontier"] == "yes"
    }
    if not training_frontier:
        raise SystemExit("F3b training frontier is empty")

    validation = [row for row in rows if row["phase"] == "validation"]
    if not validation:
        raise SystemExit("F3b validation phase is empty")

    validation_by_pair = {
        (integer(row, "base_id"), row["controller"]): row
        for row in validation
    }

    for base_id in EXPECTED_BASES:
        if (base_id, "always_refresh") not in validation_by_pair:
            raise SystemExit(
                f"F3b validation lacks paired baseline for base {base_id}"
            )

    selected = [
        row
        for row in validation
        if row["selected_frontier"] == "yes"
    ]
    if not selected:
        raise SystemExit("F3b validation frontier is empty")

    selected_ids = {integer(row, "id") for row in selected}
    if not selected_ids.issubset(training_frontier):
        raise SystemExit(
            "F3b validation frontier contains candidate absent "
            "from training frontier"
        )

    hard = [row for row in rows if row["phase"] == "hard"]
    hard_by_id: dict[int, dict[str, dict[str, str]]] = defaultdict(dict)
    for row in hard:
        candidate_id = integer(row, "id")
        split = row["split"]
        if split in hard_by_id[candidate_id]:
            raise SystemExit(
                f"duplicate F3b hard row id={candidate_id} split={split}"
            )
        hard_by_id[candidate_id][split] = row

    baseline_ids = {
        integer(row, "id")
        for row in validation
        if row["controller"] == "always_refresh"
    }
    required_hard_ids = selected_ids | baseline_ids
    for candidate_id in required_hard_ids:
        if set(hard_by_id[candidate_id]) != HARD_SPLITS:
            raise SystemExit(
                f"F3b hard coverage incomplete for id={candidate_id}: "
                f"{sorted(hard_by_id[candidate_id])}"
            )

    return {
        "training": training,
        "validation": validation,
        "validation_by_pair": validation_by_pair,
        "selected": selected,
        "hard_by_id": hard_by_id,
    }


def delta(
    row: dict[str, str],
    baseline: dict[str, str],
    field: str,
) -> int:
    return integer(row, field) - integer(baseline, field)


def main() -> int:
    if shutil.which("zig") is None:
        raise SystemExit("zig 0.16.0 is required on PATH")

    version = run("zig", "version", capture=True).stdout.decode().strip()
    if version != "0.16.0":
        raise SystemExit(f"zig 0.16.0 is required; found {version}")

    run("zig", "build", "test", timeout_s=1800)

    validation = run_f3b("validate", capture=True, timeout_s=300)
    validation_text = validation.stdout.decode()
    print(
        validation_text,
        end="" if validation_text.endswith("\n") else "\n",
    )
    for marker in (
        "candidate_count: 15",
        "expected_candidate_count: 15",
        "baseline_candidates: 3",
        "invalid_theta: 0",
        "duplicate_candidates: 0",
        "training_worlds: 48",
        "validation_worlds: 24",
    ):
        require_marker(validation_text, marker)

    baseline = run_f3b("baseline", capture=True, timeout_s=1800)
    baseline_text = baseline.stdout.decode()
    print(
        baseline_text,
        end="" if baseline_text.endswith("\n") else "\n",
    )
    for marker in (
        "base_ids: 37,51,93",
        "aggregate_checks: 6",
        "mismatches: 0",
    ):
        require_marker(baseline_text, marker)

    first = run_f3b("search", capture=True, timeout_s=3600).stdout
    second = run_f3b("search", capture=True, timeout_s=3600).stdout
    if first != second:
        raise SystemExit("F3b search evidence is not byte-identical")

    rows = parse_tsv(first)
    structure = validate_structure(rows)

    TRIAL.parent.mkdir(parents=True, exist_ok=True)
    TRIAL.write_bytes(first)
    digest = hashlib.sha256(first).hexdigest()

    selected = structure["selected"]
    validation_by_pair = structure["validation_by_pair"]
    hard_by_id = structure["hard_by_id"]
    assert isinstance(selected, list)
    assert isinstance(validation_by_pair, dict)
    assert isinstance(hard_by_id, defaultdict)

    print(f"F3b rows: {len(rows)}")
    print(f"F3b bytes: {len(first)}")
    print(f"F3b sha256: {digest}")
    print("F3b byte_identical_replay: yes")
    print("F3b violations: 0")
    print("F3b inference_accounting_failures: 0")
    print("F3b communication_accounting_failures: 0")
    print("F3b paired_baseline_mismatches: 0")

    promoted: list[tuple[dict[str, str], dict[str, str], int]] = []
    validation_improvements: list[
        tuple[dict[str, str], dict[str, str], int]
    ] = []

    print("validation frontier:")
    for row in selected:
        base_id = integer(row, "base_id")
        baseline_row = validation_by_pair[(base_id, "always_refresh")]
        hard_failures = sum(
            integer(hard_row, "failures")
            for hard_row in hard_by_id[integer(row, "id")].values()
        )

        inference_saved = (
            integer(baseline_row, "inference_sum")
            - integer(row, "inference_sum")
        )
        print(
            f"  id={row['id']} base={base_id} "
            f"controller={row['controller']} "
            f"failures={row['failures']} "
            f"rounds={row['rounds_sum']} "
            f"communication={row['communication_sum']} "
            f"duplicates={row['duplicate_sum']} "
            f"computation={row['computation_sum']} "
            f"inference={row['inference_sum']} "
            f"reuse={row['cache_reuse_sum']} "
            f"hard_failures={hard_failures}"
        )

        if row["controller"] == "always_refresh":
            continue
        if integer(row, "failures") != 0:
            continue
        if integer(baseline_row, "failures") != 0:
            continue
        if inference_saved <= 0:
            continue

        validation_improvements.append(
            (row, baseline_row, hard_failures)
        )
        if hard_failures == 0:
            promoted.append((row, baseline_row, hard_failures))

    if validation_improvements:
        print("paired state-aware improvements:")
        for row, baseline_row, hard_failures in validation_improvements:
            saved = (
                integer(baseline_row, "inference_sum")
                - integer(row, "inference_sum")
            )
            print(
                f"  base={row['base_id']} "
                f"controller={row['controller']} "
                f"saved_inference={saved} "
                f"rounds_delta={delta(row, baseline_row, 'rounds_sum')} "
                f"communication_delta="
                f"{delta(row, baseline_row, 'communication_sum')} "
                f"duplicate_delta="
                f"{delta(row, baseline_row, 'duplicate_sum')} "
                f"computation_delta="
                f"{delta(row, baseline_row, 'computation_sum')} "
                f"hard_failures={hard_failures}"
            )
    else:
        print(
            "no selected state-aware controller preserved zero validation "
            "failures while reducing inference against its paired baseline"
        )

    for base_id in sorted(EXPECTED_BASES):
        baseline_row = validation_by_pair[(base_id, "always_refresh")]
        baseline_id = integer(baseline_row, "id")
        failures = sum(
            integer(row, "failures")
            for row in hard_by_id[baseline_id].values()
        )
        communication = sum(
            integer(row, "communication_sum")
            for row in hard_by_id[baseline_id].values()
        )
        inference = sum(
            integer(row, "inference_sum")
            for row in hard_by_id[baseline_id].values()
        )
        print(
            f"baseline id{base_id} hard: failures={failures} "
            f"communication={communication} inference={inference}"
        )

    if promoted:
        print("promoted F3b controllers:")
        for row, baseline_row, _ in promoted:
            saved = (
                integer(baseline_row, "inference_sum")
                - integer(row, "inference_sum")
            )
            print(
                f"  base={row['base_id']} "
                f"controller={row['controller']} "
                f"validation_saved_inference={saved} "
                f"hard_failures=0"
            )
        outcome = "PASS"
    else:
        print(
            "no state-aware controller met the full validation + "
            "hard-holdout promotion criterion"
        )
        outcome = "LIMITATION"

    print(f"F3b {outcome}: state-aware inference-control evidence complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
