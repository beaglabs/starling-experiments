from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import shlex
from typing import Iterable

FORMAT_VERSION = "0.1"


class GeometryTokenError(ValueError):
    pass


@dataclass(frozen=True)
class MaterialToken:
    name: str
    kind: str
    parameters: tuple[float, ...]


@dataclass(frozen=True)
class PrimitiveToken:
    kind: str
    name: str
    material: str
    location: tuple[float, float, float]
    rotation_deg: tuple[float, float, float]
    parameters: tuple[float, ...]


@dataclass(frozen=True)
class GeometryDocument:
    asset: str
    materials: tuple[MaterialToken, ...]
    primitives: tuple[PrimitiveToken, ...]

    def canonical_dict(self) -> dict:
        return {
            "version": FORMAT_VERSION,
            "asset": self.asset,
            "materials": [
                {"name": m.name, "kind": m.kind, "parameters": list(m.parameters)}
                for m in self.materials
            ],
            "primitives": [
                {
                    "kind": p.kind,
                    "name": p.name,
                    "material": p.material,
                    "location": list(p.location),
                    "rotation_deg": list(p.rotation_deg),
                    "parameters": list(p.parameters),
                }
                for p in self.primitives
            ],
        }

    def canonical_json(self) -> str:
        return json.dumps(self.canonical_dict(), sort_keys=True, separators=(",", ":"))


def _floats(values: Iterable[str], *, line: int) -> tuple[float, ...]:
    try:
        return tuple(float(value) for value in values)
    except ValueError as exc:
        raise GeometryTokenError(f"line {line}: expected numeric token") from exc


def _need(parts: list[str], count: int, *, line: int, command: str) -> None:
    if len(parts) != count:
        raise GeometryTokenError(
            f"line {line}: {command} expects {count - 1} arguments, got {len(parts) - 1}"
        )


def parse_geometry_tokens(text: str) -> GeometryDocument:
    asset: str | None = None
    materials: list[MaterialToken] = []
    primitives: list[PrimitiveToken] = []
    seen_materials: set[str] = set()
    seen_primitives: set[str] = set()
    saw_header = False

    for line_number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            parts = shlex.split(stripped, comments=True, posix=True)
        except ValueError as exc:
            raise GeometryTokenError(f"line {line_number}: invalid quoting") from exc
        if not parts:
            continue

        command = parts[0].lower()
        if not saw_header:
            if command != "gtok" or len(parts) != 2 or parts[1] != FORMAT_VERSION:
                raise GeometryTokenError(
                    f"line {line_number}: first token must be 'GTOK {FORMAT_VERSION}'"
                )
            saw_header = True
            continue

        if command == "asset":
            _need(parts, 2, line=line_number, command="asset")
            if asset is not None:
                raise GeometryTokenError(f"line {line_number}: duplicate asset token")
            asset = parts[1]
            continue

        if command == "material":
            if len(parts) < 4:
                raise GeometryTokenError(f"line {line_number}: material token is incomplete")
            name, kind = parts[1], parts[2].lower()
            if name in seen_materials:
                raise GeometryTokenError(f"line {line_number}: duplicate material {name!r}")
            if kind == "pbr":
                _need(parts, 8, line=line_number, command="material pbr")
                params = _floats(parts[3:], line=line_number)
                r, g, b, metallic, roughness = params
                if not all(0.0 <= v <= 1.0 for v in (r, g, b, metallic, roughness)):
                    raise GeometryTokenError(f"line {line_number}: pbr values must be within [0, 1]")
            elif kind == "iridescent":
                _need(parts, 5, line=line_number, command="material iridescent")
                params = _floats(parts[3:], line=line_number)
                metallic, roughness = params
                if not (0.0 <= metallic <= 1.0 and 0.0 <= roughness <= 1.0):
                    raise GeometryTokenError(
                        f"line {line_number}: iridescent metallic/roughness must be within [0, 1]"
                    )
            else:
                raise GeometryTokenError(f"line {line_number}: unsupported material kind {kind!r}")
            materials.append(MaterialToken(name=name, kind=kind, parameters=params))
            seen_materials.add(name)
            continue

        if command == "ellipsoid":
            _need(parts, 12, line=line_number, command="ellipsoid")
            name, material = parts[1], parts[2]
            if name in seen_primitives:
                raise GeometryTokenError(f"line {line_number}: duplicate primitive {name!r}")
            nums = _floats(parts[3:], line=line_number)
            x, y, z, sx, sy, sz, rx, ry, rz = nums
            if min(sx, sy, sz) <= 0:
                raise GeometryTokenError(f"line {line_number}: ellipsoid scales must be positive")
            primitives.append(
                PrimitiveToken(
                    kind="ellipsoid",
                    name=name,
                    material=material,
                    location=(x, y, z),
                    rotation_deg=(rx, ry, rz),
                    parameters=(sx, sy, sz),
                )
            )
            seen_primitives.add(name)
            continue

        if command == "cone":
            _need(parts, 12, line=line_number, command="cone")
            name, material = parts[1], parts[2]
            if name in seen_primitives:
                raise GeometryTokenError(f"line {line_number}: duplicate primitive {name!r}")
            nums = _floats(parts[3:], line=line_number)
            x, y, z, radius_bottom, radius_top, depth, rx, ry, rz = nums
            if radius_bottom < 0 or radius_top < 0 or depth <= 0:
                raise GeometryTokenError(
                    f"line {line_number}: cone radii must be nonnegative and depth positive"
                )
            if radius_bottom == 0 and radius_top == 0:
                raise GeometryTokenError(
                    f"line {line_number}: cone cannot have zero radius at both ends"
                )
            primitives.append(
                PrimitiveToken(
                    kind="cone",
                    name=name,
                    material=material,
                    location=(x, y, z),
                    rotation_deg=(rx, ry, rz),
                    parameters=(radius_bottom, radius_top, depth),
                )
            )
            seen_primitives.add(name)
            continue

        raise GeometryTokenError(f"line {line_number}: unsupported token {parts[0]!r}")

    if not saw_header:
        raise GeometryTokenError(f"missing GTOK {FORMAT_VERSION} header")
    if asset is None:
        raise GeometryTokenError("missing asset token")
    if not materials:
        raise GeometryTokenError("at least one material is required")
    if not primitives:
        raise GeometryTokenError("at least one primitive is required")

    missing = sorted({primitive.material for primitive in primitives} - seen_materials)
    if missing:
        raise GeometryTokenError(f"unknown material reference(s): {', '.join(missing)}")

    return GeometryDocument(asset=asset, materials=tuple(materials), primitives=tuple(primitives))


def load_geometry_tokens(path: str | Path) -> GeometryDocument:
    return parse_geometry_tokens(Path(path).read_text(encoding="utf-8"))
