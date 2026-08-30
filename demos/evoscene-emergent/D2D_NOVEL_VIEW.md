# D2d — Deterministic Novel-View Rendering + Reprojection

Status: implementation complete on the D2d branch; local live validation pending.

D2d closes the first real iterative reconstruction loop without introducing a
generative image model.

## What it does

~~~text
D2c refined point cloud
        |
        v
orbit virtual camera
        |
        v
deterministic nearest-Z rasterization
        |
        +--> render_depth.f32le
        +--> render_mask.u8
        |
        v
backproject target pixel centers
        |
        v
transform evidence back to world/source coordinates
        |
        v
points.f32le + scene.json
        |
        v
D2c fusion/refinement
~~~

The resulting `scene.json + points.f32le` evidence directory uses the same
`evoscene.point_cloud.v1` boundary already accepted by D2c.

## Pose semantics

D2d uses the exact millidegree encoding frozen by D1.

Canonical smoke poses:

~~~text
A: azimuth  35.000 degrees, elevation 10.000 degrees
B: azimuth 325.000 degrees, elevation 10.000 degrees
~~~

Payload encoding:

~~~text
(azimuth_mdeg << 32) | elevation_mdeg
~~~

The source camera is the D2a origin. The virtual target camera orbits the
axis-aligned bounding-box center of the current D2c scene at the original
source-camera-to-center radius, then looks at that center.

Camera coordinates remain:

~~~text
+X = image right
+Y = image down
+Z = camera forward
~~~

## Rasterization

Each source point is projected through the D2a normalized intrinsics.

Raster rule:

~~~text
nearest positive float32 Z wins
tie => lower deterministic source point index wins
~~~

Invalid pixels have exactly zero depth.

Valid target pixels are then backprojected using the target pixel center and
transformed back to the original/world frame.

## Important scientific limitation

D2d does **not** invent or recover surfaces that are absent from the current
point cloud. It can only re-render and reproject existing reconstructed
geometry.

That is intentional. D2d proves:

~~~text
view request
-> real deterministic render
-> target-view depth evidence
-> world-frame reprojection
-> fusion
-> refinement
~~~

without making a claim that deterministic reprojection creates new hidden
geometry.

The optional D2f learned novel-view generator is where genuinely new image
evidence can enter the reconstruction loop.

## Iteration support

D2c is extended in this PR so a previous D2c output can be used directly as
the source state for the next D2c invocation:

~~~text
fusion.json + refined_points.f32le
~~~

This extension does not alter the already-validated D2c canonical D2b-input
path or its frozen hashes.

## Outputs

~~~text
render_depth.f32le
render_mask.u8
points.f32le
scene.json
render.json
telemetry.json
~~~

`render.json` records source/camera hashes, pose payload, target camera basis,
coverage, and all output hashes. Timing remains outside deterministic content.

## Live gate

Defaults:

~~~text
source:
  trials/evoscene-d2c-smoke/run-a

camera:
  trials/evoscene-d2a-smoke/run-a
~~~

Run:

~~~sh
python tools/verify_evoscene_d2d.py --live
~~~

The gate performs:

~~~text
Zig D2d contract validation
synthetic deterministic renderer self-test
pose A render
pose A repeated render
pose B render
exact content/hash/provenance checks
camera-basis orthogonality checks
depth/mask validation
pose sensitivity check
pose A evidence -> D2c closed-loop fusion/refinement
closed-loop byte-identical repeat
full zig build test
~~~

Expected terminal verdicts:

~~~text
D2d byte_identical_repeat: yes
D2d pose_sensitive: yes
D2d closed_loop_repeat: yes
D2d LIVE PASS: deterministic novel-view render/reprojection complete
D2d REPOSITORY PASS: full Zig test suite complete
~~~

After D2d, D2e can finalize the converged scene into the deterministic mesh
artifact. D2f remains optional if we want learned novel-view evidence before
finalization.
