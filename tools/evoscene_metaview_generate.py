#!/usr/bin/env python3
"""Pinned MetaView worker for EvoScene D2f learned novel-view generation.

This adapter keeps MetaView behind the existing NovelViewAgent/render_view tool
boundary. It validates the pinned MetaView source/checkpoint, invokes the
official inference script at seed=0 / 40 steps, crops the official stitched
output to the generated right-hand panel, and freezes the result through a
content-addressed cache.

The cache is part of experiment semantics: the first execution of a unique
generation key establishes the learned artifact; subsequent identical requests
reuse the exact bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from typing import Any, Optional

SCHEMA_VERSION = 1
ADAPTER_VERSION = 1
SCHEMA = "evoscene.learned_novel_view.v1"

BACKEND = "metaview"
METAVIEW_GIT_COMMIT = "a8a3d46c198d7cc0627e8e8a55d93d362fc1ca55"
METAVIEW_INFERENCE_BLOB_SHA1 = "e7aa670dcfbbf4d061f75ec8af01edf1b5ae8805"
METAVIEW_MODEL_REPO = "Kwai-Kolors/MetaView"
METAVIEW_MODEL_FILE = "model-2500-best.safetensors"
METAVIEW_MODEL_SHA256 = (
    "a67ae628ea665c0f9ef00be3db38eaebca02734f63a4e04118573ac9e30a74ef"
)

QWEN_IMAGE_EDIT_REVISION = "ac7f931"
DA3_GIANT_REVISION = "72ee9f8"
DA3_DEPTH_REVISION = "b2359bd"

CANONICAL_SEED = 0
CANONICAL_STEPS = 40
CANONICAL_WIDTH = 960
CANONICAL_HEIGHT = 528
RADIUS_QUANTIZATION_MM = 1

PROMPT_ID = "official-metaview-camera-trigger-v1"
POSE_CONVENTION = "metaview-yaw-pitch-radius"
OUTPUT_RULE = "right-half-of-official-stitched-output"
CACHE_KEY_RULE = "sha256-contract-input-pose-radius-model-dependencies"

GENERATED_FILENAME = "novel.png"
GENERATION_MANIFEST_FILENAME = "generation.json"
TELEMETRY_FILENAME = "telemetry.json"
DEPENDENCY_SCHEMA = "evoscene.metaview.dependencies.v1"


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
            block = handle.read(16 * 1024 * 1024)
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


def run_text(
    args: list[str],
    cwd: Optional[pathlib.Path] = None,
) -> str:
    proc = subprocess.run(
        args,
        cwd=cwd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(args)}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )
    return proc.stdout.strip()


def describe() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "backend": BACKEND,
        "metaview_git_commit": METAVIEW_GIT_COMMIT,
        "metaview_inference_blob_sha1": METAVIEW_INFERENCE_BLOB_SHA1,
        "metaview_model_repo": METAVIEW_MODEL_REPO,
        "metaview_model_file": METAVIEW_MODEL_FILE,
        "metaview_model_sha256": METAVIEW_MODEL_SHA256,
        "qwen_image_edit_revision": QWEN_IMAGE_EDIT_REVISION,
        "da3_giant_revision": DA3_GIANT_REVISION,
        "da3_depth_revision": DA3_DEPTH_REVISION,
        "canonical_seed": CANONICAL_SEED,
        "canonical_steps": CANONICAL_STEPS,
        "canonical_width": CANONICAL_WIDTH,
        "canonical_height": CANONICAL_HEIGHT,
        "prompt_id": PROMPT_ID,
        "pose_convention": POSE_CONVENTION,
        "output_rule": OUTPUT_RULE,
        "cache_key_rule": CACHE_KEY_RULE,
        "generated_filename": GENERATED_FILENAME,
        "generation_manifest_filename": GENERATION_MANIFEST_FILENAME,
        "telemetry_filename": TELEMETRY_FILENAME,
        "dependency_schema": DEPENDENCY_SCHEMA,
    }


def verify_dependencies(path: pathlib.Path) -> tuple[dict[str, Any], str]:
    data = path.read_bytes()
    manifest = json.loads(data)
    if not isinstance(manifest, dict):
        raise RuntimeError("MetaView dependency manifest must be a JSON object")
    if manifest.get("schema") != DEPENDENCY_SCHEMA:
        raise RuntimeError("MetaView dependency manifest schema mismatch")

    expected = {
        "qwen_image_edit_revision": QWEN_IMAGE_EDIT_REVISION,
        "da3_giant_revision": DA3_GIANT_REVISION,
        "da3_depth_revision": DA3_DEPTH_REVISION,
    }
    for key, wanted in expected.items():
        actual = str(manifest.get(key) or "")
        if actual != wanted:
            raise RuntimeError(
                f"dependency revision mismatch {key}: {actual!r} != {wanted!r}"
            )

    return manifest, sha256_bytes(data)


def verify_metaview_source(root: pathlib.Path) -> None:
    if not (root / ".git").exists():
        raise RuntimeError(f"MetaView root is not a Git checkout: {root}")

    head = run_text(["git", "rev-parse", "HEAD"], cwd=root)
    if head != METAVIEW_GIT_COMMIT:
        raise RuntimeError(
            f"MetaView HEAD mismatch: {head} != {METAVIEW_GIT_COMMIT}"
        )

    inference = root / "src" / "inference.py"
    if not inference.is_file():
        raise RuntimeError(f"MetaView inference.py missing: {inference}")

    blob = run_text(["git", "hash-object", "src/inference.py"], cwd=root)
    if blob != METAVIEW_INFERENCE_BLOB_SHA1:
        raise RuntimeError(
            "MetaView inference.py blob mismatch: "
            f"{blob} != {METAVIEW_INFERENCE_BLOB_SHA1}"
        )

    # Reject tracked edits in the execution-critical code while allowing the
    # officially documented untracked DepthAnything3 checkout.
    diff = subprocess.run(
        [
            "git",
            "diff",
            "--quiet",
            "HEAD",
            "--",
            "src",
            "diffsynth",
            "pyproject.toml",
        ],
        cwd=root,
        check=False,
    )
    if diff.returncode != 0:
        raise RuntimeError("MetaView execution-critical tracked files are modified")


def quantized_radius_mm(radius_m: float) -> int:
    if not math.isfinite(radius_m) or radius_m <= 0.0:
        raise RuntimeError("radius must be finite and positive")
    value = int(round(radius_m * 1000.0 / RADIUS_QUANTIZATION_MM))
    value *= RADIUS_QUANTIZATION_MM
    if value <= 0:
        raise RuntimeError("quantized radius is not positive")
    return value


def cache_key(
    input_sha256: str,
    yaw_mdeg: int,
    pitch_mdeg: int,
    radius_mm: int,
    dependency_manifest_sha256: str,
) -> str:
    key = {
        "schema": "evoscene.learned_novel_view.cache_key.v1",
        "contract": describe(),
        "input_sha256": input_sha256,
        "pose": {
            "yaw_mdeg": yaw_mdeg,
            "pitch_mdeg": pitch_mdeg,
            "radius_mm": radius_mm,
        },
        "dependency_manifest_sha256": dependency_manifest_sha256,
    }
    return sha256_bytes(canonical_json_bytes(key))


def canonical_png_bytes(image: Any) -> bytes:
    from PIL import Image

    if not isinstance(image, Image.Image):
        raise RuntimeError("expected PIL image")
    image = image.convert("RGB")

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        temp = pathlib.Path(handle.name)
    try:
        image.save(
            temp,
            format="PNG",
            optimize=False,
            compress_level=9,
        )
        return temp.read_bytes()
    finally:
        temp.unlink(missing_ok=True)


def crop_official_output(stitched_path: pathlib.Path) -> bytes:
    from PIL import Image

    with Image.open(stitched_path) as image:
        image = image.convert("RGB")
        expected = (CANONICAL_WIDTH * 2, CANONICAL_HEIGHT)
        if image.size != expected:
            raise RuntimeError(
                f"MetaView stitched output size {image.size} != {expected}"
            )
        generated = image.crop(
            (
                CANONICAL_WIDTH,
                0,
                CANONICAL_WIDTH * 2,
                CANONICAL_HEIGHT,
            )
        )
        return canonical_png_bytes(generated)


def materialize_cached(
    cache_entry: pathlib.Path,
    output_dir: pathlib.Path,
    expected_key: str,
    expected_input_sha256: str,
    expected_dependency_sha256: str,
) -> dict[str, Any]:
    manifest_path = cache_entry / GENERATION_MANIFEST_FILENAME
    image_path = cache_entry / GENERATED_FILENAME
    if not manifest_path.is_file() or not image_path.is_file():
        raise RuntimeError("incomplete D2f cache entry")

    manifest = read_json(manifest_path)
    if manifest.get("schema") != SCHEMA:
        raise RuntimeError("cached D2f manifest schema mismatch")
    if manifest.get("cache_key") != expected_key:
        raise RuntimeError("cached D2f key mismatch")
    if ((manifest.get("input") or {}).get("file_sha256")) != expected_input_sha256:
        raise RuntimeError("cached D2f input SHA mismatch")
    if (
        ((manifest.get("dependencies") or {}).get("manifest_sha256"))
        != expected_dependency_sha256
    ):
        raise RuntimeError("cached D2f dependency manifest SHA mismatch")

    model = manifest.get("model") or {}
    expected_model = {
        "metaview_git_commit": METAVIEW_GIT_COMMIT,
        "metaview_inference_blob_sha1": METAVIEW_INFERENCE_BLOB_SHA1,
        "repo": METAVIEW_MODEL_REPO,
        "file": METAVIEW_MODEL_FILE,
        "sha256": METAVIEW_MODEL_SHA256,
        "seed": CANONICAL_SEED,
        "steps": CANONICAL_STEPS,
        "width": CANONICAL_WIDTH,
        "height": CANONICAL_HEIGHT,
        "prompt_id": PROMPT_ID,
    }
    for key, wanted in expected_model.items():
        if model.get(key) != wanted:
            raise RuntimeError(
                f"cached D2f model mismatch {key}: "
                f"{model.get(key)!r} != {wanted!r}"
            )

    actual = sha256_file(image_path)
    expected = str(
        ((manifest.get("artifacts") or {}).get("novel") or {}).get("sha256")
        or ""
    )
    if actual != expected:
        raise RuntimeError(
            f"cached D2f image SHA mismatch: {actual} != {expected}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(image_path, output_dir / GENERATED_FILENAME)
    shutil.copyfile(manifest_path, output_dir / GENERATION_MANIFEST_FILENAME)
    return manifest


def require_cache_miss_environment(args: argparse.Namespace) -> dict[str, Any]:
    required = (
        "metaview_root",
        "metaview_python",
        "ckpt_path",
        "da3_giant_path",
        "da3_depth_path",
        "qwen_path",
    )
    missing = [name for name in required if getattr(args, name) is None]
    if missing:
        raise RuntimeError(
            "D2f cache miss requires MetaView environment arguments: "
            + ", ".join(missing)
        )

    root = pathlib.Path(args.metaview_root).expanduser().resolve()
    python = pathlib.Path(args.metaview_python).expanduser().resolve()
    checkpoint = pathlib.Path(args.ckpt_path).expanduser().resolve()
    da3_giant = pathlib.Path(args.da3_giant_path).expanduser().resolve()
    da3_depth = pathlib.Path(args.da3_depth_path).expanduser().resolve()
    qwen_root = pathlib.Path(args.qwen_path).expanduser().resolve()

    if not python.is_file():
        raise RuntimeError(f"MetaView Python missing: {python}")
    if not checkpoint.is_file():
        raise RuntimeError(f"MetaView checkpoint missing: {checkpoint}")
    if not (root / "DepthAnything3" / "src").is_dir():
        raise RuntimeError(
            "MetaView official import path requires "
            f"{root / 'DepthAnything3' / 'src'}"
        )
    if not (qwen_root / "Qwen-Image-Edit").is_dir():
        raise RuntimeError(
            "--qwen-path must contain Qwen-Image-Edit/"
        )
    for label, path in (
        ("DA3 giant", da3_giant),
        ("DA3 depth", da3_depth),
    ):
        if not path.exists():
            raise RuntimeError(f"{label} path missing: {path}")

    verify_metaview_source(root)

    cuda_probe = subprocess.run(
        [
            str(python),
            "-c",
            (
                "import sys, torch; "
                "assert torch.cuda.is_available(), 'CUDA unavailable'; "
                "print(sys.version.split()[0]); "
                "print(torch.__version__); "
                "print(torch.cuda.get_device_name(0))"
            ),
        ],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if cuda_probe.returncode != 0:
        raise RuntimeError(
            "MetaView CUDA preflight failed\n"
            f"stdout:\n{cuda_probe.stdout}\n"
            f"stderr:\n{cuda_probe.stderr}"
        )

    checkpoint_sha = sha256_file(checkpoint)
    if checkpoint_sha != METAVIEW_MODEL_SHA256:
        raise RuntimeError(
            "MetaView checkpoint SHA mismatch: "
            f"{checkpoint_sha} != {METAVIEW_MODEL_SHA256}"
        )

    return {
        "root": root,
        "python": python,
        "checkpoint": checkpoint,
        "da3_giant": da3_giant,
        "da3_depth": da3_depth,
        "qwen_root": qwen_root,
        "checkpoint_sha": checkpoint_sha,
        "cuda_probe": cuda_probe.stdout.strip().splitlines(),
    }


def run_generation(args: argparse.Namespace) -> dict[str, Any]:
    input_path = pathlib.Path(args.input).expanduser().resolve()
    output_dir = pathlib.Path(args.output).expanduser().resolve()
    cache_root = pathlib.Path(args.cache_dir).expanduser().resolve()
    dependency_path = pathlib.Path(
        args.dependency_manifest
    ).expanduser().resolve()

    if not input_path.is_file():
        raise RuntimeError(f"input image missing: {input_path}")
    if not dependency_path.is_file():
        raise RuntimeError(
            f"MetaView dependency manifest missing: {dependency_path}"
        )

    started = time.perf_counter_ns()
    dependencies, dependency_sha = verify_dependencies(dependency_path)

    input_sha = sha256_file(input_path)
    yaw_mdeg = int(args.yaw_mdeg)
    pitch_mdeg = int(args.pitch_mdeg)
    radius_mm = quantized_radius_mm(float(args.radius_m))
    radius_m = radius_mm / 1000.0

    key = cache_key(
        input_sha,
        yaw_mdeg,
        pitch_mdeg,
        radius_mm,
        dependency_sha,
    )
    cache_entry = cache_root / key

    cache_hit = (
        (cache_entry / GENERATED_FILENAME).is_file()
        and (cache_entry / GENERATION_MANIFEST_FILENAME).is_file()
    )

    external_env: Optional[dict[str, Any]] = None
    if cache_hit:
        manifest = materialize_cached(
            cache_entry,
            output_dir,
            key,
            input_sha,
            dependency_sha,
        )
    else:
        external_env = require_cache_miss_environment(args)
        root = external_env["root"]
        python = external_env["python"]
        checkpoint = external_env["checkpoint"]
        da3_giant = external_env["da3_giant"]
        da3_depth = external_env["da3_depth"]
        qwen_root = external_env["qwen_root"]
        checkpoint_sha = external_env["checkpoint_sha"]

        cache_entry.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="evoscene-metaview-") as tmp:
            temp = pathlib.Path(tmp)
            stitched = temp / "stitched.png"

            command = [
                str(python),
                "src/inference.py",
                "--image_path",
                str(input_path),
                "--output_path",
                str(stitched),
                "--yaw",
                f"{yaw_mdeg / 1000.0:.3f}",
                "--pitch",
                f"{pitch_mdeg / 1000.0:.3f}",
                "--radius",
                f"{radius_m:.3f}",
                "--ckpt_path",
                str(checkpoint),
                "--da3_giant_path",
                str(da3_giant),
                "--da3_depth_path",
                str(da3_depth),
                "--qwen_path",
                str(qwen_root),
            ]

            proc = subprocess.run(
                command,
                cwd=root,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=int(args.timeout_s),
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    "MetaView inference failed "
                    f"({proc.returncode})\nstdout:\n{proc.stdout}\n"
                    f"stderr:\n{proc.stderr}"
                )
            if not stitched.is_file():
                raise RuntimeError("MetaView inference did not create output")

            novel_bytes = crop_official_output(stitched)

        novel_sha = sha256_bytes(novel_bytes)
        generation = {
            "schema": SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "adapter_version": ADAPTER_VERSION,
            "backend": BACKEND,
            "cache_key": key,
            "input": {
                "file_sha256": input_sha,
            },
            "pose": {
                "yaw_mdeg": yaw_mdeg,
                "pitch_mdeg": pitch_mdeg,
                "radius_mm": radius_mm,
            },
            "model": {
                "metaview_git_commit": METAVIEW_GIT_COMMIT,
                "metaview_inference_blob_sha1": METAVIEW_INFERENCE_BLOB_SHA1,
                "repo": METAVIEW_MODEL_REPO,
                "file": METAVIEW_MODEL_FILE,
                "sha256": checkpoint_sha,
                "seed": CANONICAL_SEED,
                "steps": CANONICAL_STEPS,
                "width": CANONICAL_WIDTH,
                "height": CANONICAL_HEIGHT,
                "prompt_id": PROMPT_ID,
            },
            "dependencies": {
                "manifest_sha256": dependency_sha,
                "qwen_image_edit_revision": dependencies[
                    "qwen_image_edit_revision"
                ],
                "da3_giant_revision": dependencies["da3_giant_revision"],
                "da3_depth_revision": dependencies["da3_depth_revision"],
            },
            "config": {
                "pose_convention": POSE_CONVENTION,
                "output_rule": OUTPUT_RULE,
                "cache_key_rule": CACHE_KEY_RULE,
            },
            "artifacts": {
                "novel": {
                    "file": GENERATED_FILENAME,
                    "bytes": len(novel_bytes),
                    "sha256": novel_sha,
                    "encoding": "rgb-png-960x528",
                },
            },
        }
        manifest_bytes = canonical_json_bytes(generation)

        write_bytes(cache_entry / GENERATED_FILENAME, novel_bytes)
        write_bytes(
            cache_entry / GENERATION_MANIFEST_FILENAME,
            manifest_bytes,
        )
        shutil.copyfile(
            cache_entry / GENERATED_FILENAME,
            output_dir / GENERATED_FILENAME,
        )
        shutil.copyfile(
            cache_entry / GENERATION_MANIFEST_FILENAME,
            output_dir / GENERATION_MANIFEST_FILENAME,
        )
        manifest = generation

    elapsed_ms = (time.perf_counter_ns() - started) // 1_000_000
    telemetry = {
        "schema": "evoscene.learned_novel_view.telemetry.v1",
        "cache_hit": cache_hit,
        "generation_wall_ms": int(elapsed_ms),
        "python": sys.version.split()[0],
    }
    if external_env is not None:
        telemetry["metaview_cuda_environment"] = external_env["cuda_probe"]

    write_bytes(
        output_dir / TELEMETRY_FILENAME,
        canonical_json_bytes(telemetry),
    )

    novel_path = output_dir / GENERATED_FILENAME
    manifest_path = output_dir / GENERATION_MANIFEST_FILENAME
    return {
        "status": "ok",
        "cache_hit": cache_hit,
        "cache_key": key,
        "novel_sha256": sha256_file(novel_path),
        "generation_sha256": sha256_file(manifest_path),
        "novel": str(novel_path),
        "generation": str(manifest_path),
    }


def self_test() -> None:
    from PIL import Image

    with tempfile.TemporaryDirectory(prefix="evoscene-d2f-") as tmp:
        root = pathlib.Path(tmp)
        stitched = root / "stitched.png"

        image = Image.new(
            "RGB",
            (CANONICAL_WIDTH * 2, CANONICAL_HEIGHT),
            (0, 0, 0),
        )
        right = Image.new(
            "RGB",
            (CANONICAL_WIDTH, CANONICAL_HEIGHT),
            (17, 34, 51),
        )
        image.paste(right, (CANONICAL_WIDTH, 0))
        image.save(stitched, format="PNG")

        first = crop_official_output(stitched)
        second = crop_official_output(stitched)
        if first != second:
            raise AssertionError("canonical MetaView crop is not deterministic")

        dependency_sha = "a" * 64
        a = cache_key("b" * 64, 35_000, 10_000, 6500, dependency_sha)
        b = cache_key("b" * 64, 35_000, 10_000, 6500, dependency_sha)
        c = cache_key("b" * 64, -35_000, 10_000, 6500, dependency_sha)
        if a != b:
            raise AssertionError("D2f cache key is not deterministic")
        if a == c:
            raise AssertionError("D2f cache key ignores pose")

        cache_entry = root / a
        output_dir = root / "replayed"
        cache_entry.mkdir()

        novel_bytes = first
        novel_sha = sha256_bytes(novel_bytes)
        manifest = {
            "schema": SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "adapter_version": ADAPTER_VERSION,
            "backend": BACKEND,
            "cache_key": a,
            "input": {"file_sha256": "b" * 64},
            "pose": {
                "yaw_mdeg": 35_000,
                "pitch_mdeg": 10_000,
                "radius_mm": 6500,
            },
            "model": {
                "metaview_git_commit": METAVIEW_GIT_COMMIT,
                "metaview_inference_blob_sha1": METAVIEW_INFERENCE_BLOB_SHA1,
                "repo": METAVIEW_MODEL_REPO,
                "file": METAVIEW_MODEL_FILE,
                "sha256": METAVIEW_MODEL_SHA256,
                "seed": CANONICAL_SEED,
                "steps": CANONICAL_STEPS,
                "width": CANONICAL_WIDTH,
                "height": CANONICAL_HEIGHT,
                "prompt_id": PROMPT_ID,
            },
            "dependencies": {
                "manifest_sha256": dependency_sha,
                "qwen_image_edit_revision": QWEN_IMAGE_EDIT_REVISION,
                "da3_giant_revision": DA3_GIANT_REVISION,
                "da3_depth_revision": DA3_DEPTH_REVISION,
            },
            "config": {
                "pose_convention": POSE_CONVENTION,
                "output_rule": OUTPUT_RULE,
                "cache_key_rule": CACHE_KEY_RULE,
            },
            "artifacts": {
                "novel": {
                    "file": GENERATED_FILENAME,
                    "bytes": len(novel_bytes),
                    "sha256": novel_sha,
                    "encoding": "rgb-png-960x528",
                },
            },
        }
        write_bytes(cache_entry / GENERATED_FILENAME, novel_bytes)
        write_bytes(
            cache_entry / GENERATION_MANIFEST_FILENAME,
            canonical_json_bytes(manifest),
        )

        replayed = materialize_cached(
            cache_entry,
            output_dir,
            a,
            "b" * 64,
            dependency_sha,
        )
        if replayed["cache_key"] != a:
            raise AssertionError("D2f cached manifest replay drifted")
        if (output_dir / GENERATED_FILENAME).read_bytes() != novel_bytes:
            raise AssertionError("D2f cached novel bytes changed on replay")

    print(
        "D2f generator self-test PASS: "
        "official-output crop + content cache key exact"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--input")
    parser.add_argument("--output")
    parser.add_argument("--cache-dir")
    parser.add_argument("--yaw-mdeg", type=int)
    parser.add_argument("--pitch-mdeg", type=int)
    parser.add_argument("--radius-m", type=float)
    parser.add_argument("--metaview-root")
    parser.add_argument("--metaview-python")
    parser.add_argument("--ckpt-path")
    parser.add_argument("--da3-giant-path")
    parser.add_argument("--da3-depth-path")
    parser.add_argument("--qwen-path")
    parser.add_argument("--dependency-manifest")
    parser.add_argument("--timeout-s", type=int, default=7200)
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.self_test:
        self_test()
        return 0
    if args.describe:
        sys.stdout.buffer.write(canonical_json_bytes(describe()))
        return 0

    required = (
        "input",
        "output",
        "cache_dir",
        "yaw_mdeg",
        "pitch_mdeg",
        "radius_m",
        "dependency_manifest",
    )
    missing = [name for name in required if getattr(args, name) is None]
    if missing:
        raise SystemExit(
            "missing D2f generation arguments: " + ", ".join(missing)
        )

    try:
        result = run_generation(args)
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
