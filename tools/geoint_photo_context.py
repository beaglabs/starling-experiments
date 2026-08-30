#!/usr/bin/env python3
"""Extract trustworthy acquisition context for the GEOINT emergence trial."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
from typing import Any


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


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_datetime(value: str | None) -> str | None:
    if not value:
        return None

    candidates = (
        value,
        value.replace(":", "-", 2),
    )
    for candidate in candidates:
        try:
            parsed = dt.datetime.fromisoformat(candidate)
        except ValueError:
            continue
        return parsed.replace(tzinfo=None).isoformat(timespec="seconds")
    return None


def extract_exif_datetime(image: Any) -> tuple[str | None, str | None]:
    exif = image.getexif()
    if not exif:
        return None, None

    # EXIF DateTimeOriginal / DateTimeDigitized / DateTime.
    for tag, label in (
        (36867, "DateTimeOriginal"),
        (36868, "DateTimeDigitized"),
        (306, "DateTime"),
    ):
        raw = exif.get(tag)
        if raw:
            normalized = normalize_datetime(str(raw))
            if normalized:
                return normalized, label

    return None, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=pathlib.Path)
    parser.add_argument("--datetime")
    parser.add_argument("--object-height", type=float)
    parser.add_argument("--shadow-length", type=float)
    parser.add_argument("--sun-altitude-deg", type=float)
    args = parser.parse_args()

    image_path = args.image.expanduser().resolve()
    if not image_path.is_file():
        raise SystemExit(f"image not found: {image_path}")

    from PIL import Image

    with Image.open(image_path) as image:
        width, height = image.size
        mode = image.mode
        exif_datetime, exif_source = extract_exif_datetime(image)

    override_datetime = normalize_datetime(args.datetime)
    acquired_at = override_datetime or exif_datetime
    datetime_source = (
        "explicit"
        if override_datetime
        else exif_source
    )

    has_ratio = (
        args.object_height is not None
        and args.shadow_length is not None
        and args.object_height > 0
        and args.shadow_length > 0
    )
    has_altitude = (
        args.sun_altitude_deg is not None
        and 0 < args.sun_altitude_deg <= 90
    )

    result = {
        "schema": "starlings.geoint.photo_context.v1",
        "image": {
            "sha256": sha256_file(image_path),
            "width": width,
            "height": height,
            "mode": mode,
        },
        "acquisition": {
            "datetime": acquired_at,
            "datetime_source": datetime_source,
        },
        "shadow": {
            "object_height": args.object_height if has_ratio else None,
            "shadow_length": args.shadow_length if has_ratio else None,
            "sun_altitude_deg": (
                args.sun_altitude_deg if has_altitude else None
            ),
        },
        "capability": {
            "shadowfinder_ready": bool(
                acquired_at and (has_ratio or has_altitude)
            ),
            "blocked_reasons": [
                reason
                for condition, reason in (
                    (not acquired_at, "missing_datetime"),
                    (
                        not (has_ratio or has_altitude),
                        "missing_trusted_shadow_geometry_or_sun_altitude",
                    ),
                )
                if condition
            ],
        },
    }

    print(
        canonical_json_bytes(result).decode("utf-8"),
        end="",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
