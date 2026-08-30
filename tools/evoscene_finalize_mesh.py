#!/usr/bin/env python3
"""Deterministic D2e post-convergence projected-surface mesh finalizer.

Consumes one final D2c refined state plus the original D2a camera and emits:
- scene.glb (primary artifact)
- scene.obj
- scene_mesh.ply

Unlike the first D2e voxel-shell prototype, this finalizer reconstructs a
coherent source-visible surface:
1. z-buffer final 3D points into the source camera;
2. retain one nearest point per source pixel;
3. triangulate projected samples with deterministic Delaunay;
4. reject triangles that bridge large pixel gaps or depth discontinuities;
5. weld shared vertices and compute area-weighted vertex normals.

No scheduling decisions occur here. The finalizer runs once after convergence.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
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
ADAPTER_VERSION = 2
SCHEMA = "evoscene.mesh.v1"

CANONICAL_MAX_PIXEL_EDGE = 16.0
CANONICAL_MAX_DEPTH_JUMP_M = 0.050
CANONICAL_RELATIVE_DEPTH_JUMP = 0.030
SCIPY_VERSION = "1.18.1"

SOURCE_MANIFEST_FILENAME = "fusion.json"
SOURCE_POINTS_FILENAME = "refined_points.f32le"
CAMERA_FILENAME = "camera.json"
PRIOR_FILENAME = "prior.json"

GLB_FILENAME = "scene.glb"
OBJ_FILENAME = "scene.obj"
PLY_FILENAME = "scene_mesh.ply"
MESH_MANIFEST_FILENAME = "mesh.json"
TELEMETRY_FILENAME = "telemetry.json"

SURFACE_RULE = "source-camera-zbuffer-delaunay-filtered"
RASTER_RULE = "nearest-z-source-index-tiebreak"
TRIANGLE_RULE = "delaunay-filter-pixel-edge-depth-jump"
NORMAL_RULE = "area-weighted-vertex-normal-facing-source-camera"
COORDINATE_CONVENTION = "opencv-x-right-y-down-z-forward-meters"
VERTEX_RULE = "one-visible-source-point-per-source-pixel"
CANONICAL_ORDERING = "vertices-source-pixel-index-triangles-lexicographic"


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


def require_numeric_stack() -> tuple[Any, Any]:
    try:
        import numpy as np
        from scipy.spatial import Delaunay
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "D2e requires NumPy + SciPy; install requirements-d2e.txt"
        ) from exc

    actual_scipy = importlib.metadata.version("scipy")
    if actual_scipy != SCIPY_VERSION:
        raise RuntimeError(
            f"SciPy version mismatch: {actual_scipy} != {SCIPY_VERSION}"
        )

    return np, Delaunay


def describe() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "canonical_max_pixel_edge": CANONICAL_MAX_PIXEL_EDGE,
        "canonical_max_depth_jump_m": CANONICAL_MAX_DEPTH_JUMP_M,
        "canonical_relative_depth_jump": CANONICAL_RELATIVE_DEPTH_JUMP,
        "scipy_version": SCIPY_VERSION,
        "glb_filename": GLB_FILENAME,
        "obj_filename": OBJ_FILENAME,
        "ply_filename": PLY_FILENAME,
        "mesh_manifest_filename": MESH_MANIFEST_FILENAME,
        "telemetry_filename": TELEMETRY_FILENAME,
        "surface_rule": SURFACE_RULE,
        "raster_rule": RASTER_RULE,
        "triangle_rule": TRIANGLE_RULE,
        "normal_rule": NORMAL_RULE,
        "coordinate_convention": COORDINATE_CONVENTION,
        "vertex_rule": VERTEX_RULE,
        "canonical_ordering": CANONICAL_ORDERING,
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


def depth_jump_limit(z_near: float) -> float:
    return max(
        CANONICAL_MAX_DEPTH_JUMP_M,
        z_near * CANONICAL_RELATIVE_DEPTH_JUMP,
    )


def depth_edge_accepted(a: float, b: float) -> bool:
    if a <= 0.0 or b <= 0.0:
        return False
    return abs(a - b) <= depth_jump_limit(min(a, b))


def project_visible(
    points: list[tuple[float, float, float]],
    camera: dict[str, Any],
) -> tuple[
    list[tuple[float, float, float]],
    list[tuple[float, float]],
    list[int],
    int,
]:
    width = int(camera["image_width"])
    height = int(camera["image_height"])
    k = camera["intrinsics_normalized"]
    fx = float(k[0][0])
    fy = float(k[1][1])
    cx = float(k[0][2])
    cy = float(k[1][2])

    selected: dict[int, tuple[float, int, tuple[float, float, float], float, float]] = {}
    projected_count = 0

    for source_index, point in enumerate(points):
        x, y, z = point
        if z <= 0.0:
            continue

        u = fx * (x / z) + cx
        v = fy * (y / z) + cy
        if not (0.0 <= u < 1.0 and 0.0 <= v < 1.0):
            continue

        px = u * width
        py = v * height
        pixel_x = int(math.floor(px))
        pixel_y = int(math.floor(py))
        pixel = pixel_y * width + pixel_x
        z32 = f32(z)

        current = selected.get(pixel)
        if (
            current is None
            or z32 < current[0]
            or (z32 == current[0] and source_index < current[1])
        ):
            selected[pixel] = (
                z32,
                source_index,
                (f32(x), f32(y), f32(z)),
                px,
                py,
            )
        projected_count += 1

    if len(selected) < 3:
        raise RuntimeError("source-camera z-buffer produced fewer than 3 samples")

    vertices = []
    projected = []
    pixels = []
    for pixel in sorted(selected):
        _, _, point, px, py = selected[pixel]
        vertices.append(point)
        projected.append((px, py))
        pixels.append(pixel)

    return vertices, projected, pixels, projected_count


def pixel_edge_length(
    a: tuple[float, float],
    b: tuple[float, float],
) -> float:
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return math.sqrt(dx * dx + dy * dy)


def canonical_triangle(
    tri: tuple[int, int, int],
) -> tuple[int, int, int]:
    a, b, c = tri
    if a <= b and a <= c:
        return (a, b, c)
    if b <= a and b <= c:
        return (b, c, a)
    return (c, a, b)


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


def dot(
    a: tuple[float, float, float],
    b: tuple[float, float, float],
) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def norm(v: tuple[float, float, float]) -> float:
    return math.sqrt(dot(v, v))


def triangulate_surface(
    vertices: list[tuple[float, float, float]],
    projected: list[tuple[float, float]],
) -> list[tuple[int, int, int]]:
    np, Delaunay = require_numeric_stack()

    coords = np.asarray(projected, dtype=np.float64).copy()
    # Break exact grid/co-circular ties deterministically without changing
    # the exported 3D geometry.
    for index in range(len(coords)):
        seed = ((index + 1) * 2654435761) & 0xFFFFFFFF
        coords[index, 0] += ((seed & 0xFFFF) - 32768) * 1e-12
        coords[index, 1] += (((seed >> 16) & 0xFFFF) - 32768) * 1e-12

    delaunay = Delaunay(coords, qhull_options="Qbb Qc Qz Q12")
    accepted: set[tuple[int, int, int]] = set()

    for simplex in delaunay.simplices:
        a, b, c = (int(simplex[0]), int(simplex[1]), int(simplex[2]))
        if a == b or b == c or a == c:
            continue

        pairs = ((a, b), (b, c), (c, a))
        if any(
            pixel_edge_length(projected[i], projected[j])
            > CANONICAL_MAX_PIXEL_EDGE
            for i, j in pairs
        ):
            continue

        if any(
            not depth_edge_accepted(vertices[i][2], vertices[j][2])
            for i, j in pairs
        ):
            continue

        pa = vertices[a]
        pb = vertices[b]
        pc = vertices[c]
        geometric = cross(sub(pb, pa), sub(pc, pa))
        area2 = norm(geometric)
        if not math.isfinite(area2) or area2 <= 1e-10:
            continue

        centroid = (
            (pa[0] + pb[0] + pc[0]) / 3.0,
            (pa[1] + pb[1] + pc[1]) / 3.0,
            (pa[2] + pb[2] + pc[2]) / 3.0,
        )
        toward_camera = (-centroid[0], -centroid[1], -centroid[2])
        if dot(geometric, toward_camera) < 0.0:
            b, c = c, b

        accepted.add(canonical_triangle((a, b, c)))

    triangles = sorted(accepted)
    if not triangles:
        raise RuntimeError("Delaunay filtering removed every triangle")
    return triangles


def compact_mesh(
    vertices: list[tuple[float, float, float]],
    projected: list[tuple[float, float]],
    pixels: list[int],
    triangles: list[tuple[int, int, int]],
) -> tuple[
    list[tuple[float, float, float]],
    list[tuple[float, float]],
    list[int],
    list[tuple[int, int, int]],
]:
    used = sorted({index for tri in triangles for index in tri})
    remap = {old: new for new, old in enumerate(used)}

    compact_vertices = [vertices[index] for index in used]
    compact_projected = [projected[index] for index in used]
    compact_pixels = [pixels[index] for index in used]
    compact_triangles = [
        tuple(remap[index] for index in tri)
        for tri in triangles
    ]

    return (
        compact_vertices,
        compact_projected,
        compact_pixels,
        compact_triangles,
    )


def vertex_normals(
    vertices: list[tuple[float, float, float]],
    triangles: list[tuple[int, int, int]],
) -> list[tuple[float, float, float]]:
    sums = [[0.0, 0.0, 0.0] for _ in vertices]

    for a, b, c in triangles:
        pa = vertices[a]
        pb = vertices[b]
        pc = vertices[c]
        face = cross(sub(pb, pa), sub(pc, pa))
        for index in (a, b, c):
            sums[index][0] += face[0]
            sums[index][1] += face[1]
            sums[index][2] += face[2]

    result = []
    for index, total in enumerate(sums):
        value = (total[0], total[1], total[2])
        length = norm(value)
        if not math.isfinite(length) or length <= 1e-12:
            point = vertices[index]
            fallback = (-point[0], -point[1], -point[2])
            length = norm(fallback)
            if length <= 1e-12:
                raise RuntimeError("cannot orient degenerate vertex normal")
            value = fallback

        length = norm(value)
        normal = (
            f32(value[0] / length),
            f32(value[1] / length),
            f32(value[2] / length),
        )
        result.append(normal)

    return result


def connected_components(
    vertex_count: int,
    triangles: list[tuple[int, int, int]],
) -> int:
    adjacency = [set() for _ in range(vertex_count)]
    used = set()
    for a, b, c in triangles:
        used.update((a, b, c))
        adjacency[a].update((b, c))
        adjacency[b].update((a, c))
        adjacency[c].update((a, b))

    unseen = set(used)
    components = 0
    while unseen:
        components += 1
        start = unseen.pop()
        stack = [start]
        while stack:
            current = stack.pop()
            for neighbor in adjacency[current]:
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    stack.append(neighbor)

    return components


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


def pack_vec3_f32(values: list[tuple[float, float, float]]) -> bytes:
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
    triangles: list[tuple[int, int, int]],
) -> bytes:
    indices = [index for tri in triangles for index in tri]
    position_data = pack_vec3_f32(positions)
    normal_data = pack_vec3_f32(normals)
    index_data = pack_u32(indices)

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
            "generator": "starling-experiments D2e projected-surface",
            "version": "2.0",
        },
        "bufferViews": [
            {
                "buffer": 0,
                "byteLength": len(position_data),
                "byteOffset": 0,
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
                "attributes": {"NORMAL": 1, "POSITION": 0},
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
    total_length = 12 + 8 + len(json_chunk) + 8 + len(binary_padded)

    return (
        struct.pack("<III", 0x46546C67, 2, total_length)
        + struct.pack("<II", len(json_chunk), 0x4E4F534A)
        + json_chunk
        + struct.pack("<II", len(binary_padded), 0x004E4942)
        + binary_padded
    )


def obj_bytes(
    positions: list[tuple[float, float, float]],
    normals: list[tuple[float, float, float]],
    triangles: list[tuple[int, int, int]],
) -> bytes:
    lines = [
        "# EvoScene D2e deterministic projected-surface mesh\n",
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
    header = (
        "ply\n"
        "format ascii 1.0\n"
        "comment evoscene d2e deterministic projected-surface mesh\n"
        f"element vertex {len(positions)}\n"
        "property float x\n"
        "property float y\n"
        "property float z\n"
        "property float nx\n"
        "property float ny\n"
        "property float nz\n"
        f"element face {len(triangles)}\n"
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
    for a, b, c in triangles:
        lines.append(f"3 {a} {b} {c}\n")
    return "".join(lines).encode("ascii")


def run_finalize(args: argparse.Namespace) -> dict[str, Any]:
    source_dir = pathlib.Path(args.source_dir).expanduser().resolve()
    camera_dir = pathlib.Path(args.camera_dir).expanduser().resolve()
    output_dir = pathlib.Path(args.output).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter_ns()
    points, source_hashes = load_source(source_dir)
    camera, camera_hashes = load_camera(camera_dir)

    visible, projected, pixels, projected_count = project_visible(points, camera)
    raw_triangles = triangulate_surface(visible, projected)
    (
        positions,
        compact_projected,
        compact_pixels,
        triangles,
    ) = compact_mesh(visible, projected, pixels, raw_triangles)
    normals = vertex_normals(positions, triangles)
    components = connected_components(len(positions), triangles)

    glb = glb_bytes(positions, normals, triangles)
    obj = obj_bytes(positions, normals, triangles)
    ply = ply_bytes(positions, normals, triangles)

    glb_path = output_dir / GLB_FILENAME
    obj_path = output_dir / OBJ_FILENAME
    ply_path = output_dir / PLY_FILENAME
    write_bytes(glb_path, glb)
    write_bytes(obj_path, obj)
    write_bytes(ply_path, ply)

    glb_sha = sha256_bytes(glb)
    obj_sha = sha256_bytes(obj)
    ply_sha = sha256_bytes(ply)

    pixel_x = [uv[0] for uv in compact_projected]
    pixel_y = [uv[1] for uv in compact_projected]

    manifest = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "parents": {
            "source_manifest_sha256": source_hashes["manifest_sha256"],
            "source_points_sha256": source_hashes["points_sha256"],
            "camera_prior_sha256": camera_hashes["prior_sha256"],
            "camera_sha256": camera_hashes["camera_sha256"],
        },
        "config": {
            "surface_rule": SURFACE_RULE,
            "raster_rule": RASTER_RULE,
            "triangle_rule": TRIANGLE_RULE,
            "normal_rule": NORMAL_RULE,
            "coordinate_convention": COORDINATE_CONVENTION,
            "vertex_rule": VERTEX_RULE,
            "ordering": CANONICAL_ORDERING,
            "max_pixel_edge": CANONICAL_MAX_PIXEL_EDGE,
            "max_depth_jump_m": CANONICAL_MAX_DEPTH_JUMP_M,
            "relative_depth_jump": CANONICAL_RELATIVE_DEPTH_JUMP,
            "scipy_version": SCIPY_VERSION,
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
            "projected_source_samples": projected_count,
            "zbuffer_visible_samples": len(visible),
            "vertices": len(positions),
            "triangles": len(triangles),
            "indices": len(triangles) * 3,
            "connected_components": components,
            "source_pixel_index_min": min(compact_pixels),
            "source_pixel_index_max": max(compact_pixels),
            "projected_bounds_px": {
                "min": [qfloat(min(pixel_x)), qfloat(min(pixel_y))],
                "max": [qfloat(max(pixel_x)), qfloat(max(pixel_y))],
            },
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
        "vertices": len(positions),
        "triangles": len(triangles),
        "connected_components": components,
    }


def write_synthetic_source(directory: pathlib.Path) -> None:
    points = []
    for y in (-0.30, -0.15, 0.0, 0.15, 0.30):
        for x in (-0.30, -0.15, 0.0, 0.15, 0.30):
            z = 2.0 + 0.03 * x + 0.02 * y
            points.append((f32(x), f32(y), f32(z)))

    data = b"".join(struct.pack("<fff", *point) for point in points)
    write_bytes(directory / SOURCE_POINTS_FILENAME, data)

    manifest = {
        "schema": "evoscene.fusion_refinement.v1",
        "artifacts": {
            "refined_points": {"sha256": sha256_bytes(data)},
        },
        "summary": {"refined_voxels": len(points)},
    }
    write_bytes(
        directory / SOURCE_MANIFEST_FILENAME,
        canonical_json_bytes(manifest),
    )


def write_synthetic_camera(directory: pathlib.Path) -> None:
    camera = {
        "schema": "evoscene.camera.v1",
        "image_width": 128,
        "image_height": 128,
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
            "camera": {"sha256": sha256_bytes(camera_data)},
        },
    }
    write_bytes(directory / PRIOR_FILENAME, canonical_json_bytes(prior))


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="evoscene-d2e-") as tmp:
        root = pathlib.Path(tmp)
        source = root / "source"
        camera = root / "camera"
        out_a = root / "a"
        out_b = root / "b"
        source.mkdir()
        camera.mkdir()
        write_synthetic_source(source)
        write_synthetic_camera(camera)

        common = {
            "source_dir": str(source),
            "camera_dir": str(camera),
        }
        first = run_finalize(argparse.Namespace(output=str(out_a), **common))
        second = run_finalize(argparse.Namespace(output=str(out_b), **common))

        if first["vertices"] < 16:
            raise AssertionError("synthetic surface retained too few vertices")
        if first["triangles"] < 18:
            raise AssertionError("synthetic surface emitted too few triangles")
        if first["connected_components"] != 1:
            raise AssertionError(
                "synthetic projected surface is not one connected component"
            )

        for key in (
            "manifest_sha256",
            "glb_sha256",
            "obj_sha256",
            "ply_sha256",
        ):
            if first[key] != second[key]:
                raise AssertionError(f"D2e repeat differs for {key}")

        if not depth_edge_accepted(2.0, 2.04):
            raise AssertionError("depth filter rejected acceptable edge")
        if depth_edge_accepted(2.0, 2.08):
            raise AssertionError("depth filter accepted discontinuity")

        glb = (out_a / GLB_FILENAME).read_bytes()
        magic, version, total = struct.unpack_from("<III", glb, 0)
        if magic != 0x46546C67 or version != 2 or total != len(glb):
            raise AssertionError("synthetic GLB header is invalid")

    print(
        "D2e adapter self-test PASS: "
        "deterministic welded projected-surface GLB/OBJ/PLY exact"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--source-dir")
    parser.add_argument("--camera-dir")
    parser.add_argument("--output")
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
