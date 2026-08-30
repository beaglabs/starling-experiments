#!/usr/bin/env python3
"""Deterministic D2d point-cloud novel-view renderer + reprojection.

This stage does not invent geometry. It deterministically:
1. orbits a virtual camera around the current reconstructed scene;
2. z-buffers the current point cloud into target-view metric depth;
3. backprojects target pixel centers through that depth;
4. transforms the reprojected points back into the original/world frame.

The resulting point-cloud evidence is directly consumable by D2c.
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
SCHEMA = "evoscene.novel_view.v1"

POSE_A_AZIMUTH_MDEG = 35_000
POSE_A_ELEVATION_MDEG = 10_000
POSE_B_AZIMUTH_MDEG = 325_000
POSE_B_ELEVATION_MDEG = 10_000

SOURCE_MANIFEST_FILENAME = "fusion.json"
SOURCE_POINTS_FILENAME = "refined_points.f32le"
CAMERA_FILENAME = "camera.json"
PRIOR_FILENAME = "prior.json"

DEPTH_FILENAME = "render_depth.f32le"
MASK_FILENAME = "render_mask.u8"
POINTS_FILENAME = "points.f32le"
SCENE_FILENAME = "scene.json"
RENDER_MANIFEST_FILENAME = "render.json"
TELEMETRY_FILENAME = "telemetry.json"

RASTER_RULE = "nearest-z-source-index-tiebreak"
PIXEL_CONVENTION = "normalized-pixel-centers"
CAMERA_CONVENTION = "opencv-x-right-y-down-z-forward"
ORBIT_RULE = "source-origin-orbit-about-aabb-center-look-at-center"
EVIDENCE_RULE = "zbuffer-depth-backproject-to-world"


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


def read_json(path: pathlib.Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def qfloat(value: float, digits: int = 10) -> float:
    result = round(float(value), digits)
    if not math.isfinite(result):
        raise RuntimeError("non-finite value cannot enter canonical JSON")
    return result


def f32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", float(value)))[0]


def add(
    a: tuple[float, float, float],
    b: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(
    a: tuple[float, float, float],
    b: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def scale(
    a: tuple[float, float, float],
    s: float,
) -> tuple[float, float, float]:
    return (a[0] * s, a[1] * s, a[2] * s)


def dot(
    a: tuple[float, float, float],
    b: tuple[float, float, float],
) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(
    a: tuple[float, float, float],
    b: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def norm(a: tuple[float, float, float]) -> float:
    return math.sqrt(dot(a, a))


def normalize(
    a: tuple[float, float, float],
) -> tuple[float, float, float]:
    length = norm(a)
    if not math.isfinite(length) or length <= 1e-12:
        raise RuntimeError("cannot normalize degenerate vector")
    return scale(a, 1.0 / length)


def rotate_y(
    v: tuple[float, float, float],
    angle_rad: float,
) -> tuple[float, float, float]:
    c = math.cos(angle_rad)
    s = math.sin(angle_rad)
    return (
        c * v[0] + s * v[2],
        v[1],
        -s * v[0] + c * v[2],
    )


def rodrigues(
    v: tuple[float, float, float],
    axis: tuple[float, float, float],
    angle_rad: float,
) -> tuple[float, float, float]:
    axis = normalize(axis)
    c = math.cos(angle_rad)
    s = math.sin(angle_rad)
    return add(
        add(scale(v, c), scale(cross(axis, v), s)),
        scale(axis, dot(axis, v) * (1.0 - c)),
    )


def pose_payload(azimuth_mdeg: int, elevation_mdeg: int) -> int:
    if not 0 <= azimuth_mdeg <= 0xFFFFFFFF:
        raise RuntimeError("azimuth_mdeg outside u32")
    if not 0 <= elevation_mdeg <= 0xFFFFFFFF:
        raise RuntimeError("elevation_mdeg outside u32")
    return (azimuth_mdeg << 32) | elevation_mdeg


def describe() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "pose_a_azimuth_mdeg": POSE_A_AZIMUTH_MDEG,
        "pose_a_elevation_mdeg": POSE_A_ELEVATION_MDEG,
        "pose_b_azimuth_mdeg": POSE_B_AZIMUTH_MDEG,
        "pose_b_elevation_mdeg": POSE_B_ELEVATION_MDEG,
        "depth_filename": DEPTH_FILENAME,
        "mask_filename": MASK_FILENAME,
        "points_filename": POINTS_FILENAME,
        "scene_filename": SCENE_FILENAME,
        "render_manifest_filename": RENDER_MANIFEST_FILENAME,
        "telemetry_filename": TELEMETRY_FILENAME,
        "raster_rule": RASTER_RULE,
        "pixel_convention": PIXEL_CONVENTION,
        "camera_convention": CAMERA_CONVENTION,
        "orbit_rule": ORBIT_RULE,
        "evidence_rule": EVIDENCE_RULE,
    }


def load_source(
    source_dir: pathlib.Path,
) -> tuple[
    list[tuple[float, float, float]],
    dict[str, str],
]:
    manifest_path = source_dir / SOURCE_MANIFEST_FILENAME
    points_path = source_dir / SOURCE_POINTS_FILENAME

    if not manifest_path.is_file():
        raise RuntimeError(f"D2c source manifest missing: {manifest_path}")
    if not points_path.is_file():
        raise RuntimeError(f"D2c refined points missing: {points_path}")

    manifest = read_json(manifest_path)
    if manifest.get("schema") != "evoscene.fusion_refinement.v1":
        raise RuntimeError("source is not a D2c fusion/refinement state")

    artifacts = manifest.get("artifacts")
    summary = manifest.get("summary")
    if not isinstance(artifacts, dict) or not isinstance(summary, dict):
        raise RuntimeError("D2c source metadata is incomplete")

    points_meta = artifacts.get("refined_points")
    if not isinstance(points_meta, dict):
        raise RuntimeError("D2c refined-points metadata is missing")

    expected_sha = str(points_meta.get("sha256") or "")
    actual_sha = sha256_file(points_path)
    if len(expected_sha) != 64 or expected_sha != actual_sha:
        raise RuntimeError(
            f"D2c refined-points SHA mismatch: {actual_sha} != {expected_sha}"
        )

    declared_count = int(summary.get("refined_voxels", -1))
    data = points_path.read_bytes()
    if declared_count <= 0 or len(data) != declared_count * 12:
        raise RuntimeError("D2c refined point count/byte size mismatch")

    points = []
    for point in struct.iter_unpack("<fff", data):
        if not all(math.isfinite(value) for value in point):
            raise RuntimeError("D2c source contains non-finite point")
        points.append(point)

    if len(points) != declared_count:
        raise RuntimeError("D2c decoded point count mismatch")

    return points, {
        "manifest_sha256": sha256_file(manifest_path),
        "points_sha256": actual_sha,
    }


def load_camera(
    camera_dir: pathlib.Path,
) -> tuple[dict[str, Any], dict[str, str]]:
    camera_path = camera_dir / CAMERA_FILENAME
    prior_path = camera_dir / PRIOR_FILENAME

    if not camera_path.is_file():
        raise RuntimeError(f"D2a camera missing: {camera_path}")
    if not prior_path.is_file():
        raise RuntimeError(f"D2a prior manifest missing: {prior_path}")

    camera = read_json(camera_path)
    prior = read_json(prior_path)

    if camera.get("schema") != "evoscene.camera.v1":
        raise RuntimeError("D2a camera schema mismatch")
    if prior.get("schema") != "evoscene.spatial_prior.v1":
        raise RuntimeError("D2a prior schema mismatch")

    camera_meta = (prior.get("artifacts") or {}).get("camera")
    if not isinstance(camera_meta, dict):
        raise RuntimeError("D2a prior camera metadata missing")

    expected_sha = str(camera_meta.get("sha256") or "")
    actual_sha = sha256_file(camera_path)
    if len(expected_sha) != 64 or expected_sha != actual_sha:
        raise RuntimeError(
            f"D2a camera SHA mismatch: {actual_sha} != {expected_sha}"
        )

    width = int(camera.get("image_width", 0))
    height = int(camera.get("image_height", 0))
    intrinsics = camera.get("intrinsics_normalized")
    if width <= 0 or height <= 0:
        raise RuntimeError("D2a camera dimensions are invalid")
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
        raise RuntimeError("D2a camera intrinsics are non-finite")
    if fx <= 0.0 or fy <= 0.0:
        raise RuntimeError("D2a focal lengths must be positive")

    return camera, {
        "prior_sha256": sha256_file(prior_path),
        "camera_sha256": actual_sha,
    }


def compute_bounds(
    points: list[tuple[float, float, float]],
) -> tuple[
    tuple[float, float, float],
    tuple[float, float, float],
]:
    if not points:
        raise RuntimeError("cannot bound empty point cloud")

    minimum = [points[0][0], points[0][1], points[0][2]]
    maximum = [points[0][0], points[0][1], points[0][2]]

    for point in points[1:]:
        for axis in range(3):
            minimum[axis] = min(minimum[axis], point[axis])
            maximum[axis] = max(maximum[axis], point[axis])

    return (
        (minimum[0], minimum[1], minimum[2]),
        (maximum[0], maximum[1], maximum[2]),
    )


def target_camera(
    center: tuple[float, float, float],
    azimuth_mdeg: int,
    elevation_mdeg: int,
) -> dict[str, tuple[float, float, float]]:
    source_origin = (0.0, 0.0, 0.0)
    initial_offset = sub(source_origin, center)
    radius = norm(initial_offset)
    if radius <= 1e-6:
        raise RuntimeError("scene center is too close to source camera origin")

    azimuth = math.radians(azimuth_mdeg / 1000.0)
    elevation = math.radians(elevation_mdeg / 1000.0)

    yawed = rotate_y(initial_offset, azimuth)
    forward_after_yaw = normalize(scale(yawed, -1.0))
    world_up = (0.0, -1.0, 0.0)
    right_axis = normalize(cross(forward_after_yaw, world_up))
    elevated = rodrigues(yawed, right_axis, -elevation)

    camera_position = add(center, elevated)
    forward = normalize(sub(center, camera_position))
    right = normalize(cross(forward, world_up))
    down = normalize(cross(forward, right))

    return {
        "position": camera_position,
        "right": right,
        "down": down,
        "forward": forward,
    }


def rasterize(
    points: list[tuple[float, float, float]],
    camera: dict[str, Any],
    pose: dict[str, tuple[float, float, float]],
) -> tuple[bytes, bytes, list[tuple[float, float, float]], int]:
    width = int(camera["image_width"])
    height = int(camera["image_height"])
    intrinsics = camera["intrinsics_normalized"]
    fx = float(intrinsics[0][0])
    fy = float(intrinsics[1][1])
    cx = float(intrinsics[0][2])
    cy = float(intrinsics[1][2])

    position = pose["position"]
    right = pose["right"]
    down = pose["down"]
    forward = pose["forward"]

    pixels = width * height
    depth = [0.0] * pixels
    source_index = [-1] * pixels

    projected = 0
    for index, point in enumerate(points):
        relative = sub(point, position)
        x_cam = dot(relative, right)
        y_cam = dot(relative, down)
        z_cam = dot(relative, forward)
        if not math.isfinite(z_cam) or z_cam <= 0.0:
            continue

        u = fx * (x_cam / z_cam) + cx
        v = fy * (y_cam / z_cam) + cy
        if not (0.0 <= u < 1.0 and 0.0 <= v < 1.0):
            continue

        pixel_x = int(math.floor(u * width))
        pixel_y = int(math.floor(v * height))
        pixel = pixel_y * width + pixel_x

        z_value = f32(z_cam)
        current = depth[pixel]
        current_index = source_index[pixel]
        if (
            current_index < 0
            or z_value < current
            or (z_value == current and index < current_index)
        ):
            depth[pixel] = z_value
            source_index[pixel] = index
        projected += 1

    mask = bytes(1 if index >= 0 else 0 for index in source_index)
    depth_data = b"".join(struct.pack("<f", value) for value in depth)

    evidence: list[tuple[float, float, float]] = []
    for pixel, chosen in enumerate(source_index):
        if chosen < 0:
            continue

        y, x = divmod(pixel, width)
        u = (x + 0.5) / width
        v = (y + 0.5) / height
        z_cam = depth[pixel]

        x_cam = f32((u - cx) * z_cam / fx)
        y_cam = f32((v - cy) * z_cam / fy)

        world = add(
            position,
            add(
                add(scale(right, x_cam), scale(down, y_cam)),
                scale(forward, z_cam),
            ),
        )
        point = (f32(world[0]), f32(world[1]), f32(world[2]))
        if not all(math.isfinite(value) for value in point):
            raise RuntimeError("reprojection produced non-finite world point")
        evidence.append(point)

    return depth_data, mask, evidence, projected


def points_bytes(
    points: list[tuple[float, float, float]],
) -> bytes:
    output = bytearray(len(points) * 12)
    offset = 0
    for point in points:
        struct.pack_into("<fff", output, offset, *point)
        offset += 12
    return bytes(output)


def bounds_json(
    points: list[tuple[float, float, float]],
) -> dict[str, list[float]]:
    minimum, maximum = compute_bounds(points)
    return {
        "min": [qfloat(value) for value in minimum],
        "max": [qfloat(value) for value in maximum],
    }


def vector_json(
    vector: tuple[float, float, float],
) -> list[float]:
    return [qfloat(value) for value in vector]


def run_render(args: argparse.Namespace) -> dict[str, Any]:
    source_dir = pathlib.Path(args.source_dir).expanduser().resolve()
    camera_dir = pathlib.Path(args.camera_dir).expanduser().resolve()
    output_dir = pathlib.Path(args.output).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    azimuth_mdeg = int(args.azimuth_mdeg)
    elevation_mdeg = int(args.elevation_mdeg)
    payload = pose_payload(azimuth_mdeg, elevation_mdeg)

    started = time.perf_counter_ns()
    source_points, source_hashes = load_source(source_dir)
    camera, camera_hashes = load_camera(camera_dir)

    minimum, maximum = compute_bounds(source_points)
    center = (
        (minimum[0] + maximum[0]) / 2.0,
        (minimum[1] + maximum[1]) / 2.0,
        (minimum[2] + maximum[2]) / 2.0,
    )

    pose = target_camera(center, azimuth_mdeg, elevation_mdeg)
    depth_data, mask_data, evidence_points, projected = rasterize(
        source_points,
        camera,
        pose,
    )

    if not evidence_points:
        raise RuntimeError("novel-view render produced no visible evidence")

    points_data = points_bytes(evidence_points)

    depth_path = output_dir / DEPTH_FILENAME
    mask_path = output_dir / MASK_FILENAME
    points_path = output_dir / POINTS_FILENAME

    write_bytes(depth_path, depth_data)
    write_bytes(mask_path, mask_data)
    write_bytes(points_path, points_data)

    depth_sha = sha256_bytes(depth_data)
    mask_sha = sha256_bytes(mask_data)
    points_sha = sha256_bytes(points_data)

    width = int(camera["image_width"])
    height = int(camera["image_height"])
    valid_pixels = sum(mask_data)
    if valid_pixels != len(evidence_points):
        raise RuntimeError("render mask/evidence point count mismatch")

    scene = {
        "schema": "evoscene.point_cloud.v1",
        "schema_version": 1,
        "producer": "d2d_reprojection",
        "parents": {
            "source_manifest_sha256": source_hashes["manifest_sha256"],
            "source_points_sha256": source_hashes["points_sha256"],
            "camera_prior_sha256": camera_hashes["prior_sha256"],
            "camera_sha256": camera_hashes["camera_sha256"],
        },
        "view": {
            "azimuth_mdeg": azimuth_mdeg,
            "elevation_mdeg": elevation_mdeg,
            "payload_u64": payload,
        },
        "artifacts": {
            "points": {
                "file": POINTS_FILENAME,
                "bytes": len(points_data),
                "sha256": points_sha,
            },
        },
        "summary": {
            "point_count": len(evidence_points),
            "bounds_m": bounds_json(evidence_points),
        },
    }
    scene_bytes = canonical_json_bytes(scene)
    scene_path = output_dir / SCENE_FILENAME
    write_bytes(scene_path, scene_bytes)
    scene_sha = sha256_bytes(scene_bytes)

    render = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "parents": scene["parents"],
        "config": {
            "raster_rule": RASTER_RULE,
            "pixel_convention": PIXEL_CONVENTION,
            "camera_convention": CAMERA_CONVENTION,
            "orbit_rule": ORBIT_RULE,
            "evidence_rule": EVIDENCE_RULE,
        },
        "view": {
            "azimuth_mdeg": azimuth_mdeg,
            "elevation_mdeg": elevation_mdeg,
            "payload_u64": payload,
            "scene_center_m": vector_json(center),
            "camera_position_m": vector_json(pose["position"]),
            "camera_right": vector_json(pose["right"]),
            "camera_down": vector_json(pose["down"]),
            "camera_forward": vector_json(pose["forward"]),
        },
        "image": {
            "width": width,
            "height": height,
            "valid_pixels": valid_pixels,
            "valid_fraction": qfloat(valid_pixels / (width * height)),
            "projected_source_samples": projected,
        },
        "artifacts": {
            "depth": {
                "file": DEPTH_FILENAME,
                "bytes": len(depth_data),
                "sha256": depth_sha,
            },
            "mask": {
                "file": MASK_FILENAME,
                "bytes": len(mask_data),
                "sha256": mask_sha,
            },
            "points": {
                "file": POINTS_FILENAME,
                "bytes": len(points_data),
                "sha256": points_sha,
            },
            "scene": {
                "file": SCENE_FILENAME,
                "bytes": len(scene_bytes),
                "sha256": scene_sha,
            },
        },
    }

    render_bytes = canonical_json_bytes(render)
    render_path = output_dir / RENDER_MANIFEST_FILENAME
    write_bytes(render_path, render_bytes)

    elapsed_ms = (time.perf_counter_ns() - started) // 1_000_000
    telemetry = {
        "schema": "evoscene.novel_view.telemetry.v1",
        "render_reprojection_ms": int(elapsed_ms),
        "python": platform.python_version(),
        "platform": platform.platform(),
    }
    write_bytes(
        output_dir / TELEMETRY_FILENAME,
        canonical_json_bytes(telemetry),
    )

    return {
        "status": "ok",
        "render": str(render_path),
        "render_sha256": sha256_bytes(render_bytes),
        "depth_sha256": depth_sha,
        "mask_sha256": mask_sha,
        "points_sha256": points_sha,
        "scene_sha256": scene_sha,
        "valid_pixels": valid_pixels,
        "valid_fraction": render["image"]["valid_fraction"],
        "pose_payload_u64": payload,
    }


def write_synthetic_source(directory: pathlib.Path) -> None:
    points = []
    for y in (-0.4, -0.2, 0.0, 0.2, 0.4):
        for x in (-0.4, -0.2, 0.0, 0.2, 0.4):
            points.append((x, y, 4.0))
    data = points_bytes(points)
    write_bytes(directory / SOURCE_POINTS_FILENAME, data)

    manifest = {
        "schema": "evoscene.fusion_refinement.v1",
        "artifacts": {
            "refined_points": {
                "sha256": sha256_bytes(data),
            },
        },
        "summary": {
            "refined_voxels": len(points),
            "refined_bounds_m": bounds_json(points),
        },
    }
    write_bytes(
        directory / SOURCE_MANIFEST_FILENAME,
        canonical_json_bytes(manifest),
    )


def write_synthetic_camera(directory: pathlib.Path) -> None:
    camera = {
        "schema": "evoscene.camera.v1",
        "image_width": 64,
        "image_height": 64,
        "intrinsics_normalized": [
            [0.8, 0.0, 0.5],
            [0.0, 0.8, 0.5],
            [0.0, 0.0, 1.0],
        ],
    }
    camera_data = canonical_json_bytes(camera)
    write_bytes(directory / CAMERA_FILENAME, camera_data)

    prior = {
        "schema": "evoscene.spatial_prior.v1",
        "artifacts": {
            "camera": {
                "sha256": sha256_bytes(camera_data),
            },
        },
    }
    write_bytes(directory / PRIOR_FILENAME, canonical_json_bytes(prior))


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="evoscene-d2d-") as tmp:
        root = pathlib.Path(tmp)
        source = root / "source"
        camera = root / "camera"
        out_a = root / "a"
        out_b = root / "b"
        out_c = root / "c"
        source.mkdir()
        camera.mkdir()

        write_synthetic_source(source)
        write_synthetic_camera(camera)

        common = {
            "source_dir": str(source),
            "camera_dir": str(camera),
            "azimuth_mdeg": 15_000,
            "elevation_mdeg": 5_000,
        }
        first = run_render(
            argparse.Namespace(output=str(out_a), **common)
        )
        second = run_render(
            argparse.Namespace(output=str(out_b), **common)
        )
        different = run_render(
            argparse.Namespace(
                source_dir=str(source),
                camera_dir=str(camera),
                output=str(out_c),
                azimuth_mdeg=345_000,
                elevation_mdeg=5_000,
            )
        )

        for key in (
            "render_sha256",
            "depth_sha256",
            "mask_sha256",
            "points_sha256",
            "scene_sha256",
        ):
            if first[key] != second[key]:
                raise AssertionError(f"D2d repeat differs for {key}")

        if first["render_sha256"] == different["render_sha256"]:
            raise AssertionError("D2d pose change did not change render")
        if first["valid_pixels"] <= 0 or different["valid_pixels"] <= 0:
            raise AssertionError("D2d synthetic render has no valid pixels")

    print(
        "D2d adapter self-test PASS: "
        "deterministic pose-sensitive z-buffer + reprojection exact"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--source-dir")
    parser.add_argument("--camera-dir")
    parser.add_argument("--output")
    parser.add_argument(
        "--azimuth-mdeg",
        type=int,
        default=POSE_A_AZIMUTH_MDEG,
    )
    parser.add_argument(
        "--elevation-mdeg",
        type=int,
        default=POSE_A_ELEVATION_MDEG,
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    modes = sum(
        (
            bool(args.self_test),
            bool(args.describe),
            bool(args.source_dir or args.camera_dir or args.output),
        )
    )
    if modes != 1:
        raise SystemExit(
            "choose exactly one mode: --self-test, --describe, "
            "or --source-dir DIR --camera-dir DIR --output DIR"
        )

    if args.self_test:
        self_test()
        return 0

    if args.describe:
        sys.stdout.buffer.write(canonical_json_bytes(describe()))
        return 0

    if not args.source_dir or not args.camera_dir or not args.output:
        raise SystemExit(
            "--source-dir, --camera-dir, and --output are required together"
        )

    try:
        result = run_render(args)
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
