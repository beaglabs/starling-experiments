#!/usr/bin/env python3
"""Pinned Bellingcat ShadowFinder adapter for the GEOINT emergence trial.

ShadowFinder is a physics/geolocation operator, not the pixel observer. This
wrapper accepts already-established shadow geometry or sun altitude plus an
acquisition datetime and emits a compact candidate-location summary.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import pathlib
import subprocess
import sys
from typing import Any

SHADOWFINDER_VERSION = "0.7.0"
SHADOWFINDER_GIT_COMMIT = "f12f8d6d30094f94c8cacb27f3c77db7e443d6fd"
SHADOWFINDER_SOURCE_BLOB = "6dc9781cf3d32e37c0c507929d06cedbd818fe26"
SCHEMA = "starlings.geoint.shadowfinder.v1"
DEFAULT_THRESHOLD = 0.02
DEFAULT_TOP_N = 20


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


def sun_altitude_degrees(object_height: float, shadow_length: float) -> float:
    if not math.isfinite(object_height) or object_height <= 0:
        raise ValueError("object_height must be finite and positive")
    if not math.isfinite(shadow_length) or shadow_length <= 0:
        raise ValueError("shadow_length must be finite and positive")
    return math.degrees(math.atan(object_height / shadow_length))


def describe() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "shadowfinder_version": SHADOWFINDER_VERSION,
        "shadowfinder_git_commit": SHADOWFINDER_GIT_COMMIT,
        "shadowfinder_source_blob": SHADOWFINDER_SOURCE_BLOB,
        "threshold_semantics": "absolute-relative-solar-altitude-error",
        "default_threshold": DEFAULT_THRESHOLD,
        "default_top_n": DEFAULT_TOP_N,
    }


def verify_source(root: pathlib.Path) -> None:
    if not (root / ".git").exists():
        raise RuntimeError(f"ShadowFinder root is not a Git checkout: {root}")

    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
    ).strip()
    if head != SHADOWFINDER_GIT_COMMIT:
        raise RuntimeError(
            f"ShadowFinder HEAD mismatch: {head} != {SHADOWFINDER_GIT_COMMIT}"
        )

    blob = subprocess.check_output(
        ["git", "hash-object", "src/shadowfinder/shadowfinder.py"],
        cwd=root,
        text=True,
    ).strip()
    if blob != SHADOWFINDER_SOURCE_BLOB:
        raise RuntimeError(
            f"ShadowFinder source blob mismatch: {blob} != {SHADOWFINDER_SOURCE_BLOB}"
        )


def parse_datetime(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def summarize_candidates(
    finder: Any,
    threshold: float,
    top_n: int,
) -> dict[str, Any]:
    import numpy as np

    errors = np.abs(finder.location_likelihoods)
    valid = np.isfinite(errors) & (errors <= threshold)
    indices = np.argwhere(valid)

    if indices.size == 0:
        return {
            "candidate_cells": 0,
            "bounds": None,
            "top": [],
        }

    rows = []
    for row, col in indices:
        rows.append(
            (
                float(errors[row, col]),
                float(finder.lats[row, col]),
                float(finder.lons[row, col]),
            )
        )
    rows.sort(key=lambda item: (item[0], item[1], item[2]))

    lats = [item[1] for item in rows]
    lons = [item[2] for item in rows]
    return {
        "candidate_cells": len(rows),
        "bounds": {
            "min_lat": min(lats),
            "max_lat": max(lats),
            "min_lon": min(lons),
            "max_lon": max(lons),
        },
        "top": [
            {
                "relative_error": round(error, 10),
                "lat": lat,
                "lon": lon,
            }
            for error, lat, lon in rows[:top_n]
        ],
    }


def run_live(args: argparse.Namespace) -> dict[str, Any]:
    root = pathlib.Path(args.shadowfinder_root).expanduser().resolve()
    verify_source(root)

    source_root = root / "src"
    sys.path.insert(0, str(source_root))
    from shadowfinder import ShadowFinder

    date_time = parse_datetime(args.datetime)
    if args.sun_altitude_deg is not None:
        altitude = float(args.sun_altitude_deg)
        object_height = None
        shadow_length = None
    else:
        if args.object_height is None or args.shadow_length is None:
            raise RuntimeError(
                "provide --sun-altitude-deg OR both "
                "--object-height and --shadow-length"
            )
        object_height = float(args.object_height)
        shadow_length = float(args.shadow_length)
        altitude = sun_altitude_degrees(object_height, shadow_length)

    if not 0 < altitude <= 90:
        raise RuntimeError("derived/provided sun altitude must be in (0, 90]")

    finder = ShadowFinder()
    grid = (
        pathlib.Path(args.timezone_grid).expanduser().resolve()
        if args.timezone_grid
        else root / "timezone_grid.json"
    )
    if not grid.is_file():
        raise RuntimeError(f"ShadowFinder timezone grid missing: {grid}")

    finder.load_timezone_grid(str(grid))
    finder.set_details(
        date_time=date_time,
        time_format=args.time_format,
        sun_altitude_angle=altitude,
    )
    finder.find_shadows()

    summary = summarize_candidates(
        finder,
        float(args.threshold),
        int(args.top_n),
    )

    result = {
        "schema": SCHEMA,
        "backend": {
            "version": SHADOWFINDER_VERSION,
            "git_commit": SHADOWFINDER_GIT_COMMIT,
            "source_blob": SHADOWFINDER_SOURCE_BLOB,
        },
        "input": {
            "datetime": date_time.isoformat(timespec="seconds"),
            "time_format": args.time_format,
            "sun_altitude_deg": round(altitude, 10),
            "object_height": object_height,
            "shadow_length": shadow_length,
        },
        "selection": {
            "threshold": float(args.threshold),
            "top_n": int(args.top_n),
        },
        "candidates": summary,
    }
    encoded = canonical_json_bytes(result)
    result["result_sha256"] = sha256_bytes(encoded)
    return result


def self_test() -> None:
    angle = sun_altitude_degrees(1.0, 1.0)
    if abs(angle - 45.0) > 1e-12:
        raise AssertionError("shadow altitude geometry drifted")

    angle = sun_altitude_degrees(1.8, 1.2)
    if not 56.0 < angle < 57.0:
        raise AssertionError("shadow ratio conversion drifted")

    a = canonical_json_bytes(describe())
    b = canonical_json_bytes(describe())
    if a != b:
        raise AssertionError("ShadowFinder adapter description is not canonical")

    print(
        "GEOINT ShadowFinder self-test PASS: "
        "shadow geometry + pinned contract exact"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--shadowfinder-root")
    parser.add_argument("--timezone-grid")
    parser.add_argument("--datetime")
    parser.add_argument("--time-format", choices=("utc", "local"), default="utc")
    parser.add_argument("--sun-altitude-deg", type=float)
    parser.add_argument("--object-height", type=float)
    parser.add_argument("--shadow-length", type=float)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0
    if args.describe:
        sys.stdout.buffer.write(canonical_json_bytes(describe()))
        return 0
    if not args.live:
        parser.error("choose --self-test, --describe, or --live")

    if not args.shadowfinder_root or not args.datetime:
        parser.error("--live requires --shadowfinder-root and --datetime")

    try:
        result = run_live(args)
    except Exception as exc:
        sys.stderr.buffer.write(
            canonical_json_bytes(
                {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )
        )
        return 2

    sys.stdout.buffer.write(canonical_json_bytes(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
