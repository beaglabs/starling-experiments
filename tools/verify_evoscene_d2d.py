#!/usr/bin/env python3
"""D2d deterministic novel-view render/reprojection + closed-loop gate."""

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
ADAPTER = ROOT / "tools" / "evoscene_render_reproject.py"
D2C_ADAPTER = ROOT / "tools" / "evoscene_fuse_refine.py"

DEFAULT_SOURCE = ROOT / "trials" / "evoscene-d2c-smoke" / "run-a"
DEFAULT_CAMERA = ROOT / "trials" / "evoscene-d2a-smoke" / "run-a"
DEFAULT_OUTPUT = ROOT / "trials" / "evoscene-d2d-smoke"


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
            f"D2d gate failed: {' '.join(args)} exit={proc.returncode}"
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

    run("zig", "build", "test-demo-evoscene-d2d", timeout_s=600)

    proc = run(
        "zig",
        "build",
        "-Doptimize=ReleaseFast",
        "run-demo-evoscene-d2d",
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
        "pose_a_azimuth_mdeg": str(description["pose_a_azimuth_mdeg"]),
        "pose_a_elevation_mdeg": str(description["pose_a_elevation_mdeg"]),
        "pose_b_azimuth_mdeg": str(description["pose_b_azimuth_mdeg"]),
        "pose_b_elevation_mdeg": str(description["pose_b_elevation_mdeg"]),
        "raster_rule": str(description["raster_rule"]),
        "pixel_convention": str(description["pixel_convention"]),
        "camera_convention": str(description["camera_convention"]),
        "orbit_rule": str(description["orbit_rule"]),
        "evidence_rule": str(description["evidence_rule"]),
    }
    for key, wanted in expected.items():
        actual = contract.get(key)
        if actual != wanted:
            raise SystemExit(
                f"D2d Zig/Python contract mismatch {key}: "
                f"{actual!r} != {wanted!r}"
            )

    payload_a = (
        int(description["pose_a_azimuth_mdeg"]) << 32
    ) | int(description["pose_a_elevation_mdeg"])
    payload_b = (
        int(description["pose_b_azimuth_mdeg"]) << 32
    ) | int(description["pose_b_elevation_mdeg"])
    if contract.get("pose_a_payload_u64") != str(payload_a):
        raise SystemExit("D2d pose A payload mismatch")
    if contract.get("pose_b_payload_u64") != str(payload_b):
        raise SystemExit("D2d pose B payload mismatch")
    if contract.get("pose_encoding") != "PASS":
        raise SystemExit("D2d Zig pose encoding did not pass")
    if not contract_text.rstrip().endswith(
        "D2d CONTRACT PASS: deterministic novel-view boundary frozen"
    ):
        raise SystemExit("D2d Zig contract verdict missing")

    return description


def norm(values: list[float]) -> float:
    return math.sqrt(sum(value * value for value in values))


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def validate_basis(view: dict[str, Any]) -> None:
    right = [float(v) for v in view["camera_right"]]
    down = [float(v) for v in view["camera_down"]]
    forward = [float(v) for v in view["camera_forward"]]

    for name, vector in (
        ("right", right),
        ("down", down),
        ("forward", forward),
    ):
        if len(vector) != 3:
            raise SystemExit(f"D2d camera {name} is not 3D")
        if abs(norm(vector) - 1.0) > 1e-8:
            raise SystemExit(f"D2d camera {name} is not unit length")

    if abs(dot(right, down)) > 1e-8:
        raise SystemExit("D2d camera right/down are not orthogonal")
    if abs(dot(right, forward)) > 1e-8:
        raise SystemExit("D2d camera right/forward are not orthogonal")
    if abs(dot(down, forward)) > 1e-8:
        raise SystemExit("D2d camera down/forward are not orthogonal")


def validate_render(
    directory: pathlib.Path,
    source_dir: pathlib.Path,
    camera_dir: pathlib.Path,
    description: dict[str, Any],
    azimuth_mdeg: int,
    elevation_mdeg: int,
) -> dict[str, Any]:
    render_path = directory / description["render_manifest_filename"]
    scene_path = directory / description["scene_filename"]
    depth_path = directory / description["depth_filename"]
    mask_path = directory / description["mask_filename"]
    points_path = directory / description["points_filename"]
    telemetry_path = directory / description["telemetry_filename"]

    for required in (
        render_path,
        scene_path,
        depth_path,
        mask_path,
        points_path,
        telemetry_path,
    ):
        if not required.is_file():
            raise SystemExit(f"D2d output missing: {required}")

    render = read_json(render_path)
    scene = read_json(scene_path)
    telemetry = read_json(telemetry_path)
    camera = read_json(camera_dir / "camera.json")

    if render.get("schema") != "evoscene.novel_view.v1":
        raise SystemExit("D2d render schema mismatch")
    if render.get("schema_version") != description["schema_version"]:
        raise SystemExit("D2d render schema version mismatch")
    if render.get("adapter_version") != description["adapter_version"]:
        raise SystemExit("D2d adapter version mismatch")

    config = render.get("config") or {}
    expected_config = {
        "raster_rule": description["raster_rule"],
        "pixel_convention": description["pixel_convention"],
        "camera_convention": description["camera_convention"],
        "orbit_rule": description["orbit_rule"],
        "evidence_rule": description["evidence_rule"],
    }
    for key, wanted in expected_config.items():
        if config.get(key) != wanted:
            raise SystemExit(
                f"D2d config mismatch {key}: "
                f"{config.get(key)!r} != {wanted!r}"
            )

    source_manifest = source_dir / "fusion.json"
    source_points = source_dir / "refined_points.f32le"
    prior = camera_dir / "prior.json"
    camera_path = camera_dir / "camera.json"

    parents = render.get("parents") or {}
    expected_parents = {
        "source_manifest_sha256": sha256_file(source_manifest),
        "source_points_sha256": sha256_file(source_points),
        "camera_prior_sha256": sha256_file(prior),
        "camera_sha256": sha256_file(camera_path),
    }
    for key, wanted in expected_parents.items():
        if parents.get(key) != wanted:
            raise SystemExit(
                f"D2d parent provenance mismatch {key}: "
                f"{parents.get(key)!r} != {wanted!r}"
            )

    view = render.get("view") or {}
    if int(view.get("azimuth_mdeg", -1)) != azimuth_mdeg:
        raise SystemExit("D2d azimuth mismatch")
    if int(view.get("elevation_mdeg", -1)) != elevation_mdeg:
        raise SystemExit("D2d elevation mismatch")
    payload = (azimuth_mdeg << 32) | elevation_mdeg
    if int(view.get("payload_u64", -1)) != payload:
        raise SystemExit("D2d pose payload mismatch")
    validate_basis(view)

    width = int(camera["image_width"])
    height = int(camera["image_height"])
    pixels = width * height
    image = render.get("image") or {}
    valid_pixels = int(image.get("valid_pixels", 0))
    if int(image.get("width", 0)) != width:
        raise SystemExit("D2d render width mismatch")
    if int(image.get("height", 0)) != height:
        raise SystemExit("D2d render height mismatch")
    if not 0 < valid_pixels <= pixels:
        raise SystemExit("D2d valid-pixel count is invalid")

    mask = mask_path.read_bytes()
    depth = depth_path.read_bytes()
    points = points_path.read_bytes()
    if len(mask) != pixels:
        raise SystemExit("D2d mask byte size mismatch")
    if len(depth) != pixels * 4:
        raise SystemExit("D2d depth byte size mismatch")
    if len(points) != valid_pixels * 12:
        raise SystemExit("D2d evidence point byte size mismatch")
    if any(value not in (0, 1) for value in mask):
        raise SystemExit("D2d mask contains values outside {0,1}")
    if sum(mask) != valid_pixels:
        raise SystemExit("D2d mask valid count mismatch")

    decoded_depth = struct.iter_unpack("<f", depth)
    for index, (value,) in enumerate(decoded_depth):
        if mask[index]:
            if not math.isfinite(value) or value <= 0.0:
                raise SystemExit("D2d valid pixel has invalid depth")
        elif value != 0.0:
            raise SystemExit("D2d invalid pixel depth is not canonical zero")

    artifacts = render.get("artifacts") or {}
    file_map = {
        "depth": depth_path,
        "mask": mask_path,
        "points": points_path,
        "scene": scene_path,
    }
    for key, path in file_map.items():
        meta = artifacts.get(key) or {}
        if int(meta.get("bytes", -1)) != path.stat().st_size:
            raise SystemExit(f"D2d {key} byte count mismatch")
        if meta.get("sha256") != sha256_file(path):
            raise SystemExit(f"D2d {key} SHA-256 mismatch")

    if scene.get("schema") != "evoscene.point_cloud.v1":
        raise SystemExit("D2d evidence scene schema mismatch")
    scene_points = (scene.get("artifacts") or {}).get("points") or {}
    if scene_points.get("sha256") != sha256_file(points_path):
        raise SystemExit("D2d evidence scene points SHA mismatch")
    if int((scene.get("summary") or {}).get("point_count", 0)) != valid_pixels:
        raise SystemExit("D2d evidence scene point count mismatch")

    bounds = (scene.get("summary") or {}).get("bounds_m") or {}
    minimum = [float(v) for v in bounds.get("min", [])]
    maximum = [float(v) for v in bounds.get("max", [])]
    if len(minimum) != 3 or len(maximum) != 3:
        raise SystemExit("D2d evidence bounds are not 3D")
    if any(hi <= lo for lo, hi in zip(minimum, maximum)):
        raise SystemExit("D2d evidence cloud is degenerate")

    if telemetry.get("schema") != "evoscene.novel_view.telemetry.v1":
        raise SystemExit("D2d telemetry schema mismatch")
    elapsed = int(telemetry.get("render_reprojection_ms", -1))
    if elapsed < 0:
        raise SystemExit("D2d timing is invalid")

    return {
        "render_sha256": sha256_file(render_path),
        "scene_sha256": sha256_file(scene_path),
        "depth_sha256": sha256_file(depth_path),
        "mask_sha256": sha256_file(mask_path),
        "points_sha256": sha256_file(points_path),
        "valid_pixels": valid_pixels,
        "valid_fraction": float(image["valid_fraction"]),
        "bounds": bounds,
        "elapsed_ms": elapsed,
    }


def run_renderer(
    source_dir: pathlib.Path,
    camera_dir: pathlib.Path,
    output: pathlib.Path,
    azimuth_mdeg: int,
    elevation_mdeg: int,
) -> None:
    run(
        sys.executable,
        str(ADAPTER),
        "--source-dir",
        str(source_dir),
        "--camera-dir",
        str(camera_dir),
        "--output",
        str(output),
        "--azimuth-mdeg",
        str(azimuth_mdeg),
        "--elevation-mdeg",
        str(elevation_mdeg),
        capture=True,
        timeout_s=600,
    )


def validate_closed_loop(
    source_dir: pathlib.Path,
    evidence_a: pathlib.Path,
    evidence_b: pathlib.Path,
    output_root: pathlib.Path,
) -> dict[str, Any]:
    d2c_description = json.loads(
        run(
            sys.executable,
            str(D2C_ADAPTER),
            "--describe",
            capture=True,
            timeout_s=120,
        ).stdout
    )
    voxel = str(d2c_description["canonical_voxel_size_m"])
    neighbors = str(d2c_description["canonical_min_neighbors"])

    outputs = []
    for label, evidence in (("a", evidence_a), ("b", evidence_b)):
        output = output_root / f"closed-loop-{label}"
        run(
            sys.executable,
            str(D2C_ADAPTER),
            "--source-dir",
            str(source_dir),
            "--evidence-dir",
            str(evidence),
            "--output",
            str(output),
            "--voxel-size-m",
            voxel,
            "--min-neighbors",
            neighbors,
            capture=True,
            timeout_s=600,
        )
        manifest_path = output / "fusion.json"
        refined_path = output / "refined_points.f32le"
        if not manifest_path.is_file() or not refined_path.is_file():
            raise SystemExit("D2d closed-loop D2c output missing")

        manifest = read_json(manifest_path)
        summary = manifest.get("summary") or {}
        refined_count = int(summary.get("refined_voxels", 0))
        if refined_count <= 0:
            raise SystemExit("D2d closed loop produced empty refined cloud")

        parents = manifest.get("parents") or {}
        if parents.get("source_scene_sha256") != sha256_file(
            source_dir / "fusion.json"
        ):
            raise SystemExit("D2d closed-loop source provenance mismatch")
        if parents.get("evidence_scene_sha256") != sha256_file(
            evidence / "scene.json"
        ):
            raise SystemExit("D2d closed-loop evidence provenance mismatch")

        outputs.append(
            {
                "manifest_sha256": sha256_file(manifest_path),
                "refined_sha256": sha256_file(refined_path),
                "refined_count": refined_count,
                "removed": int(summary.get("removed_voxels", -1)),
            }
        )

    first, second = outputs
    if first["manifest_sha256"] != second["manifest_sha256"]:
        raise SystemExit("D2d closed-loop fusion manifest is not repeatable")
    if first["refined_sha256"] != second["refined_sha256"]:
        raise SystemExit("D2d closed-loop refined geometry is not repeatable")

    return first


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
            f"D2c source missing at {source_dir}. "
            "Run tools/verify_evoscene_d2c.py --live first."
        )
    if not (camera_dir / "camera.json").is_file():
        raise SystemExit(
            f"D2a camera missing at {camera_dir}. "
            "Run tools/verify_evoscene_d2a.py --live first."
        )

    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True)

    pose_a1 = output_root / "pose-a-run-a"
    pose_a2 = output_root / "pose-a-run-b"
    pose_b = output_root / "pose-b"

    az_a = int(description["pose_a_azimuth_mdeg"])
    el_a = int(description["pose_a_elevation_mdeg"])
    az_b = int(description["pose_b_azimuth_mdeg"])
    el_b = int(description["pose_b_elevation_mdeg"])

    run_renderer(source_dir, camera_dir, pose_a1, az_a, el_a)
    run_renderer(source_dir, camera_dir, pose_a2, az_a, el_a)
    run_renderer(source_dir, camera_dir, pose_b, az_b, el_b)

    first = validate_render(
        pose_a1, source_dir, camera_dir, description, az_a, el_a
    )
    second = validate_render(
        pose_a2, source_dir, camera_dir, description, az_a, el_a
    )
    other = validate_render(
        pose_b, source_dir, camera_dir, description, az_b, el_b
    )

    for key in (
        "render_sha256",
        "scene_sha256",
        "depth_sha256",
        "mask_sha256",
        "points_sha256",
    ):
        if first[key] != second[key]:
            raise SystemExit(
                f"D2d repeated pose A differs for {key}: "
                f"{first[key]} != {second[key]}"
            )

    if first["render_sha256"] == other["render_sha256"]:
        raise SystemExit("D2d pose A/B renders are unexpectedly identical")
    if first["points_sha256"] == other["points_sha256"]:
        raise SystemExit("D2d pose A/B evidence clouds are unexpectedly identical")

    closed = validate_closed_loop(
        source_dir,
        pose_a1,
        pose_a2,
        output_root,
    )

    print(f"D2d pose_a_valid_pixels: {first['valid_pixels']}")
    print(f"D2d pose_a_valid_fraction: {first['valid_fraction']}")
    print(f"D2d pose_a_depth_sha256: {first['depth_sha256']}")
    print(f"D2d pose_a_mask_sha256: {first['mask_sha256']}")
    print(f"D2d pose_a_points_sha256: {first['points_sha256']}")
    print(f"D2d pose_a_scene_sha256: {first['scene_sha256']}")
    print(f"D2d pose_a_render_sha256: {first['render_sha256']}")
    print(f"D2d pose_b_valid_pixels: {other['valid_pixels']}")
    print(f"D2d pose_b_points_sha256: {other['points_sha256']}")
    print(f"D2d pose_b_render_sha256: {other['render_sha256']}")
    print(f"D2d closed_loop_refined_voxels: {closed['refined_count']}")
    print(f"D2d closed_loop_removed_voxels: {closed['removed']}")
    print(f"D2d closed_loop_refined_sha256: {closed['refined_sha256']}")
    print("D2d byte_identical_repeat: yes")
    print("D2d pose_sensitive: yes")
    print("D2d closed_loop_repeat: yes")
    print(
        "D2d LIVE PASS: deterministic novel-view render/reprojection complete"
    )


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
        print("D2d STRUCTURAL PASS: Zig/Python novel-view contracts agree")
        return 0

    run_live(
        args.source_dir,
        args.camera_dir,
        args.output,
        description,
    )

    run("zig", "build", "test", timeout_s=1800)
    print("D2d REPOSITORY PASS: full Zig test suite complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
