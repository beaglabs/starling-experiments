#!/usr/bin/env python3
"""D2e projected-surface mesh-finalization engineering/live gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import shutil
import struct
import subprocess
import sys
from typing import Any, Optional

ROOT = pathlib.Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "tools" / "evoscene_finalize_mesh.py"

DEFAULT_SOURCE = ROOT / "trials" / "evoscene-d2d-smoke" / "closed-loop-a"
DEFAULT_CAMERA = ROOT / "trials" / "evoscene-d2a-smoke" / "run-a"
DEFAULT_OUTPUT = ROOT / "trials" / "evoscene-d2e-smoke"


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
            f"D2e gate failed: {' '.join(args)} exit={proc.returncode}"
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

    run("zig", "build", "test-demo-evoscene-d2e", timeout_s=600)

    proc = run(
        "zig",
        "build",
        "-Doptimize=ReleaseFast",
        "run-demo-evoscene-d2e",
        "--",
        "validate",
        capture=True,
        timeout_s=600,
    )
    contract_text = proc.stdout.decode()
    print(contract_text, end="" if contract_text.endswith("\n") else "\n")
    contract = parse_contract(contract_text)

    self_test = run(
        sys.executable,
        str(ADAPTER),
        "--self-test",
        capture=True,
        timeout_s=180,
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
        "canonical_max_pixel_edge": str(
            int(description["canonical_max_pixel_edge"])
        ),
        "canonical_max_depth_jump_mm": str(
            round(float(description["canonical_max_depth_jump_m"]) * 1000)
        ),
        "canonical_relative_depth_jump_ppm": str(
            round(float(description["canonical_relative_depth_jump"]) * 1_000_000)
        ),
        "scipy_version": str(description["scipy_version"]),
        "surface_rule": str(description["surface_rule"]),
        "raster_rule": str(description["raster_rule"]),
        "triangle_rule": str(description["triangle_rule"]),
        "normal_rule": str(description["normal_rule"]),
        "coordinate_convention": str(description["coordinate_convention"]),
        "vertex_rule": str(description["vertex_rule"]),
        "canonical_ordering": str(description["canonical_ordering"]),
    }
    for key, wanted in expected.items():
        actual = contract.get(key)
        if actual != wanted:
            raise SystemExit(
                f"D2e Zig/Python contract mismatch {key}: "
                f"{actual!r} != {wanted!r}"
            )

    if contract.get("surface_primitive") != "PASS":
        raise SystemExit("D2e Zig surface primitive did not pass")
    if not contract_text.rstrip().endswith(
        "D2e CONTRACT PASS: deterministic projected-surface boundary frozen"
    ):
        raise SystemExit("D2e Zig contract verdict missing")

    return description


def parse_glb(path: pathlib.Path) -> tuple[dict[str, Any], bytes]:
    data = path.read_bytes()
    if len(data) < 28:
        raise SystemExit("D2e GLB is too short")

    magic, version, total_length = struct.unpack_from("<III", data, 0)
    if magic != 0x46546C67:
        raise SystemExit("D2e GLB magic is invalid")
    if version != 2:
        raise SystemExit(f"D2e GLB version {version} != 2")
    if total_length != len(data):
        raise SystemExit(
            f"D2e GLB length header {total_length} != {len(data)}"
        )

    offset = 12
    json_length, json_type = struct.unpack_from("<II", data, offset)
    offset += 8
    if json_type != 0x4E4F534A:
        raise SystemExit("D2e GLB first chunk is not JSON")
    json_bytes = data[offset:offset + json_length]
    offset += json_length

    try:
        gltf = json.loads(json_bytes.decode("utf-8").rstrip(" "))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit("D2e GLB JSON chunk is invalid") from exc

    if offset + 8 > len(data):
        raise SystemExit("D2e GLB has no BIN chunk")
    bin_length, bin_type = struct.unpack_from("<II", data, offset)
    offset += 8
    if bin_type != 0x004E4942:
        raise SystemExit("D2e GLB second chunk is not BIN")

    binary = data[offset:offset + bin_length]
    offset += bin_length
    if offset != len(data):
        raise SystemExit("D2e GLB has trailing bytes")

    return gltf, binary


def project_point(
    point: tuple[float, float, float],
    camera: dict[str, Any],
) -> tuple[float, float]:
    x, y, z = point
    if z <= 0.0:
        raise SystemExit("D2e mesh contains non-positive source-camera depth")

    width = int(camera["image_width"])
    height = int(camera["image_height"])
    k = camera["intrinsics_normalized"]
    fx = float(k[0][0])
    fy = float(k[1][1])
    cx = float(k[0][2])
    cy = float(k[1][2])

    u = fx * (x / z) + cx
    v = fy * (y / z) + cy
    return (u * width, v * height)


def pixel_edge_length(
    a: tuple[float, float],
    b: tuple[float, float],
) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def depth_edge_accepted(
    a: float,
    b: float,
    description: dict[str, Any],
) -> bool:
    if a <= 0.0 or b <= 0.0:
        return False
    absolute = float(description["canonical_max_depth_jump_m"])
    relative = float(description["canonical_relative_depth_jump"])
    limit = max(absolute, min(a, b) * relative)
    return abs(a - b) <= limit + 1e-9


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
    result = 0
    while unseen:
        result += 1
        start = unseen.pop()
        stack = [start]
        while stack:
            current = stack.pop()
            for neighbor in adjacency[current]:
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    stack.append(neighbor)

    return result


def validate_glb(
    path: pathlib.Path,
    camera: dict[str, Any],
    vertices: int,
    indices: int,
    triangles: int,
    expected_components: int,
    expected_bounds: dict[str, Any],
    description: dict[str, Any],
) -> None:
    gltf, binary = parse_glb(path)

    asset = gltf.get("asset") or {}
    if asset.get("version") != "2.0":
        raise SystemExit("D2e GLB asset version is not 2.0")

    accessors = gltf.get("accessors")
    views = gltf.get("bufferViews")
    buffers = gltf.get("buffers")
    meshes = gltf.get("meshes")
    if not all(
        isinstance(value, list)
        for value in (accessors, views, buffers, meshes)
    ):
        raise SystemExit("D2e GLB core arrays are missing")

    if len(accessors) != 3 or len(views) != 3 or len(buffers) != 1:
        raise SystemExit("D2e GLB accessor/bufferView layout drifted")

    position_accessor = accessors[0]
    normal_accessor = accessors[1]
    index_accessor = accessors[2]

    if position_accessor.get("componentType") != 5126:
        raise SystemExit("D2e GLB POSITION is not float32")
    if position_accessor.get("type") != "VEC3":
        raise SystemExit("D2e GLB POSITION is not VEC3")
    if int(position_accessor.get("count", -1)) != vertices:
        raise SystemExit("D2e GLB POSITION count mismatch")

    if normal_accessor.get("componentType") != 5126:
        raise SystemExit("D2e GLB NORMAL is not float32")
    if normal_accessor.get("type") != "VEC3":
        raise SystemExit("D2e GLB NORMAL is not VEC3")
    if int(normal_accessor.get("count", -1)) != vertices:
        raise SystemExit("D2e GLB NORMAL count mismatch")

    if index_accessor.get("componentType") != 5125:
        raise SystemExit("D2e GLB indices are not uint32")
    if index_accessor.get("type") != "SCALAR":
        raise SystemExit("D2e GLB indices are not SCALAR")
    if int(index_accessor.get("count", -1)) != indices:
        raise SystemExit("D2e GLB index count mismatch")
    if indices != triangles * 3:
        raise SystemExit("D2e GLB triangle/index accounting mismatch")

    primitive = meshes[0]["primitives"][0]
    if primitive.get("mode") != 4:
        raise SystemExit("D2e GLB primitive is not TRIANGLES")
    attrs = primitive.get("attributes") or {}
    if attrs.get("POSITION") != 0 or attrs.get("NORMAL") != 1:
        raise SystemExit("D2e GLB primitive attributes mismatch")
    if primitive.get("indices") != 2:
        raise SystemExit("D2e GLB primitive index accessor mismatch")

    expected_binary = vertices * 12 + vertices * 12 + indices * 4
    if int(buffers[0].get("byteLength", -1)) != expected_binary:
        raise SystemExit("D2e GLB binary payload length mismatch")
    if len(binary) < expected_binary or len(binary) > expected_binary + 3:
        raise SystemExit("D2e GLB BIN padding is invalid")

    positions = list(
        struct.iter_unpack("<fff", binary[:vertices * 12])
    )
    normal_start = vertices * 12
    normal_end = normal_start + vertices * 12
    normals = list(
        struct.iter_unpack("<fff", binary[normal_start:normal_end])
    )
    decoded_indices = [
        value[0]
        for value in struct.iter_unpack(
            "<I",
            binary[normal_end:normal_end + indices * 4],
        )
    ]

    if any(
        not all(math.isfinite(value) for value in point)
        for point in positions
    ):
        raise SystemExit("D2e GLB contains non-finite position")

    for normal in normals:
        length = math.sqrt(sum(value * value for value in normal))
        if abs(length - 1.0) > 2e-6:
            raise SystemExit("D2e GLB contains non-unit normal")

    if not decoded_indices:
        raise SystemExit("D2e GLB has no indices")
    if min(decoded_indices) < 0 or max(decoded_indices) >= vertices:
        raise SystemExit("D2e GLB index outside vertex range")

    tris = [
        tuple(decoded_indices[i:i + 3])
        for i in range(0, len(decoded_indices), 3)
    ]
    if any(len(set(tri)) != 3 for tri in tris):
        raise SystemExit("D2e GLB contains degenerate index triangle")

    actual_components = connected_components(vertices, tris)
    if actual_components != expected_components:
        raise SystemExit(
            "D2e connected-component mismatch: "
            f"{actual_components} != {expected_components}"
        )

    # This is the explicit guard against the failed voxel-shell prototype:
    # triangles must share indexed vertices somewhere in any nontrivial mesh.
    shared_references = len(decoded_indices) - len(set(decoded_indices))
    if triangles > 2 and shared_references <= 0:
        raise SystemExit(
            "D2e mesh has no shared indexed topology; "
            "face-local island regression detected"
        )

    projected = [project_point(point, camera) for point in positions]
    max_pixel_edge = float(description["canonical_max_pixel_edge"])

    for tri in tris:
        a, b, c = tri
        pa = positions[a]
        pb = positions[b]
        pc = positions[c]

        pairs = ((a, b), (b, c), (c, a))
        for i, j in pairs:
            if pixel_edge_length(projected[i], projected[j]) > max_pixel_edge + 1e-7:
                raise SystemExit("D2e triangle exceeds canonical pixel gap")
            if not depth_edge_accepted(
                positions[i][2],
                positions[j][2],
                description,
            ):
                raise SystemExit("D2e triangle crosses depth discontinuity")

        ab = (
            pb[0] - pa[0],
            pb[1] - pa[1],
            pb[2] - pa[2],
        )
        ac = (
            pc[0] - pa[0],
            pc[1] - pa[1],
            pc[2] - pa[2],
        )
        geometric = (
            ab[1] * ac[2] - ab[2] * ac[1],
            ab[2] * ac[0] - ab[0] * ac[2],
            ab[0] * ac[1] - ab[1] * ac[0],
        )
        area2 = math.sqrt(sum(value * value for value in geometric))
        if area2 <= 1e-10:
            raise SystemExit("D2e GLB contains zero-area triangle")

        centroid = (
            (pa[0] + pb[0] + pc[0]) / 3.0,
            (pa[1] + pb[1] + pc[1]) / 3.0,
            (pa[2] + pb[2] + pc[2]) / 3.0,
        )
        toward_camera = (-centroid[0], -centroid[1], -centroid[2])
        facing = sum(g * t for g, t in zip(geometric, toward_camera))
        if facing <= 0.0:
            raise SystemExit("D2e triangle winding does not face source camera")

    minimum = [
        min(point[axis] for point in positions)
        for axis in range(3)
    ]
    maximum = [
        max(point[axis] for point in positions)
        for axis in range(3)
    ]
    declared_min = [float(value) for value in expected_bounds["min"]]
    declared_max = [float(value) for value in expected_bounds["max"]]

    for actual, declared in zip(minimum, declared_min):
        if abs(actual - declared) > 1e-8:
            raise SystemExit("D2e GLB/manifest minimum bound mismatch")
    for actual, declared in zip(maximum, declared_max):
        if abs(actual - declared) > 1e-8:
            raise SystemExit("D2e GLB/manifest maximum bound mismatch")


def validate_obj(
    path: pathlib.Path,
    vertices: int,
    triangles: int,
) -> None:
    vertex_count = 0
    normal_count = 0
    face_count = 0
    for line in path.read_text(encoding="ascii").splitlines():
        if line.startswith("v "):
            vertex_count += 1
        elif line.startswith("vn "):
            normal_count += 1
        elif line.startswith("f "):
            if len(line.split()) != 4:
                raise SystemExit("D2e OBJ contains non-triangle face")
            face_count += 1

    if vertex_count != vertices:
        raise SystemExit("D2e OBJ vertex count mismatch")
    if normal_count != vertices:
        raise SystemExit("D2e OBJ normal count mismatch")
    if face_count != triangles:
        raise SystemExit("D2e OBJ triangle count mismatch")


def validate_ply(
    path: pathlib.Path,
    vertices: int,
    triangles: int,
) -> None:
    lines = path.read_text(encoding="ascii").splitlines()
    if not lines or lines[0] != "ply":
        raise SystemExit("D2e PLY magic is invalid")
    if len(lines) < 12 or lines[1] != "format ascii 1.0":
        raise SystemExit("D2e PLY header is invalid")
    if f"element vertex {vertices}" not in lines:
        raise SystemExit("D2e PLY vertex count missing")
    if f"element face {triangles}" not in lines:
        raise SystemExit("D2e PLY face count missing")

    try:
        end_header = lines.index("end_header")
    except ValueError as exc:
        raise SystemExit("D2e PLY has no end_header") from exc

    payload = lines[end_header + 1:]
    if len(payload) != vertices + triangles:
        raise SystemExit("D2e PLY row count mismatch")

    for row in payload[:vertices]:
        if len(row.split()) != 6:
            raise SystemExit("D2e PLY vertex row must contain XYZ+normal")

    for row in payload[vertices:]:
        parts = row.split()
        if len(parts) != 4 or parts[0] != "3":
            raise SystemExit("D2e PLY face row is not a triangle")


def validate_output(
    directory: pathlib.Path,
    source_dir: pathlib.Path,
    camera_dir: pathlib.Path,
    description: dict[str, Any],
) -> dict[str, Any]:
    manifest_path = directory / description["mesh_manifest_filename"]
    glb_path = directory / description["glb_filename"]
    obj_path = directory / description["obj_filename"]
    ply_path = directory / description["ply_filename"]
    telemetry_path = directory / description["telemetry_filename"]

    for required in (
        manifest_path,
        glb_path,
        obj_path,
        ply_path,
        telemetry_path,
    ):
        if not required.is_file():
            raise SystemExit(f"D2e output missing: {required}")

    manifest = read_json(manifest_path)
    telemetry = read_json(telemetry_path)
    camera = read_json(camera_dir / "camera.json")

    if manifest.get("schema") != "evoscene.mesh.v1":
        raise SystemExit("D2e mesh manifest schema mismatch")
    if manifest.get("schema_version") != description["schema_version"]:
        raise SystemExit("D2e schema version mismatch")
    if manifest.get("adapter_version") != description["adapter_version"]:
        raise SystemExit("D2e adapter version mismatch")

    config = manifest.get("config") or {}
    required_config = {
        "surface_rule": description["surface_rule"],
        "raster_rule": description["raster_rule"],
        "triangle_rule": description["triangle_rule"],
        "normal_rule": description["normal_rule"],
        "coordinate_convention": description["coordinate_convention"],
        "vertex_rule": description["vertex_rule"],
        "ordering": description["canonical_ordering"],
        "max_pixel_edge": description["canonical_max_pixel_edge"],
        "max_depth_jump_m": description["canonical_max_depth_jump_m"],
        "relative_depth_jump": description["canonical_relative_depth_jump"],
        "scipy_version": description["scipy_version"],
    }
    for key, wanted in required_config.items():
        if config.get(key) != wanted:
            raise SystemExit(
                f"D2e config mismatch {key}: "
                f"{config.get(key)!r} != {wanted!r}"
            )

    parents = manifest.get("parents") or {}
    expected_parents = {
        "source_manifest_sha256": sha256_file(source_dir / "fusion.json"),
        "source_points_sha256": sha256_file(
            source_dir / "refined_points.f32le"
        ),
        "camera_prior_sha256": sha256_file(camera_dir / "prior.json"),
        "camera_sha256": sha256_file(camera_dir / "camera.json"),
    }
    for key, wanted in expected_parents.items():
        if parents.get(key) != wanted:
            raise SystemExit(
                f"D2e parent provenance mismatch {key}: "
                f"{parents.get(key)!r} != {wanted!r}"
            )

    summary = manifest.get("summary") or {}
    source_points = int(summary.get("source_points", 0))
    projected = int(summary.get("projected_source_samples", 0))
    visible = int(summary.get("zbuffer_visible_samples", 0))
    vertices = int(summary.get("vertices", 0))
    triangles = int(summary.get("triangles", 0))
    indices = int(summary.get("indices", 0))
    components = int(summary.get("connected_components", 0))

    if not (0 < vertices <= visible <= projected <= source_points):
        raise SystemExit(
            "D2e source/projected/visible/vertex count ordering failed"
        )
    if triangles <= 0 or indices != triangles * 3:
        raise SystemExit("D2e triangle/index accounting failed")
    if components <= 0 or components > vertices:
        raise SystemExit("D2e connected-component count invalid")

    bounds = summary.get("bounds_m") or {}
    minimum = [float(v) for v in bounds.get("min", [])]
    maximum = [float(v) for v in bounds.get("max", [])]
    if len(minimum) != 3 or len(maximum) != 3:
        raise SystemExit("D2e mesh bounds are not 3D")
    if any(hi <= lo for lo, hi in zip(minimum, maximum)):
        raise SystemExit("D2e mesh bounds are degenerate")
    if minimum[2] <= 0.0:
        raise SystemExit("D2e mesh has non-positive source-camera depth")

    artifacts = manifest.get("artifacts") or {}
    for key, path in (
        ("glb", glb_path),
        ("obj", obj_path),
        ("ply", ply_path),
    ):
        meta = artifacts.get(key) or {}
        if int(meta.get("bytes", -1)) != path.stat().st_size:
            raise SystemExit(f"D2e {key} byte count mismatch")
        if meta.get("sha256") != sha256_file(path):
            raise SystemExit(f"D2e {key} SHA-256 mismatch")

    validate_glb(
        glb_path,
        camera,
        vertices,
        indices,
        triangles,
        components,
        bounds,
        description,
    )
    validate_obj(obj_path, vertices, triangles)
    validate_ply(ply_path, vertices, triangles)

    if telemetry.get("schema") != "evoscene.mesh.telemetry.v1":
        raise SystemExit("D2e telemetry schema mismatch")
    elapsed = int(telemetry.get("mesh_finalization_ms", -1))
    if elapsed < 0:
        raise SystemExit("D2e timing is invalid")

    return {
        "manifest_sha256": sha256_file(manifest_path),
        "glb_sha256": sha256_file(glb_path),
        "obj_sha256": sha256_file(obj_path),
        "ply_sha256": sha256_file(ply_path),
        "source_points": source_points,
        "visible_samples": visible,
        "vertices": vertices,
        "triangles": triangles,
        "components": components,
        "bounds": bounds,
        "elapsed_ms": elapsed,
    }


def run_live(
    source_dir: pathlib.Path,
    camera_dir: pathlib.Path,
    output_root: pathlib.Path,
    description: dict[str, Any],
) -> None:
    source_dir = source_dir.expanduser().resolve()
    camera_dir = camera_dir.expanduser().resolve()
    output_root = output_root.expanduser().resolve()

    if not (source_dir / "fusion.json").is_file():
        raise SystemExit(
            f"D2c final state missing at {source_dir}. "
            "Run tools/verify_evoscene_d2d.py --live first."
        )
    if not (camera_dir / "camera.json").is_file():
        raise SystemExit(
            f"D2a camera missing at {camera_dir}. "
            "Run tools/verify_evoscene_d2a.py --live first."
        )

    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True)

    results = []
    for label in ("run-a", "run-b"):
        output = output_root / label
        proc = run(
            sys.executable,
            str(ADAPTER),
            "--source-dir",
            str(source_dir),
            "--camera-dir",
            str(camera_dir),
            "--output",
            str(output),
            capture=True,
            timeout_s=1200,
        )
        response = json.loads(proc.stdout)
        if response.get("status") != "ok":
            raise SystemExit(f"D2e adapter response failed: {response}")
        results.append(
            validate_output(
                output,
                source_dir,
                camera_dir,
                description,
            )
        )

    first, second = results
    for key in (
        "manifest_sha256",
        "glb_sha256",
        "obj_sha256",
        "ply_sha256",
    ):
        if first[key] != second[key]:
            raise SystemExit(
                f"D2e repeated finalization differs for {key}: "
                f"{first[key]} != {second[key]}"
            )

    print(f"D2e source_points: {first['source_points']}")
    print(f"D2e zbuffer_visible_samples: {first['visible_samples']}")
    print(f"D2e vertices: {first['vertices']}")
    print(f"D2e triangles: {first['triangles']}")
    print(f"D2e connected_components: {first['components']}")
    print(f"D2e glb_sha256: {first['glb_sha256']}")
    print(f"D2e obj_sha256: {first['obj_sha256']}")
    print(f"D2e ply_sha256: {first['ply_sha256']}")
    print(f"D2e manifest_sha256: {first['manifest_sha256']}")
    print(
        "D2e bounds_min_m: "
        + ",".join(str(v) for v in first["bounds"]["min"])
    )
    print(
        "D2e bounds_max_m: "
        + ",".join(str(v) for v in first["bounds"]["max"])
    )
    print(f"D2e mesh_finalization_ms: {first['elapsed_ms']}")
    print("D2e welded_topology: yes")
    print("D2e byte_identical_repeat: yes")
    print("D2e GLB validation: PASS")
    print("D2e OBJ validation: PASS")
    print("D2e PLY validation: PASS")
    print("D2e LIVE PASS: deterministic projected surface generation complete")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--source-dir",
        type=pathlib.Path,
        default=DEFAULT_SOURCE,
    )
    parser.add_argument(
        "--camera-dir",
        type=pathlib.Path,
        default=DEFAULT_CAMERA,
    )
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=DEFAULT_OUTPUT,
    )
    args = parser.parse_args()

    description = validate_static_contract()

    if not args.live:
        print("D2e STRUCTURAL PASS: Zig/Python projected-surface contracts agree")
        return 0

    run_live(
        args.source_dir,
        args.camera_dir,
        args.output,
        description,
    )

    run("zig", "build", "test", timeout_s=1800)
    print("D2e REPOSITORY PASS: full Zig test suite complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
