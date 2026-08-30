#!/usr/bin/env python3
"""D2f learned novel-view + geometry-ingestion + fusion end-to-end gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import statistics
import struct
import subprocess
import sys
from typing import Any, Optional

ROOT = pathlib.Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "tools" / "evoscene_metaview_generate.py"
MOGE = ROOT / "tools" / "evoscene_moge_prior.py"
BRIDGE = ROOT / "tools" / "evoscene_ingest_learned_view.py"
FUSION = ROOT / "tools" / "evoscene_fuse_refine.py"
FINALIZER = ROOT / "tools" / "evoscene_finalize_multiview.py"
DEPENDENCIES = (
    ROOT / "demos" / "evoscene-emergent" / "metaview-dependencies.json"
)

DEFAULT_OUTPUT = ROOT / "trials" / "evoscene-d2f-smoke"
DEFAULT_YAW_MDEG = 35_000
DEFAULT_PITCH_MDEG = 10_000


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
            f"D2f gate failed: {' '.join(args)} exit={proc.returncode}"
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
    result: dict[str, str] = {}
    for line in text.splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            result[key] = value
    return result


def validate_static_contract() -> dict[str, Any]:
    if shutil.which("zig") is None:
        raise SystemExit("zig 0.16.0 is required on PATH")

    version = run("zig", "version", capture=True).stdout.decode().strip()
    if version != "0.16.0":
        raise SystemExit(f"zig 0.16.0 is required; found {version}")

    run("zig", "build", "test-demo-evoscene-d2f", timeout_s=600)

    proc = run(
        "zig",
        "build",
        "-Doptimize=ReleaseFast",
        "run-demo-evoscene-d2f",
        "--",
        "validate",
        capture=True,
        timeout_s=600,
    )
    text = proc.stdout.decode()
    print(text, end="" if text.endswith("\n") else "\n")
    contract = parse_contract(text)

    for tool in (GENERATOR, BRIDGE, FINALIZER):
        result = run(
            sys.executable,
            str(tool),
            "--self-test",
            capture=True,
            timeout_s=180,
        )
        output = result.stdout.decode()
        print(output, end="" if output.endswith("\n") else "\n")

    description = json.loads(
        run(
            sys.executable,
            str(GENERATOR),
            "--describe",
            capture=True,
            timeout_s=120,
        ).stdout
    )

    expected = {
        "schema_version": str(description["schema_version"]),
        "adapter_version": str(description["adapter_version"]),
        "backend": str(description["backend"]),
        "metaview_git_commit": str(description["metaview_git_commit"]),
        "metaview_inference_blob_sha1": str(
            description["metaview_inference_blob_sha1"]
        ),
        "metaview_model_repo": str(description["metaview_model_repo"]),
        "metaview_model_sha256": str(description["metaview_model_sha256"]),
        "qwen_image_edit_revision": str(
            description["qwen_image_edit_revision"]
        ),
        "da3_giant_revision": str(description["da3_giant_revision"]),
        "da3_depth_revision": str(description["da3_depth_revision"]),
        "canonical_seed": str(description["canonical_seed"]),
        "canonical_steps": str(description["canonical_steps"]),
        "canonical_width": str(description["canonical_width"]),
        "canonical_height": str(description["canonical_height"]),
        "prompt_id": str(description["prompt_id"]),
        "pose_convention": str(description["pose_convention"]),
        "output_rule": str(description["output_rule"]),
        "cache_key_rule": str(description["cache_key_rule"]),
    }
    for key, wanted in expected.items():
        actual = contract.get(key)
        if actual != wanted:
            raise SystemExit(
                f"D2f Zig/Python contract mismatch {key}: "
                f"{actual!r} != {wanted!r}"
            )

    if not text.rstrip().endswith(
        "D2f CONTRACT PASS: learned novel-view boundary frozen"
    ):
        raise SystemExit("D2f Zig contract verdict missing")

    deps = read_json(DEPENDENCIES)
    if deps.get("schema") != description["dependency_schema"]:
        raise SystemExit("D2f dependency manifest schema mismatch")
    for key in (
        "qwen_image_edit_revision",
        "da3_giant_revision",
        "da3_depth_revision",
    ):
        if deps.get(key) != description[key]:
            raise SystemExit(
                f"D2f dependency manifest mismatch {key}: "
                f"{deps.get(key)!r} != {description[key]!r}"
            )

    return description


def source_radius_m(source_dir: pathlib.Path) -> float:
    points_path = source_dir / "refined_points.f32le"
    manifest_path = source_dir / "fusion.json"
    if not points_path.is_file() or not manifest_path.is_file():
        raise SystemExit("D2f live source must be a D2c refined state")

    manifest = read_json(manifest_path)
    count = int((manifest.get("summary") or {}).get("refined_voxels", -1))
    data = points_path.read_bytes()
    if count <= 0 or len(data) != count * 12:
        raise SystemExit("D2f live source point count is invalid")

    z_values = [
        point[2]
        for point in struct.iter_unpack("<fff", data)
        if point[2] > 0.0
    ]
    if not z_values:
        raise SystemExit("D2f live source has no positive forward depth")

    return round(float(statistics.median(z_values)), 3)


def verify_source_image(
    input_path: pathlib.Path,
    source_camera_dir: pathlib.Path,
) -> None:
    prior_path = source_camera_dir / "prior.json"
    camera_path = source_camera_dir / "camera.json"
    if not prior_path.is_file() or not camera_path.is_file():
        raise SystemExit("D2f source camera directory is incomplete")

    prior = read_json(prior_path)
    expected = ((prior.get("input") or {}).get("sha256"))
    actual = sha256_file(input_path)
    if expected != actual:
        raise SystemExit(
            "D2f original input does not match source D2a prior: "
            f"{actual} != {expected}"
        )


def run_generator(
    args: argparse.Namespace,
    output: pathlib.Path,
    radius_m: float,
) -> dict[str, Any]:
    proc = run(
        sys.executable,
        str(GENERATOR),
        "--input",
        str(args.input),
        "--output",
        str(output),
        "--cache-dir",
        str(args.cache_dir),
        "--yaw-mdeg",
        str(args.yaw_mdeg),
        "--pitch-mdeg",
        str(args.pitch_mdeg),
        "--radius-m",
        f"{radius_m:.3f}",
        "--metaview-root",
        str(args.metaview_root),
        "--metaview-python",
        str(args.metaview_python),
        "--ckpt-path",
        str(args.ckpt_path),
        "--da3-giant-path",
        str(args.da3_giant_path),
        "--da3-depth-path",
        str(args.da3_depth_path),
        "--qwen-path",
        str(args.qwen_path),
        "--dependency-manifest",
        str(DEPENDENCIES),
        capture=True,
        timeout_s=args.generator_timeout_s,
    )
    return json.loads(proc.stdout)


def run_moge(novel: pathlib.Path, output: pathlib.Path) -> None:
    run(
        sys.executable,
        str(MOGE),
        "--input",
        str(novel),
        "--output",
        str(output),
        "--device",
        "cpu",
        "--num-tokens",
        "1200",
        "--threads",
        "1",
        timeout_s=3600,
    )


def run_bridge(
    source_dir: pathlib.Path,
    generation_dir: pathlib.Path,
    prior_dir: pathlib.Path,
    output: pathlib.Path,
) -> dict[str, Any]:
    proc = run(
        sys.executable,
        str(BRIDGE),
        "--source-dir",
        str(source_dir),
        "--generation-dir",
        str(generation_dir),
        "--prior-dir",
        str(prior_dir),
        "--output",
        str(output),
        capture=True,
        timeout_s=1200,
    )
    return json.loads(proc.stdout)


def run_fusion(
    source_dir: pathlib.Path,
    evidence_dir: pathlib.Path,
    output: pathlib.Path,
) -> None:
    description = json.loads(
        run(
            sys.executable,
            str(FUSION),
            "--describe",
            capture=True,
            timeout_s=120,
        ).stdout
    )
    run(
        sys.executable,
        str(FUSION),
        "--source-dir",
        str(source_dir),
        "--evidence-dir",
        str(evidence_dir),
        "--output",
        str(output),
        "--voxel-size-m",
        str(description["canonical_voxel_size_m"]),
        "--min-neighbors",
        str(description["canonical_min_neighbors"]),
        timeout_s=1200,
    )


def run_finalizer(
    source_dir: pathlib.Path,
    output: pathlib.Path,
) -> None:
    run(
        sys.executable,
        str(FINALIZER),
        "--source-dir",
        str(source_dir),
        "--output",
        str(output),
        timeout_s=1200,
    )


def run_live(
    args: argparse.Namespace,
    description: dict[str, Any],
) -> None:
    input_path = args.input.expanduser().resolve()
    source_dir = args.source_dir.expanduser().resolve()
    camera_dir = args.source_camera_dir.expanduser().resolve()
    output_root = args.output.expanduser().resolve()
    args.cache_dir = args.cache_dir.expanduser().resolve()

    if not input_path.is_file():
        raise SystemExit(f"D2f input image missing: {input_path}")
    verify_source_image(input_path, camera_dir)

    radius_m = source_radius_m(source_dir)

    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True)

    generation_a = output_root / "generation-a"
    generation_b = output_root / "generation-b"

    first = run_generator(args, generation_a, radius_m)
    second = run_generator(args, generation_b, radius_m)

    for key in ("cache_key", "novel_sha256", "generation_sha256"):
        if first.get(key) != second.get(key):
            raise SystemExit(
                f"D2f cached repeat differs for {key}: "
                f"{first.get(key)} != {second.get(key)}"
            )
    if not second.get("cache_hit"):
        raise SystemExit("D2f second identical learned-view request missed cache")

    learned_prior = output_root / "learned-prior"
    run_moge(generation_a / "novel.png", learned_prior)

    evidence_a = output_root / "evidence-a"
    evidence_b = output_root / "evidence-b"
    bridge_a = run_bridge(
        source_dir,
        generation_a,
        learned_prior,
        evidence_a,
    )
    bridge_b = run_bridge(
        source_dir,
        generation_b,
        learned_prior,
        evidence_b,
    )

    for key in ("scene_sha256", "evidence_sha256", "points_sha256"):
        if bridge_a.get(key) != bridge_b.get(key):
            raise SystemExit(
                f"D2f bridge repeat differs for {key}: "
                f"{bridge_a.get(key)} != {bridge_b.get(key)}"
            )

    novel_points = int(bridge_a.get("novel_points", 0))
    accepted_points = int(bridge_a.get("accepted_points", 0))
    if novel_points <= 0 or accepted_points <= novel_points:
        raise SystemExit(
            "D2f learned evidence did not contain both novel and aligned "
            "overlap geometry"
        )

    fused_a = output_root / "fused-a"
    fused_b = output_root / "fused-b"
    run_fusion(source_dir, evidence_a, fused_a)
    run_fusion(source_dir, evidence_b, fused_b)

    fused_manifest_a = read_json(fused_a / "fusion.json")
    fused_manifest_b = read_json(fused_b / "fusion.json")
    refined_a = sha256_file(fused_a / "refined_points.f32le")
    refined_b = sha256_file(fused_b / "refined_points.f32le")
    if refined_a != refined_b:
        raise SystemExit("D2f learned fusion is not repeatable")
    if sha256_file(fused_a / "fusion.json") != sha256_file(
        fused_b / "fusion.json"
    ):
        raise SystemExit("D2f learned fusion manifest is not repeatable")

    source_count = int(
        (read_json(source_dir / "fusion.json").get("summary") or {}).get(
            "refined_voxels",
            0,
        )
    )
    fused_count = int(
        (fused_manifest_a.get("summary") or {}).get("refined_voxels", 0)
    )
    if fused_count <= 0:
        raise SystemExit("D2f learned fusion produced empty refined state")

    mesh_a = output_root / "mesh-a"
    mesh_b = output_root / "mesh-b"
    run_finalizer(fused_a, mesh_a)
    run_finalizer(fused_b, mesh_b)

    glb_a = sha256_file(mesh_a / "scene.glb")
    glb_b = sha256_file(mesh_b / "scene.glb")
    if glb_a != glb_b:
        raise SystemExit("D2f final mesh is not repeatable")

    print(f"D2f backend: {description['backend']}")
    print(f"D2f radius_m: {radius_m:.3f}")
    print(f"D2f yaw_mdeg: {args.yaw_mdeg}")
    print(f"D2f pitch_mdeg: {args.pitch_mdeg}")
    print(f"D2f generation_cache_key: {first['cache_key']}")
    print(f"D2f first_generation_cache_hit: {'yes' if first['cache_hit'] else 'no'}")
    print("D2f second_generation_cache_hit: yes")
    print(f"D2f novel_rgb_sha256: {first['novel_sha256']}")
    print(f"D2f learned_depth_scale: {bridge_a['scale']}")
    print(f"D2f accepted_evidence_points: {accepted_points}")
    print(f"D2f novel_evidence_points: {novel_points}")
    print(f"D2f source_refined_voxels: {source_count}")
    print(f"D2f learned_fused_refined_voxels: {fused_count}")
    print(f"D2f learned_fused_sha256: {refined_a}")
    print(f"D2f final_glb_sha256: {glb_a}")
    print("D2f cached_repeat: yes")
    print("D2f bridge_repeat: yes")
    print("D2f learned_fusion_repeat: yes")
    print("D2f LIVE PASS: learned unseen-region reconstruction loop complete")

    run("zig", "build", "test", timeout_s=1800)
    print("D2f REPOSITORY PASS: full Zig test suite complete")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--input", type=pathlib.Path)
    parser.add_argument("--source-dir", type=pathlib.Path)
    parser.add_argument("--source-camera-dir", type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cache-dir", type=pathlib.Path)
    parser.add_argument("--yaw-mdeg", type=int, default=DEFAULT_YAW_MDEG)
    parser.add_argument("--pitch-mdeg", type=int, default=DEFAULT_PITCH_MDEG)
    parser.add_argument("--metaview-root", type=pathlib.Path)
    parser.add_argument("--metaview-python", type=pathlib.Path)
    parser.add_argument("--ckpt-path", type=pathlib.Path)
    parser.add_argument("--da3-giant-path", type=pathlib.Path)
    parser.add_argument("--da3-depth-path", type=pathlib.Path)
    parser.add_argument("--qwen-path", type=pathlib.Path)
    parser.add_argument("--generator-timeout-s", type=int, default=7200)
    args = parser.parse_args()

    description = validate_static_contract()

    if not args.live:
        print(
            "D2f STRUCTURAL PASS: "
            "Zig/MetaView/geometry-bridge contracts agree"
        )
        return 0

    required = (
        "input",
        "source_dir",
        "source_camera_dir",
        "cache_dir",
        "metaview_root",
        "metaview_python",
        "ckpt_path",
        "da3_giant_path",
        "da3_depth_path",
        "qwen_path",
    )
    missing = [name for name in required if getattr(args, name) is None]
    if missing:
        raise SystemExit(
            "D2f --live missing required arguments: " + ", ".join(missing)
        )

    run_live(args, description)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
