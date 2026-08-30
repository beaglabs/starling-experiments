#!/usr/bin/env python3
"""Deterministic D2e post-convergence voxel-surface mesh finalizer.

Consumes one final D2c refined state and emits the same triangle mesh as:
- scene.glb (primary demo artifact)
- scene.obj
- scene_mesh.ply

No scheduling decisions occur here. The finalizer runs once after convergence.
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
SCHEMA = "evoscene.mesh.v1"

CANONICAL_VOXEL_SIZE_M = 0.025

SOURCE_MANIFEST_FILENAME = "fusion.json"
SOURCE_POINTS_FILENAME = "refined_points.f32le"

GLB_FILENAME = "scene.glb"
OBJ_FILENAME = "scene.obj"
PLY_FILENAME = "scene_mesh.ply"
MESH_MANIFEST_FILENAME = "mesh.json"
TELEMETRY_FILENAME = "telemetry.json"

SURFACE_RULE = "occupied-voxel-exposed-faces"
FACE_ORDER = "negx-posx-negy-posy-negz-posz"
TRIANGLE_RULE = "quad-v0-v1-v2-v0-v2-v3"
COORDINATE_CONVENTION = "opencv-x-right-y-down-z-forward-meters"
VERTEX_RULE = "four-face-local-vertices-per-exposed-quad"

FACE_DEFS = (
    ((-1, 0, 0), (-1.0, 0.0, 0.0), (
        (0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0),
    )),
    ((1, 0, 0), (1.0, 0.0, 0.0), (
        (1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1),
    )),
    ((0, -1, 0), (0.0, -1.0, 0.0), (
        (0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1),
    )),
    ((0, 1, 0), (0.0, 1.0, 0.0), (
        (0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0),
    )),
    ((0, 0, -1), (0.0, 0.0, -1.0), (
        (0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0),
    )),
    ((0, 0, 1), (0.0, 0.0, 1.0), (
        (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1),
    )),
)


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


def describe() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "canonical_voxel_size_m": CANONICAL_VOXEL_SIZE_M,
        "glb_filename": GLB_FILENAME,
        "obj_filename": OBJ_FILENAME,
        "ply_filename": PLY_FILENAME,
        "mesh_manifest_filename": MESH_MANIFEST_FILENAME,
        "telemetry_filename": TELEMETRY_FILENAME,
        "surface_rule": SURFACE_RULE,
        "face_order": FACE_ORDER,
        "triangle_rule": TRIANGLE_RULE,
        "coordinate_convention": COORDINATE_CONVENTION,
        "vertex_rule": VERTEX_RULE,
    }


def load_source(
    source_dir: pathlib.Path,
) -> tuple[list[tuple[float, float, float]], dict[str, str]]:
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

    refined = artifacts.get("refined_points")
    if not isinstance(refined, dict):
        raise RuntimeError("D2c refined-points metadata is missing")

    expected_sha = str(refined.get("sha256") or "")
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


def voxel_key(
    point: tuple[float, float, float],
    voxel_size_m: float,
) -> tuple[int, int, int]:
    return (
        math.floor(point[0] / voxel_size_m),
        math.floor(point[1] / voxel_size_m),
        math.floor(point[2] / voxel_size_m),
    )


def occupied_voxels(
    points: list[tuple[float, float, float]],
    voxel_size_m: float,
) -> list[tuple[int, int, int]]:
    if not math.isfinite(voxel_size_m) or voxel_size_m <= 0.0:
        raise RuntimeError("voxel size must be finite and positive")
    return sorted({voxel_key(point, voxel_size_m) for point in points})


def build_mesh(
    keys: list[tuple[int, int, int]],
    voxel_size_m: float,
) -> tuple[
    list[tuple[float, float, float]],
    list[tuple[float, float, float]],
    list[int],
    int,
]:
    occupied = set(keys)
    positions: list[tuple[float, float, float]] = []
    normals: list[tuple[float, float, float]] = []
    indices: list[int] = []
    exposed_faces = 0

    for key in keys:
        ix, iy, iz = key
        for direction, normal, corners in FACE_DEFS:
            neighbor = (
                ix + direction[0],
                iy + direction[1],
                iz + direction[2],
            )
            if neighbor in occupied:
                continue

            base = len(positions)
            for corner in corners:
                position = (
                    f32((ix + corner[0]) * voxel_size_m),
                    f32((iy + corner[1]) * voxel_size_m),
                    f32((iz + corner[2]) * voxel_size_m),
                )
                positions.append(position)
                normals.append(normal)

            indices.extend((
                base,
                base + 1,
                base + 2,
                base,
                base + 2,
                base + 3,
            ))
            exposed_faces += 1

    if not positions or not indices:
        raise RuntimeError("surface extraction emitted an empty mesh")

    return positions, normals, indices, exposed_faces


def mesh_bounds(
    positions: list[tuple[float, float, float]],
) -> dict[str, list[float]]:
    minimum = [positions[0][0], positions[0][1], positions[0][2]]
    maximum = [positions[0][0], positions[0][1], positions[0][2]]

    for point in positions[1:]:
        for axis in range(3):
            minimum[axis] = min(minimum[axis], point[axis])
            maximum[axis] = max(maximum[axis], point[axis])

    return {
        "min": [qfloat(value) for value in minimum],
        "max": [qfloat(value) for value in maximum],
    }


def pack_vec3_f32(
    values: list[tuple[float, float, float]],
) -> bytes:
    output = bytearray(len(values) * 12)
    offset = 0
    for value in values:
        struct.pack_into("<fff", output, offset, *value)
        offset += 12
    return bytes(output)


def pack_u32(values: list[int]) -> bytes:
    output = bytearray(len(values) * 4)
    offset = 0
    for value in values:
        if value < 0 or value > 0xFFFFFFFF:
            raise RuntimeError("mesh index outside uint32 range")
        struct.pack_into("<I", output, offset, value)
        offset += 4
    return bytes(output)


def pad4(data: bytes, fill: bytes) -> bytes:
    padding = (-len(data)) % 4
    if padding:
        data += fill * padding
    return data


def glb_bytes(
    positions: list[tuple[float, float, float]],
    normals: list[tuple[float, float, float]],
    indices: list[int],
) -> bytes:
    position_data = pack_vec3_f32(positions)
    normal_data = pack_vec3_f32(normals)
    index_data = pack_u32(indices)

    position_offset = 0
    normal_offset = len(position_data)
    index_offset = normal_offset + len(normal_data)
    binary = position_data + normal_data + index_data
    binary_padded = pad4(binary, b"\x00")

    bounds = mesh_bounds(positions)
    gltf = {
        "accessors": [
            {
                "bufferView": 0,
                "byteOffset": 0,
                "componentType": 5126,
                "count": len(positions),
                "type": "VEC3",
                "min": bounds["min"],
                "max": bounds["max"],
            },
            {
                "bufferView": 1,
                "byteOffset": 0,
                "componentType": 5126,
                "count": len(normals),
                "type": "VEC3",
            },
            {
                "bufferView": 2,
                "byteOffset": 0,
                "componentType": 5125,
                "count": len(indices),
                "type": "SCALAR",
            },
        ],
        "asset": {
            "generator": "starling-experiments D2e",
            "version": "2.0",
        },
        "bufferViews": [
            {
                "buffer": 0,
                "byteLength": len(position_data),
                "byteOffset": position_offset,
                "target": 34962,
            },
            {
                "buffer": 0,
                "byteLength": len(normal_data),
                "byteOffset": normal_offset,
                "target": 34962,
            },
            {
                "buffer": 0,
                "byteLength": len(index_data),
                "byteOffset": index_offset,
                "target": 34963,
            },
        ],
        "buffers": [{"byteLength": len(binary)}],
        "meshes": [{
            "primitives": [{
                "attributes": {
                    "NORMAL": 1,
                    "POSITION": 0,
                },
                "indices": 2,
                "mode": 4,
            }],
        }],
        "nodes": [{"mesh": 0}],
        "scene": 0,
        "scenes": [{"nodes": [0]}],
    }

    json_chunk = pad4(
        json.dumps(
            gltf,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8"),
        b" ",
    )

    total_length = (
        12
        + 8 + len(json_chunk)
        + 8 + len(binary_padded)
    )

    header = struct.pack("<III", 0x46546C67, 2, total_length)
    json_header = struct.pack("<II", len(json_chunk), 0x4E4F534A)
    bin_header = struct.pack("<II", len(binary_padded), 0x004E4942)

    return header + json_header + json_chunk + bin_header + binary_padded


def obj_bytes(
    positions: list[tuple[float, float, float]],
    normals: list[tuple[float, float, float]],
    indices: list[int],
) -> bytes:
    lines = [
        "# EvoScene D2e deterministic voxel-surface mesh\n",
        f"# coordinate_convention {COORDINATE_CONVENTION}\n",
    ]

    for x, y, z in positions:
        lines.append(
            f"v {format(x, '.9g')} {format(y, '.9g')} {format(z, '.9g')}\n"
        )
    for x, y, z in normals:
        lines.append(
            f"vn {format(x, '.9g')} {format(y, '.9g')} {format(z, '.9g')}\n"
        )

    for index in range(0, len(indices), 3):
        tri = indices[index:index + 3]
        a, b, c = (value + 1 for value in tri)
        lines.append(f"f {a}//{a} {b}//{b} {c}//{c}\n")

    return "".join(lines).encode("ascii")


def ply_bytes(
    positions: list[tuple[float, float, float]],
    normals: list[tuple[float, float, float]],
    indices: list[int],
) -> bytes:
    triangle_count = len(indices) // 3
    header = (
        "ply\n"
        "format ascii 1.0\n"
        "comment evoscene d2e deterministic voxel-surface mesh\n"
        f"element vertex {len(positions)}\n"
        "property float x\n"
        "property float y\n"
        "property float z\n"
        "property float nx\n"
        "property float ny\n"
        "property float nz\n"
        f"element face {triangle_count}\n"
        "property list uchar uint vertex_indices\n"
        "end_header\n"
    )
    lines = [header]

    for position, normal in zip(positions, normals):
        lines.append(
            f"{format(position[0], '.9g')} "
            f"{format(position[1], '.9g')} "
            f"{format(position[2], '.9g')} "
            f"{format(normal[0], '.9g')} "
            f"{format(normal[1], '.9g')} "
            f"{format(normal[2], '.9g')}\n"
        )

    for index in range(0, len(indices), 3):
        a, b, c = indices[index:index + 3]
        lines.append(f"3 {a} {b} {c}\n")

    return "".join(lines).encode("ascii")


def topology_edges(indices: list[int]) -> dict[tuple[int, int], int]:
    counts: dict[tuple[int, int], int] = {}
    for index in range(0, len(indices), 3):
        tri = indices[index:index + 3]
        for a, b in (
            (tri[0], tri[1]),
            (tri[1], tri[2]),
            (tri[2], tri[0]),
        ):
            edge = (a, b) if a < b else (b, a)
            counts[edge] = counts.get(edge, 0) + 1
    return counts


def run_finalize(args: argparse.Namespace) -> dict[str, Any]:
    source_dir = pathlib.Path(args.source_dir).expanduser().resolve()
    output_dir = pathlib.Path(args.output).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    voxel_size_m = float(args.voxel_size_m)
    started = time.perf_counter_ns()

    points, source_hashes = load_source(source_dir)
    keys = occupied_voxels(points, voxel_size_m)
    positions, normals, indices, exposed_faces = build_mesh(
        keys,
        voxel_size_m,
    )

    glb = glb_bytes(positions, normals, indices)
    obj = obj_bytes(positions, normals, indices)
    ply = ply_bytes(positions, normals, indices)

    glb_path = output_dir / GLB_FILENAME
    obj_path = output_dir / OBJ_FILENAME
    ply_path = output_dir / PLY_FILENAME

    write_bytes(glb_path, glb)
    write_bytes(obj_path, obj)
    write_bytes(ply_path, ply)

    glb_sha = sha256_bytes(glb)
    obj_sha = sha256_bytes(obj)
    ply_sha = sha256_bytes(ply)

    triangles = len(indices) // 3
    edges = topology_edges(indices)

    manifest = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "parents": {
            "source_manifest_sha256": source_hashes["manifest_sha256"],
            "source_points_sha256": source_hashes["points_sha256"],
        },
        "config": {
            "voxel_size_m": qfloat(voxel_size_m),
            "surface_rule": SURFACE_RULE,
            "face_order": FACE_ORDER,
            "triangle_rule": TRIANGLE_RULE,
            "coordinate_convention": COORDINATE_CONVENTION,
            "vertex_rule": VERTEX_RULE,
        },
        "artifacts": {
            "glb": {
                "file": GLB_FILENAME,
                "bytes": len(glb),
                "sha256": glb_sha,
            },
            "obj": {
                "file": OBJ_FILENAME,
                "bytes": len(obj),
                "sha256": obj_sha,
            },
            "ply": {
                "file": PLY_FILENAME,
                "bytes": len(ply),
                "sha256": ply_sha,
            },
        },
        "summary": {
            "source_points": len(points),
            "occupied_voxels": len(keys),
            "exposed_quads": exposed_faces,
            "vertices": len(positions),
            "triangles": triangles,
            "indices": len(indices),
            "unique_face_local_edges": len(edges),
            "bounds_m": mesh_bounds(positions),
        },
    }

    manifest_bytes = canonical_json_bytes(manifest)
    manifest_path = output_dir / MESH_MANIFEST_FILENAME
    write_bytes(manifest_path, manifest_bytes)

    elapsed_ms = (time.perf_counter_ns() - started) // 1_000_000
    telemetry = {
        "schema": "evoscene.mesh.telemetry.v1",
        "mesh_finalization_ms": int(elapsed_ms),
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
        "glb_sha256": glb_sha,
        "obj_sha256": obj_sha,
        "ply_sha256": ply_sha,
        "occupied_voxels": len(keys),
        "exposed_quads": exposed_faces,
        "vertices": len(positions),
        "triangles": triangles,
    }


def write_synthetic_source(directory: pathlib.Path) -> None:
    # Two face-adjacent occupied voxels. Their shared face must disappear,
    # giving 10 exposed quads instead of 12.
    points = [
        (0.0125, 0.0125, 0.0125),
        (0.0375, 0.0125, 0.0125),
    ]
    data = b"".join(struct.pack("<fff", *point) for point in points)
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
        },
    }
    write_bytes(
        directory / SOURCE_MANIFEST_FILENAME,
        canonical_json_bytes(manifest),
    )


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="evoscene-d2e-") as tmp:
        root = pathlib.Path(tmp)
        source = root / "source"
        out_a = root / "a"
        out_b = root / "b"
        source.mkdir()
        write_synthetic_source(source)

        common = {
            "source_dir": str(source),
            "voxel_size_m": CANONICAL_VOXEL_SIZE_M,
        }
        first = run_finalize(
            argparse.Namespace(output=str(out_a), **common)
        )
        second = run_finalize(
            argparse.Namespace(output=str(out_b), **common)
        )

        if first["occupied_voxels"] != 2:
            raise AssertionError("synthetic occupied voxel count is wrong")
        if first["exposed_quads"] != 10:
            raise AssertionError(
                f"shared voxel face was not removed: {first['exposed_quads']}"
            )
        if first["vertices"] != 40:
            raise AssertionError("synthetic vertex count is wrong")
        if first["triangles"] != 20:
            raise AssertionError("synthetic triangle count is wrong")

        for key in (
            "manifest_sha256",
            "glb_sha256",
            "obj_sha256",
            "ply_sha256",
        ):
            if first[key] != second[key]:
                raise AssertionError(f"D2e repeat differs for {key}")

        glb = (out_a / GLB_FILENAME).read_bytes()
        if len(glb) < 20:
            raise AssertionError("synthetic GLB is too short")
        magic, version, total = struct.unpack_from("<III", glb, 0)
        if magic != 0x46546C67 or version != 2 or total != len(glb):
            raise AssertionError("synthetic GLB header is invalid")

    print(
        "D2e adapter self-test PASS: "
        "deterministic exposed-voxel GLB/OBJ/PLY mesh exact"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--source-dir")
    parser.add_argument("--output")
    parser.add_argument(
        "--voxel-size-m",
        type=float,
        default=CANONICAL_VOXEL_SIZE_M,
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    modes = sum(
        (
            bool(args.self_test),
            bool(args.describe),
            bool(args.source_dir or args.output),
        )
    )
    if modes != 1:
        raise SystemExit(
            "choose exactly one mode: --self-test, --describe, "
            "or --source-dir DIR --output DIR"
        )

    if args.self_test:
        self_test()
        return 0

    if args.describe:
        sys.stdout.buffer.write(canonical_json_bytes(describe()))
        return 0

    if not args.source_dir or not args.output:
        raise SystemExit("--source-dir and --output are required together")

    try:
        result = run_finalize(args)
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
