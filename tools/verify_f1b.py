#!/usr/bin/env python3
"""F1b P2Panda-wired candidate transport evidence gate."""

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
MANIFEST = ROOT / "modules" / "p2panda" / "Cargo.toml"
BIN = ROOT / "modules" / "p2panda" / "target" / "release" / "starlings-stage7c-p2panda"
K = 3

PROFILES = ("theta37", "theta51", "theta93", "novel_first")
TOPOLOGIES = ("ring", "grid")
SEEDS = (0, 1, 2)

COMMON_ARGS = (
    "--nodes", "8",
    "--facts", "32",
    "--redundancy", "2",
    "--bandwidth", "2",
    "--tick-ms", "5",
    "--startup-ms", "1000",
    "--drain-ms", "300",
    "--max-ticks", "4096",
    "--sim-max-rounds", "4096",
)


def run(*args: str, capture: bool = False, timeout_s: int | None = None):
    print("+", " ".join(args), file=sys.stderr)
    return subprocess.run(
        args,
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE if capture else None,
        timeout=timeout_s,
    )


def run_world(
    profile: str,
    topology: str,
    seed: int,
    fault: str,
) -> dict[str, str]:
    proc = run(
        str(BIN),
        "--profile", profile,
        "--topology", topology,
        "--seed", str(seed),
        "--fault", fault,
        *COMMON_ARGS,
        capture=True,
        timeout_s=45,
    )
    rows = list(
        csv.DictReader(io.StringIO(proc.stdout.decode()), delimiter="\t")
    )
    if len(rows) != 1:
        raise SystemExit(
            f"expected one F1b result row for {profile}/{topology}/{seed}/{fault}; "
            f"got {len(rows)}"
        )
    return rows[0]


def int_field(row: dict[str, str], name: str) -> int:
    return int(row[name], 10)


def structural_check(
    row: dict[str, str],
    label: str,
    hard_failures: list[str],
) -> None:
    if row["envelope_accounted"] != "yes":
        hard_failures.append(f"{label}: envelope ledger identity failed")
    if row["missing_accounted"] != "yes":
        hard_failures.append(f"{label}: missing-fact ledger identity failed")
    if row["communication_accounted"] != "yes":
        hard_failures.append(f"{label}: communication identity failed")
    if int_field(row, "ledger_interface_violations") != 0:
        hard_failures.append(f"{label}: transport-ledger interface violation")
    if int_field(row, "policy_errors") != 0:
        hard_failures.append(f"{label}: Zig policy FFI errors")
    attempts = int_field(row, "transport_attempts")
    terminals = (
        int_field(row, "delivered")
        + int_field(row, "partitioned")
        + int_field(row, "crashed")
        + int_field(row, "pending")
    )
    if attempts != terminals:
        hard_failures.append(
            f"{label}: attempt identity {attempts} != {terminals}"
        )


def main() -> int:
    if shutil.which("zig") is None:
        raise SystemExit("zig 0.16.0 is required on PATH")
    if shutil.which("cargo") is None:
        raise SystemExit("Rust/Cargo 1.98.0 toolchain is required on PATH")

    zig = run("zig", "version", capture=True).stdout.decode().strip()
    if zig != "0.16.0":
        raise SystemExit(f"zig 0.16.0 is required; found {zig}")

    run("zig", "build", "test")
    run(
        "cargo", "build", "--release",
        "--manifest-path", str(MANIFEST),
        timeout_s=600,
    )

    records: list[dict[str, str]] = []
    hard_failures: list[str] = []
    limitations: list[str] = []

    # Full fault-free transfer box, one canonical run each.
    for topology in TOPOLOGIES:
        for profile in PROFILES:
            for seed in SEEDS:
                row = run_world(profile, topology, seed, "no_fault")
                structural_check(
                    row,
                    f"fault_free/{profile}/{topology}/{seed}",
                    hard_failures,
                )
                if row["dist_success"] != "yes":
                    limitations.append(
                        f"fault-free non-convergence: {profile}/{topology}/{seed}"
                    )
                if int_field(row, "unattributed") != 0:
                    limitations.append(
                        f"fault-free unattributed loss: {profile}/{topology}/{seed}"
                    )
                if int_field(row, "sync_errors") != 0:
                    limitations.append(
                        f"fault-free p2panda sync errors: {profile}/{topology}/{seed}"
                    )
                row = {"phase": "fault_free", "repeat": "0", **row}
                records.append(row)

    # K-rerun determinism audit on representative fixed configurations.
    audit_worlds = (
        ("theta37", "ring", 0),
        ("theta51", "grid", 1),
        ("theta93", "ring", 2),
        ("novel_first", "grid", 2),
    )
    signatures: dict[tuple[str, str, int], list[str]] = defaultdict(list)
    audit_results: dict[tuple[str, str, int], list[tuple[tuple[str, str], ...]]] = defaultdict(list)
    for profile, topology, seed in audit_worlds:
        for repeat in range(K):
            row = run_world(profile, topology, seed, "no_fault")
            structural_check(
                row,
                f"audit/{profile}/{topology}/{seed}/r{repeat}",
                hard_failures,
            )
            signatures[(profile, topology, seed)].append(
                row["result_signature"]
            )
            audit_results[(profile, topology, seed)].append(
                tuple(sorted(row.items()))
            )
            if int_field(row, "unattributed") != 0:
                limitations.append(
                    f"audit unattributed loss: {profile}/{topology}/{seed}/r{repeat}"
                )
            row = {
                "phase": "determinism_audit",
                "repeat": str(repeat),
                **row,
            }
            records.append(row)

    for key, values in signatures.items():
        if len(set(values)) != 1:
            limitations.append(
                f"result-signature nondeterminism: {key} -> {values}"
            )
        if len(set(audit_results[key])) != 1:
            limitations.append(
                f"full-result nondeterminism: {key}"
            )

    # Contested subset: application-level deterministic fault gates around the
    # actual P2Panda movement path.
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
            structural_check(
                row,
                f"contested/{profile}/{topology}/{seed}/{fault}",
                hard_failures,
            )
            if int_field(row, "unattributed") != 0:
                limitations.append(
                    f"contested unattributed loss: {profile}/{topology}/{seed}/{fault}"
                )
            if fault == "partition" and int_field(row, "partitioned") == 0:
                hard_failures.append(
                    f"contested partition did not exercise fault: {profile}/{topology}/{seed}"
                )
            if fault.startswith("crash_restart") and int_field(row, "crashed") == 0:
                hard_failures.append(
                    f"contested crash did not exercise fault: {profile}/{topology}/{seed}/{fault}"
                )
            row = {"phase": "contested", "repeat": "0", **row}
            records.append(row)

    if hard_failures:
        for failure in hard_failures:
            print(f"HARD FAIL: {failure}", file=sys.stderr)
        return 1

    if not records:
        raise SystemExit("F1b produced no evidence rows")

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

    trial = ROOT / "trials" / "f1b-p2panda-wired.tsv"
    trial.parent.mkdir(parents=True, exist_ok=True)
    trial.write_bytes(data)

    digest = hashlib.sha256(data).hexdigest()
    fault_free = [r for r in records if r["phase"] == "fault_free"]
    audit = [r for r in records if r["phase"] == "determinism_audit"]
    contested = [r for r in records if r["phase"] == "contested"]

    print(f"F1b rows: {len(records)}")
    print(f"F1b bytes: {len(data)}")
    print(f"F1b sha256: {digest}")
    print(f"fault_free_rows: {len(fault_free)}")
    print(
        "fault_free_successes: "
        f"{sum(r['dist_success'] == 'yes' for r in fault_free)}/{len(fault_free)}"
    )
    print(f"determinism_audit_rows: {len(audit)}")
    print(f"determinism_K: {K}")
    print(f"contested_rows: {len(contested)}")
    print("envelope_accounting_failures: 0")
    print("missing_accounting_failures: 0")
    print("communication_accounting_failures: 0")
    print("ledger_interface_violations: 0")

    unattributed = sum(int_field(r, "unattributed") for r in records)
    pending = sum(int_field(r, "pending_at_censor") for r in records)
    sync_errors = sum(int_field(r, "sync_errors") for r in records)
    print(f"unattributed_missing: {unattributed}")
    print(f"pending_at_censor: {pending}")
    print(f"sync_errors: {sync_errors}")

    for key in sorted(signatures):
        values = signatures[key]
        stable = len(set(values)) == 1
        print(
            "audit "
            f"profile={key[0]} topology={key[1]} seed={key[2]} "
            f"stable={'yes' if stable else 'no'} signatures={','.join(values)}"
        )

    if limitations:
        print("F1b limitation reasons:")
        for reason in dict.fromkeys(limitations):
            print(f"  - {reason}")
        print(
            "F1b LIMITATION: P2Panda remains a measured candidate only; "
            "deterministic Zig remains authoritative"
        )
    else:
        print(
            "F1b PASS: P2Panda candidate is stable across the audit and "
            "fully accounted under the contested subset"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
