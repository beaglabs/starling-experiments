#!/usr/bin/env python3
"""Deterministic D2b backprojection from D2a depth/camera artifacts.

Inputs are the frozen D2a prior artifacts:
- depth.f32le
- mask.u8
- camera.json
- prior.json

Outputs:
- points.f32le: row-major valid-pixel XYZ float32 triplets in meters
- scene.ply: deterministic ASCII PLY view of the same quantized points
- scene.json: deterministic content/provenance manifest
- telemetry.json: non-content timing/environment metadata
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import platform
import struct
import sys
import tempfile
import time
from typing import Any

SCHEMA_VERSION = 1
ADAPTER_VERSION = 1
SCHEMA = "evoscene.point_cloud.v1"

INPUT_MANIFEST_FILENAME = "prior.json"
INPUT_DEPTH_FILENAME = "depth.f32le"
INPUT_MASK_FILENAME = "mask.u8"
INPUT_CAMERA_FILENAME = "camera.json"

POINTS_FILENAME = "points.f32le"
PLY_FILENAME = "scene.ply"
SCENE_MANIFEST_FILENAME = "scene.json"
TELEMETRY_FILENAME = "telemetry.json"

POINTS_ENCODING = "row-major-valid-pixels-xyz-f32le-meters"
PIXEL_CONVENTION = "normalized-pixel-centers"
CAMERA_CONVENTION = "opencv-x-right-y-down-z-forward"
PLY_FORMAT = "ascii-ply-v1-xyz-only"


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(8 * 1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def write_bytes(path: pathlib.Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def qfloat(value: float, digits: int = 10) -> float:
    result = round(float(value), digits)
    if not math.isfinite(result):
        raise RuntimeError("non-finite value cannot enter canonical JSON")
    return result


def f32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", float(value)))[0]


def describe() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "input_manifest_filename": INPUT_MANIFEST_FILENAME,
        "input_depth_filename": INPUT_DEPTH_FILENAME,
        "input_mask_filename": INPUT_MASK_FILENAME,
        "input_camera_filename": INPUT_CAMERA_FILENAME,
        "points_filename": POINTS_FILENAME,
        "ply_filename": PLY_FILENAME,
        "scene_manifest_filename": SCENE_MANIFEST_FILENAME,
        "telemetry_filename": TELEMETRY_FILENAME,
        "points_encoding": POINTS_ENCODING,
        "pixel_convention": PIXEL_CONVENTION,
        "camera_convention": CAMERA_CONVENTION,
        "ply_format": PLY_FORMAT,
    }


def load_json(path: pathlib.Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def require_sha(path: pathlib.Path, expected: Any, label: str) -> str:
    if not isinstance(expected, str) or len(expected) != 64:
        raise RuntimeError(f"{label} has invalid declared SHA-256")
    actual = sha256_file(path)
    if actual != expected:
        raise RuntimeError(
            f"{label} SHA-256 mismatch: {actual} != {expected}"
        )
    return actual


def validate_prior(
    prior_dir: pathlib.Path,
) -> tuple[dict[str, Any], dict[str, Any], bytes, bytes, dict[str, str]]:
    manifest_path = prior_dir / INPUT_MANIFEST_FILENAME
    depth_path = prior_dir / INPUT_DEPTH_FILENAME
    mask_path = prior_dir / INPUT_MASK_FILENAME
    camera_path = prior_dir / INPUT_CAMERA_FILENAME

    for path in (manifest_path, depth_path, mask_path, camera_path):
        if not path.is_file():
            raise RuntimeError(f"missing D2a input artifact: {path}")

    prior = load_json(manifest_path)
    camera = load_json(camera_path)

    if prior.get("schema") != "evoscene.spatial_prior.v1":
        raise RuntimeError("D2a prior manifest schema mismatch")

    artifacts = prior.get("artifacts")
    if not isinstance(artifacts, dict):
        raise RuntimeError("D2a prior manifest has no artifacts object")

    depth_meta = artifacts.get("depth")
    mask_meta = artifacts.get("mask")
    camera_meta = artifacts.get("camera")
    if not all(isinstance(v, dict) for v in (depth_meta, mask_meta, camera_meta)):
        raise RuntimeError("D2a prior manifest artifact metadata is incomplete")

    hashes = {
        "prior": sha256_file(manifest_path),
        "depth": require_sha(depth_path, depth_meta.get("sha256"), "depth"),
        "mask": require_sha(mask_path, mask_meta.get("sha256"), "mask"),
        "camera": require_sha(camera_path, camera_meta.get("sha256"), "camera"),
    }

    input_meta = prior.get("input")
    summary = prior.get("summary")
    if not isinstance(input_meta, dict) or not isinstance(summary, dict):
        raise RuntimeError("D2a prior manifest input/summary is incomplete")

    width = int(input_meta.get("width", 0))
    height = int(input_meta.get("height", 0))
    if width <= 0 or height <= 0:
        raise RuntimeError("D2a prior dimensions are invalid")
    pixels = width * height

    depth_bytes = depth_path.read_bytes()
    mask_bytes = mask_path.read_bytes()
    if len(depth_bytes) != pixels * 4:
        raise RuntimeError(
            f"D2a depth bytes {len(depth_bytes)} != expected {pixels * 4}"
        )
    if len(mask_bytes) != pixels:
        raise RuntimeError(
            f"D2a mask bytes {len(mask_bytes)} != expected {pixels}"
        )

    declared_valid = int(summary.get("valid_pixels", -1))
    actual_valid = sum(mask_bytes)
    if any(value not in (0, 1) for value in mask_bytes):
        raise RuntimeError("D2a mask contains values outside {0,1}")
    if declared_valid != actual_valid or actual_valid <= 0:
        raise RuntimeError(
            f"D2a valid-pixel mismatch: {actual_valid} != {declared_valid}"
        )

    if camera.get("schema") != "evoscene.camera.v1":
        raise RuntimeError("D2a camera schema mismatch")
    if int(camera.get("image_width", 0)) != width:
        raise RuntimeError("D2a camera width does not match prior")
    if int(camera.get("image_height", 0)) != height:
        raise RuntimeError("D2a camera height does not match prior")

    intrinsics = camera.get("intrinsics_normalized")
    if (
        not isinstance(intrinsics, list)
        or len(intrinsics) != 3
        or any(not isinstance(row, list) or len(row) != 3 for row in intrinsics)
    ):
        raise RuntimeError("D2a normalized intrinsics are not 3x3")

    fx = float(intrinsics[0][0])
    fy = float(intrinsics[1][1])
    cx = float(intrinsics[0][2])
    cy = float(intrinsics[1][2])
    if not all(math.isfinite(v) for v in (fx, fy, cx, cy)):
        raise RuntimeError("D2a camera intrinsics contain non-finite values")
    if fx <= 0.0 or fy <= 0.0:
        raise RuntimeError("D2a focal lengths must be positive")

    return prior, camera, depth_bytes, mask_bytes, hashes


def backproject(
    width: int,
    height: int,
    depth_bytes: bytes,
    mask_bytes: bytes,
    intrinsics: list[list[float]],
) -> list[tuple[float, float, float]]:
    fx = float(intrinsics[0][0])
    fy = float(intrinsics[1][1])
    cx = float(intrinsics[0][2])
    cy = float(intrinsics[1][2])

    points: list[tuple[float, float, float]] = []
    for index, (depth,) in enumerate(struct.iter_unpack("<f", depth_bytes)):
        valid = mask_bytes[index]
        if not valid:
            if depth != 0.0:
                raise RuntimeError(
                    "D2a invalid pixel has nonzero canonical depth"
                )
            continue

        if not math.isfinite(depth) or depth <= 0.0:
            raise RuntimeError("D2a valid pixel has invalid depth")

        y, x = divmod(index, width)
        u = (x + 0.5) / width
        v = (y + 0.5) / height

        px = f32((u - cx) * depth / fx)
        py = f32((v - cy) * depth / fy)
        pz = f32(depth)

        if not all(math.isfinite(vv) for vv in (px, py, pz)):
            raise RuntimeError("backprojection produced non-finite point")

        points.append((px, py, pz))

    return points


def points_bytes(points: list[tuple[float, float, float]]) -> bytes:
    output = bytearray(len(points) * 12)
    offset = 0
    for point in points:
        struct.pack_into("<fff", output, offset, *point)
        offset += 12
    return bytes(output)


def ply_bytes(points: list[tuple[float, float, float]]) -> bytes:
    header = (
        "ply\n"
        "format ascii 1.0\n"
        "comment evoscene d2b deterministic backprojection\n"
        f"element vertex {len(points)}\n"
        "property float x\n"
        "property float y\n"
        "property float z\n"
        "end_header\n"
    )
    lines = [
        f"{format(x, '.9g')} {format(y, '.9g')} {format(z, '.9g')}\n"
        for x, y, z in points
    ]
    return (header + "".join(lines)).encode("ascii")


def bounds(points: list[tuple[float, float, float]]) -> dict[str, list[float]]:
    if not points:
        raise RuntimeError("cannot compute bounds of empty point cloud")

    min_x = max_x = points[0][0]
    min_y = max_y = points[0][1]
    min_z = max_z = points[0][2]

    for x, y, z in points[1:]:
        min_x = min(min_x, x)
        max_x = max(max_x, x)
        min_y = min(min_y, y)
        max_y = max(max_y, y)
        min_z = min(min_z, z)
        max_z = max(max_z, z)

    return {
        "min": [qfloat(min_x), qfloat(min_y), qfloat(min_z)],
        "max": [qfloat(max_x), qfloat(max_y), qfloat(max_z)],
    }


def run_backprojection(args: argparse.Namespace) -> dict[str, Any]:
    prior_dir = pathlib.Path(args.prior_dir).expanduser().resolve()
    output_dir = pathlib.Path(args.output).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter_ns()
    prior, camera, depth_data, mask_data, parent_hashes = validate_prior(
        prior_dir
    )

    width = int(prior["input"]["width"])
    height = int(prior["input"]["height"])
    intrinsics = camera["intrinsics_normalized"]

    points = backproject(
        width,
        height,
        depth_data,
        mask_data,
        intrinsics,
    )
    expected_points = int(prior["summary"]["valid_pixels"])
    if len(points) != expected_points:
        raise RuntimeError(
            f"backprojected point count {len(points)} != {expected_points}"
        )

    raw_points = points_bytes(points)
    ply = ply_bytes(points)

    raw_path = output_dir / POINTS_FILENAME
    ply_path = output_dir / PLY_FILENAME
    write_bytes(raw_path, raw_points)
    write_bytes(ply_path, ply)

    point_sha = sha256_bytes(raw_points)
    ply_sha = sha256_bytes(ply)
    cloud_bounds = bounds(points)

    scene = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "parents": {
            "prior_manifest_sha256": parent_hashes["prior"],
            "depth_sha256": parent_hashes["depth"],
            "mask_sha256": parent_hashes["mask"],
            "camera_sha256": parent_hashes["camera"],
        },
        "input": {
            "width": width,
            "height": height,
            "valid_pixels": expected_points,
        },
        "camera": {
            "intrinsics_normalized": intrinsics,
        },
        "config": {
            "points_encoding": POINTS_ENCODING,
            "pixel_convention": PIXEL_CONVENTION,
            "camera_convention": CAMERA_CONVENTION,
            "ordering": "source-row-major-valid-pixels-only",
            "ply_format": PLY_FORMAT,
        },
        "artifacts": {
            "points": {
                "file": POINTS_FILENAME,
                "bytes": len(raw_points),
                "sha256": point_sha,
            },
            "ply": {
                "file": PLY_FILENAME,
                "bytes": len(ply),
                "sha256": ply_sha,
            },
        },
        "summary": {
            "point_count": len(points),
            "bounds_m": cloud_bounds,
        },
    }

    scene_bytes = canonical_json_bytes(scene)
    scene_path = output_dir / SCENE_MANIFEST_FILENAME
    write_bytes(scene_path, scene_bytes)

    elapsed_ms = (time.perf_counter_ns() - started) // 1_000_000
    telemetry = {
        "schema": "evoscene.point_cloud.telemetry.v1",
        "backprojection_ms": int(elapsed_ms),
        "python": platform.python_version(),
        "platform": platform.platform(),
    }
    write_bytes(
        output_dir / TELEMETRY_FILENAME,
        canonical_json_bytes(telemetry),
    )

    return {
        "status": "ok",
        "scene": str(scene_path),
        "scene_sha256": sha256_bytes(scene_bytes),
        "points_sha256": point_sha,
        "ply_sha256": ply_sha,
        "point_count": len(points),
        "bounds_m": cloud_bounds,
    }


def write_synthetic_prior(root: pathlib.Path) -> None:
    width, height = 2, 2
    depths = (2.0, 0.0, 4.0, 8.0)
    mask = bytes((1, 0, 1, 1))
    depth_data = b"".join(struct.pack("<f", value) for value in depths)

    camera = {
        "schema": "evoscene.camera.v1",
        "convention": "opencv-normalized-image-coordinates",
        "image_width": width,
        "image_height": height,
        "intrinsics_normalized": [
            [0.5, 0.0, 0.5],
            [0.0, 0.5, 0.5],
            [0.0, 0.0, 1.0],
        ],
    }
    camera_data = canonical_json_bytes(camera)

    write_bytes(root / INPUT_DEPTH_FILENAME, depth_data)
    write_bytes(root / INPUT_MASK_FILENAME, mask)
    write_bytes(root / INPUT_CAMERA_FILENAME, camera_data)

    prior = {
        "schema": "evoscene.spatial_prior.v1",
        "input": {
            "width": width,
            "height": height,
        },
        "artifacts": {
            "depth": {"sha256": sha256_bytes(depth_data)},
            "mask": {"sha256": sha256_bytes(mask)},
            "camera": {"sha256": sha256_bytes(camera_data)},
        },
        "summary": {
            "valid_pixels": 3,
        },
    }
    write_bytes(
        root / INPUT_MANIFEST_FILENAME,
        canonical_json_bytes(prior),
    )


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="evoscene-d2b-") as tmp:
        root = pathlib.Path(tmp)
        prior_dir = root / "prior"
        output_a = root / "a"
        output_b = root / "b"
        prior_dir.mkdir()
        write_synthetic_prior(prior_dir)

        args_a = argparse.Namespace(
            prior_dir=str(prior_dir),
            output=str(output_a),
        )
        args_b = argparse.Namespace(
            prior_dir=str(prior_dir),
            output=str(output_b),
        )
        first = run_backprojection(args_a)
        second = run_backprojection(args_b)

        if first["points_sha256"] != second["points_sha256"]:
            raise AssertionError("point bytes are not deterministic")
        if first["ply_sha256"] != second["ply_sha256"]:
            raise AssertionError("PLY bytes are not deterministic")
        if first["scene_sha256"] != second["scene_sha256"]:
            raise AssertionError("scene manifest is not deterministic")
        if first["point_count"] != 3:
            raise AssertionError("synthetic point count is wrong")

        raw = (output_a / POINTS_FILENAME).read_bytes()
        points = list(struct.iter_unpack("<fff", raw))
        expected = [
            (-1.0, -1.0, 2.0),
            (-2.0, 2.0, 4.0),
            (4.0, 4.0, 8.0),
        ]
        if len(points) != len(expected):
            raise AssertionError("synthetic point output length is wrong")

        for actual, wanted in zip(points, expected):
            if actual != wanted:
                raise AssertionError(
                    f"backprojection mismatch: {actual} != {wanted}"
                )

    print("D2b adapter self-test PASS: deterministic backprojection exact")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--prior-dir")
    parser.add_argument("--output")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    modes = sum(
        (
            bool(args.self_test),
            bool(args.describe),
            bool(args.prior_dir or args.output),
        )
    )
    if modes != 1:
        raise SystemExit(
            "choose exactly one mode: --self-test, --describe, "
            "or --prior-dir DIR --output DIR"
        )

    if args.self_test:
        self_test()
        return 0

    if args.describe:
        sys.stdout.buffer.write(canonical_json_bytes(describe()))
        return 0

    if not args.prior_dir or not args.output:
        raise SystemExit("--prior-dir and --output are required together")

    try:
        result = run_backprojection(args)
    except Exception as exc:
        sys.stderr.buffer.write(
            canonical_json_bytes(
                {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )
        )
        return 2

    sys.stdout.buffer.write(canonical_json_bytes(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
