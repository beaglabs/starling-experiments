# D2c — Deterministic Fusion + Geometry Refinement

Status: implementation complete on the D2c branch; local live validation pending.

D2c consumes two D2b point-cloud scene states and produces an auditable fused
cloud plus a refined cloud.

## Algorithm

Fusion:

~~~text
source points + evidence points
            |
            v
canonicalize by voxel key + XYZ
            |
            v
25 mm voxel buckets
            |
            v
float32 centroid per occupied voxel
            |
            v
fused_points.f32le
~~~

Refinement:

~~~text
fused occupied voxels
        |
        v
count occupied neighbors in 26-connected neighborhood
        |
        v
keep voxel when neighbors >= 2
        |
        v
refined_points.f32le + refined.ply
~~~

Both settings are canonical smoke parameters, not yet the D5 benchmark freeze.

## Determinism

All input points are canonicalized before accumulation. Source/evidence order
therefore cannot change fused geometry bytes.

Centroids are accumulated in canonical order with math.fsum and quantized to
float32 before becoming authoritative geometry.

## Live smoke evidence

Until D2d creates genuinely new view evidence, the D2c live gate uses the two
independently generated, byte-identical D2b runs:

~~~text
source:   trials/evoscene-d2b-smoke/run-a
evidence: trials/evoscene-d2b-smoke/run-b
~~~

This intentionally validates the real 41k-point fusion/refinement machinery
without pretending duplicated evidence is a scientific novel-view result.

The self-test separately uses offset evidence to prove centroid fusion and
isolated-voxel pruning.

## Outputs

~~~text
fused_points.f32le
refined_points.f32le
refined.ply
fusion.json
telemetry.json
~~~

`fusion.json` contains deterministic parent hashes, configuration, contribution
accounting, fused/refined counts, hashes, and metric bounds. Timing stays only
in telemetry.

## Gate

~~~sh
python tools/verify_evoscene_d2c.py --live
~~~

The gate includes:

~~~text
Zig 0.16 D2c contract tests
Zig/Python contract agreement
exact synthetic centroid/pruning self-test
D2b parent provenance validation
contribution accounting
voxel/refinement count identities
binary artifact size/hash validation
PLY exact float32 round-trip
non-degenerate metric bounds
two byte-identical D2c executions
full zig build test
~~~

Expected terminal verdicts:

~~~text
D2c LIVE PASS: deterministic fusion + geometry refinement complete
D2c REPOSITORY PASS: full Zig test suite complete
~~~

D2d will replace duplicated smoke evidence with deterministic novel-view
render/reprojection evidence while keeping this same fusion boundary.
