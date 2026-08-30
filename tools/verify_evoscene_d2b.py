#!/usr/bin/env python3
"""D2b deterministic backprojection engineering/live gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import struct
import subprocess
import sys
from typing import Any, Optional

ROOT = pathlib.Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "tools" / "evoscene_backproject.py"
DEFAULT_PRIOR = ROOT / "trials" / "evoscene-d2a-smoke" / "run-a"
DEFAULT_OUTPUT = ROOT / "trials" / "evoscene-d2b-smoke"


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
            f"D2b gate failed: {' '.join(args)} exit={proc.returncode}"
        )
    return proc


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(8 * 1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def read_json(path: pathlib.Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"expected JSON object: {path}")
    return value


def parse_contract(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            values[key] = value
    return values


def validate_static_contract() -> dict[str, Any]:
    if shutil.which("zig") is None:
        raise SystemExit("zig 0.16.0 is required on PATH")

    version = run("zig", "version", capture=True).stdout.decode().strip()
    if version != "0.16.0":
        raise SystemExit(f"zig 0.16.0 is required; found {version}")

    run("zig", "build", "test-demo-evoscene-d2b", timeout_s=600)
    proc = run(
        "zig",
        "build",
        "-Doptimize=ReleaseFast",
        "run-demo-evoscene-d2b",
        "--",
        "validate",
        capture=True,
        timeout_s=600,
    )
    contract_text = proc.stdout.decode()
    print(
        contract_text,
        end="" if contract_text.endswith("\n") else "\n",
    )
    contract = parse_contract(contract_text)

    self_test = run(
        sys.executable,
        str(ADAPTER),
        "--self-test",
        capture=True,
        timeout_s=120,
    )
    self_text = self_test.stdout.decode()
    print(self_text, end="" if self_text.endswith("\n") else "\n")

    description = json.loads(
        run(
            sys.executable,
            str(ADAPTER),
            "--describe",
            capture=True,
            timeout_s=120,
        ).stdout
    )

    expected = {
        "schema_version": str(description["schema_version"]),
        "adapter_version": str(description["adapter_version"]),
        "points_encoding": str(description["points_encoding"]),
        "pixel_convention": str(description["pixel_convention"]),
        "camera_convention": str(description["camera_convention"]),
        "ply_format": str(description["ply_format"]),
    }
    for key, wanted in expected.items():
        actual = contract.get(key)
        if actual != wanted:
            raise SystemExit(
                f"D2b Zig/Python contract mismatch {key}: "
                f"{actual!r} != {wanted!r}"
            )

    if contract.get("sample_backprojection") != "PASS":
        raise SystemExit("D2b Zig sample backprojection did not pass")
    if not contract_text.rstrip().endswith(
        "D2b CONTRACT PASS: deterministic point-cloud boundary frozen"
    ):
        raise SystemExit("D2b Zig contract verdict missing")

    return description


def validate_ply(
    ply_path: pathlib.Path,
    points_path: pathlib.Path,
    expected_count: int,
) -> None:
    raw = points_path.read_bytes()
    if len(raw) != expected_count * 12:
        raise SystemExit("D2b points byte count does not match point count")

    lines = ply_path.read_text(encoding="ascii").splitlines()
    if len(lines) < 8:
        raise SystemExit("D2b PLY is too short")
    if lines[0] != "ply" or lines[1] != "format ascii 1.0":
        raise SystemExit("D2b PLY header is invalid")
    if f"element vertex {expected_count}" not in lines:
        raise SystemExit("D2b PLY vertex count is missing")

    try:
        end_header = lines.index("end_header")
    except ValueError as exc:
        raise SystemExit("D2b PLY has no end_header") from exc

    vertices = lines[end_header + 1 :]
    if len(vertices) != expected_count:
        raise SystemExit(
            f"D2b PLY vertex rows {len(vertices)} != {expected_count}"
        )

    for index, ((x, y, z), line) in enumerate(
        zip(struct.iter_unpack("<fff", raw), vertices)
    ):
        parts = line.split()
        if len(parts) != 3:
            raise SystemExit(f"D2b malformed PLY row {index}")
        ascii_point = tuple(float(value) for value in parts)
        packed_ascii = struct.pack("<fff", *ascii_point)
        packed_raw = struct.pack("<fff", x, y, z)
        if packed_ascii != packed_raw:
            raise SystemExit(
                f"D2b PLY row {index} does not round-trip to point bytes"
            )


def validate_output(
    path: pathlib.Path,
    prior_dir: pathlib.Path,
    description: dict[str, Any],
) -> dict[str, Any]:
    scene_path = path / description["scene_manifest_filename"]
    points_path = path / description["points_filename"]
    ply_path = path / description["ply_filename"]
    telemetry_path = path / description["telemetry_filename"]

    for required in (scene_path, points_path, ply_path, telemetry_path):
        if not required.is_file():
            raise SystemExit(f"D2b output missing: {required}")

    scene = read_json(scene_path)
    telemetry = read_json(telemetry_path)
    prior = read_json(prior_dir / "prior.json")

    if scene.get("schema") != "evoscene.point_cloud.v1":
        raise SystemExit("D2b scene schema mismatch")
    if scene.get("schema_version") != description["schema_version"]:
        raise SystemExit("D2b scene schema version mismatch")
    if scene.get("adapter_version") != description["adapter_version"]:
        raise SystemExit("D2b scene adapter version mismatch")

    config = scene.get("config") or {}
    expected_config = {
        "points_encoding": description["points_encoding"],
        "pixel_convention": description["pixel_convention"],
        "camera_convention": description["camera_convention"],
        "ply_format": description["ply_format"],
        "ordering": "source-row-major-valid-pixels-only",
    }
    for key, wanted in expected_config.items():
        if config.get(key) != wanted:
            raise SystemExit(
                f"D2b scene config mismatch {key}: "
                f"{config.get(key)!r} != {wanted!r}"
            )

    parents = scene.get("parents") or {}
    expected_parents = {
        "prior_manifest_sha256": sha256_file(prior_dir / "prior.json"),
        "depth_sha256": sha256_file(prior_dir / "depth.f32le"),
        "mask_sha256": sha256_file(prior_dir / "mask.u8"),
        "camera_sha256": sha256_file(prior_dir / "camera.json"),
    }
    for key, wanted in expected_parents.items():
        if parents.get(key) != wanted:
            raise SystemExit(
                f"D2b parent provenance mismatch {key}: "
                f"{parents.get(key)!r} != {wanted!r}"
            )

    point_count = int(scene["summary"]["point_count"])
    expected_count = int(prior["summary"]["valid_pixels"])
    if point_count != expected_count or point_count <= 0:
        raise SystemExit(
            f"D2b point count {point_count} != D2a valid pixels {expected_count}"
        )

    artifacts = scene.get("artifacts") or {}
    points_meta = artifacts.get("points") or {}
    ply_meta = artifacts.get("ply") or {}

    if int(points_meta.get("bytes", -1)) != point_count * 12:
        raise SystemExit("D2b points byte size is not point_count * 12")
    if points_meta.get("sha256") != sha256_file(points_path):
        raise SystemExit("D2b points SHA-256 mismatch")
    if int(ply_meta.get("bytes", -1)) != ply_path.stat().st_size:
        raise SystemExit("D2b PLY byte size mismatch")
    if ply_meta.get("sha256") != sha256_file(ply_path):
        raise SystemExit("D2b PLY SHA-256 mismatch")

    validate_ply(ply_path, points_path, point_count)

    bounds = scene["summary"]["bounds_m"]
    minimum = [float(v) for v in bounds["min"]]
    maximum = [float(v) for v in bounds["max"]]
    if len(minimum) != 3 or len(maximum) != 3:
        raise SystemExit("D2b bounds are not 3D")
    extents = [hi - lo for lo, hi in zip(minimum, maximum)]
    if any(extent <= 0.0 for extent in extents):
        raise SystemExit(f"D2b point cloud is degenerate: extents={extents}")
    if minimum[2] <= 0.0:
        raise SystemExit("D2b point cloud contains non-positive forward depth")

    if telemetry.get("schema") != "evoscene.point_cloud.telemetry.v1":
        raise SystemExit("D2b telemetry schema mismatch")
    if int(telemetry.get("backprojection_ms", -1)) < 0:
        raise SystemExit("D2b telemetry timing is invalid")

    return {
        "scene_sha256": sha256_file(scene_path),
        "points_sha256": points_meta["sha256"],
        "ply_sha256": ply_meta["sha256"],
        "point_count": point_count,
        "bounds": bounds,
        "backprojection_ms": int(telemetry["backprojection_ms"]),
    }


def run_live(
    prior_dir: pathlib.Path,
    output_root: pathlib.Path,
    description: dict[str, Any],
) -> None:
    prior_dir = prior_dir.expanduser().resolve()
    output_root = output_root.expanduser().resolve()

    if not (prior_dir / "prior.json").is_file():
        raise SystemExit(
            f"D2a prior not found at {prior_dir}. "
            "Run tools/verify_evoscene_d2a.py --live first."
        )

    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True)

    results = []
    for label in ("run-a", "run-b"):
        out = output_root / label
        proc = run(
            sys.executable,
            str(ADAPTER),
            "--prior-dir",
            str(prior_dir),
            "--output",
            str(out),
            capture=True,
            timeout_s=600,
        )
        response = json.loads(proc.stdout)
        if response.get("status") != "ok":
            raise SystemExit(f"D2b adapter response failed: {response}")
        results.append(validate_output(out, prior_dir, description))

    first, second = results
    for key in ("scene_sha256", "points_sha256", "ply_sha256"):
        if first[key] != second[key]:
            raise SystemExit(
                f"D2b repeated execution differs for {key}: "
                f"{first[key]} != {second[key]}"
            )

    bounds = first["bounds"]
    print(f"D2b point_count: {first['point_count']}")
    print(f"D2b points_sha256: {first['points_sha256']}")
    print(f"D2b ply_sha256: {first['ply_sha256']}")
    print(f"D2b scene_sha256: {first['scene_sha256']}")
    print(
        "D2b bounds_min_m: "
        + ",".join(str(v) for v in bounds["min"])
    )
    print(
        "D2b bounds_max_m: "
        + ",".join(str(v) for v in bounds["max"])
    )
    print(f"D2b backprojection_ms: {first['backprojection_ms']}")
    print("D2b byte_identical_repeat: yes")
    print("D2b LIVE PASS: deterministic point-cloud reconstruction complete")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--prior-dir",
        type=pathlib.Path,
        default=DEFAULT_PRIOR,
    )
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=DEFAULT_OUTPUT,
    )
    args = parser.parse_args()

    description = validate_static_contract()

    if not args.live:
        print("D2b STRUCTURAL PASS: Zig/Python backprojection contracts agree")
        return 0

    run_live(args.prior_dir, args.output, description)

    run("zig", "build", "test", timeout_s=1800)
    print("D2b REPOSITORY PASS: full Zig test suite complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
