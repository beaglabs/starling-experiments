#!/usr/bin/env python3
"""Deterministic D2c voxel fusion + occupancy refinement.

D2c consumes two D2b point-cloud scene directories and emits:
- fused_points.f32le: one float32 centroid per occupied voxel;
- refined_points.f32le: fused voxels that pass local occupancy support;
- refined.ply: inspectable ASCII PLY of the refined cloud;
- fusion.json: deterministic provenance/config/content manifest;
- telemetry.json: non-content timing/environment metadata.

The algorithm is intentionally simple and explicit. It performs no view
generation, no learned inference, and no scheduling decisions.
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
SCHEMA = "evoscene.fusion_refinement.v1"

CANONICAL_VOXEL_SIZE_M = 0.025
CANONICAL_MIN_NEIGHBORS = 2

SCENE_FILENAME = "scene.json"
INPUT_POINTS_FILENAME = "points.f32le"

FUSED_POINTS_FILENAME = "fused_points.f32le"
REFINED_POINTS_FILENAME = "refined_points.f32le"
REFINED_PLY_FILENAME = "refined.ply"
FUSION_MANIFEST_FILENAME = "fusion.json"
TELEMETRY_FILENAME = "telemetry.json"

POINTS_ENCODING = "voxel-centroid-xyz-f32le-meters"
VOXEL_INDEXING = "floor-coordinate-over-voxel-size"
REFINEMENT_RULE = "keep-if-occupied-26-neighbors-gte-threshold"
CANONICAL_ORDERING = "lexicographic-voxel-key"


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
        "canonical_voxel_size_m": CANONICAL_VOXEL_SIZE_M,
        "canonical_min_neighbors": CANONICAL_MIN_NEIGHBORS,
        "fused_points_filename": FUSED_POINTS_FILENAME,
        "refined_points_filename": REFINED_POINTS_FILENAME,
        "refined_ply_filename": REFINED_PLY_FILENAME,
        "fusion_manifest_filename": FUSION_MANIFEST_FILENAME,
        "telemetry_filename": TELEMETRY_FILENAME,
        "points_encoding": POINTS_ENCODING,
        "voxel_indexing": VOXEL_INDEXING,
        "refinement_rule": REFINEMENT_RULE,
        "canonical_ordering": CANONICAL_ORDERING,
    }


def read_json(path: pathlib.Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def load_scene(scene_dir: pathlib.Path, label: str) -> dict[str, Any]:
    scene_path = scene_dir / SCENE_FILENAME
    points_path = scene_dir / INPUT_POINTS_FILENAME

    if not scene_path.is_file():
        raise RuntimeError(f"{label} scene manifest missing: {scene_path}")
    if not points_path.is_file():
        raise RuntimeError(f"{label} points missing: {points_path}")

    scene = read_json(scene_path)
    if scene.get("schema") != "evoscene.point_cloud.v1":
        raise RuntimeError(f"{label} is not a D2b point-cloud scene")

    artifacts = scene.get("artifacts")
    summary = scene.get("summary")
    if not isinstance(artifacts, dict) or not isinstance(summary, dict):
        raise RuntimeError(f"{label} scene metadata is incomplete")

    points_meta = artifacts.get("points")
    if not isinstance(points_meta, dict):
        raise RuntimeError(f"{label} points metadata is missing")

    expected_sha = points_meta.get("sha256")
    actual_sha = sha256_file(points_path)
    if not isinstance(expected_sha, str) or expected_sha != actual_sha:
        raise RuntimeError(
            f"{label} points SHA-256 mismatch: {actual_sha} != {expected_sha}"
        )

    data = points_path.read_bytes()
    declared_count = int(summary.get("point_count", -1))
    if declared_count <= 0:
        raise RuntimeError(f"{label} point count is invalid")
    if len(data) != declared_count * 12:
        raise RuntimeError(
            f"{label} points byte count {len(data)} != {declared_count * 12}"
        )

    points = []
    for point in struct.iter_unpack("<fff", data):
        if not all(math.isfinite(value) for value in point):
            raise RuntimeError(f"{label} contains non-finite point")
        points.append(point)

    if len(points) != declared_count:
        raise RuntimeError(f"{label} decoded point count mismatch")

    return {
        "scene_sha256": sha256_file(scene_path),
        "points_sha256": actual_sha,
        "point_count": declared_count,
        "points": points,
    }


def voxel_key(
    point: tuple[float, float, float],
    voxel_size_m: float,
) -> tuple[int, int, int]:
    return (
        math.floor(point[0] / voxel_size_m),
        math.floor(point[1] / voxel_size_m),
        math.floor(point[2] / voxel_size_m),
    )


def fuse_points(
    point_sets: list[list[tuple[float, float, float]]],
    voxel_size_m: float,
) -> list[tuple[tuple[int, int, int], tuple[float, float, float], int]]:
    if not math.isfinite(voxel_size_m) or voxel_size_m <= 0.0:
        raise RuntimeError("voxel size must be finite and positive")

    canonical = []
    for points in point_sets:
        for point in points:
            key = voxel_key(point, voxel_size_m)
            canonical.append((key, point[0], point[1], point[2]))

    canonical.sort()

    fused = []
    index = 0
    while index < len(canonical):
        key = canonical[index][0]
        xs = []
        ys = []
        zs = []

        while index < len(canonical) and canonical[index][0] == key:
            _, x, y, z = canonical[index]
            xs.append(x)
            ys.append(y)
            zs.append(z)
            index += 1

        count = len(xs)
        centroid = (
            f32(math.fsum(xs) / count),
            f32(math.fsum(ys) / count),
            f32(math.fsum(zs) / count),
        )
        fused.append((key, centroid, count))

    return fused


def neighbor_count(
    key: tuple[int, int, int],
    occupied: set[tuple[int, int, int]],
) -> int:
    result = 0
    x, y, z = key
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                if dx == 0 and dy == 0 and dz == 0:
                    continue
                if (x + dx, y + dy, z + dz) in occupied:
                    result += 1
    return result


def refine_points(
    fused: list[
        tuple[tuple[int, int, int], tuple[float, float, float], int]
    ],
    min_neighbors: int,
) -> tuple[
    list[tuple[tuple[int, int, int], tuple[float, float, float], int]],
    int,
    int,
]:
    if min_neighbors < 0 or min_neighbors > 26:
        raise RuntimeError("min_neighbors must be between 0 and 26")

    occupied = {key for key, _, _ in fused}
    refined = []
    min_observed = 26
    max_observed = 0

    for item in fused:
        count = neighbor_count(item[0], occupied)
        min_observed = min(min_observed, count)
        max_observed = max(max_observed, count)
        if count >= min_neighbors:
            refined.append(item)

    if not fused:
        min_observed = 0

    return refined, min_observed, max_observed


def point_bytes(
    items: list[
        tuple[tuple[int, int, int], tuple[float, float, float], int]
    ],
) -> bytes:
    output = bytearray(len(items) * 12)
    offset = 0
    for _, point, _ in items:
        struct.pack_into("<fff", output, offset, *point)
        offset += 12
    return bytes(output)


def ply_bytes(
    items: list[
        tuple[tuple[int, int, int], tuple[float, float, float], int]
    ],
) -> bytes:
    header = (
        "ply\n"
        "format ascii 1.0\n"
        "comment evoscene d2c deterministic refined cloud\n"
        f"element vertex {len(items)}\n"
        "property float x\n"
        "property float y\n"
        "property float z\n"
        "end_header\n"
    )
    rows = [
        f"{format(x, '.9g')} {format(y, '.9g')} {format(z, '.9g')}\n"
        for _, (x, y, z), _ in items
    ]
    return (header + "".join(rows)).encode("ascii")


def bounds(
    items: list[
        tuple[tuple[int, int, int], tuple[float, float, float], int]
    ],
) -> dict[str, list[float]]:
    if not items:
        raise RuntimeError("point cloud is empty")

    first = items[0][1]
    minimum = [first[0], first[1], first[2]]
    maximum = [first[0], first[1], first[2]]

    for _, point, _ in items[1:]:
        for axis in range(3):
            minimum[axis] = min(minimum[axis], point[axis])
            maximum[axis] = max(maximum[axis], point[axis])

    return {
        "min": [qfloat(value) for value in minimum],
        "max": [qfloat(value) for value in maximum],
    }


def run_fusion(args: argparse.Namespace) -> dict[str, Any]:
    source_dir = pathlib.Path(args.source_dir).expanduser().resolve()
    evidence_dir = pathlib.Path(args.evidence_dir).expanduser().resolve()
    output_dir = pathlib.Path(args.output).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    voxel_size_m = float(args.voxel_size_m)
    min_neighbors = int(args.min_neighbors)

    started = time.perf_counter_ns()
    source = load_scene(source_dir, "source")
    evidence = load_scene(evidence_dir, "evidence")

    fused = fuse_points(
        [source["points"], evidence["points"]],
        voxel_size_m,
    )
    if not fused:
        raise RuntimeError("fusion emitted an empty cloud")

    refined, min_observed, max_observed = refine_points(
        fused,
        min_neighbors,
    )
    if not refined:
        raise RuntimeError("refinement removed every fused voxel")

    fused_data = point_bytes(fused)
    refined_data = point_bytes(refined)
    ply_data = ply_bytes(refined)

    fused_path = output_dir / FUSED_POINTS_FILENAME
    refined_path = output_dir / REFINED_POINTS_FILENAME
    ply_path = output_dir / REFINED_PLY_FILENAME

    write_bytes(fused_path, fused_data)
    write_bytes(refined_path, refined_data)
    write_bytes(ply_path, ply_data)

    fused_sha = sha256_bytes(fused_data)
    refined_sha = sha256_bytes(refined_data)
    ply_sha = sha256_bytes(ply_data)

    total_contributions = source["point_count"] + evidence["point_count"]
    support_counts = [count for _, _, count in fused]

    manifest = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "parents": {
            "source_scene_sha256": source["scene_sha256"],
            "source_points_sha256": source["points_sha256"],
            "evidence_scene_sha256": evidence["scene_sha256"],
            "evidence_points_sha256": evidence["points_sha256"],
        },
        "config": {
            "voxel_size_m": qfloat(voxel_size_m),
            "min_neighbors": min_neighbors,
            "points_encoding": POINTS_ENCODING,
            "voxel_indexing": VOXEL_INDEXING,
            "refinement_rule": REFINEMENT_RULE,
            "ordering": CANONICAL_ORDERING,
            "neighborhood": "26-connected-voxel-occupancy",
        },
        "artifacts": {
            "fused_points": {
                "file": FUSED_POINTS_FILENAME,
                "bytes": len(fused_data),
                "sha256": fused_sha,
            },
            "refined_points": {
                "file": REFINED_POINTS_FILENAME,
                "bytes": len(refined_data),
                "sha256": refined_sha,
            },
            "refined_ply": {
                "file": REFINED_PLY_FILENAME,
                "bytes": len(ply_data),
                "sha256": ply_sha,
            },
        },
        "summary": {
            "source_points": source["point_count"],
            "evidence_points": evidence["point_count"],
            "total_contributions": total_contributions,
            "fused_voxels": len(fused),
            "refined_voxels": len(refined),
            "removed_voxels": len(fused) - len(refined),
            "max_contributions_per_voxel": max(support_counts),
            "min_contributions_per_voxel": min(support_counts),
            "min_occupied_neighbors": min_observed,
            "max_occupied_neighbors": max_observed,
            "fused_bounds_m": bounds(fused),
            "refined_bounds_m": bounds(refined),
        },
    }

    manifest_bytes = canonical_json_bytes(manifest)
    manifest_path = output_dir / FUSION_MANIFEST_FILENAME
    write_bytes(manifest_path, manifest_bytes)

    elapsed_ms = (time.perf_counter_ns() - started) // 1_000_000
    telemetry = {
        "schema": "evoscene.fusion_refinement.telemetry.v1",
        "fusion_refinement_ms": int(elapsed_ms),
        "python": platform.python_version(),
        "platform": platform.platform(),
    }
    write_bytes(
        output_dir / TELEMETRY_FILENAME,
        canonical_json_bytes(telemetry),
    )

    return {
        "status": "ok",
        "manifest": str(manifest_path),
        "manifest_sha256": sha256_bytes(manifest_bytes),
        "fused_points_sha256": fused_sha,
        "refined_points_sha256": refined_sha,
        "refined_ply_sha256": ply_sha,
        "fused_voxels": len(fused),
        "refined_voxels": len(refined),
    }


def write_scene(
    directory: pathlib.Path,
    points: list[tuple[float, float, float]],
) -> None:
    data = b"".join(struct.pack("<fff", *point) for point in points)
    write_bytes(directory / INPUT_POINTS_FILENAME, data)

    scene = {
        "schema": "evoscene.point_cloud.v1",
        "artifacts": {
            "points": {
                "sha256": sha256_bytes(data),
            },
        },
        "summary": {
            "point_count": len(points),
        },
    }
    write_bytes(directory / SCENE_FILENAME, canonical_json_bytes(scene))


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="evoscene-d2c-") as tmp:
        root = pathlib.Path(tmp)
        source = root / "source"
        evidence = root / "evidence"
        out_a = root / "a"
        out_b = root / "b"
        source.mkdir()
        evidence.mkdir()

        # Three adjacent voxels on X. The center voxel has two occupied
        # neighbors; endpoints have one. Duplicate/offset contributions in the
        # center also prove deterministic centroid fusion.
        write_scene(
            source,
            [
                (0.10, 0.10, 1.10),
                (1.10, 0.10, 1.10),
                (2.10, 0.10, 1.10),
            ],
        )
        write_scene(
            evidence,
            [
                (0.20, 0.10, 1.10),
                (1.30, 0.10, 1.10),
                (2.20, 0.10, 1.10),
            ],
        )

        args_a = argparse.Namespace(
            source_dir=str(source),
            evidence_dir=str(evidence),
            output=str(out_a),
            voxel_size_m=1.0,
            min_neighbors=2,
        )
        args_b = argparse.Namespace(
            source_dir=str(evidence),
            evidence_dir=str(source),
            output=str(out_b),
            voxel_size_m=1.0,
            min_neighbors=2,
        )

        first = run_fusion(args_a)
        second = run_fusion(args_b)

        if first["fused_points_sha256"] != second["fused_points_sha256"]:
            raise AssertionError("fusion depends on source/evidence order")
        if first["refined_points_sha256"] != second["refined_points_sha256"]:
            raise AssertionError("refinement depends on source/evidence order")
        if first["refined_ply_sha256"] != second["refined_ply_sha256"]:
            raise AssertionError("PLY depends on source/evidence order")
        if first["fused_voxels"] != 3 or first["refined_voxels"] != 1:
            raise AssertionError("synthetic fusion/refinement counts are wrong")

        refined = list(
            struct.iter_unpack(
                "<fff",
                (out_a / REFINED_POINTS_FILENAME).read_bytes(),
            )
        )
        if refined != [(1.2000000476837158, 0.10000000149011612, 1.100000023841858)]:
            raise AssertionError(f"unexpected refined centroid: {refined}")

    print(
        "D2c adapter self-test PASS: "
        "order-independent fusion + occupancy refinement exact"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--source-dir")
    parser.add_argument("--evidence-dir")
    parser.add_argument("--output")
    parser.add_argument(
        "--voxel-size-m",
        type=float,
        default=CANONICAL_VOXEL_SIZE_M,
    )
    parser.add_argument(
        "--min-neighbors",
        type=int,
        default=CANONICAL_MIN_NEIGHBORS,
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    modes = sum(
        (
            bool(args.self_test),
            bool(args.describe),
            bool(args.source_dir or args.evidence_dir or args.output),
        )
    )
    if modes != 1:
        raise SystemExit(
            "choose exactly one mode: --self-test, --describe, "
            "or --source-dir DIR --evidence-dir DIR --output DIR"
        )

    if args.self_test:
        self_test()
        return 0

    if args.describe:
        sys.stdout.buffer.write(canonical_json_bytes(describe()))
        return 0

    if not args.source_dir or not args.evidence_dir or not args.output:
        raise SystemExit(
            "--source-dir, --evidence-dir, and --output are required together"
        )

    try:
        result = run_fusion(args)
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
