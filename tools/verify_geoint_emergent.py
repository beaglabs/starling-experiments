#!/usr/bin/env python3
"""Structural and optional live gate for the GEOINT emergence trial."""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
from typing import Optional

ROOT = pathlib.Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "tools" / "geoint_shadowfinder.py"
PHOTO_CONTEXT = ROOT / "tools" / "geoint_photo_context.py"


def run(
    *args: str,
    capture: bool = False,
    timeout_s: Optional[int] = None,
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
            f"GEOINT gate failed: {' '.join(args)} exit={proc.returncode}"
        )
    return proc


def structural_gate() -> None:
    version = run("zig", "version", capture=True).stdout.decode().strip()
    if version != "0.16.0":
        raise SystemExit(f"zig 0.16.0 required; found {version}")

    run("zig", "build", "test-demo-geoint-emergent", timeout_s=600)

    result = run(
        "zig",
        "build",
        "-Doptimize=ReleaseFast",
        "run-demo-geoint-emergent",
        "--",
        "validate",
        capture=True,
        timeout_s=600,
    )
    output = result.stdout.decode()
    print(output, end="" if output.endswith("\n") else "\n")

    required = (
        "seed_trajectory_distinct: yes",
        "context_trajectory_distinct: yes",
        "shadowfinder_state_dependent: yes",
        "epistemic_guard: PASS",
        "runtime_invariants: PASS",
        "GEOINT PASS: state-dependent operator emergence complete",
    )
    for marker in required:
        if marker not in output:
            raise SystemExit(f"GEOINT validator missing marker: {marker}")

    adapter_test = run(
        sys.executable,
        str(ADAPTER),
        "--self-test",
        capture=True,
        timeout_s=120,
    )
    self_test_output = adapter_test.stdout.decode()
    print(
        self_test_output,
        end="" if self_test_output.endswith("\n") else "\n",
    )

    description = json.loads(
        run(
            sys.executable,
            str(ADAPTER),
            "--describe",
            capture=True,
            timeout_s=120,
        ).stdout
    )
    if description["shadowfinder_version"] != "0.7.0":
        raise SystemExit("ShadowFinder adapter version pin drifted")
    if (
        description["shadowfinder_git_commit"]
        != "f12f8d6d30094f94c8cacb27f3c77db7e443d6fd"
    ):
        raise SystemExit("ShadowFinder adapter source pin drifted")

    run("zig", "build", "test", timeout_s=1800)
    print(
        "GEOINT STRUCTURAL PASS: "
        "typed evidence graph + state-dependent ShadowFinder boundary complete"
    )


def inspect_photo_context(args: argparse.Namespace) -> dict:
    command = [
        sys.executable,
        str(PHOTO_CONTEXT),
        "--image",
        str(args.photo),
    ]
    if args.datetime:
        command.extend(["--datetime", args.datetime])
    if args.sun_altitude_deg is not None:
        command.extend(["--sun-altitude-deg", str(args.sun_altitude_deg)])
    if args.object_height is not None:
        command.extend(["--object-height", str(args.object_height)])
    if args.shadow_length is not None:
        command.extend(["--shadow-length", str(args.shadow_length)])

    result = run(*command, capture=True, timeout_s=120)
    payload = json.loads(result.stdout)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return payload


def live_shadowfinder(args: argparse.Namespace) -> None:
    command = [
        sys.executable,
        str(ADAPTER),
        "--live",
        "--shadowfinder-root",
        str(args.shadowfinder_root),
        "--datetime",
        args.datetime,
        "--time-format",
        args.time_format,
        "--threshold",
        str(args.threshold),
        "--top-n",
        str(args.top_n),
    ]

    if args.timezone_grid:
        command.extend(["--timezone-grid", str(args.timezone_grid)])

    if args.sun_altitude_deg is not None:
        command.extend(
            ["--sun-altitude-deg", str(args.sun_altitude_deg)]
        )
    else:
        if args.object_height is None or args.shadow_length is None:
            raise SystemExit(
                "--shadowfinder-live requires --sun-altitude-deg "
                "or both --object-height and --shadow-length"
            )
        command.extend(
            [
                "--object-height",
                str(args.object_height),
                "--shadow-length",
                str(args.shadow_length),
            ]
        )

    result = run(
        *command,
        capture=True,
        timeout_s=3600,
    )
    payload = json.loads(result.stdout)
    print(json.dumps(payload, indent=2, sort_keys=True))

    count = int(payload["candidates"]["candidate_cells"])
    if count <= 0:
        raise SystemExit(
            "ShadowFinder live gate produced zero candidate cells "
            "inside the configured tolerance"
        )
    print(
        "GEOINT SHADOWFINDER LIVE PASS: "
        f"{count} candidate cells inside tolerance"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shadowfinder-live", action="store_true")
    parser.add_argument("--photo", type=pathlib.Path)
    parser.add_argument("--shadowfinder-root", type=pathlib.Path)
    parser.add_argument("--timezone-grid", type=pathlib.Path)
    parser.add_argument("--datetime")
    parser.add_argument(
        "--time-format",
        choices=("utc", "local"),
        default="utc",
    )
    parser.add_argument("--sun-altitude-deg", type=float)
    parser.add_argument("--object-height", type=float)
    parser.add_argument("--shadow-length", type=float)
    parser.add_argument("--threshold", type=float, default=0.02)
    parser.add_argument("--top-n", type=int, default=20)
    args = parser.parse_args()

    structural_gate()

    if args.photo:
        inspect_photo_context(args)

    if args.shadowfinder_live:
        if not args.shadowfinder_root or not args.datetime:
            raise SystemExit(
                "--shadowfinder-live requires "
                "--shadowfinder-root and --datetime"
            )
        live_shadowfinder(args)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
