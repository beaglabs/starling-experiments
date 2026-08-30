# Geometry Tokens E0 — one beautiful procedural mesh

This experiment asks a deliberately narrow question:

> Can a compact, deterministic Geometry Tokens representation compile into a visually polished Blender asset through Geometry Nodes?

E0 does **not** use an LLM, Starlings scheduling, rigging, SkinTokens, or image-to-3D. Those come later. The first gate is whether the representation itself can drive a useful procedural asset.

The initial target is a stylized **Iridescent Starling** sculpture.

## Pipeline

```text
iridescent_starling.gtok
        |
        v
strict Geometry Tokens parser
        |
        v
canonical typed document
        |
        v
Geometry Nodes compiler
        |
        v
Blender mesh + materials
        |
        +--> studio render (.png)
        +--> editable scene (.blend)
        +--> portable asset (.glb)
```

The generated object is built by a Geometry Nodes modifier. Each primitive token becomes a mesh primitive, transform, and material stage; the compiler joins the branches into one procedural asset before applying the modifier for export.

## Geometry Tokens v0.1

The format is intentionally tiny.

```text
GTOK 0.1
asset "Iridescent Starling"

material plumage iridescent 0.62 0.20
material beak pbr 0.20 0.115 0.045 0.12 0.31

ellipsoid body plumage 0.00 0.10 0.05 0.70 0.50 0.82 0 0 0
cone beak beak 0.00 -0.62 0.84 0.155 0.015 0.52 90 0 0
```

The first vocabulary contains:

- `material ... pbr`
- `material ... iridescent`
- `ellipsoid`
- `cone`

This is intentionally an intermediate representation rather than generated `bpy` source. A future model should predict constrained tokens or parameters, while deterministic code owns Blender execution.

## Run

Requires Blender 4.5 LTS or a compatible newer Blender build.

From this directory:

```sh
blender --background \
  --python generate_blender.py -- \
  specs/iridescent_starling.gtok
```

On macOS, if `blender` is not on `PATH`:

```sh
/Applications/Blender.app/Contents/MacOS/Blender --background \
  --python generate_blender.py -- \
  specs/iridescent_starling.gtok
```

Outputs are written to `output/`:

```text
iridescent_starling.png
iridescent_starling.glb
iridescent_starling.blend
```

Generated outputs are intentionally not committed.

## Parser tests

The token parser has no Blender dependency:

```sh
PYTHONPATH=demos/geometry-tokens-e0 \
python3 -m unittest discover \
  -s demos/geometry-tokens-e0/tests \
  -p 'test_*.py'
```

The parser is fail-closed for format version, duplicate names, malformed numeric fields, unsupported material types, invalid dimensions, and dangling material references.

## E0 acceptance gate

E0 is successful when one invocation produces an asset that satisfies all of the following:

1. the `.gtok` document parses deterministically;
2. Blender creates the Geometry Nodes graph without manual editing;
3. the resulting asset renders successfully;
4. Blender exports a readable `.glb`;
5. the mesh has a deliberately authored silhouette and material treatment rather than looking like a raw primitive test;
6. changing only Geometry Tokens changes the asset without changing Python code.

Visual quality remains a human gate in E0. We should not invent a numeric "beauty" score before we have multiple outputs to compare.

## Next experiments

If E0 produces a convincing mesh, the useful sequence is:

```text
E0  token -> beautiful mesh
E1  richer generator vocabulary / reusable Geometry Node groups
E2  deterministic mesh quality operators
E3  token proposal/refinement by a small multimodal model
E4  skeleton + skin variables
E5  SkinTokens-like rigging operator
E6  deformation tests and closed-loop refinement
```

The long-term representation may become much richer than this text format. The point of E0 is to establish the architectural boundary:

> models propose compact geometry state; deterministic Blender operators compile and verify it.
