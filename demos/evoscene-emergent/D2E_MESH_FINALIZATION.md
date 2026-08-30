# D2e — Deterministic Final Mesh Generation / Export

Status: implementation complete on the D2e branch; local live validation pending.

D2e is the post-convergence finalizer for the D2 reconstruction pipeline.
It runs exactly once after STOP/convergence and is not controlled by the
fixed or emergent scheduler.

## Input

One final D2c refined state:

~~~text
fusion.json
refined_points.f32le
~~~

The default live gate uses the closed-loop D2d result:

~~~text
trials/evoscene-d2d-smoke/closed-loop-a
~~~

## Surface extraction

The finalizer uses the same 25 mm voxel lattice as D2c.

~~~text
final refined points
      |
      v
occupied voxel set
      |
      v
emit only exposed voxel faces
      |
      v
one quad per exposed face
      |
      v
two deterministic triangles / quad
~~~

Adjacent occupied voxels therefore do not emit their shared internal face.

Face order is frozen:

~~~text
-X, +X, -Y, +Y, -Z, +Z
~~~

Triangle rule for each face-local quad:

~~~text
(v0, v1, v2)
(v0, v2, v3)
~~~

Each exposed quad owns four face-local vertices so flat normals remain exact
and export behavior is deterministic across GLB, OBJ, and PLY.

## Coordinate convention

~~~text
+X = image right
+Y = image down
+Z = camera forward
units = meters
~~~

No implicit coordinate conversion is applied during finalization.

## Outputs

~~~text
scene.glb       primary demo artifact
scene.obj       interoperable text export
scene_mesh.ply  inspectable triangle mesh
mesh.json       deterministic provenance/content manifest
telemetry.json  timing/environment metadata
~~~

The GLB is a glTF 2.0 binary container with:

~~~text
POSITION: float32 VEC3
NORMAL:   float32 VEC3
indices:  uint32 SCALAR
mode:     TRIANGLES
~~~

## Determinism

`mesh.json` records the final D2c parent hashes, voxel/surface rules, counts,
metric bounds, and the SHA-256 of all three mesh exports. Timing is excluded
from deterministic content.

Two finalizer runs must produce byte-identical:

~~~text
scene.glb
scene.obj
scene_mesh.ply
mesh.json
~~~

## Gate

~~~sh
python tools/verify_evoscene_d2e.py --live
~~~

The gate performs:

~~~text
Zig D2e contract validation
synthetic two-adjacent-voxel surface test
shared internal-face removal check
real final-state surface extraction
GLB header/chunk/accessor/bufferView validation
finite POSITION + unit NORMAL validation
OBJ vertex/normal/triangle count validation
PLY vertex/triangle count validation
mesh accounting identities
parent provenance checks
metric bounds checks
byte-identical duplicate finalization
full zig build test
~~~

Expected terminal verdicts:

~~~text
D2e byte_identical_repeat: yes
D2e GLB validation: PASS
D2e OBJ validation: PASS
D2e PLY validation: PASS
D2e LIVE PASS: deterministic final mesh generation complete
D2e REPOSITORY PASS: full Zig test suite complete
~~~

## Experimental fairness

The same D2e finalizer is used exactly once for both fixed and emergent arms.
Coordination cost can therefore be reported both before finalization and
end-to-end including finalization, without mesh generation becoming a
scheduling degree of freedom.

D2f remains optional and is not required for the deterministic D2 pipeline.
