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
    run("zig", "build", "-Doptimize=ReleaseFast", "run-f3", "--", "validate")

    corner = run(
        "zig", "build", "-Doptimize=ReleaseFast", "run-f3", "--", "corner",
        capture=True,
    ).stdout.decode()
    if "mismatches: 0" not in corner:
        raise SystemExit("F3 c=1000 corner did not reproduce Stage 7B")
    print(corner.rstrip())

    first = run(
        "zig", "build", "-Doptimize=ReleaseFast", "run-f3", "--", "search",
        capture=True,
    ).stdout
    second = run(
        "zig", "build", "-Doptimize=ReleaseFast", "run-f3", "--", "search",
        capture=True,
    ).stdout
    if first != second:
        raise SystemExit("F3 search evidence is not byte-identical across replay")

    rows = parse_tsv(first)
    if not rows:
        raise SystemExit("F3 search emitted no evidence rows")

    for row in rows:
        if int(row["violations"]) != 0:
            raise SystemExit(
                f"F3 protocol violation in {row['phase']} id={row['id']}"
            )
        if int(row["inference_sum"]) > int(row["computation_sum"]):
            raise SystemExit(
                f"F3 inference accounting exceeds computation calls id={row['id']}"
            )

    validation = {
        int(row["id"]): row
        for row in rows
        if row["phase"] == "validation"
    }
    twins = {
        int(row["id"]): row
        for row in rows
        if row["phase"] == "validation_ungated_twin"
    }
    selected = [
        row for row in rows
        if row["phase"] == "validation" and row["selected_frontier"] == "yes"
    ]
    if not selected:
        raise SystemExit("F3 validation frontier is empty")

    gated_improvements: list[tuple[dict[str, str], dict[str, str]]] = []
    for row in selected:
        candidate_id = int(row["id"])
        if int(row["c"]) >= 1000 or int(row["failures"]) != 0:
            continue
        twin = twins.get(candidate_id)
        if twin is None:
            raise SystemExit(f"selected gated candidate {candidate_id} lacks twin")
        if int(twin["failures"]) != 0:
            continue
        if int(row["inference_sum"]) < int(twin["inference_sum"]):
            gated_improvements.append((row, twin))

    hard_frozen = [
        row for row in rows if row["phase"] == "hard_frozen_stage7b"
    ]
    if len(hard_frozen) != 18:
        raise SystemExit(
            f"expected 18 frozen Stage 7B hard rows; found {len(hard_frozen)}"
        )
    if {int(row["id"]) for row in hard_frozen} != {37, 51, 93}:
        raise SystemExit("hard frozen-family IDs do not match 37/51/93")

    trial = ROOT / "trials" / "f3-inference-control.tsv"
    trial.parent.mkdir(parents=True, exist_ok=True)
    trial.write_bytes(first)

    digest = hashlib.sha256(first).hexdigest()
    print(f"F3 rows: {len(rows)}")
    print(f"F3 bytes: {len(first)}")
    print(f"F3 sha256: {digest}")
    print("byte_identical_replay: yes")
    print("violations: 0")

    print("validation frontier:")
    for row in selected:
        print(
            f"  id={row['id']} theta=({row['n']},{row['e']},{row['r']},"
            f"{row['u']},{row['c']}) failures={row['failures']} "
            f"communication={row['communication_sum']} "
            f"inference={row['inference_sum']}"
        )

    if gated_improvements:
        print("gated validation improvements:")
        for row, twin in gated_improvements:
            saved = int(twin["inference_sum"]) - int(row["inference_sum"])
            print(
                f"  id={row['id']} c={row['c']} failures=0 "
                f"inference={row['inference_sum']} "
                f"ungated={twin['inference_sum']} saved={saved}"
            )
        outcome = "PASS"
    else:
        print(
            "no selected gated theta preserved zero validation failures while "
            "strictly reducing inference units"
        )
        outcome = "LIMITATION"

    by_frozen: dict[int, list[dict[str, str]]] = defaultdict(list)
    for row in hard_frozen:
        by_frozen[int(row["id"])].append(row)
    for candidate_id in sorted(by_frozen):
        failures = sum(int(row["failures"]) for row in by_frozen[candidate_id])
        inference = sum(int(row["inference_sum"]) for row in by_frozen[candidate_id])
        communication = sum(
            int(row["communication_sum"]) for row in by_frozen[candidate_id]
        )
        print(
            f"frozen id{candidate_id} hard: failures={failures} "
            f"communication={communication} inference={inference}"
        )

    print(f"F3 {outcome}: local inference-control evidence complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
