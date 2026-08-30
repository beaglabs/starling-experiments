#!/usr/bin/env python3
"""D2a real spatial-prior engineering and live-model gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import struct
import subprocess
import sys
from typing import Any, Optional

ROOT = pathlib.Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "tools" / "evoscene_moge_prior.py"
DEFAULT_OUTPUT = ROOT / "trials" / "evoscene-d2a-smoke"


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
            f"D2a gate failed: {' '.join(args)} exit={proc.returncode}"
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


def parse_contract(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if ": " not in line:
            continue
        key, value = line.split(": ", 1)
        values[key] = value
    return values


def read_json(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_static_contract() -> dict[str, Any]:
    if shutil.which("zig") is None:
        raise SystemExit("zig 0.16.0 is required on PATH")

    version = run("zig", "version", capture=True).stdout.decode().strip()
    if version != "0.16.0":
        raise SystemExit(f"zig 0.16.0 is required; found {version}")

    run("zig", "build", "test-demo-evoscene-d2a", timeout_s=600)

    contract_proc = run(
        "zig",
        "build",
        "-Doptimize=ReleaseFast",
        "run-demo-evoscene-d2a",
        "--",
        "validate",
        capture=True,
        timeout_s=600,
    )
    contract_text = contract_proc.stdout.decode()
    print(
        contract_text,
        end="" if contract_text.endswith("\n") else "\n",
    )
    contract = parse_contract(contract_text)

    self_test = run(
        sys.executable,
        str(ADAPTER),
        "--self-test",
        capture=True,
        timeout_s=120,
    )
    self_test_text = self_test.stdout.decode()
    print(
        self_test_text,
        end="" if self_test_text.endswith("\n") else "\n",
    )

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
        "moge_commit": str(description["moge_git_commit"]),
        "moge_source_identity": str(description["moge_source_identity"]),
        "model_repo": str(description["model_repo"]),
        "model_sha256": str(description["model_sha256"]),
        "canonical_device": str(description["canonical_device"]),
        "canonical_num_tokens": str(description["canonical_num_tokens"]),
        "depth_encoding": str(description["depth_encoding"]),
        "mask_encoding": str(description["mask_encoding"]),
        "camera_convention": str(description["camera_convention"]),
    }
    for key, wanted in expected.items():
        actual = contract.get(key)
        if actual != wanted:
            raise SystemExit(
                f"D2a Zig/Python contract mismatch {key}: "
                f"{actual!r} != {wanted!r}"
            )

    fp16_text = "yes" if description["canonical_fp16"] else "no"
    if contract.get("canonical_fp16") != fp16_text:
        raise SystemExit(
            "D2a Zig/Python contract mismatch canonical_fp16: "
            f"{contract.get('canonical_fp16')!r} != {fp16_text!r}"
        )

    if contract.get("sample_file_contract") != "PASS":
        raise SystemExit("D2a Zig file contract did not pass")
    if not contract_text.rstrip().endswith(
        "D2a CONTRACT PASS: real spatial-prior boundary frozen"
    ):
        raise SystemExit("D2a Zig contract verdict missing")

    return description


def validate_artifact_dir(
    path: pathlib.Path,
    description: dict[str, Any],
) -> dict[str, Any]:
    manifest_path = path / description["manifest_filename"]
    camera_path = path / description["camera_filename"]
    depth_path = path / description["depth_filename"]
    mask_path = path / description["mask_filename"]
    telemetry_path = path / "telemetry.json"

    for required in (
        manifest_path,
        camera_path,
        depth_path,
        mask_path,
        telemetry_path,
    ):
        if not required.is_file():
            raise SystemExit(f"D2a output missing: {required}")

    manifest = read_json(manifest_path)
    camera = read_json(camera_path)
    telemetry = read_json(telemetry_path)

    if manifest.get("schema") != "evoscene.spatial_prior.v1":
        raise SystemExit("D2a manifest schema mismatch")
    if manifest.get("schema_version") != description["schema_version"]:
        raise SystemExit("D2a manifest schema version mismatch")
    if manifest.get("adapter_version") != description["adapter_version"]:
        raise SystemExit("D2a manifest adapter version mismatch")

    model = manifest.get("model") or {}
    required_model = {
        "moge_git_commit": description["moge_git_commit"],
        "repo": description["model_repo"],
        "filename": description["model_filename"],
        "weight_sha256": description["model_sha256"],
    }
    for key, wanted in required_model.items():
        if model.get(key) != wanted:
            raise SystemExit(
                f"D2a model identity mismatch {key}: "
                f"{model.get(key)!r} != {wanted!r}"
            )

    config = manifest.get("config") or {}
    if config.get("device") != description["canonical_device"]:
        raise SystemExit("D2a live run is not on canonical CPU device")
    if config.get("num_tokens") != description["canonical_num_tokens"]:
        raise SystemExit("D2a live run token count drifted")
    if bool(config.get("fp16")) != bool(description["canonical_fp16"]):
        raise SystemExit("D2a live run precision drifted")
    if config.get("torch_threads") != 1:
        raise SystemExit("D2a canonical run must use one torch thread")

    width = int(manifest["input"]["width"])
    height = int(manifest["input"]["height"])
    pixels = width * height
    if width <= 0 or height <= 0:
        raise SystemExit("D2a output dimensions are invalid")

    artifacts = manifest.get("artifacts") or {}
    depth_meta = artifacts.get("depth") or {}
    mask_meta = artifacts.get("mask") or {}
    camera_meta = artifacts.get("camera") or {}

    if int(depth_meta.get("bytes", -1)) != pixels * 4:
        raise SystemExit("D2a depth byte count does not match dimensions")
    if int(mask_meta.get("bytes", -1)) != pixels:
        raise SystemExit("D2a mask byte count does not match dimensions")

    for file_path, meta in (
        (depth_path, depth_meta),
        (mask_path, mask_meta),
        (camera_path, camera_meta),
    ):
        actual = sha256_file(file_path)
        if meta.get("sha256") != actual:
            raise SystemExit(
                f"D2a artifact checksum mismatch for {file_path.name}: "
                f"{meta.get('sha256')} != {actual}"
            )

    mask = mask_path.read_bytes()
    if len(mask) != pixels:
        raise SystemExit("D2a mask file length mismatch")
    if any(value not in (0, 1) for value in mask):
        raise SystemExit("D2a mask contains values outside {0,1}")
    valid_pixels = sum(mask)
    if valid_pixels <= 0:
        raise SystemExit("D2a mask contains no valid pixels")
    if valid_pixels != int(manifest["summary"]["valid_pixels"]):
        raise SystemExit("D2a valid-pixel count mismatch")

    depth_data = depth_path.read_bytes()
    if len(depth_data) != pixels * 4:
        raise SystemExit("D2a depth file length mismatch")

    valid_depth: list[float] = []
    for index, (value,) in enumerate(struct.iter_unpack("<f", depth_data)):
        if mask[index]:
            if not (value > 0.0):
                raise SystemExit("D2a valid pixel has non-positive depth")
            valid_depth.append(value)
        elif value != 0.0:
            raise SystemExit("D2a invalid pixel depth must be canonical zero")

    if not valid_depth:
        raise SystemExit("D2a depth artifact has no valid samples")
    depth_min = min(valid_depth)
    depth_max = max(valid_depth)
    if not depth_max > depth_min:
        raise SystemExit("D2a depth map is degenerate/constant")

    intrinsics = camera.get("intrinsics_normalized")
    if (
        not isinstance(intrinsics, list)
        or len(intrinsics) != 3
        or any(not isinstance(row, list) or len(row) != 3 for row in intrinsics)
    ):
        raise SystemExit("D2a camera intrinsics are not 3x3")

    fx = float(intrinsics[0][0])
    fy = float(intrinsics[1][1])
    cx = float(intrinsics[0][2])
    cy = float(intrinsics[1][2])
    if fx <= 0 or fy <= 0:
        raise SystemExit("D2a camera focal length is non-positive")
    if not (0.0 <= cx <= 1.0 and 0.0 <= cy <= 1.0):
        raise SystemExit("D2a camera principal point is outside image")

    if telemetry.get("canonical_run") is not True:
        raise SystemExit("D2a telemetry does not identify a canonical run")
    if int(telemetry.get("inference_ms", -1)) < 0:
        raise SystemExit("D2a inference timing is invalid")

    return {
        "manifest": manifest,
        "camera": camera,
        "telemetry": telemetry,
        "depth_sha256": depth_meta["sha256"],
        "mask_sha256": mask_meta["sha256"],
        "camera_sha256": camera_meta["sha256"],
        "manifest_sha256": sha256_file(manifest_path),
        "depth_min_m": depth_min,
        "depth_max_m": depth_max,
    }


def run_live(
    description: dict[str, Any],
    input_path: Optional[pathlib.Path],
    output_root: pathlib.Path,
) -> None:
    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True)

    if input_path is None:
        input_path = output_root / "fixture.ppm"
        fixture = run(
            sys.executable,
            str(ADAPTER),
            "--write-fixture",
            str(input_path),
            capture=True,
            timeout_s=120,
        )
        fixture_text = fixture.stdout.decode()
        print(
            fixture_text,
            end="" if fixture_text.endswith("\n") else "\n",
        )
    else:
        input_path = input_path.expanduser().resolve()
        if not input_path.is_file():
            raise SystemExit(f"D2a input image not found: {input_path}")

    results: list[dict[str, Any]] = []
    for label in ("run-a", "run-b"):
        output_dir = output_root / label
        proc = run(
            sys.executable,
            str(ADAPTER),
            "--input",
            str(input_path),
            "--output",
            str(output_dir),
            "--device",
            description["canonical_device"],
            "--num-tokens",
            str(description["canonical_num_tokens"]),
            "--threads",
            "1",
            capture=True,
            timeout_s=3600,
        )
        response = json.loads(proc.stdout)
        if response.get("status") != "ok":
            raise SystemExit(f"D2a adapter response failed: {response}")
        if response.get("canonical_run") is not True:
            raise SystemExit("D2a adapter response is not canonical")
        results.append(validate_artifact_dir(output_dir, description))

    first, second = results
    for key in (
        "depth_sha256",
        "mask_sha256",
        "camera_sha256",
        "manifest_sha256",
    ):
        if first[key] != second[key]:
            raise SystemExit(
                f"D2a repeated execution is not byte-identical for {key}: "
                f"{first[key]} != {second[key]}"
            )

    manifest = first["manifest"]
    summary = manifest["summary"]
    telemetry = first["telemetry"]

    print(f"D2a input_sha256: {manifest['input']['sha256']}")
    print(f"D2a depth_sha256: {first['depth_sha256']}")
    print(f"D2a mask_sha256: {first['mask_sha256']}")
    print(f"D2a camera_sha256: {first['camera_sha256']}")
    print(f"D2a manifest_sha256: {first['manifest_sha256']}")
    print(
        "D2a dimensions: "
        f"{manifest['input']['width']}x{manifest['input']['height']}"
    )
    print(f"D2a valid_fraction: {summary['valid_fraction']}")
    print(f"D2a depth_min_m: {summary['depth_min_m']}")
    print(f"D2a depth_median_m: {summary['depth_median_m']}")
    print(f"D2a depth_max_m: {summary['depth_max_m']}")
    print(f"D2a inference_ms: {telemetry['inference_ms']}")
    print("D2a byte_identical_repeat: yes")
    print("D2a LIVE PASS: real metric depth + camera prior complete")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run the pinned MoGe model twice and validate real artifacts.",
    )
    parser.add_argument(
        "--input",
        type=pathlib.Path,
        help="Optional image for the live gate; otherwise use the fixture.",
    )
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=DEFAULT_OUTPUT,
        help="Ignored-by-git directory for live artifacts.",
    )
    args = parser.parse_args()

    description = validate_static_contract()

    if args.input is not None and not args.live:
        raise SystemExit("--input requires --live")

    if not args.live:
        print(
            "D2a STRUCTURAL PASS: Zig/Python spatial-prior contracts agree"
        )
        return 0

    try:
        run_live(
            description,
            args.input,
            args.output.expanduser().resolve(),
        )
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "D2a live dependencies are missing. Create/activate a virtualenv "
            "and run: pip install -r "
            "demos/evoscene-emergent/requirements-d2a.txt"
        ) from exc

    run("zig", "build", "test", timeout_s=1800)
    print("D2a REPOSITORY PASS: full Zig test suite complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
