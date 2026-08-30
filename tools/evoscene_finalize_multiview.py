#!/usr/bin/env python3
"""Deterministic full-world mesh finalizer for D2f multi-view geometry.

The deterministic D2e projected-surface finalizer is correct when only the
source-visible surface exists. Once D2f has added learned side/back evidence,
finalization must use the entire world-space point set.

This finalizer:
- quantizes the final D2c points onto the same 25 mm lattice;
- removes tiny disconnected voxel components;
- extracts exposed voxel faces with globally welded lattice-corner vertices;
- applies deterministic Taubin smoothing to reduce voxel stair-stepping;
- recomputes shared area-weighted normals;
- exports GLB/OBJ/PLY from the same indexed triangle stream.
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

SCHEMA = "evoscene.multiview_mesh.v1"
SCHEMA_VERSION = 1
ADAPTER_VERSION = 1

PITCH_M = 0.025
MIN_COMPONENT_VOXELS = 8
TAUBIN_ITERATIONS = 5
TAUBIN_LAMBDA = 0.45
TAUBIN_MU = -0.47

GLB_FILENAME = "scene.glb"
OBJ_FILENAME = "scene.obj"
PLY_FILENAME = "scene_mesh.ply"
MANIFEST_FILENAME = "mesh.json"
TELEMETRY_FILENAME = "telemetry.json"

FACE_DEFS = (
    ((-1, 0, 0), (
        (0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0),
    )),
    ((1, 0, 0), (
        (1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1),
    )),
    ((0, -1, 0), (
        (0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1),
    )),
    ((0, 1, 0), (
        (0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0),
    )),
    ((0, 0, -1), (
        (0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0),
    )),
    ((0, 0, 1), (
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
        raise RuntimeError("multiview finalizer requires D2c refined state")

    manifest = read_json(manifest_path)
    if manifest.get("schema") != "evoscene.fusion_refinement.v1":
        raise RuntimeError("multiview finalizer source schema mismatch")

    refined = ((manifest.get("artifacts") or {}).get("refined_points") or {})
    expected = str(refined.get("sha256") or "")
    actual = sha256_file(points_path)
    if expected != actual:
        raise RuntimeError("multiview finalizer source SHA mismatch")

    count = int((manifest.get("summary") or {}).get("refined_voxels", -1))
    data = points_path.read_bytes()
    if count <= 0 or len(data) != count * 12:
        raise RuntimeError("multiview finalizer source count/size mismatch")

    points = list(struct.iter_unpack("<fff", data))
    if any(
        not all(math.isfinite(value) for value in point)
        for point in points
    ):
        raise RuntimeError("multiview finalizer source contains non-finite point")

    return points, {
        "manifest_sha256": sha256_file(manifest_path),
        "points_sha256": actual,
    }


def voxel_key(
    point: tuple[float, float, float],
) -> tuple[int, int, int]:
    return (
        math.floor(point[0] / PITCH_M),
        math.floor(point[1] / PITCH_M),
        math.floor(point[2] / PITCH_M),
    )


def filter_components(
    keys: set[tuple[int, int, int]],
) -> tuple[list[tuple[int, int, int]], int, int]:
    unseen = set(keys)
    kept: list[tuple[int, int, int]] = []
    total_components = 0
    kept_components = 0

    while unseen:
        total_components += 1
        start = min(unseen)
        unseen.remove(start)
        stack = [start]
        component = [start]

        while stack:
            x, y, z = stack.pop()
            for dx, dy, dz in (
                (-1, 0, 0),
                (1, 0, 0),
                (0, -1, 0),
                (0, 1, 0),
                (0, 0, -1),
                (0, 0, 1),
            ):
                neighbor = (x + dx, y + dy, z + dz)
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    stack.append(neighbor)
                    component.append(neighbor)

        if len(component) >= MIN_COMPONENT_VOXELS:
            kept_components += 1
            kept.extend(component)

    kept.sort()
    if not kept:
        raise RuntimeError("component filter removed every occupied voxel")
    return kept, total_components, kept_components


def build_welded_mesh(
    keys: list[tuple[int, int, int]],
) -> tuple[
    list[tuple[float, float, float]],
    list[tuple[int, int, int]],
    int,
]:
    occupied = set(keys)
    vertex_map: dict[tuple[int, int, int], int] = {}
    lattice_vertices: list[tuple[int, int, int]] = []
    triangles: list[tuple[int, int, int]] = []
    quads = 0

    def vertex_index(corner: tuple[int, int, int]) -> int:
        index = vertex_map.get(corner)
        if index is not None:
            return index
        index = len(lattice_vertices)
        vertex_map[corner] = index
        lattice_vertices.append(corner)
        return index

    for ix, iy, iz in keys:
        for direction, corners in FACE_DEFS:
            neighbor = (
                ix + direction[0],
                iy + direction[1],
                iz + direction[2],
            )
            if neighbor in occupied:
                continue

            indices = []
            for cx, cy, cz in corners:
                indices.append(
                    vertex_index((ix + cx, iy + cy, iz + cz))
                )
            a, b, c, d = indices
            triangles.append((a, b, c))
            triangles.append((a, c, d))
            quads += 1

    positions = [
        (
            f32(x * PITCH_M),
            f32(y * PITCH_M),
            f32(z * PITCH_M),
        )
        for x, y, z in lattice_vertices
    ]
    if not positions or not triangles:
        raise RuntimeError("multiview surface extraction emitted empty mesh")
    return positions, triangles, quads


def adjacency(
    vertex_count: int,
    triangles: list[tuple[int, int, int]],
) -> list[list[int]]:
    neighbors = [set() for _ in range(vertex_count)]
    for a, b, c in triangles:
        neighbors[a].update((b, c))
        neighbors[b].update((a, c))
        neighbors[c].update((a, b))
    return [sorted(values) for values in neighbors]


def smooth_pass(
    positions: list[tuple[float, float, float]],
    neighbors: list[list[int]],
    factor: float,
) -> list[tuple[float, float, float]]:
    result = []
    for index, point in enumerate(positions):
        connected = neighbors[index]
        if not connected:
            result.append(point)
            continue

        mean = (
            sum(positions[j][0] for j in connected) / len(connected),
            sum(positions[j][1] for j in connected) / len(connected),
            sum(positions[j][2] for j in connected) / len(connected),
        )
        result.append((
            point[0] + factor * (mean[0] - point[0]),
            point[1] + factor * (mean[1] - point[1]),
            point[2] + factor * (mean[2] - point[2]),
        ))
    return result


def taubin_smooth(
    positions: list[tuple[float, float, float]],
    triangles: list[tuple[int, int, int]],
) -> list[tuple[float, float, float]]:
    neighbors = adjacency(len(positions), triangles)
    current = [(float(x), float(y), float(z)) for x, y, z in positions]
    for _ in range(TAUBIN_ITERATIONS):
        current = smooth_pass(current, neighbors, TAUBIN_LAMBDA)
        current = smooth_pass(current, neighbors, TAUBIN_MU)
    return [(f32(x), f32(y), f32(z)) for x, y, z in current]


def sub(
    a: tuple[float, float, float],
    b: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def cross(
    a: tuple[float, float, float],
    b: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def vertex_normals(
    positions: list[tuple[float, float, float]],
    triangles: list[tuple[int, int, int]],
) -> list[tuple[float, float, float]]:
    sums = [[0.0, 0.0, 0.0] for _ in positions]
    for a, b, c in triangles:
        face = cross(
            sub(positions[b], positions[a]),
            sub(positions[c], positions[a]),
        )
        for index in (a, b, c):
            sums[index][0] += face[0]
            sums[index][1] += face[1]
            sums[index][2] += face[2]

    normals = []
    for total in sums:
        length = math.sqrt(sum(value * value for value in total))
        if not math.isfinite(length) or length <= 1e-12:
            normals.append((0.0, 0.0, 1.0))
        else:
            normals.append((
                f32(total[0] / length),
                f32(total[1] / length),
                f32(total[2] / length),
            ))
    return normals


def mesh_components(
    vertex_count: int,
    triangles: list[tuple[int, int, int]],
) -> int:
    neighbors = adjacency(vertex_count, triangles)
    unseen = {i for i, values in enumerate(neighbors) if values}
    result = 0
    while unseen:
        result += 1
        start = unseen.pop()
        stack = [start]
        while stack:
            current = stack.pop()
            for neighbor in neighbors[current]:
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    stack.append(neighbor)
    return result


def mesh_bounds(
    positions: list[tuple[float, float, float]],
) -> dict[str, list[float]]:
    minimum = list(positions[0])
    maximum = list(positions[0])
    for point in positions[1:]:
        for axis in range(3):
            minimum[axis] = min(minimum[axis], point[axis])
            maximum[axis] = max(maximum[axis], point[axis])
    return {
        "min": [qfloat(value) for value in minimum],
        "max": [qfloat(value) for value in maximum],
    }


def pack_vec3(values: list[tuple[float, float, float]]) -> bytes:
    data = bytearray(len(values) * 12)
    offset = 0
    for value in values:
        struct.pack_into("<fff", data, offset, *value)
        offset += 12
    return bytes(data)


def pack_indices(triangles: list[tuple[int, int, int]]) -> bytes:
    flat = [index for triangle in triangles for index in triangle]
    data = bytearray(len(flat) * 4)
    offset = 0
    for value in flat:
        struct.pack_into("<I", data, offset, value)
        offset += 4
    return bytes(data)


def pad4(data: bytes, fill: bytes) -> bytes:
    return data + fill * ((-len(data)) % 4)


def glb_bytes(
    positions: list[tuple[float, float, float]],
    normals: list[tuple[float, float, float]],
    triangles: list[tuple[int, int, int]],
) -> bytes:
    position_data = pack_vec3(positions)
    normal_data = pack_vec3(normals)
    index_data = pack_indices(triangles)
    binary = position_data + normal_data + index_data
    padded_binary = pad4(binary, b"\x00")

    bounds = mesh_bounds(positions)
    index_count = len(triangles) * 3
    gltf = {
        "asset": {
            "version": "2.0",
            "generator": "starling-experiments D2f multiview finalizer",
        },
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {
                "buffer": 0,
                "byteOffset": 0,
                "byteLength": len(position_data),
                "target": 34962,
            },
            {
                "buffer": 0,
                "byteOffset": len(position_data),
                "byteLength": len(normal_data),
                "target": 34962,
            },
            {
                "buffer": 0,
                "byteOffset": len(position_data) + len(normal_data),
                "byteLength": len(index_data),
                "target": 34963,
            },
        ],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": len(positions),
                "type": "VEC3",
                "min": bounds["min"],
                "max": bounds["max"],
            },
            {
                "bufferView": 1,
                "componentType": 5126,
                "count": len(normals),
                "type": "VEC3",
            },
            {
                "bufferView": 2,
                "componentType": 5125,
                "count": index_count,
                "type": "SCALAR",
            },
        ],
        "meshes": [{
            "primitives": [{
                "attributes": {"POSITION": 0, "NORMAL": 1},
                "indices": 2,
                "mode": 4,
            }],
        }],
        "nodes": [{"mesh": 0}],
        "scenes": [{"nodes": [0]}],
        "scene": 0,
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
    total = 12 + 8 + len(json_chunk) + 8 + len(padded_binary)
    return (
        struct.pack("<III", 0x46546C67, 2, total)
        + struct.pack("<II", len(json_chunk), 0x4E4F534A)
        + json_chunk
        + struct.pack("<II", len(padded_binary), 0x004E4942)
        + padded_binary
    )


def obj_bytes(
    positions: list[tuple[float, float, float]],
    normals: list[tuple[float, float, float]],
    triangles: list[tuple[int, int, int]],
) -> bytes:
    lines = ["# EvoScene D2f welded multiview mesh\n"]
    for x, y, z in positions:
        lines.append(
            f"v {format(x, '.9g')} {format(y, '.9g')} {format(z, '.9g')}\n"
        )
    for x, y, z in normals:
        lines.append(
            f"vn {format(x, '.9g')} {format(y, '.9g')} {format(z, '.9g')}\n"
        )
    for a, b, c in triangles:
        a += 1
        b += 1
        c += 1
        lines.append(f"f {a}//{a} {b}//{b} {c}//{c}\n")
    return "".join(lines).encode("ascii")


def ply_bytes(
    positions: list[tuple[float, float, float]],
    normals: list[tuple[float, float, float]],
    triangles: list[tuple[int, int, int]],
) -> bytes:
    lines = [
        "ply\n",
        "format ascii 1.0\n",
        "comment evoscene d2f welded multiview mesh\n",
        f"element vertex {len(positions)}\n",
        "property float x\n",
        "property float y\n",
        "property float z\n",
        "property float nx\n",
        "property float ny\n",
        "property float nz\n",
        f"element face {len(triangles)}\n",
        "property list uchar uint vertex_indices\n",
        "end_header\n",
    ]
    for point, normal in zip(positions, normals):
        lines.append(
            f"{format(point[0], '.9g')} "
            f"{format(point[1], '.9g')} "
            f"{format(point[2], '.9g')} "
            f"{format(normal[0], '.9g')} "
            f"{format(normal[1], '.9g')} "
            f"{format(normal[2], '.9g')}\n"
        )
    for a, b, c in triangles:
        lines.append(f"3 {a} {b} {c}\n")
    return "".join(lines).encode("ascii")


def run_finalize(args: argparse.Namespace) -> dict[str, Any]:
    source_dir = pathlib.Path(args.source_dir).expanduser().resolve()
    output_dir = pathlib.Path(args.output).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter_ns()
    points, parents = load_source(source_dir)
    raw_keys = {voxel_key(point) for point in points}
    keys, raw_components, kept_components = filter_components(raw_keys)

    positions, triangles, quads = build_welded_mesh(keys)
    smoothed = taubin_smooth(positions, triangles)
    normals = vertex_normals(smoothed, triangles)
    components = mesh_components(len(smoothed), triangles)

    glb = glb_bytes(smoothed, normals, triangles)
    obj = obj_bytes(smoothed, normals, triangles)
    ply = ply_bytes(smoothed, normals, triangles)

    write_bytes(output_dir / GLB_FILENAME, glb)
    write_bytes(output_dir / OBJ_FILENAME, obj)
    write_bytes(output_dir / PLY_FILENAME, ply)

    manifest = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "parents": {
            "source_manifest_sha256": parents["manifest_sha256"],
            "source_points_sha256": parents["points_sha256"],
        },
        "config": {
            "pitch_m": PITCH_M,
            "min_component_voxels": MIN_COMPONENT_VOXELS,
            "taubin_iterations": TAUBIN_ITERATIONS,
            "taubin_lambda": TAUBIN_LAMBDA,
            "taubin_mu": TAUBIN_MU,
            "surface_rule": "welded-exposed-voxel-boundary",
        },
        "artifacts": {
            "glb": {
                "file": GLB_FILENAME,
                "bytes": len(glb),
                "sha256": sha256_bytes(glb),
            },
            "obj": {
                "file": OBJ_FILENAME,
                "bytes": len(obj),
                "sha256": sha256_bytes(obj),
            },
            "ply": {
                "file": PLY_FILENAME,
                "bytes": len(ply),
                "sha256": sha256_bytes(ply),
            },
        },
        "summary": {
            "source_points": len(points),
            "raw_occupied_voxels": len(raw_keys),
            "kept_occupied_voxels": len(keys),
            "raw_voxel_components": raw_components,
            "kept_voxel_components": kept_components,
            "exposed_quads": quads,
            "vertices": len(smoothed),
            "triangles": len(triangles),
            "connected_components": components,
            "bounds_m": mesh_bounds(smoothed),
        },
    }
    manifest_bytes = canonical_json_bytes(manifest)
    write_bytes(output_dir / MANIFEST_FILENAME, manifest_bytes)

    elapsed_ms = (time.perf_counter_ns() - started) // 1_000_000
    write_bytes(
        output_dir / TELEMETRY_FILENAME,
        canonical_json_bytes({
            "schema": "evoscene.multiview_mesh.telemetry.v1",
            "finalization_ms": int(elapsed_ms),
            "python": platform.python_version(),
        }),
    )

    return {
        "status": "ok",
        "manifest_sha256": sha256_bytes(manifest_bytes),
        "glb_sha256": sha256_bytes(glb),
        "obj_sha256": sha256_bytes(obj),
        "ply_sha256": sha256_bytes(ply),
        "vertices": len(smoothed),
        "triangles": len(triangles),
        "components": components,
    }


def write_synthetic_source(directory: pathlib.Path) -> None:
    # 2x2x2 block => one connected closed surface. Add a one-voxel speck that
    # must be removed by MIN_COMPONENT_VOXELS.
    points = []
    for z in (0, 1):
        for y in (0, 1):
            for x in (0, 1):
                points.append((
                    (x + 0.5) * PITCH_M,
                    (y + 0.5) * PITCH_M,
                    (z + 0.5) * PITCH_M,
                ))
    points.append((10.5 * PITCH_M, 0.5 * PITCH_M, 0.5 * PITCH_M))

    data = b"".join(struct.pack("<fff", *point) for point in points)
    write_bytes(directory / "refined_points.f32le", data)
    manifest = {
        "schema": "evoscene.fusion_refinement.v1",
        "artifacts": {
            "refined_points": {"sha256": sha256_bytes(data)},
        },
        "summary": {"refined_voxels": len(points)},
    }
    write_bytes(directory / "fusion.json", canonical_json_bytes(manifest))


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="evoscene-d2f-mesh-") as tmp:
        root = pathlib.Path(tmp)
        source = root / "source"
        a = root / "a"
        b = root / "b"
        source.mkdir()
        write_synthetic_source(source)

        first = run_finalize(
            argparse.Namespace(source_dir=str(source), output=str(a))
        )
        second = run_finalize(
            argparse.Namespace(source_dir=str(source), output=str(b))
        )
        if first["components"] != 1:
            raise AssertionError("D2f multiview mesh is not connected")
        if first["vertices"] >= first["triangles"] * 3:
            raise AssertionError("D2f multiview mesh did not weld vertices")
        for key in (
            "manifest_sha256",
            "glb_sha256",
            "obj_sha256",
            "ply_sha256",
        ):
            if first[key] != second[key]:
                raise AssertionError(f"D2f multiview repeat differs for {key}")

    print(
        "D2f multiview finalizer self-test PASS: "
        "welded full-world surface + smoothing exact"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--source-dir")
    parser.add_argument("--output")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0
    if not args.source_dir or not args.output:
        raise SystemExit("--source-dir and --output are required together")

    try:
        result = run_finalize(args)
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
