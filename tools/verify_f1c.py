#!/usr/bin/env python3
"""F1c pinned-zquic candidate transport evidence gate."""

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
BIN = ROOT / "zig-out" / "bin" / "run-f1c"
TRIAL = ROOT / "trials" / "f1c-zquic-wired.tsv"

ZQUIC_TAG = "v1.7.48"
ZQUIC_COMMIT = "4bd041ac95425fb0aa229b74c9d3316c74aaf829"
ZQUIC_URL = (
    "https://github.com/zigstack/zquic/archive/refs/tags/v1.7.48.tar.gz"
)
ZQUIC_HASH = "zquic-1.7.0-2zRc1PSAFgDCESpm-vZsUr4O02HM0dpzmVJSx5WXW6ES"

PROFILES = ("theta37", "theta51", "theta93", "novel_first")
TOPOLOGIES = ("ring", "grid")
SEEDS = (0, 1, 2)
K = 3


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
        if capture:
            stderr = proc.stderr.decode(errors="replace")
            if stderr:
                print(stderr, end="" if stderr.endswith("\n") else "\n", file=sys.stderr)
        raise SystemExit(
            f"F1c engineering gate failed: {' '.join(args)} "
            f"exit={proc.returncode}"
        )
    return proc


def verify_dependency_pin() -> None:
    zon = (ROOT / "build.zig.zon").read_text()
    required = (ZQUIC_URL, ZQUIC_HASH)
    missing = [value for value in required if value not in zon]
    if missing:
        raise SystemExit(f"F1c zquic dependency pin drifted: missing {missing}")


def run_world(
    profile: str,
    topology: str,
    seed: int,
    fault: str,
) -> dict[str, str]:
    args = (
        str(BIN),
        "world",
        profile,
        topology,
        str(seed),
        fault,
    )
    print("+", " ".join(args), file=sys.stderr)
    proc = subprocess.run(
        args,
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=90,
    )
    stderr = proc.stderr.decode(errors="replace")
    if stderr:
        print(stderr, end="" if stderr.endswith("\n") else "\n", file=sys.stderr)
    if proc.returncode != 0:
        raise SystemExit(
            f"F1c world failed to execute: "
            f"{profile}/{topology}/{seed}/{fault} exit={proc.returncode}"
        )

    rows = list(
        csv.DictReader(io.StringIO(proc.stdout.decode()), delimiter="\t")
    )
    if len(rows) != 1:
        raise SystemExit(
            f"expected one F1c result row for "
            f"{profile}/{topology}/{seed}/{fault}; got {len(rows)}"
        )

    panic_count = (
        stderr.count("panic:")
        + stderr.count("panicked at")
        + stderr.count("thread panic")
    )
    return {**rows[0], "transport_panics": str(panic_count)}


def int_field(row: dict[str, str], key: str) -> int:
    return int(row[key], 10)


def structural_check(
    row: dict[str, str],
    label: str,
    hard_failures: list[str],
) -> None:
    for field, description in (
        ("envelope_accounted", "attempt terminal identity"),
        ("missing_accounted", "missing-fact identity"),
        ("communication_accounted", "communication identity"),
    ):
        if row[field] != "yes":
            hard_failures.append(f"{label}: {description} failed")

    for field, description in (
        ("violations", "protocol violations"),
        ("transport_duplicate_deliveries", "duplicate logical deliveries"),
        ("send_failures", "unaccepted stream sends"),
        ("malformed_frames", "malformed stream frames"),
    ):
        if int_field(row, field) != 0:
            hard_failures.append(f"{label}: {description}={row[field]}")

    attempted_units = int_field(row, "attempted_communication_units")
    delivered_units = int_field(row, "communication_units")
    if delivered_units > attempted_units:
        hard_failures.append(
            f"{label}: delivered communication exceeds attempted communication"
        )

    expected_fully = (
        row["envelope_accounted"] == "yes"
        and row["missing_accounted"] == "yes"
        and row["communication_accounted"] == "yes"
        and int_field(row, "unattributed") == 0
    )
    actual_fully = row["fully_accounted"] == "yes"
    if actual_fully != expected_fully:
        hard_failures.append(
            f"{label}: fully_accounted flag is internally inconsistent"
        )


def main() -> int:
    if shutil.which("zig") is None:
        raise SystemExit("zig 0.16.0 is required on PATH")

    version = run("zig", "version", capture=True).stdout.decode().strip()
    if version != "0.16.0":
        raise SystemExit(f"zig 0.16.0 is required; found {version}")

    verify_dependency_pin()

    run("zig", "build", "test", timeout_s=600)
    run("zig", "build", "-Doptimize=ReleaseFast", timeout_s=600)
    if not BIN.exists():
        raise SystemExit("F1c release runner was not installed")

    smoke = run(str(BIN), "smoke", capture=True, timeout_s=90)
    smoke_rows = list(
        csv.DictReader(
            io.StringIO(smoke.stdout.decode()),
            delimiter="\t",
        )
    )
    if len(smoke_rows) != 1:
        raise SystemExit("F1c smoke did not emit exactly one result row")

    records: list[dict[str, str]] = []
    hard_failures: list[str] = []
    limitations: list[str] = []

    for topology in TOPOLOGIES:
        for profile in PROFILES:
            for seed in SEEDS:
                row = run_world(profile, topology, seed, "no_fault")
                label = f"fault_free/{profile}/{topology}/{seed}"
                structural_check(row, label, hard_failures)

                if row["success"] != "yes":
                    limitations.append(
                        f"fault-free non-convergence: "
                        f"{profile}/{topology}/{seed}"
                    )
                if int_field(row, "unattributed") != 0:
                    limitations.append(
                        f"fault-free unattributed loss: "
                        f"{profile}/{topology}/{seed}"
                    )
                if int_field(row, "transport_panics") != 0:
                    limitations.append(
                        f"fault-free transport panic: "
                        f"{profile}/{topology}/{seed}"
                    )
                if (
                    int_field(row, "partitioned") != 0
                    or int_field(row, "crashed") != 0
                ):
                    hard_failures.append(
                        f"{label}: no-fault world contains fault terminals"
                    )

                records.append(
                    {"phase": "fault_free", "repeat": "0", **row}
                )

    audit_worlds = (
        ("theta37", "ring", 0),
        ("theta51", "grid", 1),
        ("theta93", "ring", 2),
        ("novel_first", "grid", 2),
    )
    signatures: dict[tuple[str, str, int], list[str]] = defaultdict(list)
    audit_rows: dict[
        tuple[str, str, int],
        list[tuple[tuple[str, str], ...]],
    ] = defaultdict(list)

    for profile, topology, seed in audit_worlds:
        key = (profile, topology, seed)
        for repeat in range(K):
            row = run_world(profile, topology, seed, "no_fault")
            label = f"audit/{profile}/{topology}/{seed}/r{repeat}"
            structural_check(row, label, hard_failures)

            signatures[key].append(row["result_signature"])
            audit_rows[key].append(tuple(sorted(row.items())))
            if row["success"] != "yes":
                limitations.append(
                    f"audit non-convergence: "
                    f"{profile}/{topology}/{seed}/r{repeat}"
                )
            if int_field(row, "unattributed") != 0:
                limitations.append(
                    f"audit unattributed loss: "
                    f"{profile}/{topology}/{seed}/r{repeat}"
                )
            if int_field(row, "transport_panics") != 0:
                limitations.append(
                    f"audit transport panic: "
                    f"{profile}/{topology}/{seed}/r{repeat}"
                )

            records.append(
                {
                    "phase": "determinism_audit",
                    "repeat": str(repeat),
                    **row,
                }
            )

    for key in audit_worlds:
        if len(set(signatures[key])) != 1:
            limitations.append(
                f"result-signature nondeterminism: "
                f"{key} -> {signatures[key]}"
            )
        if len(set(audit_rows[key])) != 1:
            limitations.append(f"full-result nondeterminism: {key}")

    contested_worlds = (
        ("theta51", "ring", 0),
        ("novel_first", "grid", 2),
    )
    contested_faults = (
        "partition",
        "crash_restart_persist",
        "crash_restart_reset",
    )
    for profile, topology, seed in contested_worlds:
        for fault in contested_faults:
            row = run_world(profile, topology, seed, fault)
            label = f"contested/{profile}/{topology}/{seed}/{fault}"
            structural_check(row, label, hard_failures)

            if int_field(row, "unattributed") != 0:
                limitations.append(
                    f"contested unattributed loss: "
                    f"{profile}/{topology}/{seed}/{fault}"
                )
            if int_field(row, "transport_panics") != 0:
                limitations.append(
                    f"contested transport panic: "
                    f"{profile}/{topology}/{seed}/{fault}"
                )

            if fault == "partition":
                if int_field(row, "partitioned") == 0:
                    hard_failures.append(
                        f"{label}: partition fault was not exercised"
                    )
                if int_field(row, "crashed") != 0:
                    hard_failures.append(
                        f"{label}: partition world contains crash terminals"
                    )
            else:
                if int_field(row, "crashed") == 0:
                    hard_failures.append(
                        f"{label}: crash fault was not exercised"
                    )
                if int_field(row, "partitioned") != 0:
                    hard_failures.append(
                        f"{label}: crash world contains partition terminals"
                    )

            records.append(
                {"phase": "contested", "repeat": "0", **row}
            )

    if hard_failures:
        for failure in hard_failures[:100]:
            print(f"HARD FAIL: {failure}", file=sys.stderr)
        if len(hard_failures) > 100:
            print(
                f"... {len(hard_failures) - 100} more hard failures",
                file=sys.stderr,
            )
        return 1

    if len(records) != 42:
        raise SystemExit(f"expected 42 F1c rows; found {len(records)}")

    fieldnames = list(records[0].keys())
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=fieldnames,
        delimiter="\t",
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(records)
    data = buf.getvalue().encode()

    TRIAL.parent.mkdir(parents=True, exist_ok=True)
    TRIAL.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()

    fault_free = [r for r in records if r["phase"] == "fault_free"]
    audit = [r for r in records if r["phase"] == "determinism_audit"]
    contested = [r for r in records if r["phase"] == "contested"]

    print(f"F1c rows: {len(records)}")
    print(f"F1c bytes: {len(data)}")
    print(f"F1c sha256: {digest}")
    print(f"zquic tag: {ZQUIC_TAG}")
    print(f"zquic commit: {ZQUIC_COMMIT}")
    print(f"zquic package hash: {ZQUIC_HASH}")
    print(f"fault_free_rows: {len(fault_free)}")
    print(
        "fault_free_successes: "
        f"{sum(r['success'] == 'yes' for r in fault_free)}/"
        f"{len(fault_free)}"
    )
    print(f"determinism_audit_rows: {len(audit)}")
    print(f"determinism_K: {K}")
    print(f"contested_rows: {len(contested)}")
    print("envelope_accounting_failures: 0")
    print("missing_accounting_failures: 0")
    print("communication_accounting_failures: 0")
    print("protocol_violations: 0")
    print("send_failures: 0")
    print("malformed_frames: 0")

    print(
        "unattributed_missing: "
        f"{sum(int_field(r, 'unattributed') for r in records)}"
    )
    print(
        "pending_at_censor: "
        f"{sum(int_field(r, 'pending_at_censor') for r in records)}"
    )
    print(
        "transport_panics: "
        f"{sum(int_field(r, 'transport_panics') for r in records)}"
    )
    print(
        "udp_datagrams: "
        f"{sum(int_field(r, 'udp_datagrams') for r in records)}"
    )
    print(
        "backpressure_events: "
        f"{sum(int_field(r, 'backpressure_events') for r in records)}"
    )

    for key in sorted(signatures):
        values = signatures[key]
        print(
            "audit "
            f"profile={key[0]} topology={key[1]} seed={key[2]} "
            f"stable={'yes' if len(set(audit_rows[key])) == 1 else 'no'} "
            f"signatures={','.join(values)}"
        )

    if limitations:
        print("F1c limitation reasons:")
        for reason in dict.fromkeys(limitations):
            print(f"  - {reason}")
        print(
            "F1c LIMITATION: zquic remains a measured candidate only; "
            "deterministic Zig substrate remains authoritative"
        )
    else:
        print(
            "F1c PASS: pinned zquic transport is stable across the audit "
            "and fully accounted under the contested subset"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
