# D2b — Explicit Backprojection + Point-Cloud Scene State

Status: implementation complete on the D2b branch; local live validation pending.

D2b converts the validated D2a spatial-prior artifacts into the first real
3D scene representation owned by the Geometry operator.

## Experimental boundary

D2a provides only:

~~~text
depth.f32le
mask.u8
camera.json
prior.json
~~~

D2b performs the geometry itself:

~~~text
metric depth + normalized intrinsics + validity mask
                    |
                    v
           explicit backprojection
                    |
                    v
        canonical XYZ point cloud
                    |
             +------+------+
             |             |
             v             v
      points.f32le      scene.ply
             |
             v
          scene.json
~~~

MoGe's native point-map output is not used.

## Coordinate convention

D2b freezes OpenCV camera space:

~~~text
+X = image right
+Y = image down
+Z = camera forward
units = meters
~~~

For source pixel (x, y) in an image of width W and height H, D2b uses the
pixel center:

~~~text
u = (x + 0.5) / W
v = (y + 0.5) / H
~~~

With normalized camera intrinsics (fx, fy, cx, cy) and metric depth z:

~~~text
X = (u - cx) * z / fx
Y = (v - cy) * z / fy
Z = z
~~~

Only valid D2a pixels are emitted.

## Canonical point ordering

Points are ordered by the source raster:

~~~text
row 0 left -> right
row 1 left -> right
...
invalid pixels skipped
~~~

This means one validated D2a mask uniquely determines the point ordering.

## Canonical artifacts

### points.f32le

~~~text
XYZ triplets
float32 little-endian
meters
12 bytes per point
source-row-major valid-pixel ordering
~~~

The calculated coordinates are quantized to float32 before any output or
summary is generated. The binary point file is the authoritative geometry
payload.

### scene.ply

Deterministic ASCII PLY containing the exact same float32 XYZ values.

The PLY contains no colors, normals, faces, or hidden use of the source RGB
image. D2b therefore still has exactly the D0 geometry inputs:

~~~text
DepthMap + CameraEstimate -> PointCloud
~~~

The verifier requires every ASCII PLY coordinate to round-trip to the exact
float32 bytes in points.f32le.

### scene.json

Deterministic content/provenance manifest containing:

~~~text
D2a prior manifest SHA-256
D2a depth SHA-256
D2a mask SHA-256
D2a camera SHA-256
point-cloud configuration
point count
3D metric bounds
points.f32le SHA-256
scene.ply SHA-256
~~~

### telemetry.json

Non-content execution metadata, currently including backprojection time,
Python version, and platform. Timing is intentionally excluded from scene.json.

## No new runtime dependency

The D2b adapter uses only the Python standard library. It can run from the
existing D2a virtual environment but does not depend on PyTorch, NumPy, MoGe,
or another 3D framework.

## Structural gate

~~~sh
zig build test-demo-evoscene-d2b

zig build -Doptimize=ReleaseFast \
  run-demo-evoscene-d2b -- validate

python tools/evoscene_backproject.py --self-test

python tools/verify_evoscene_d2b.py
~~~

The self-test uses a synthetic 2x2 depth/camera fixture and verifies three
exact known backprojected points.

Expected verdict:

~~~text
D2b STRUCTURAL PASS: Zig/Python backprojection contracts agree
~~~

## Live gate

D2b defaults to the canonical D2a output already created by the D2a gate:

~~~text
trials/evoscene-d2a-smoke/run-a
~~~

Run:

~~~sh
python tools/verify_evoscene_d2b.py --live
~~~

Outputs are written under ignored trial state:

~~~text
trials/evoscene-d2b-smoke/
  run-a/
    points.f32le
    scene.ply
    scene.json
    telemetry.json
  run-b/
    ...
~~~

The gate requires:

~~~text
all D2a parent hashes match their bytes
point count == D2a valid-pixel count
point file size == point_count * 12
all XYZ values finite
point-cloud bounds non-degenerate on X/Y/Z
all forward Z values positive
PLY point count exact
every PLY point round-trips to exact binary float32 coordinates
two point files byte-identical
two PLY files byte-identical
two scene manifests byte-identical
full Zig repository tests pass
~~~

Expected final verdicts:

~~~text
D2b LIVE PASS: deterministic point-cloud reconstruction complete
D2b REPOSITORY PASS: full Zig test suite complete
~~~

Do not begin D2c fusion/refinement until this live gate passes locally.
