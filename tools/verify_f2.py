#!/usr/bin/env python3
"""F2 asynchrony-gap and scaling evidence gate."""

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


def parse_tsv(data: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(data.decode()), delimiter="\t"))


def main() -> int:
    if shutil.which("zig") is None:
        raise SystemExit("zig 0.16.0 is required on PATH")
    version = capture_text("zig", "version")
    if version != "0.16.0":
        raise SystemExit(f"zig 0.16.0 is required; found {version}")

    run("zig", "build", "test")
    run("zig", "build", "-Doptimize=ReleaseFast", "run-f2", "--", "validate")

    gap1 = run(
        "zig", "build", "-Doptimize=ReleaseFast", "run-f2", "--", "gap",
        capture=True,
    ).stdout
    gap2 = run(
        "zig", "build", "-Doptimize=ReleaseFast", "run-f2", "--", "gap",
        capture=True,
    ).stdout
    if gap1 != gap2:
        raise SystemExit("F2.1 gap dataset is not byte-identical across replay")

    scaling1 = run(
        "zig", "build", "-Doptimize=ReleaseFast", "run-f2", "--", "scaling",
        capture=True,
    ).stdout
    scaling2 = run(
        "zig", "build", "-Doptimize=ReleaseFast", "run-f2", "--", "scaling",
        capture=True,
    ).stdout
    if scaling1 != scaling2:
        raise SystemExit("F2.2 scaling dataset is not byte-identical across replay")

    gap_rows = parse_tsv(gap1)
    scaling_rows = parse_tsv(scaling1)
    if len(gap_rows) != 36:
        raise SystemExit(f"expected 36 F2.1 rows; got {len(gap_rows)}")
    if len(scaling_rows) != 240:
        raise SystemExit(f"expected 240 F2.2 rows; got {len(scaling_rows)}")

    for row in gap_rows:
        if int(row["violations"]) != 0:
            raise SystemExit("F2.1 contains protocol violations")
        if row["async_success"] == "no" and (
            int(row["async_min_decisions"]) != 4096 or
            int(row["async_max_decisions"]) != 4096
        ):
            raise SystemExit("F2.1 censored before equal per-operator budget")

    for row in scaling_rows:
        if int(row["violations"]) != 0:
            raise SystemExit("F2.2 contains protocol violations")
        if row["success"] == "no" and (
            row["censored"] != "yes" or
            int(row["min_local_decisions"]) != 4096 or
            int(row["max_local_decisions"]) != 4096
        ):
            raise SystemExit("F2.2 censoring did not consume equal local budgets")

    trials = ROOT / "trials"
    trials.mkdir(parents=True, exist_ok=True)
    (trials / "f2-gap.tsv").write_bytes(gap1)
    (trials / "f2-scaling.tsv").write_bytes(scaling1)

    gap_sha = hashlib.sha256(gap1).hexdigest()
    scaling_sha = hashlib.sha256(scaling1).hexdigest()

    print(f"F2.1 rows: {len(gap_rows)}")
    print(f"F2.1 bytes: {len(gap1)}")
    print(f"F2.1 sha256: {gap_sha}")
    print("F2.1 byte_identical_replay: yes")

    by_profile: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in gap_rows:
        by_profile[row["profile"]].append(row)

    for profile in sorted(by_profile):
        rows = by_profile[profile]
        sync_success = sum(row["sync_success"] == "yes" for row in rows)
        async_success = sum(row["async_success"] == "yes" for row in rows)
        comm_delta = sum(int(row["communication_delta"]) for row in rows)
        duplicate_delta = sum(int(row["duplicate_delta"]) for row in rows)
        policy_delta = sum(int(row["policy_call_delta"]) for row in rows)
        print(
            f"gap {profile}: sync_success={sync_success}/{len(rows)} "
            f"async_success={async_success}/{len(rows)} "
            f"communication_delta={comm_delta} "
            f"duplicate_delta={duplicate_delta} "
            f"policy_call_delta={policy_delta}"
        )

    print(f"F2.2 rows: {len(scaling_rows)}")
    print(f"F2.2 bytes: {len(scaling1)}")
    print(f"F2.2 sha256: {scaling_sha}")
    print("F2.2 byte_identical_replay: yes")

    groups: dict[tuple[str, str, int], list[dict[str, str]]] = defaultdict(list)
    for row in scaling_rows:
        groups[(row["profile"], row["topology"], int(row["facts_per_node"]))].append(row)

    for key in sorted(groups):
        profile, topology, density = key
        rows = groups[key]
        censored_ns = sorted({
            int(row["nodes"]) for row in rows if row["success"] == "no"
        })
        boundary = str(censored_ns[0]) if censored_ns else "none"
        success_count = sum(row["success"] == "yes" for row in rows)
        print(
            f"boundary {profile}/{topology}/F_per_N={density}: "
            f"smallest_censored_N={boundary} successes={success_count}/{len(rows)}"
        )

    print("F2 PASS: hashes ready to freeze before scaffold deletion")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
