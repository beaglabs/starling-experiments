# D2e — Deterministic Projected-Surface Mesh Finalization

Status: implementation revised after the first real-image mesh inspection;
local live validation pending.

D2e runs exactly once after convergence and is outside both the fixed and
emergent schedulers.

## Why the first D2e mesher was replaced

The first prototype treated every occupied 25 mm D2c cell as a solid cube and
emitted exposed cube faces. A real-photo test exposed two problems:

~~~text
sparse 2.5D surface samples -> thick blocky voxel shell
four face-local vertices / quad -> every quad becomes an indexed mesh island
~~~

That representation was deterministic, but it was the wrong surface model.

The revised D2e reconstructs the visible surface in the original D2a camera
chart instead of inventing a volumetric cube shell.

## Inputs

Final D2c state:

~~~text
fusion.json
refined_points.f32le
~~~

Original D2a camera:

~~~text
prior.json
camera.json
~~~

The same final state and camera treatment are used for both experimental arms.

## Revised surface algorithm

~~~text
final refined 3D points
        |
        v
project into original D2a camera
        |
        v
nearest-Z z-buffer
one point / source pixel
        |
        v
deterministically perturbed 2D chart coordinates
(tie-breaking only; exported XYZ unchanged)
        |
        v
SciPy Delaunay triangulation
        |
        v
filter triangles:
  projected edge <= 16 px
  depth jump <= max(50 mm, 3% near depth)
        |
        v
orient triangles toward source camera
        |
        v
weld shared vertices
        |
        v
area-weighted shared vertex normals
        |
        v
GLB / OBJ / PLY
~~~

## Frozen numerical dependency

~~~text
SciPy 1.18.1
~~~

The existing D2a virtual environment already contains this version. The
adapter verifies it before triangulation.

## Scientific meaning

This produces a coherent **source-visible 2.5D surface**, not a fabricated
closed backside.

That matches what the deterministic D2a-D2d substrate actually knows:

~~~text
visible metric geometry
+ deterministic reprojections of existing geometry
~~~

D2d does not reveal unseen surfaces, so D2e should not pretend it has a
watertight full object. Learned D2f evidence can later justify a multi-view
surface/TSDF finalizer if we want complete unseen geometry.

## Topology

Vertices are shared by adjacent accepted triangles. This is intentionally the
opposite of the failed face-local voxel export.

The manifest reports actual indexed connected components, and the verifier
recomputes them directly from the GLB index stream.

## Coordinate convention

~~~text
+X = image right
+Y = image down
+Z = camera forward
units = meters
~~~

## Outputs

~~~text
scene.glb       primary artifact
scene.obj       text interoperability/debug export
scene_mesh.ply  inspectable triangle mesh
mesh.json       deterministic provenance/content manifest
telemetry.json  non-content timing/environment metadata
~~~

## Deterministic ordering

~~~text
visible vertices: ascending source pixel index
Delaunay degeneracy: deterministic tiny chart-only perturbation
accepted triangles: canonical cyclic form + lexicographic order
~~~

The perturbation affects only the Delaunay decision chart, never the exported
3D positions.

## Gate

~~~sh
python tools/verify_evoscene_d2e.py --live
~~~

The default gate uses:

~~~text
source:
  trials/evoscene-d2d-smoke/closed-loop-a

camera:
  trials/evoscene-d2a-smoke/run-a
~~~

The gate validates:

~~~text
Zig/Python D2e contract agreement
exact SciPy version
synthetic one-component welded surface
source + camera provenance
source-camera z-buffer accounting
triangle pixel-gap rule
triangle depth-discontinuity rule
triangle winding toward source camera
shared indexed topology
connected-component count
finite positions
unit shared normals
GLB 2.0 structure/accessors/bufferViews
OBJ counts
PLY counts
byte-identical duplicate finalization
full zig build test
~~~

Expected terminal verdicts:

~~~text
D2e welded_topology: yes
D2e byte_identical_repeat: yes
D2e GLB validation: PASS
D2e OBJ validation: PASS
D2e PLY validation: PASS
D2e LIVE PASS: deterministic projected surface generation complete
D2e REPOSITORY PASS: full Zig test suite complete
~~~

D2f remains optional and is not required before D3.
