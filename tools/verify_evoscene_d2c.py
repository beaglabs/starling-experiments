#!/usr/bin/env python3
"""D2c deterministic fusion/refinement engineering/live gate."""

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
ADAPTER = ROOT / "tools" / "evoscene_fuse_refine.py"
DEFAULT_SOURCE = ROOT / "trials" / "evoscene-d2b-smoke" / "run-a"
DEFAULT_EVIDENCE = ROOT / "trials" / "evoscene-d2b-smoke" / "run-b"
DEFAULT_OUTPUT = ROOT / "trials" / "evoscene-d2c-smoke"


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
            f"D2c gate failed: {' '.join(args)} exit={proc.returncode}"
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

    run("zig", "build", "test-demo-evoscene-d2c", timeout_s=600)

    proc = run(
        "zig",
        "build",
        "-Doptimize=ReleaseFast",
        "run-demo-evoscene-d2c",
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
        "canonical_min_neighbors": str(
            description["canonical_min_neighbors"]
        ),
        "points_encoding": str(description["points_encoding"]),
        "voxel_indexing": str(description["voxel_indexing"]),
        "refinement_rule": str(description["refinement_rule"]),
        "canonical_ordering": str(description["canonical_ordering"]),
    }
    for key, wanted in expected.items():
        actual = contract.get(key)
        if actual != wanted:
            raise SystemExit(
                f"D2c Zig/Python contract mismatch {key}: "
                f"{actual!r} != {wanted!r}"
            )

    if contract.get("sample_centroid") != "PASS":
        raise SystemExit("D2c Zig centroid primitive did not pass")
    if not contract_text.rstrip().endswith(
        "D2c CONTRACT PASS: deterministic fusion/refinement boundary frozen"
    ):
        raise SystemExit("D2c Zig contract verdict missing")

    return description


def validate_ply(
    ply_path: pathlib.Path,
    points_path: pathlib.Path,
    count: int,
) -> None:
    raw = points_path.read_bytes()
    if len(raw) != count * 12:
        raise SystemExit("D2c refined point byte size mismatch")

    lines = ply_path.read_text(encoding="ascii").splitlines()
    if len(lines) < 8:
        raise SystemExit("D2c refined PLY is too short")
    if lines[0] != "ply" or lines[1] != "format ascii 1.0":
        raise SystemExit("D2c refined PLY header is invalid")
    if f"element vertex {count}" not in lines:
        raise SystemExit("D2c refined PLY vertex count missing")

    try:
        end_header = lines.index("end_header")
    except ValueError as exc:
        raise SystemExit("D2c refined PLY has no end_header") from exc

    vertices = lines[end_header + 1 :]
    if len(vertices) != count:
        raise SystemExit(
            f"D2c refined PLY rows {len(vertices)} != {count}"
        )

    for index, ((x, y, z), row) in enumerate(
        zip(struct.iter_unpack("<fff", raw), vertices)
    ):
        parts = row.split()
        if len(parts) != 3:
            raise SystemExit(f"D2c malformed PLY row {index}")
        ascii_bytes = struct.pack(
            "<fff",
            float(parts[0]),
            float(parts[1]),
            float(parts[2]),
        )
        raw_bytes = struct.pack("<fff", x, y, z)
        if ascii_bytes != raw_bytes:
            raise SystemExit(
                f"D2c PLY row {index} does not round-trip to binary points"
            )


def validate_output(
    directory: pathlib.Path,
    source_dir: pathlib.Path,
    evidence_dir: pathlib.Path,
    description: dict[str, Any],
) -> dict[str, Any]:
    manifest_path = directory / description["fusion_manifest_filename"]
    fused_path = directory / description["fused_points_filename"]
    refined_path = directory / description["refined_points_filename"]
    ply_path = directory / description["refined_ply_filename"]
    telemetry_path = directory / description["telemetry_filename"]

    for required in (
        manifest_path,
        fused_path,
        refined_path,
        ply_path,
        telemetry_path,
    ):
        if not required.is_file():
            raise SystemExit(f"D2c output missing: {required}")

    manifest = read_json(manifest_path)
    telemetry = read_json(telemetry_path)

    if manifest.get("schema") != "evoscene.fusion_refinement.v1":
        raise SystemExit("D2c manifest schema mismatch")
    if manifest.get("schema_version") != description["schema_version"]:
        raise SystemExit("D2c schema version mismatch")
    if manifest.get("adapter_version") != description["adapter_version"]:
        raise SystemExit("D2c adapter version mismatch")

    config = manifest.get("config") or {}
    required_config = {
        "voxel_size_m": description["canonical_voxel_size_m"],
        "min_neighbors": description["canonical_min_neighbors"],
        "points_encoding": description["points_encoding"],
        "voxel_indexing": description["voxel_indexing"],
        "refinement_rule": description["refinement_rule"],
        "ordering": description["canonical_ordering"],
    }
    for key, wanted in required_config.items():
        if config.get(key) != wanted:
            raise SystemExit(
                f"D2c config mismatch {key}: "
                f"{config.get(key)!r} != {wanted!r}"
            )

    parents = manifest.get("parents") or {}
    expected_parents = {
        "source_scene_sha256": sha256_file(source_dir / "scene.json"),
        "source_points_sha256": sha256_file(source_dir / "points.f32le"),
        "evidence_scene_sha256": sha256_file(evidence_dir / "scene.json"),
        "evidence_points_sha256": sha256_file(evidence_dir / "points.f32le"),
    }
    for key, wanted in expected_parents.items():
        if parents.get(key) != wanted:
            raise SystemExit(
                f"D2c parent provenance mismatch {key}: "
                f"{parents.get(key)!r} != {wanted!r}"
            )

    summary = manifest.get("summary") or {}
    source_points = int(summary.get("source_points", 0))
    evidence_points = int(summary.get("evidence_points", 0))
    contributions = int(summary.get("total_contributions", 0))
    fused = int(summary.get("fused_voxels", 0))
    refined = int(summary.get("refined_voxels", 0))
    removed = int(summary.get("removed_voxels", -1))

    if source_points <= 0 or evidence_points <= 0:
        raise SystemExit("D2c input point counts are invalid")
    if contributions != source_points + evidence_points:
        raise SystemExit("D2c contribution accounting identity failed")
    if not (0 < refined <= fused <= contributions):
        raise SystemExit(
            "D2c fusion/refinement count ordering failed: "
            f"refined={refined} fused={fused} contributions={contributions}"
        )
    if removed != fused - refined:
        raise SystemExit("D2c removed-voxel accounting identity failed")
    if int(summary.get("min_contributions_per_voxel", 0)) < 2:
        raise SystemExit(
            "D2c live duplicate-evidence smoke expected >=2 contributions/voxel"
        )

    artifacts = manifest.get("artifacts") or {}
    fused_meta = artifacts.get("fused_points") or {}
    refined_meta = artifacts.get("refined_points") or {}
    ply_meta = artifacts.get("refined_ply") or {}

    checks = (
        (fused_path, fused_meta, fused * 12),
        (refined_path, refined_meta, refined * 12),
        (ply_path, ply_meta, ply_path.stat().st_size),
    )
    for path, meta, expected_bytes in checks:
        if int(meta.get("bytes", -1)) != expected_bytes:
            raise SystemExit(f"D2c byte count mismatch for {path.name}")
        if meta.get("sha256") != sha256_file(path):
            raise SystemExit(f"D2c SHA-256 mismatch for {path.name}")

    validate_ply(ply_path, refined_path, refined)

    bounds = summary.get("refined_bounds_m") or {}
    minimum = [float(v) for v in bounds.get("min", [])]
    maximum = [float(v) for v in bounds.get("max", [])]
    if len(minimum) != 3 or len(maximum) != 3:
        raise SystemExit("D2c refined bounds are not 3D")
    extents = [hi - lo for lo, hi in zip(minimum, maximum)]
    if any(extent <= 0.0 for extent in extents):
        raise SystemExit(f"D2c refined cloud is degenerate: {extents}")
    if minimum[2] <= 0.0:
        raise SystemExit("D2c refined cloud has non-positive forward depth")

    if telemetry.get("schema") != "evoscene.fusion_refinement.telemetry.v1":
        raise SystemExit("D2c telemetry schema mismatch")
    elapsed = int(telemetry.get("fusion_refinement_ms", -1))
    if elapsed < 0:
        raise SystemExit("D2c timing is invalid")

    return {
        "manifest_sha256": sha256_file(manifest_path),
        "fused_sha256": fused_meta["sha256"],
        "refined_sha256": refined_meta["sha256"],
        "ply_sha256": ply_meta["sha256"],
        "source_points": source_points,
        "evidence_points": evidence_points,
        "fused_voxels": fused,
        "refined_voxels": refined,
        "removed_voxels": removed,
        "bounds": bounds,
        "elapsed_ms": elapsed,
    }


def run_live(
    source_dir: pathlib.Path,
    evidence_dir: pathlib.Path,
    output_root: pathlib.Path,
    description: dict[str, Any],
) -> None:
    source_dir = source_dir.expanduser().resolve()
    evidence_dir = evidence_dir.expanduser().resolve()
    output_root = output_root.expanduser().resolve()

    for label, directory in (
        ("source", source_dir),
        ("evidence", evidence_dir),
    ):
        if not (directory / "scene.json").is_file():
            raise SystemExit(
                f"D2b {label} scene missing at {directory}. "
                "Run tools/verify_evoscene_d2b.py --live first."
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
            "--evidence-dir",
            str(evidence_dir),
            "--output",
            str(output),
            "--voxel-size-m",
            str(description["canonical_voxel_size_m"]),
            "--min-neighbors",
            str(description["canonical_min_neighbors"]),
            capture=True,
            timeout_s=600,
        )
        response = json.loads(proc.stdout)
        if response.get("status") != "ok":
            raise SystemExit(f"D2c adapter response failed: {response}")
        results.append(
            validate_output(
                output,
                source_dir,
                evidence_dir,
                description,
            )
        )

    first, second = results
    for key in (
        "manifest_sha256",
        "fused_sha256",
        "refined_sha256",
        "ply_sha256",
    ):
        if first[key] != second[key]:
            raise SystemExit(
                f"D2c repeated execution differs for {key}: "
                f"{first[key]} != {second[key]}"
            )

    print(f"D2c source_points: {first['source_points']}")
    print(f"D2c evidence_points: {first['evidence_points']}")
    print(f"D2c fused_voxels: {first['fused_voxels']}")
    print(f"D2c refined_voxels: {first['refined_voxels']}")
    print(f"D2c removed_voxels: {first['removed_voxels']}")
    print(f"D2c fused_points_sha256: {first['fused_sha256']}")
    print(f"D2c refined_points_sha256: {first['refined_sha256']}")
    print(f"D2c refined_ply_sha256: {first['ply_sha256']}")
    print(f"D2c manifest_sha256: {first['manifest_sha256']}")
    print(
        "D2c refined_bounds_min_m: "
        + ",".join(str(v) for v in first["bounds"]["min"])
    )
    print(
        "D2c refined_bounds_max_m: "
        + ",".join(str(v) for v in first["bounds"]["max"])
    )
    print(f"D2c fusion_refinement_ms: {first['elapsed_ms']}")
    print("D2c byte_identical_repeat: yes")
    print(
        "D2c LIVE PASS: deterministic fusion + geometry refinement complete"
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
        "--evidence-dir",
        type=pathlib.Path,
        default=DEFAULT_EVIDENCE,
    )
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=DEFAULT_OUTPUT,
    )
    args = parser.parse_args()

    description = validate_static_contract()

    if not args.live:
        print("D2c STRUCTURAL PASS: Zig/Python fusion contracts agree")
        return 0

    run_live(
        args.source_dir,
        args.evidence_dir,
        args.output,
        description,
    )

    run("zig", "build", "test", timeout_s=1800)
    print("D2c REPOSITORY PASS: full Zig test suite complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
