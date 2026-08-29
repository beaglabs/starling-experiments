#!/usr/bin/env python3
"""S0 reproducibility gate.

This script intentionally fails closed. It first proves that the re-materialized
historical source files are byte-identical Git blobs, then runs the package and
historical validation gates, regenerates the Stage 7C first suite, and requires
its canonical SHA-256 to match the frozen value.
"""

from __future__ import annotations

import hashlib
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXPECTED_STAGE7C_SHA256 = "c89d1985af0479191126fca91265b1fe7f49e7b34db471e13c74e8bb28195a36"

FROZEN_GIT_BLOBS = {
    "src/substrate/stage5/stage5a_scaling.zig": "0b45d61506611b9b2d370391a6eafe5b5de0569b",
    "src/substrate/stage5/stage5a_cli.zig": "3dc58e065f222a3ebb8efcff1357a44bd557d2cc",
    "src/substrate/stage7/stage7a_policy.zig": "3efd8d3702d3918e91eab3ae5500f155364a5b34",
    "src/substrate/stage7/stage7a_cli.zig": "f95ce6aaa81fcae09524fb6527733ea4b6c682e3",
    "src/substrate/stage7/stage7c_async_transfer.zig": "d232fadb975c222daab26eb879ae1546fb44bb54",
    "src/substrate/stage7/stage7c_cli.zig": "69b9a1211ffc08fbb27823dc816bf2a65718585a",
}


def git_blob_sha1(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode()
    return hashlib.sha1(header + data).hexdigest()


def check_frozen_sources() -> None:
    for rel, expected in FROZEN_GIT_BLOBS.items():
        data = (ROOT / rel).read_bytes()
        actual = git_blob_sha1(data)
        if actual != expected:
            raise SystemExit(
                f"frozen source mismatch: {rel}\n"
                f"expected git blob {expected}\n"
                f"actual   git blob {actual}"
            )
    print(f"frozen sources: {len(FROZEN_GIT_BLOBS)} byte-identical blobs")


def run(*args: str, capture: bool = False) -> subprocess.CompletedProcess[bytes]:
    print("+", " ".join(args), file=sys.stderr)
    return subprocess.run(
        args,
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE if capture else None,
    )


def main() -> int:
    if shutil.which("zig") is None:
        raise SystemExit("zig 0.16.0 is required on PATH")

    check_frozen_sources()
    run("zig", "build", "test")
    run("zig", "build", "run-stage5a", "--", "validate")
    run("zig", "build", "run-stage7a", "--", "validate")
    run("zig", "build", "run-stage7c", "--", "validate")

    suite = run("zig", "build", "-Doptimize=ReleaseFast", "run-stage7c", "--", "suite", capture=True).stdout
    actual = hashlib.sha256(suite).hexdigest()

    trial_path = ROOT / "trials" / "s0-stage7c.tsv"
    trial_path.parent.mkdir(parents=True, exist_ok=True)
    trial_path.write_bytes(suite)

    print(f"stage7c bytes: {len(suite)}")
    print(f"stage7c sha256: {actual}")

    if actual != EXPECTED_STAGE7C_SHA256:
        print(f"expected sha256: {EXPECTED_STAGE7C_SHA256}", file=sys.stderr)
        return 1

    print("S0 PASS: package boundary and frozen Stage 7C reproduction verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
