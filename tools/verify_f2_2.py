#!/usr/bin/env python3
"""F2.2 deterministic asynchronous scaling-boundary evidence gate."""

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
BIN = ROOT / "zig-out" / "bin" / "run-f2-2"
TRIAL = ROOT / "trials" / "f2-scaling.tsv"

PROFILES = ("theta37", "theta51", "theta93", "novel_first")
POPULATIONS = (8, 16, 32, 64, 128)
DENSITIES = (1, 2)
TOPOLOGIES = ("ring", "grid")
SEEDS = (0, 1, 2)
DECISION_BUDGET = 4096
EXPECTED_ROWS = 240


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
            f"F2.2 engineering gate failed: {' '.join(args)} "
            f"exit={proc.returncode}"
        )
    return proc


def parse_tsv(data: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(data.decode()), delimiter="\t"))


def integer(row: dict[str, str], field: str) -> int:
    return int(row[field], 10)


def expected_worlds() -> set[tuple[str, str, int, int, int]]:
    return {
        (profile, topology, seed, nodes, density)
        for nodes in POPULATIONS
        for density in DENSITIES
        for topology in TOPOLOGIES
        for profile in PROFILES
        for seed in SEEDS
    }


def validate_rows(rows: list[dict[str, str]]) -> None:
    if len(rows) != EXPECTED_ROWS:
        raise SystemExit(
            f"expected {EXPECTED_ROWS} F2.2 rows; got {len(rows)}"
        )

    seen: set[tuple[str, str, int, int, int]] = set()
    for row in rows:
        key = (
            row["profile"],
            row["topology"],
            integer(row, "seed"),
            integer(row, "nodes"),
            integer(row, "facts_per_node"),
        )
        if key in seen:
            raise SystemExit(f"duplicate F2.2 world: {key}")
        seen.add(key)

        nodes = integer(row, "nodes")
        density = integer(row, "facts_per_node")
        facts = integer(row, "facts")

        if nodes not in POPULATIONS:
            raise SystemExit(f"population drift in F2.2 row: {key}")
        if density not in DENSITIES:
            raise SystemExit(f"fact-density drift in F2.2 row: {key}")
        if facts != nodes * density:
            raise SystemExit(
                f"fact-count mismatch in {key}: facts={facts}"
            )
        if integer(row, "decision_budget") != DECISION_BUDGET:
            raise SystemExit(f"decision-budget drift in F2.2 row: {key}")

        if row["envelope_accounted"] != "yes":
            raise SystemExit(f"transport accounting failure: {key}")
        if row["communication_accounted"] != "yes":
            raise SystemExit(f"communication accounting failure: {key}")
        if integer(row, "violations") != 0:
            raise SystemExit(f"protocol violation in F2.2 row: {key}")

        for field in (
            "dropped",
            "partitioned",
            "crashed",
            "queue_overflow",
            "duplicate_copies",
        ):
            if integer(row, field) != 0:
                raise SystemExit(
                    f"unexpected no-fault terminal/copy {field}="
                    f"{row[field]} in {key}"
                )

        attempts = integer(row, "transport_attempts")
        delivered = integer(row, "delivered")
        pending = integer(row, "pending")
        if attempts != delivered + pending:
            raise SystemExit(
                f"no-fault transport identity mismatch in {key}: "
                f"attempts={attempts} delivered={delivered} pending={pending}"
            )

        communication = integer(row, "communication_units")
        useful = integer(row, "useful")
        duplicate = integer(row, "duplicate")
        if communication != useful + duplicate:
            raise SystemExit(
                f"communication identity mismatch in {key}"
            )

        minimum = integer(row, "min_local_decisions")
        maximum = integer(row, "max_local_decisions")
        policy_ticks = integer(row, "policy_ticks")
        if minimum > maximum or maximum > DECISION_BUDGET:
            raise SystemExit(
                f"invalid local-decision range in {key}: "
                f"{minimum}..{maximum}"
            )
        if not (
            minimum * nodes <= policy_ticks <= maximum * nodes
        ):
            raise SystemExit(
                f"policy-tick total outside local-decision bounds in {key}"
            )

        success = row["success"] == "yes"
        censored = row["censored"] == "yes"
        if success == censored:
            raise SystemExit(
                f"F2.2 row must be exactly success or censored: {key}"
            )

        exhausted_tick = integer(row, "budget_exhausted_tick")
        drain_ticks = integer(row, "drain_ticks")
        collector_final = integer(row, "collector_final")

        if success:
            if collector_final != facts:
                raise SystemExit(
                    f"successful row lacks complete collector state: {key}"
                )
            if exhausted_tick == 0 and drain_ticks != 0:
                raise SystemExit(
                    f"non-budget-bound success has drain ticks in {key}"
                )
            if exhausted_tick != 0 and (
                minimum != DECISION_BUDGET
                or maximum != DECISION_BUDGET
            ):
                raise SystemExit(
                    f"budget-exhausted success lacks equal full budgets: {key}"
                )
        else:
            if collector_final >= facts:
                raise SystemExit(
                    f"censored row already has complete collector state: {key}"
                )
            if minimum != DECISION_BUDGET or maximum != DECISION_BUDGET:
                raise SystemExit(
                    f"censored row did not consume equal full budgets: {key}"
                )
            if exhausted_tick == 0:
                raise SystemExit(
                    f"censored row lacks budget-exhausted tick: {key}"
                )
            if pending != 0:
                raise SystemExit(
                    f"censored row still has in-flight deliveries: {key}"
                )

    missing = expected_worlds() - seen
    extra = seen - expected_worlds()
    if missing or extra:
        raise SystemExit(
            f"F2.2 world matrix mismatch: missing={sorted(missing)} "
            f"extra={sorted(extra)}"
        )


def fmt_boundary(value: int | None) -> str:
    return "none" if value is None else str(value)


def main() -> int:
    if shutil.which("zig") is None:
        raise SystemExit("zig 0.16.0 is required on PATH")

    version = run("zig", "version", capture=True).stdout.decode().strip()
    if version != "0.16.0":
        raise SystemExit(f"zig 0.16.0 is required; found {version}")

    run("zig", "build", "test", timeout_s=900)
    run("zig", "build", "-Doptimize=ReleaseFast", timeout_s=900)
    if not BIN.exists():
        raise SystemExit("F2.2 release runner was not installed")

    first = run(str(BIN), "scaling", capture=True, timeout_s=1800).stdout
    second = run(str(BIN), "scaling", capture=True, timeout_s=1800).stdout
    if first != second:
        raise SystemExit(
            "F2.2 scaling dataset is not byte-identical across replay"
        )

    rows = parse_tsv(first)
    validate_rows(rows)

    TRIAL.parent.mkdir(parents=True, exist_ok=True)
    TRIAL.write_bytes(first)
    digest = hashlib.sha256(first).hexdigest()

    successes = sum(row["success"] == "yes" for row in rows)
    censored = sum(row["censored"] == "yes" for row in rows)

    print(f"F2.2 rows: {len(rows)}")
    print(f"F2.2 bytes: {len(first)}")
    print(f"F2.2 sha256: {digest}")
    print("F2.2 byte_identical_replay: yes")
    print(f"F2.2 successes: {successes}/{len(rows)}")
    print(f"F2.2 censored: {censored}")
    print("F2.2 violations: 0")
    print("F2.2 accounting_failures: 0")
    print("F2.2 communication_failures: 0")
    print("F2.2 queue_overflow: 0")
    print("F2.2 unexpected_fault_terminals: 0")

    per_profile: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        per_profile[row["profile"]].append(row)

    for profile in PROFILES:
        profile_rows = per_profile[profile]
        profile_successes = sum(
            row["success"] == "yes" for row in profile_rows
        )
        print(
            f"profile {profile}: "
            f"successes={profile_successes}/{len(profile_rows)} "
            f"censored={len(profile_rows) - profile_successes} "
            f"communication_units="
            f"{sum(integer(r, 'communication_units') for r in profile_rows)} "
            f"duplicate_units="
            f"{sum(integer(r, 'duplicate') for r in profile_rows)}"
        )

    groups: dict[
        tuple[str, str, int],
        dict[int, list[dict[str, str]]],
    ] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        groups[
            (
                row["profile"],
                row["topology"],
                integer(row, "facts_per_node"),
            )
        ][integer(row, "nodes")].append(row)

    for key in sorted(groups):
        profile, topology, density = key
        by_n = groups[key]
        success_counts: list[int] = []
        pattern_parts: list[str] = []

        for nodes in POPULATIONS:
            node_rows = by_n[nodes]
            if len(node_rows) != len(SEEDS):
                raise SystemExit(
                    f"boundary group {key} N={nodes} has "
                    f"{len(node_rows)} rows"
                )
            count = sum(row["success"] == "yes" for row in node_rows)
            success_counts.append(count)
            pattern_parts.append(f"{nodes}:{count}/3")

        first_any_censored = next(
            (
                n
                for n, count in zip(POPULATIONS, success_counts)
                if count < len(SEEDS)
            ),
            None,
        )
        first_all_censored = next(
            (
                n
                for n, count in zip(POPULATIONS, success_counts)
                if count == 0
            ),
            None,
        )
        all_success_ns = [
            n
            for n, count in zip(POPULATIONS, success_counts)
            if count == len(SEEDS)
        ]
        largest_all_success = max(all_success_ns) if all_success_ns else None
        monotone = all(
            later <= earlier
            for earlier, later in zip(
                success_counts,
                success_counts[1:],
            )
        )

        print(
            "boundary "
            f"profile={profile} topology={topology} F_per_N={density} "
            f"pattern={','.join(pattern_parts)} "
            f"largest_all_success_N={fmt_boundary(largest_all_success)} "
            f"first_any_censored_N={fmt_boundary(first_any_censored)} "
            f"first_all_censored_N={fmt_boundary(first_all_censored)} "
            f"monotone_success_counts={'yes' if monotone else 'no'}"
        )

    print(
        "F2.2 PASS: asynchronous scaling dataset is deterministic, "
        "fully accounted, and every non-convergent world is "
        "budget-exhausted after draining in-flight deliveries"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
