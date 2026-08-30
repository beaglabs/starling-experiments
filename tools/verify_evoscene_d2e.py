#!/usr/bin/env python3
"""D2e deterministic mesh-finalization engineering/live gate."""

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
        "canonical_voxel_size_mm": str(
            round(float(description["canonical_voxel_size_m"]) * 1000)
        ),
        "surface_rule": str(description["surface_rule"]),
        "face_order": str(description["face_order"]),
        "triangle_rule": str(description["triangle_rule"]),
        "coordinate_convention": str(description["coordinate_convention"]),
        "vertex_rule": str(description["vertex_rule"]),
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
        "D2e CONTRACT PASS: deterministic mesh-finalization boundary frozen"
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


def validate_glb(
    path: pathlib.Path,
    vertices: int,
    indices: int,
    triangles: int,
    expected_bounds: dict[str, Any],
) -> None:
    gltf, binary = parse_glb(path)

    asset = gltf.get("asset") or {}
    if asset.get("version") != "2.0":
        raise SystemExit("D2e GLB asset version is not 2.0")

    if gltf.get("scene") != 0:
        raise SystemExit("D2e GLB default scene is not zero")
    scenes = gltf.get("scenes")
    nodes = gltf.get("nodes")
    meshes = gltf.get("meshes")
    accessors = gltf.get("accessors")
    views = gltf.get("bufferViews")
    buffers = gltf.get("buffers")
    if not all(
        isinstance(value, list)
        for value in (scenes, nodes, meshes, accessors, views, buffers)
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
        raise SystemExit("D2e GLB POSITION type is not VEC3")
    if int(position_accessor.get("count", -1)) != vertices:
        raise SystemExit("D2e GLB POSITION count mismatch")

    if normal_accessor.get("componentType") != 5126:
        raise SystemExit("D2e GLB NORMAL is not float32")
    if normal_accessor.get("type") != "VEC3":
        raise SystemExit("D2e GLB NORMAL type is not VEC3")
    if int(normal_accessor.get("count", -1)) != vertices:
        raise SystemExit("D2e GLB NORMAL count mismatch")

    if index_accessor.get("componentType") != 5125:
        raise SystemExit("D2e GLB indices are not uint32")
    if index_accessor.get("type") != "SCALAR":
        raise SystemExit("D2e GLB index type is not SCALAR")
    if int(index_accessor.get("count", -1)) != indices:
        raise SystemExit("D2e GLB index count mismatch")
    if indices != triangles * 3:
        raise SystemExit("D2e GLB triangle/index accounting mismatch")

    primitive = meshes[0]["primitives"][0]
    if primitive.get("mode") != 4:
        raise SystemExit("D2e GLB primitive is not TRIANGLES")
    if primitive.get("indices") != 2:
        raise SystemExit("D2e GLB primitive index accessor mismatch")
    attrs = primitive.get("attributes") or {}
    if attrs.get("POSITION") != 0 or attrs.get("NORMAL") != 1:
        raise SystemExit("D2e GLB primitive attributes mismatch")

    buffer_length = int(buffers[0].get("byteLength", -1))
    expected_binary = vertices * 12 + vertices * 12 + indices * 4
    if buffer_length != expected_binary:
        raise SystemExit(
            f"D2e GLB buffer length {buffer_length} != {expected_binary}"
        )
    if len(binary) < buffer_length or len(binary) > buffer_length + 3:
        raise SystemExit("D2e GLB BIN chunk padding is invalid")

    position_view = views[0]
    normal_view = views[1]
    index_view = views[2]
    if int(position_view.get("byteOffset", -1)) != 0:
        raise SystemExit("D2e GLB POSITION offset mismatch")
    if int(position_view.get("byteLength", -1)) != vertices * 12:
        raise SystemExit("D2e GLB POSITION byteLength mismatch")
    if int(normal_view.get("byteOffset", -1)) != vertices * 12:
        raise SystemExit("D2e GLB NORMAL offset mismatch")
    if int(normal_view.get("byteLength", -1)) != vertices * 12:
        raise SystemExit("D2e GLB NORMAL byteLength mismatch")
    if int(index_view.get("byteOffset", -1)) != vertices * 24:
        raise SystemExit("D2e GLB index offset mismatch")
    if int(index_view.get("byteLength", -1)) != indices * 4:
        raise SystemExit("D2e GLB index byteLength mismatch")

    decoded_positions = list(
        struct.iter_unpack("<fff", binary[:vertices * 12])
    )
    if len(decoded_positions) != vertices:
        raise SystemExit("D2e GLB decoded POSITION count mismatch")
    if any(
        not all(math.isfinite(value) for value in point)
        for point in decoded_positions
    ):
        raise SystemExit("D2e GLB contains non-finite position")

    normal_start = vertices * 12
    normal_end = normal_start + vertices * 12
    decoded_normals = list(
        struct.iter_unpack("<fff", binary[normal_start:normal_end])
    )
    for normal in decoded_normals:
        length = math.sqrt(sum(value * value for value in normal))
        if abs(length - 1.0) > 1e-7:
            raise SystemExit("D2e GLB contains non-unit normal")

    index_start = normal_end
    decoded_indices = [
        value[0]
        for value in struct.iter_unpack(
            "<I",
            binary[index_start:index_start + indices * 4],
        )
    ]
    if len(decoded_indices) != indices:
        raise SystemExit("D2e GLB decoded index count mismatch")
    if min(decoded_indices) < 0 or max(decoded_indices) >= vertices:
        raise SystemExit("D2e GLB index outside vertex range")

    minimum = [
        min(point[axis] for point in decoded_positions)
        for axis in range(3)
    ]
    maximum = [
        max(point[axis] for point in decoded_positions)
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
            parts = line.split()[1:]
            if len(parts) != 3:
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
        parts = row.split()
        if len(parts) != 6:
            raise SystemExit("D2e PLY vertex row does not contain XYZ+normal")

    for row in payload[vertices:]:
        parts = row.split()
        if len(parts) != 4 or parts[0] != "3":
            raise SystemExit("D2e PLY face row is not a triangle")


def validate_output(
    directory: pathlib.Path,
    source_dir: pathlib.Path,
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

    if manifest.get("schema") != "evoscene.mesh.v1":
        raise SystemExit("D2e mesh manifest schema mismatch")
    if manifest.get("schema_version") != description["schema_version"]:
        raise SystemExit("D2e schema version mismatch")
    if manifest.get("adapter_version") != description["adapter_version"]:
        raise SystemExit("D2e adapter version mismatch")

    config = manifest.get("config") or {}
    required_config = {
        "voxel_size_m": description["canonical_voxel_size_m"],
        "surface_rule": description["surface_rule"],
        "face_order": description["face_order"],
        "triangle_rule": description["triangle_rule"],
        "coordinate_convention": description["coordinate_convention"],
        "vertex_rule": description["vertex_rule"],
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
    }
    for key, wanted in expected_parents.items():
        if parents.get(key) != wanted:
            raise SystemExit(
                f"D2e parent provenance mismatch {key}: "
                f"{parents.get(key)!r} != {wanted!r}"
            )

    summary = manifest.get("summary") or {}
    source_points = int(summary.get("source_points", 0))
    occupied = int(summary.get("occupied_voxels", 0))
    quads = int(summary.get("exposed_quads", 0))
    vertices = int(summary.get("vertices", 0))
    triangles = int(summary.get("triangles", 0))
    indices = int(summary.get("indices", 0))

    if source_points <= 0 or occupied <= 0 or quads <= 0:
        raise SystemExit("D2e source/voxel/quad counts are invalid")
    if occupied > source_points:
        raise SystemExit("D2e occupied voxel count exceeds source points")
    if vertices != quads * 4:
        raise SystemExit("D2e vertex/quad accounting identity failed")
    if triangles != quads * 2:
        raise SystemExit("D2e triangle/quad accounting identity failed")
    if indices != triangles * 3:
        raise SystemExit("D2e index/triangle accounting identity failed")

    bounds = summary.get("bounds_m") or {}
    minimum = [float(v) for v in bounds.get("min", [])]
    maximum = [float(v) for v in bounds.get("max", [])]
    if len(minimum) != 3 or len(maximum) != 3:
        raise SystemExit("D2e mesh bounds are not 3D")
    if any(hi <= lo for lo, hi in zip(minimum, maximum)):
        raise SystemExit("D2e mesh bounds are degenerate")

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

    validate_glb(glb_path, vertices, indices, triangles, bounds)
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
        "occupied_voxels": occupied,
        "exposed_quads": quads,
        "vertices": vertices,
        "triangles": triangles,
        "bounds": bounds,
        "elapsed_ms": elapsed,
    }


def run_live(
    source_dir: pathlib.Path,
    output_root: pathlib.Path,
    description: dict[str, Any],
) -> None:
    source_dir = source_dir.expanduser().resolve()
    output_root = output_root.expanduser().resolve()

    if not (source_dir / "fusion.json").is_file():
        raise SystemExit(
            f"D2c final state missing at {source_dir}. "
            "Run tools/verify_evoscene_d2d.py --live first."
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
            "--output",
            str(output),
            "--voxel-size-m",
            str(description["canonical_voxel_size_m"]),
            capture=True,
            timeout_s=1200,
        )
        response = json.loads(proc.stdout)
        if response.get("status") != "ok":
            raise SystemExit(f"D2e adapter response failed: {response}")
        results.append(validate_output(output, source_dir, description))

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
    print(f"D2e occupied_voxels: {first['occupied_voxels']}")
    print(f"D2e exposed_quads: {first['exposed_quads']}")
    print(f"D2e vertices: {first['vertices']}")
    print(f"D2e triangles: {first['triangles']}")
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
    print("D2e byte_identical_repeat: yes")
    print("D2e GLB validation: PASS")
    print("D2e OBJ validation: PASS")
    print("D2e PLY validation: PASS")
    print("D2e LIVE PASS: deterministic final mesh generation complete")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--source-dir",
        type=pathlib.Path,
        default=DEFAULT_SOURCE,
    )
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=DEFAULT_OUTPUT,
    )
    args = parser.parse_args()

    description = validate_static_contract()

    if not args.live:
        print("D2e STRUCTURAL PASS: Zig/Python mesh contracts agree")
        return 0

    run_live(args.source_dir, args.output, description)

    run("zig", "build", "test", timeout_s=1800)
    print("D2e REPOSITORY PASS: full Zig test suite complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
