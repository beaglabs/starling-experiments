# D2a — Real Spatial Prior

Status: implementation complete on the D2a branch; live local model validation pending.

D2a replaces the mock spatial-prior kernel with a real single-image geometry
model while preserving the D0/D1 experimental boundaries.

## Model choice

D2a uses **MoGe-2 ViT-S normal**:

~~~text
model repo:
Ruicheng/moge-2-vits-normal

weights:
model.pt

weights SHA-256:
79a16621928c2bf0ed04659218c55c01075e950507f40bb3332fb4c873d3e1dc

MoGe source commit:
925b8ed835a7a9cdb7578ba15c658a0afc969030
~~~

The source is pinned to the final MoGe-2 line before the MoGe-3 merge. The
installed package is verified through its PEP 610 `direct_url.json` VCS
metadata: it must resolve to the Microsoft/MoGe Git origin and the exact pinned
commit above. This avoids relying on installed source-file bytes, which Python
packaging may rewrite while building/installing a wheel.

The 35M-parameter ViT-S checkpoint is small enough for a local smoke gate and
emits metric geometry plus camera intrinsics in one inference pass.

## Deliberate operator boundary

MoGe can emit:

~~~text
point map
metric depth
validity mask
surface normals
camera intrinsics
~~~

D2a intentionally persists only:

~~~text
metric depth
validity mask
camera intrinsics
~~~

The point map and normals are discarded.

D2b must reconstruct the initial point cloud from the D2a depth and intrinsics.
This keeps:

~~~text
SpatialPrior
    !=
Geometry
~~~

instead of allowing one foundation model to silently perform both experimental
operators.

## Canonical smoke configuration

The first evidence gate freezes:

~~~text
device:       cpu
precision:    fp32
torch threads: 1
ViT tokens:   1200
projection:   forced
mask:         applied
FOV input:    none / inferred
~~~

MPS and CUDA are available as noncanonical adapter options, but the D2a gate
uses CPU/FP32 first so repeatability is tested before acceleration.

The canonical benchmark configuration is not permanently frozen here. D5 may
raise the token budget after the real reconstruction loop has been evaluated,
but both experiment arms must always use the same tool configuration.

## Artifact record

The adapter writes:

~~~text
depth.f32le
mask.u8
camera.json
prior.json
telemetry.json
~~~

### depth.f32le

~~~text
row-major
float32 little-endian
metric depth in meters
invalid pixels exactly 0.0
~~~

### mask.u8

~~~text
row-major
one byte per pixel
0 = invalid
1 = valid
~~~

### camera.json

Contains normalized OpenCV-style intrinsics, image dimensions, derived focal
length in pixels, principal point in pixels, and horizontal/vertical FOV.

### prior.json

Deterministic content manifest containing:

~~~text
input SHA-256
exact model identity
exact model-weight SHA-256
MoGe source commit
inference configuration
depth/mask/camera hashes
dimensions
valid-pixel count
depth summary
~~~

Wall-clock measurements and environment versions are deliberately excluded from
this deterministic record.

### telemetry.json

Contains non-content metadata:

~~~text
checkpoint lookup time
model load time
inference time
Python version
PyTorch version
NumPy version
platform
canonical-run flag
~~~

## Setup

Use an isolated environment:

~~~sh
python3 -m venv .venv-evoscene-d2a
source .venv-evoscene-d2a/bin/activate

python -m pip install --upgrade pip
python -m pip install -r demos/evoscene-emergent/requirements-d2a.txt
~~~

The first live run verifies that the installed MoGe package came from the
pinned VCS commit, then downloads the pinned 141 MB MoGe-2 ViT-S checkpoint
through Hugging Face and verifies its SHA-256 before loading it.

## Structural gate

This does not load the model:

~~~sh
zig build test-demo-evoscene-d2a

zig build -Doptimize=ReleaseFast \
  run-demo-evoscene-d2a -- validate

python3 tools/evoscene_moge_prior.py --self-test

python3 tools/verify_evoscene_d2a.py
~~~

Expected verdict:

~~~text
D2a STRUCTURAL PASS: Zig/Python spatial-prior contracts agree
~~~

## Live gate

Run the pinned real model twice against the deterministic fixture:

~~~sh
python3 tools/verify_evoscene_d2a.py --live
~~~

The ignored live artifacts are written under:

~~~text
trials/evoscene-d2a-smoke/
~~~

The live gate requires:

~~~text
checkpoint SHA-256 matches
real positive metric depth exists
depth is non-constant
valid mask is non-empty
camera focal lengths are positive
camera principal point is in-frame
raw file byte sizes match image dimensions
all declared artifact hashes match bytes
repeat depth bytes are identical
repeat mask bytes are identical
repeat camera bytes are identical
repeat deterministic manifest bytes are identical
full Zig repository test suite passes
~~~

Expected final verdict:

~~~text
D2a LIVE PASS: real metric depth + camera prior complete
D2a REPOSITORY PASS: full Zig test suite complete
~~~

A real image can be substituted for the fixture:

~~~sh
python3 tools/verify_evoscene_d2a.py \
  --live \
  --input /path/to/image.jpg
~~~

Do not begin D2b until the canonical live gate passes locally.
