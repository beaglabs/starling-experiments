#!/usr/bin/env python3
"""D2f learned-view geometry bridge.

Consumes:
- current D2c refined world-space geometry;
- one cached MetaView generation;
- a D2a/MoGe prior computed from that generated RGB.

It aligns the generated-view MoGe depth to the existing scene using target-view
overlap, rejects contradictory overlap samples, preserves uncovered valid
samples as genuinely new evidence, transforms accepted target-camera points
back into the original world frame, and emits evoscene.point_cloud.v1 for D2c.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import platform
import statistics
import struct
import sys
import tempfile
import time
from typing import Any

SCHEMA_VERSION = 1
ADAPTER_VERSION = 1
SCHEMA = "evoscene.learned_evidence.v1"

MIN_SCALE_OVERLAP = 64
SCALE_RATIO_MIN = 0.25
SCALE_RATIO_MAX = 4.0
SCALE_INLIER_FRACTION = 0.25
OVERLAP_ABS_TOLERANCE_M = 0.050
OVERLAP_REL_TOLERANCE = 0.050

POINTS_FILENAME = "points.f32le"
SCENE_FILENAME = "scene.json"
EVIDENCE_MANIFEST_FILENAME = "evidence.json"
ACCEPTED_MASK_FILENAME = "accepted_mask.u8"
NOVEL_MASK_FILENAME = "novel_mask.u8"
TELEMETRY_FILENAME = "telemetry.json"


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
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes(data)
    temp.replace(path)


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


def load_source(
    source_dir: pathlib.Path,
) -> tuple[list[tuple[float, float, float]], dict[str, str]]:
    manifest_path = source_dir / "fusion.json"
    points_path = source_dir / "refined_points.f32le"
    if not manifest_path.is_file() or not points_path.is_file():
        raise RuntimeError("D2f source must be a D2c refined state")

    manifest = read_json(manifest_path)
    if manifest.get("schema") != "evoscene.fusion_refinement.v1":
        raise RuntimeError("D2f source schema mismatch")

    refined = ((manifest.get("artifacts") or {}).get("refined_points") or {})
    expected_sha = str(refined.get("sha256") or "")
    actual_sha = sha256_file(points_path)
    if expected_sha != actual_sha:
        raise RuntimeError("D2f source refined-points SHA mismatch")

    count = int((manifest.get("summary") or {}).get("refined_voxels", -1))
    data = points_path.read_bytes()
    if count <= 0 or len(data) != count * 12:
        raise RuntimeError("D2f source point count/byte size mismatch")

    points = list(struct.iter_unpack("<fff", data))
    if len(points) != count:
        raise RuntimeError("D2f source decoded point count mismatch")
    if any(
        not all(math.isfinite(value) for value in point)
        for point in points
    ):
        raise RuntimeError("D2f source contains non-finite point")

    return points, {
        "manifest_sha256": sha256_file(manifest_path),
        "points_sha256": actual_sha,
    }


def load_generation(
    generation_dir: pathlib.Path,
) -> tuple[dict[str, Any], dict[str, str]]:
    manifest_path = generation_dir / "generation.json"
    novel_path = generation_dir / "novel.png"
    if not manifest_path.is_file() or not novel_path.is_file():
        raise RuntimeError("D2f generation directory is incomplete")

    manifest = read_json(manifest_path)
    if manifest.get("schema") != "evoscene.learned_novel_view.v1":
        raise RuntimeError("D2f generation schema mismatch")

    expected_sha = str(
        (((manifest.get("artifacts") or {}).get("novel") or {}).get("sha256"))
        or ""
    )
    actual_sha = sha256_file(novel_path)
    if expected_sha != actual_sha:
        raise RuntimeError("D2f learned RGB SHA mismatch")

    return manifest, {
        "manifest_sha256": sha256_file(manifest_path),
        "novel_sha256": actual_sha,
    }


def load_prior(
    prior_dir: pathlib.Path,
    learned_rgb_sha256: str,
) -> tuple[
    dict[str, Any],
    bytes,
    bytes,
    dict[str, str],
]:
    manifest_path = prior_dir / "prior.json"
    depth_path = prior_dir / "depth.f32le"
    mask_path = prior_dir / "mask.u8"
    camera_path = prior_dir / "camera.json"

    for path in (manifest_path, depth_path, mask_path, camera_path):
        if not path.is_file():
            raise RuntimeError(f"D2f learned-view prior missing: {path}")

    prior = read_json(manifest_path)
    camera = read_json(camera_path)
    if prior.get("schema") != "evoscene.spatial_prior.v1":
        raise RuntimeError("D2f learned-view prior schema mismatch")
    if camera.get("schema") != "evoscene.camera.v1":
        raise RuntimeError("D2f learned-view camera schema mismatch")

    input_meta = prior.get("input") or {}
    if input_meta.get("sha256") != learned_rgb_sha256:
        raise RuntimeError(
            "D2f learned-view prior input does not match generated RGB"
        )

    artifacts = prior.get("artifacts") or {}
    expected = {
        "depth": (depth_path, (artifacts.get("depth") or {}).get("sha256")),
        "mask": (mask_path, (artifacts.get("mask") or {}).get("sha256")),
        "camera": (camera_path, (artifacts.get("camera") or {}).get("sha256")),
    }
    hashes: dict[str, str] = {
        "manifest": sha256_file(manifest_path),
    }
    for key, (path, wanted) in expected.items():
        actual = sha256_file(path)
        if actual != wanted:
            raise RuntimeError(f"D2f prior {key} SHA mismatch")
        hashes[key] = actual

    width = int(input_meta.get("width", 0))
    height = int(input_meta.get("height", 0))
    if width <= 0 or height <= 0:
        raise RuntimeError("D2f prior dimensions are invalid")
    if int(camera.get("image_width", 0)) != width:
        raise RuntimeError("D2f prior/camera width mismatch")
    if int(camera.get("image_height", 0)) != height:
        raise RuntimeError("D2f prior/camera height mismatch")

    depth_data = depth_path.read_bytes()
    mask_data = mask_path.read_bytes()
    if len(depth_data) != width * height * 4:
        raise RuntimeError("D2f prior depth byte size mismatch")
    if len(mask_data) != width * height:
        raise RuntimeError("D2f prior mask byte size mismatch")
    if any(value not in (0, 1) for value in mask_data):
        raise RuntimeError("D2f prior mask contains values outside {0,1}")

    return camera, depth_data, mask_data, hashes


def matmul3(
    a: tuple[tuple[float, float, float], ...],
    b: tuple[tuple[float, float, float], ...],
) -> tuple[tuple[float, float, float], ...]:
    return tuple(
        tuple(
            sum(a[i][k] * b[k][j] for k in range(3))
            for j in range(3)
        )
        for i in range(3)
    )


def matvec3(
    matrix: tuple[tuple[float, float, float], ...],
    vector: tuple[float, float, float],
) -> tuple[float, float, float]:
    return tuple(
        sum(matrix[i][j] * vector[j] for j in range(3))
        for i in range(3)
    )


def transpose3(
    matrix: tuple[tuple[float, float, float], ...],
) -> tuple[tuple[float, float, float], ...]:
    return tuple(
        tuple(matrix[j][i] for j in range(3))
        for i in range(3)
    )


def add3(
    a: tuple[float, float, float],
    b: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub3(
    a: tuple[float, float, float],
    b: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def metaview_extrinsic(
    yaw_mdeg: int,
    pitch_mdeg: int,
    radius_m: float,
) -> tuple[
    tuple[tuple[float, float, float], ...],
    tuple[float, float, float],
]:
    yaw = math.radians(yaw_mdeg / 1000.0)
    pitch = math.radians(pitch_mdeg / 1000.0)

    ry = (
        (math.cos(yaw), 0.0, math.sin(yaw)),
        (0.0, 1.0, 0.0),
        (-math.sin(yaw), 0.0, math.cos(yaw)),
    )
    rx = (
        (1.0, 0.0, 0.0),
        (0.0, math.cos(pitch), -math.sin(pitch)),
        (0.0, math.sin(pitch), math.cos(pitch)),
    )
    rotation = matmul3(ry, rx)
    center = (0.0, 0.0, radius_m)
    translation = sub3(center, matvec3(rotation, center))
    return rotation, translation


def render_current_depth(
    points: list[tuple[float, float, float]],
    camera: dict[str, Any],
    rotation: tuple[tuple[float, float, float], ...],
    translation: tuple[float, float, float],
) -> list[float]:
    width = int(camera["image_width"])
    height = int(camera["image_height"])
    k = camera["intrinsics_normalized"]
    fx = float(k[0][0])
    fy = float(k[1][1])
    cx = float(k[0][2])
    cy = float(k[1][2])

    depth = [0.0] * (width * height)
    source_index = [-1] * (width * height)

    for index, point in enumerate(points):
        camera_point = add3(matvec3(rotation, point), translation)
        x, y, z = camera_point
        if not math.isfinite(z) or z <= 0.0:
            continue

        u = fx * (x / z) + cx
        v = fy * (y / z) + cy
        if not (0.0 <= u < 1.0 and 0.0 <= v < 1.0):
            continue

        px = int(math.floor(u * width))
        py = int(math.floor(v * height))
        pixel = py * width + px
        z32 = f32(z)

        current = depth[pixel]
        current_index = source_index[pixel]
        if (
            current_index < 0
            or z32 < current
            or (z32 == current and index < current_index)
        ):
            depth[pixel] = z32
            source_index[pixel] = index

    return depth


def robust_scale(
    current_depth: list[float],
    learned_depth: list[float],
    learned_mask: bytes,
) -> tuple[float, int, int]:
    ratios = []
    for current, learned, valid in zip(
        current_depth,
        learned_depth,
        learned_mask,
    ):
        if not valid or current <= 0.0:
            continue
        if not math.isfinite(learned) or learned <= 0.0:
            continue
        ratio = current / learned
        if SCALE_RATIO_MIN <= ratio <= SCALE_RATIO_MAX:
            ratios.append(ratio)

    if len(ratios) < MIN_SCALE_OVERLAP:
        raise RuntimeError(
            "D2f has insufficient target-view overlap for metric alignment: "
            f"{len(ratios)} < {MIN_SCALE_OVERLAP}"
        )

    first = statistics.median(ratios)
    low = first * (1.0 - SCALE_INLIER_FRACTION)
    high = first * (1.0 + SCALE_INLIER_FRACTION)
    inliers = [ratio for ratio in ratios if low <= ratio <= high]

    if len(inliers) < MIN_SCALE_OVERLAP:
        raise RuntimeError(
            "D2f scale alignment rejected too many overlap samples: "
            f"{len(inliers)} < {MIN_SCALE_OVERLAP}"
        )

    return float(statistics.median(inliers)), len(ratios), len(inliers)


def evidence_points(
    camera: dict[str, Any],
    learned_depth: list[float],
    learned_mask: bytes,
    current_depth: list[float],
    scale: float,
    rotation: tuple[tuple[float, float, float], ...],
    translation: tuple[float, float, float],
) -> tuple[
    list[tuple[float, float, float]],
    bytes,
    bytes,
    dict[str, int],
]:
    width = int(camera["image_width"])
    height = int(camera["image_height"])
    k = camera["intrinsics_normalized"]
    fx = float(k[0][0])
    fy = float(k[1][1])
    cx = float(k[0][2])
    cy = float(k[1][2])
    inv_rotation = transpose3(rotation)

    accepted_mask = bytearray(width * height)
    novel_mask = bytearray(width * height)
    points: list[tuple[float, float, float]] = []

    novel = 0
    consistent = 0
    rejected_overlap = 0

    for pixel, (learned, valid) in enumerate(
        zip(learned_depth, learned_mask)
    ):
        if not valid:
            continue
        scaled_z = learned * scale
        if not math.isfinite(scaled_z) or scaled_z <= 0.0:
            continue

        current = current_depth[pixel]
        is_novel = current <= 0.0

        if not is_novel:
            tolerance = max(
                OVERLAP_ABS_TOLERANCE_M,
                current * OVERLAP_REL_TOLERANCE,
            )
            if abs(scaled_z - current) > tolerance:
                rejected_overlap += 1
                continue
            consistent += 1
        else:
            novel += 1
            novel_mask[pixel] = 1

        y, x = divmod(pixel, width)
        u = (x + 0.5) / width
        v = (y + 0.5) / height
        x_cam = (u - cx) * scaled_z / fx
        y_cam = (v - cy) * scaled_z / fy
        camera_point = (x_cam, y_cam, scaled_z)

        world = matvec3(
            inv_rotation,
            sub3(camera_point, translation),
        )
        point = (f32(world[0]), f32(world[1]), f32(world[2]))
        if not all(math.isfinite(value) for value in point):
            raise RuntimeError("D2f evidence produced non-finite world point")

        accepted_mask[pixel] = 1
        points.append(point)

    if not points:
        raise RuntimeError("D2f evidence bridge emitted no accepted points")
    if novel == 0:
        raise RuntimeError(
            "D2f learned generation produced no uncovered geometry evidence"
        )

    return (
        points,
        bytes(accepted_mask),
        bytes(novel_mask),
        {
            "novel_points": novel,
            "consistent_overlap_points": consistent,
            "rejected_overlap_points": rejected_overlap,
        },
    )


def points_bytes(
    points: list[tuple[float, float, float]],
) -> bytes:
    output = bytearray(len(points) * 12)
    offset = 0
    for point in points:
        struct.pack_into("<fff", output, offset, *point)
        offset += 12
    return bytes(output)


def bounds(
    points: list[tuple[float, float, float]],
) -> dict[str, list[float]]:
    minimum = list(points[0])
    maximum = list(points[0])
    for point in points[1:]:
        for axis in range(3):
            minimum[axis] = min(minimum[axis], point[axis])
            maximum[axis] = max(maximum[axis], point[axis])
    return {
        "min": [qfloat(value) for value in minimum],
        "max": [qfloat(value) for value in maximum],
    }


def run_bridge(args: argparse.Namespace) -> dict[str, Any]:
    source_dir = pathlib.Path(args.source_dir).expanduser().resolve()
    generation_dir = pathlib.Path(args.generation_dir).expanduser().resolve()
    prior_dir = pathlib.Path(args.prior_dir).expanduser().resolve()
    output_dir = pathlib.Path(args.output).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter_ns()
    source_points, source_hashes = load_source(source_dir)
    generation, generation_hashes = load_generation(generation_dir)
    camera, depth_data, mask_data, prior_hashes = load_prior(
        prior_dir,
        generation_hashes["novel_sha256"],
    )

    pose = generation.get("pose") or {}
    yaw_mdeg = int(pose.get("yaw_mdeg"))
    pitch_mdeg = int(pose.get("pitch_mdeg"))
    radius_mm = int(pose.get("radius_mm"))
    if radius_mm <= 0:
        raise RuntimeError("D2f generation radius is invalid")
    radius_m = radius_mm / 1000.0

    rotation, translation = metaview_extrinsic(
        yaw_mdeg,
        pitch_mdeg,
        radius_m,
    )
    current_depth = render_current_depth(
        source_points,
        camera,
        rotation,
        translation,
    )
    learned_depth = [
        value[0] for value in struct.iter_unpack("<f", depth_data)
    ]

    scale, overlap_candidates, scale_inliers = robust_scale(
        current_depth,
        learned_depth,
        mask_data,
    )

    (
        evidence,
        accepted_mask,
        novel_mask,
        counts,
    ) = evidence_points(
        camera,
        learned_depth,
        mask_data,
        current_depth,
        scale,
        rotation,
        translation,
    )

    point_data = points_bytes(evidence)
    point_sha = sha256_bytes(point_data)
    accepted_sha = sha256_bytes(accepted_mask)
    novel_sha = sha256_bytes(novel_mask)

    write_bytes(output_dir / POINTS_FILENAME, point_data)
    write_bytes(output_dir / ACCEPTED_MASK_FILENAME, accepted_mask)
    write_bytes(output_dir / NOVEL_MASK_FILENAME, novel_mask)

    scene = {
        "schema": "evoscene.point_cloud.v1",
        "schema_version": 1,
        "producer": "d2f_learned_novel_view",
        "parents": {
            "source_manifest_sha256": source_hashes["manifest_sha256"],
            "source_points_sha256": source_hashes["points_sha256"],
            "generation_manifest_sha256": generation_hashes[
                "manifest_sha256"
            ],
            "learned_rgb_sha256": generation_hashes["novel_sha256"],
            "learned_prior_manifest_sha256": prior_hashes["manifest"],
            "learned_depth_sha256": prior_hashes["depth"],
            "learned_mask_sha256": prior_hashes["mask"],
            "learned_camera_sha256": prior_hashes["camera"],
        },
        "view": {
            "yaw_mdeg": yaw_mdeg,
            "pitch_mdeg": pitch_mdeg,
            "radius_mm": radius_mm,
        },
        "artifacts": {
            "points": {
                "file": POINTS_FILENAME,
                "bytes": len(point_data),
                "sha256": point_sha,
            },
        },
        "summary": {
            "point_count": len(evidence),
            "novel_points": counts["novel_points"],
            "consistent_overlap_points": counts[
                "consistent_overlap_points"
            ],
            "bounds_m": bounds(evidence),
        },
    }
    scene_bytes = canonical_json_bytes(scene)
    write_bytes(output_dir / SCENE_FILENAME, scene_bytes)

    manifest = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "parents": scene["parents"],
        "view": scene["view"],
        "alignment": {
            "scale": qfloat(scale),
            "overlap_candidates": overlap_candidates,
            "scale_inliers": scale_inliers,
            "ratio_range": [
                SCALE_RATIO_MIN,
                SCALE_RATIO_MAX,
            ],
            "inlier_fraction": SCALE_INLIER_FRACTION,
        },
        "policy": {
            "new_pixels": "accept",
            "overlap_pixels": "accept-only-if-depth-consistent",
            "overlap_abs_tolerance_m": OVERLAP_ABS_TOLERANCE_M,
            "overlap_relative_tolerance": OVERLAP_REL_TOLERANCE,
        },
        "artifacts": {
            "points": {
                "file": POINTS_FILENAME,
                "bytes": len(point_data),
                "sha256": point_sha,
            },
            "accepted_mask": {
                "file": ACCEPTED_MASK_FILENAME,
                "bytes": len(accepted_mask),
                "sha256": accepted_sha,
            },
            "novel_mask": {
                "file": NOVEL_MASK_FILENAME,
                "bytes": len(novel_mask),
                "sha256": novel_sha,
            },
            "scene": {
                "file": SCENE_FILENAME,
                "bytes": len(scene_bytes),
                "sha256": sha256_bytes(scene_bytes),
            },
        },
        "summary": {
            **counts,
            "accepted_points": len(evidence),
            "bounds_m": scene["summary"]["bounds_m"],
        },
    }
    manifest_bytes = canonical_json_bytes(manifest)
    write_bytes(
        output_dir / EVIDENCE_MANIFEST_FILENAME,
        manifest_bytes,
    )

    elapsed_ms = (time.perf_counter_ns() - started) // 1_000_000
    telemetry = {
        "schema": "evoscene.learned_evidence.telemetry.v1",
        "bridge_ms": int(elapsed_ms),
        "python": platform.python_version(),
    }
    write_bytes(
        output_dir / TELEMETRY_FILENAME,
        canonical_json_bytes(telemetry),
    )

    return {
        "status": "ok",
        "scene_sha256": sha256_bytes(scene_bytes),
        "evidence_sha256": sha256_bytes(manifest_bytes),
        "points_sha256": point_sha,
        "accepted_points": len(evidence),
        "novel_points": counts["novel_points"],
        "scale": manifest["alignment"]["scale"],
    }


def self_test() -> None:
    # Exact MetaView transform inverse round trip.
    rotation, translation = metaview_extrinsic(35_000, 10_000, 2.0)
    inverse = transpose3(rotation)
    point = (0.2, -0.1, 2.3)
    camera = add3(matvec3(rotation, point), translation)
    recovered = matvec3(inverse, sub3(camera, translation))
    for actual, wanted in zip(recovered, point):
        if abs(actual - wanted) > 1e-12:
            raise AssertionError("MetaView extrinsic inverse drifted")

    current = [0.0] * 100
    learned = [0.0] * 100
    mask = bytearray(100)
    for index in range(80):
        current[index] = 4.0
        learned[index] = 2.0
        mask[index] = 1
    scale, candidates, inliers = robust_scale(
        current,
        learned,
        bytes(mask),
    )
    if scale != 2.0 or candidates != 80 or inliers != 80:
        raise AssertionError("D2f robust scale alignment is incorrect")

    print(
        "D2f bridge self-test PASS: "
        "MetaView extrinsic + metric overlap alignment exact"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--source-dir")
    parser.add_argument("--generation-dir")
    parser.add_argument("--prior-dir")
    parser.add_argument("--output")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.self_test:
        self_test()
        return 0

    if not all(
        (
            args.source_dir,
            args.generation_dir,
            args.prior_dir,
            args.output,
        )
    ):
        raise SystemExit(
            "--source-dir, --generation-dir, --prior-dir, and --output "
            "are required together"
        )

    try:
        result = run_bridge(args)
    except Exception as exc:
        sys.stderr.buffer.write(
            canonical_json_bytes({
                "status": "error",
                "error_type": type(exc).__name__,
                "message": str(exc),
            })
        )
        return 2

    sys.stdout.buffer.write(canonical_json_bytes(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
