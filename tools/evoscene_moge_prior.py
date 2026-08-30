#!/usr/bin/env python3
"""Real single-image spatial-prior adapter for EvoScene-emergent D2a.

The adapter is deliberately narrow:
- input: one RGB image;
- model: pinned MoGe-2 ViT-S normal weights;
- output: metric depth, validity mask, and normalized OpenCV intrinsics.

MoGe's predicted point map and normals are not persisted here. D2b must
backproject depth + intrinsics itself so the geometry operator remains an
independent experimental stage.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import pathlib
import platform
import sys
import time
from typing import Any

ADAPTER_VERSION = 1
SCHEMA_VERSION = 1
SCHEMA = "evoscene.spatial_prior.v1"

MOGE_GIT_COMMIT = "925b8ed835a7a9cdb7578ba15c658a0afc969030"
MOGE_SOURCE_URL_SUFFIX = "github.com/microsoft/MoGe"
MODEL_REPO = "Ruicheng/moge-2-vits-normal"
MODEL_FILENAME = "model.pt"
MODEL_SHA256 = (
    "79a16621928c2bf0ed04659218c55c01075e950507f40bb3332fb4c873d3e1dc"
)

CANONICAL_DEVICE = "cpu"
CANONICAL_NUM_TOKENS = 1200
CANONICAL_FP16 = False

DEPTH_FILENAME = "depth.f32le"
MASK_FILENAME = "mask.u8"
CAMERA_FILENAME = "camera.json"
MANIFEST_FILENAME = "prior.json"
TELEMETRY_FILENAME = "telemetry.json"

DEPTH_ENCODING = "row-major-f32le-meters-invalid-zero"
MASK_ENCODING = "row-major-u8-valid-0-or-1"
CAMERA_CONVENTION = "opencv-normalized-image-coordinates"


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


def installed_moge_source_identity() -> dict[str, str]:
    """Verify the installed MoGe distribution came from the pinned Git commit."""
    try:
        dist = importlib.metadata.distribution("moge")
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeError("the pinned moge package is not installed") from exc

    raw = dist.read_text("direct_url.json")
    if not raw:
        raise RuntimeError(
            "installed MoGe package has no PEP 610 direct_url.json; "
            "install it from requirements-d2a.txt"
        )

    try:
        info = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "installed MoGe direct_url.json is invalid JSON"
        ) from exc

    vcs = info.get("vcs_info")
    if not isinstance(vcs, dict):
        raise RuntimeError(
            "installed MoGe direct_url.json has no vcs_info"
        )

    vcs_name = str(vcs.get("vcs") or "")
    commit_id = str(vcs.get("commit_id") or "")
    url = str(info.get("url") or "")

    if vcs_name != "git":
        raise RuntimeError(
            f"installed MoGe source is not Git VCS: {vcs_name!r}"
        )
    if commit_id != MOGE_GIT_COMMIT:
        raise RuntimeError(
            "installed MoGe commit mismatch: "
            f"{commit_id} != {MOGE_GIT_COMMIT}"
        )

    normalized_url = url.rstrip("/")
    if normalized_url.endswith(".git"):
        normalized_url = normalized_url[:-4]
    if not normalized_url.endswith(MOGE_SOURCE_URL_SUFFIX):
        raise RuntimeError(
            "installed MoGe source URL mismatch: "
            f"{url!r} does not identify {MOGE_SOURCE_URL_SUFFIX!r}"
        )

    return {
        "vcs": vcs_name,
        "commit_id": commit_id,
        "url": url,
    }


def write_bytes(path: pathlib.Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def describe() -> dict[str, Any]:
    return {
        "adapter_version": ADAPTER_VERSION,
        "schema_version": SCHEMA_VERSION,
        "schema": SCHEMA,
        "moge_git_commit": MOGE_GIT_COMMIT,
        "moge_source_identity": "pep610-direct-url-vcs-commit",
        "model_repo": MODEL_REPO,
        "model_filename": MODEL_FILENAME,
        "model_sha256": MODEL_SHA256,
        "canonical_device": CANONICAL_DEVICE,
        "canonical_num_tokens": CANONICAL_NUM_TOKENS,
        "canonical_fp16": CANONICAL_FP16,
        "depth_filename": DEPTH_FILENAME,
        "mask_filename": MASK_FILENAME,
        "camera_filename": CAMERA_FILENAME,
        "manifest_filename": MANIFEST_FILENAME,
        "depth_encoding": DEPTH_ENCODING,
        "mask_encoding": MASK_ENCODING,
        "camera_convention": CAMERA_CONVENTION,
    }


def fixture_ppm_bytes(width: int = 320, height: int = 240) -> bytes:
    """Create a deterministic simple scene without third-party dependencies."""
    if width < 64 or height < 64:
        raise ValueError("fixture dimensions are too small")

    pixels = bytearray(width * height * 3)

    horizon = height * 11 // 20
    box1 = (
        width * 3 // 20,
        height * 7 // 20,
        width * 9 // 20,
        height * 17 // 20,
    )
    box2 = (
        width * 11 // 20,
        height * 9 // 20,
        width * 17 // 20,
        height * 16 // 20,
    )

    for y in range(height):
        for x in range(width):
            if y < horizon:
                t = y / max(1, horizon - 1)
                r = int(34 + 46 * t)
                g = int(72 + 58 * t)
                b = int(126 + 62 * t)
            else:
                t = (y - horizon) / max(1, height - horizon - 1)
                checker = ((x // 24) + ((y - horizon) // 18)) & 1
                base = 92 if checker else 116
                r = int(base + 44 * t)
                g = int(base + 36 * t)
                b = int(base + 28 * t)

            if box1[0] <= x < box1[2] and box1[1] <= y < box1[3]:
                edge = (
                    x - box1[0] < 4
                    or box1[2] - 1 - x < 4
                    or y - box1[1] < 4
                    or box1[3] - 1 - y < 4
                )
                r, g, b = (228, 193, 78) if not edge else (78, 54, 20)

            if box2[0] <= x < box2[2] and box2[1] <= y < box2[3]:
                edge = (
                    x - box2[0] < 4
                    or box2[2] - 1 - x < 4
                    or y - box2[1] < 4
                    or box2[3] - 1 - y < 4
                )
                r, g, b = (166, 70, 64) if not edge else (54, 24, 24)

            index = (y * width + x) * 3
            pixels[index : index + 3] = bytes((r, g, b))

    header = f"P6\n{width} {height}\n255\n".encode("ascii")
    return header + bytes(pixels)


def write_fixture(path: pathlib.Path) -> None:
    write_bytes(path, fixture_ppm_bytes())


def qfloat(value: float, digits: int = 10) -> float:
    result = round(float(value), digits)
    if not math.isfinite(result):
        raise ValueError("non-finite value cannot enter canonical JSON")
    return result


def resolve_device(torch: Any, requested: str) -> str:
    if requested == "cpu":
        return "cpu"
    if requested == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but is unavailable")
        return "cuda"
    if requested == "mps":
        if not hasattr(torch.backends, "mps") or not torch.backends.mps.is_available():
            raise RuntimeError("MPS was requested but is unavailable")
        return "mps"
    if requested != "auto":
        raise ValueError(f"unsupported device: {requested}")

    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def synchronize(torch: Any, device: str) -> None:
    if device == "cuda":
        torch.cuda.synchronize()
    elif device == "mps" and hasattr(torch, "mps"):
        torch.mps.synchronize()


def ensure_model_checkpoint(hf_hub_download: Any) -> tuple[pathlib.Path, str]:
    checkpoint = pathlib.Path(
        hf_hub_download(
            repo_id=MODEL_REPO,
            repo_type="model",
            filename=MODEL_FILENAME,
        )
    ).resolve()

    actual_sha256 = sha256_file(checkpoint)
    if actual_sha256 != MODEL_SHA256:
        raise RuntimeError(
            "MoGe model checksum mismatch: "
            f"{actual_sha256} != {MODEL_SHA256}"
        )
    return checkpoint, actual_sha256


def camera_record(intrinsics: Any, width: int, height: int) -> dict[str, Any]:
    values = [[qfloat(v) for v in row] for row in intrinsics.tolist()]
    fx = values[0][0]
    fy = values[1][1]
    cx = values[0][2]
    cy = values[1][2]

    if fx <= 0 or fy <= 0:
        raise RuntimeError("MoGe emitted non-positive focal length")

    fov_x_deg = math.degrees(2.0 * math.atan(0.5 / fx))
    fov_y_deg = math.degrees(2.0 * math.atan(0.5 / fy))

    return {
        "schema": "evoscene.camera.v1",
        "convention": CAMERA_CONVENTION,
        "image_width": width,
        "image_height": height,
        "intrinsics_normalized": values,
        "focal_px": {
            "fx": qfloat(fx * width),
            "fy": qfloat(fy * height),
        },
        "principal_px": {
            "cx": qfloat(cx * width),
            "cy": qfloat(cy * height),
        },
        "fov_deg": {
            "x": qfloat(fov_x_deg),
            "y": qfloat(fov_y_deg),
        },
    }


def run_prior(args: argparse.Namespace) -> dict[str, Any]:
    try:
        import numpy as np
        import torch
        from huggingface_hub import hf_hub_download
        from PIL import Image
        from moge.model.v2 import MoGeModel
    except ImportError as exc:
        raise RuntimeError(
            "D2a dependencies are missing. Install "
            "demos/evoscene-emergent/requirements-d2a.txt in a virtualenv."
        ) from exc

    try:
        moge_version = importlib.metadata.version("moge")
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeError("the pinned moge package is not installed") from exc
    if moge_version != "2.0.0":
        raise RuntimeError(
            f"D2a requires pinned MoGe 2.0.0; found {moge_version!r}"
        )

    moge_source = installed_moge_source_identity()

    input_path = pathlib.Path(args.input).expanduser().resolve()
    if not input_path.is_file():
        raise RuntimeError(f"input image not found: {input_path}")

    output_dir = pathlib.Path(args.output).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    input_bytes = input_path.read_bytes()
    input_sha256 = sha256_bytes(input_bytes)

    image = Image.open(input_path).convert("RGB")
    width, height = image.size
    image_array = np.asarray(image, dtype=np.uint8).copy()

    torch.manual_seed(0)
    torch.set_num_threads(args.threads)
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass
    torch.use_deterministic_algorithms(True, warn_only=True)

    device = resolve_device(torch, args.device)
    if args.fp16 and device == "cpu":
        raise RuntimeError("--fp16 is not supported by the canonical CPU path")

    checkpoint_start = time.perf_counter_ns()
    checkpoint_path, checkpoint_sha256 = ensure_model_checkpoint(
        hf_hub_download
    )
    checkpoint_ms = (time.perf_counter_ns() - checkpoint_start) // 1_000_000

    load_start = time.perf_counter_ns()
    model = MoGeModel.from_pretrained(str(checkpoint_path)).to(device).eval()
    model_load_ms = (time.perf_counter_ns() - load_start) // 1_000_000

    input_tensor = (
        torch.from_numpy(image_array)
        .permute(2, 0, 1)
        .to(device=device, dtype=torch.float32)
        / 255.0
    )

    synchronize(torch, device)
    inference_start = time.perf_counter_ns()
    output = model.infer(
        input_tensor,
        num_tokens=args.num_tokens,
        force_projection=True,
        apply_mask=True,
        fov_x=None,
        use_fp16=args.fp16,
    )
    synchronize(torch, device)
    inference_ms = (time.perf_counter_ns() - inference_start) // 1_000_000

    required = {"depth", "mask", "intrinsics"}
    missing = required - set(output)
    if missing:
        raise RuntimeError(f"MoGe output missing keys: {sorted(missing)}")

    depth = output["depth"].detach().float().cpu().numpy()
    mask = output["mask"].detach().cpu().numpy().astype(bool)
    intrinsics = output["intrinsics"].detach().float().cpu().numpy()

    if depth.shape != (height, width):
        raise RuntimeError(
            f"unexpected depth shape {depth.shape}; expected {(height, width)}"
        )
    if mask.shape != (height, width):
        raise RuntimeError(
            f"unexpected mask shape {mask.shape}; expected {(height, width)}"
        )
    if intrinsics.shape != (3, 3):
        raise RuntimeError(
            f"unexpected intrinsics shape {intrinsics.shape}; expected (3, 3)"
        )

    valid = mask & np.isfinite(depth) & (depth > 0)
    valid_count = int(valid.sum())
    pixel_count = width * height
    if valid_count == 0:
        raise RuntimeError("MoGe emitted no valid positive depth pixels")

    valid_depth = depth[valid].astype(np.float64)
    depth_canonical = np.where(valid, depth, 0.0).astype("<f4", copy=False)
    mask_canonical = valid.astype(np.uint8, copy=False)

    depth_bytes = depth_canonical.tobytes(order="C")
    mask_bytes = mask_canonical.tobytes(order="C")
    camera = camera_record(intrinsics, width, height)
    camera_bytes = canonical_json_bytes(camera)

    depth_path = output_dir / DEPTH_FILENAME
    mask_path = output_dir / MASK_FILENAME
    camera_path = output_dir / CAMERA_FILENAME
    write_bytes(depth_path, depth_bytes)
    write_bytes(mask_path, mask_bytes)
    write_bytes(camera_path, camera_bytes)

    depth_sha256 = sha256_bytes(depth_bytes)
    mask_sha256 = sha256_bytes(mask_bytes)
    camera_sha256 = sha256_bytes(camera_bytes)

    manifest = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "input": {
            "sha256": input_sha256,
            "width": width,
            "height": height,
        },
        "model": {
            "moge_git_commit": MOGE_GIT_COMMIT,
            "source_vcs": moge_source["vcs"],
            "source_url": moge_source["url"],
            "repo": MODEL_REPO,
            "filename": MODEL_FILENAME,
            "weight_sha256": checkpoint_sha256,
        },
        "config": {
            "device": device,
            "num_tokens": args.num_tokens,
            "fp16": bool(args.fp16),
            "force_projection": True,
            "apply_mask": True,
            "fov_x": None,
            "torch_threads": args.threads,
        },
        "artifacts": {
            "depth": {
                "file": DEPTH_FILENAME,
                "encoding": DEPTH_ENCODING,
                "bytes": len(depth_bytes),
                "sha256": depth_sha256,
            },
            "mask": {
                "file": MASK_FILENAME,
                "encoding": MASK_ENCODING,
                "bytes": len(mask_bytes),
                "sha256": mask_sha256,
            },
            "camera": {
                "file": CAMERA_FILENAME,
                "convention": CAMERA_CONVENTION,
                "bytes": len(camera_bytes),
                "sha256": camera_sha256,
            },
        },
        "summary": {
            "valid_pixels": valid_count,
            "pixel_count": pixel_count,
            "valid_fraction": qfloat(valid_count / pixel_count),
            "depth_min_m": qfloat(float(valid_depth.min())),
            "depth_median_m": qfloat(float(np.median(valid_depth))),
            "depth_max_m": qfloat(float(valid_depth.max())),
        },
    }

    manifest_bytes = canonical_json_bytes(manifest)
    manifest_path = output_dir / MANIFEST_FILENAME
    write_bytes(manifest_path, manifest_bytes)

    telemetry = {
        "schema": "evoscene.spatial_prior.telemetry.v1",
        "device": device,
        "checkpoint_lookup_ms": int(checkpoint_ms),
        "model_load_ms": int(model_load_ms),
        "inference_ms": int(inference_ms),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "moge": moge_version,
        "moge_source_commit": moge_source["commit_id"],
        "moge_source_url": moge_source["url"],
        "torch": str(torch.__version__),
        "numpy": str(np.__version__),
        "canonical_run": (
            device == CANONICAL_DEVICE
            and args.num_tokens == CANONICAL_NUM_TOKENS
            and bool(args.fp16) == CANONICAL_FP16
            and args.threads == 1
        ),
    }
    telemetry_bytes = canonical_json_bytes(telemetry)
    write_bytes(output_dir / TELEMETRY_FILENAME, telemetry_bytes)

    return {
        "status": "ok",
        "manifest": str(manifest_path),
        "manifest_sha256": sha256_bytes(manifest_bytes),
        "depth_sha256": depth_sha256,
        "mask_sha256": mask_sha256,
        "camera_sha256": camera_sha256,
        "valid_fraction": manifest["summary"]["valid_fraction"],
        "depth_median_m": manifest["summary"]["depth_median_m"],
        "device": device,
        "canonical_run": telemetry["canonical_run"],
    }


def self_test() -> None:
    first = canonical_json_bytes({"b": 2, "a": 1})
    second = canonical_json_bytes({"a": 1, "b": 2})
    if first != second:
        raise AssertionError("canonical JSON ordering is unstable")

    fixture_a = fixture_ppm_bytes()
    fixture_b = fixture_ppm_bytes()
    if fixture_a != fixture_b:
        raise AssertionError("fixture generation is not byte deterministic")
    if not fixture_a.startswith(b"P6\n320 240\n255\n"):
        raise AssertionError("fixture PPM header is invalid")

    info = describe()
    if len(info["model_sha256"]) != 64:
        raise AssertionError("model SHA-256 is malformed")
    if info["canonical_num_tokens"] != 1200:
        raise AssertionError("canonical token count drifted")

    print(
        "D2a adapter self-test PASS: "
        f"fixture_sha256={sha256_bytes(fixture_a)}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--write-fixture")
    parser.add_argument("--input")
    parser.add_argument("--output")
    parser.add_argument(
        "--device",
        choices=("cpu", "mps", "cuda", "auto"),
        default=CANONICAL_DEVICE,
    )
    parser.add_argument(
        "--num-tokens",
        type=int,
        default=CANONICAL_NUM_TOKENS,
    )
    parser.add_argument("--fp16", action="store_true")
    parser.add_argument("--threads", type=int, default=1)
    return parser


def main() -> int:
    args = build_parser().parse_args()

    modes = sum(
        [
            bool(args.self_test),
            bool(args.describe),
            bool(args.write_fixture),
            bool(args.input or args.output),
        ]
    )
    if modes != 1:
        raise SystemExit(
            "choose exactly one mode: --self-test, --describe, "
            "--write-fixture PATH, or --input IMAGE --output DIR"
        )

    if args.self_test:
        self_test()
        return 0

    if args.describe:
        sys.stdout.buffer.write(canonical_json_bytes(describe()))
        return 0

    if args.write_fixture:
        path = pathlib.Path(args.write_fixture).expanduser().resolve()
        write_fixture(path)
        print(f"wrote D2a fixture: {path} sha256={sha256_file(path)}")
        return 0

    if not args.input or not args.output:
        raise SystemExit("--input and --output are required together")
    if args.num_tokens < 256 or args.num_tokens > 4096:
        raise SystemExit("--num-tokens must be between 256 and 4096")
    if args.threads < 1:
        raise SystemExit("--threads must be >= 1")

    try:
        result = run_prior(args)
    except Exception as exc:
        print(
            canonical_json_bytes(
                {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            ).decode("utf-8"),
            end="",
            file=sys.stderr,
        )
        return 2

    sys.stdout.buffer.write(canonical_json_bytes(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
