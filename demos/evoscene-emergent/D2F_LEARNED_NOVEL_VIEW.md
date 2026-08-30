# D2f — Learned Novel-View Evidence

Status: implemented behind the existing NovelViewAgent/render_view boundary;
local structural validation pending; CUDA live validation required.

D2f adds the missing unseen-region generation behavior needed for an
EvoScene-faithful experiment.

## Protocol mapping

~~~text
ViewPlannerAgent
  -> proposes yaw / pitch

NovelViewAgent
  -> render_view
  -> pinned MetaView generation
  -> content-addressed learned RGB artifact

SpatialPriorAgent
  -> existing D2a MoGe on learned RGB
  -> target-view metric depth + mask + intrinsics

GeometryAgent
  -> target-view overlap render
  -> robust metric depth alignment
  -> reject contradictory overlap
  -> backproject accepted learned depth
  -> MetaView target-camera -> world transform

FusionAgent
  -> existing D2c fusion/refinement

CriticAgent
  -> evaluates quality / coverage / cost
  -> either requests another view or proposes STOP
~~~

No new protocol action is added. D2f is a real backend implementation of the
already-frozen `render_view` semantic action.

## Canonical learned backend

~~~text
MetaView
source commit:
  a8a3d46c198d7cc0627e8e8a55d93d362fc1ca55

src/inference.py Git blob:
  e7aa670dcfbbf4d061f75ec8af01edf1b5ae8805

checkpoint:
  Kwai-Kolors/MetaView/model-2500-best.safetensors

checkpoint SHA-256:
  a67ae628ea665c0f9ef00be3db38eaebca02734f63a4e04118573ac9e30a74ef

seed:  0
steps: 40
output: 960x528 RGB
~~~

The wrapper executes the pinned official MetaView `src/inference.py`. The
official script writes `[source | generated]`; the wrapper deterministically
extracts the right-hand 960x528 generated panel.

## Canonical dependency revisions

~~~text
Qwen/Qwen-Image-Edit:                    ac7f931
depth-anything/DA3-GIANT-1.1:            72ee9f8
depth-anything/DA3NESTED-GIANT-LARGE-1.1:b2359bd
~~~

They are mirrored in `metaview-dependencies.json` and participate in the
learned-generation cache key.

## Research-license boundary

The MetaView checkpoint/model card is Apache-2.0, but its required canonical
Depth Anything 3 GIANT/NESTED checkpoints are CC-BY-NC-4.0. Therefore this
canonical D2f backend is for research/evaluation, not a commercial production
backend. The protocol boundary is backend-neutral and can later substitute a
commercially suitable novel-view model without changing scheduling semantics.

## CUDA boundary

The official MetaView inference implementation instantiates its generation
pipeline on CUDA. The canonical D2f live gate therefore runs on a CUDA worker,
not the macOS D2a environment.

The repository-side D2f structural gate remains lightweight and runs without
the MetaView weights.

## Learned-generation cache

Diffusion GPU kernels need not be byte-identical across machines. D2f avoids
letting that nondeterminism contaminate the coordination experiment.

One cache key is computed from:

~~~text
D2f contract
original input SHA-256
yaw_mdeg
pitch_mdeg
radius_mm
MetaView source/checkpoint identity
dependency-manifest SHA-256
seed / steps / resolution
~~~

The first request establishes the learned artifact. Every identical request
from either experimental arm reuses those exact bytes.

This means fixed and emergent arms pay an expensive learned-view invocation
only for distinct requested views, while repeated identical views resolve to
the same frozen evidence.

## Target camera

D2f uses MetaView's official yaw/pitch/radius transform exactly:

~~~text
R = R_yaw * R_pitch
C = [0, 0, radius]
t = C - R*C
x_target = R*x_world + t
~~~

For the live integration gate, radius is the median positive Z of the current
D2c refined state, quantized to 1 mm before generation.

## Turning learned RGB into new 3D evidence

After MetaView creates `novel.png`, the existing D2a MoGe adapter estimates
target-view metric depth.

The current D2c scene is separately rendered into that same target camera.
Overlapping pixels establish a robust scale factor:

~~~text
ratio = current_target_depth / learned_MoGe_depth

discard ratios outside [0.25, 4.0]
median
keep ratios within +/-25% of median
median again -> learned depth scale
~~~

Evidence policy:

~~~text
current geometry absent at pixel
  -> accept as novel learned evidence

current geometry present
  -> accept only when scaled learned depth agrees within
     max(50 mm, 5% current depth)

contradictory overlap
  -> reject
~~~

Accepted target-camera points are transformed back into the original/world
coordinate system and emitted as:

~~~text
points.f32le
scene.json       evoscene.point_cloud.v1
evidence.json
accepted_mask.u8
novel_mask.u8
~~~

That `scene.json + points.f32le` pair enters D2c without a special fusion path.

## Structural gate

~~~sh
python tools/verify_evoscene_d2f.py
~~~

This runs:

~~~text
Zig 0.16 D2f contract tests
Zig/Python model/config agreement
MetaView output-crop/cache-key self-test
MetaView extrinsic inverse self-test
metric-overlap alignment self-test
canonical dependency-manifest validation
~~~

Expected:

~~~text
D2f STRUCTURAL PASS: Zig/MetaView/geometry-bridge contracts agree
~~~

## CUDA live gate

Prepare the pinned MetaView checkout/environment and pinned local model
snapshots. Then run from the Starling repository environment containing D2a
MoGe and D2e SciPy:

~~~sh
python tools/verify_evoscene_d2f.py --live \
  --input /path/to/original.png \
  --source-dir /path/to/current/d2c-state \
  --source-camera-dir /path/to/original/d2a \
  --cache-dir /path/to/shared/learned-cache \
  --metaview-root /path/to/MetaView \
  --metaview-python /path/to/metaview/bin/python \
  --ckpt-path /path/to/model-2500-best.safetensors \
  --da3-giant-path /path/to/DA3-GIANT-1.1 \
  --da3-depth-path /path/to/DA3NESTED-GIANT-LARGE-1.1 \
  --qwen-path /path/to/Qwen
~~~

`--qwen-path` is the parent directory containing `Qwen-Image-Edit/`, matching
the official MetaView local-path loader.

The live gate executes one real learned view, then immediately repeats the
same request and requires a cache hit. It then runs:

~~~text
learned RGB
-> D2a MoGe
-> D2f metric/world bridge
-> D2c fusion/refinement
-> revised D2e final mesh
~~~

and requires content-identical bridge/fusion/mesh results from the cached
learned artifact.

Expected terminal verdicts:

~~~text
D2f second_generation_cache_hit: yes
D2f cached_repeat: yes
D2f bridge_repeat: yes
D2f learned_fusion_repeat: yes
D2f LIVE PASS: learned unseen-region reconstruction loop complete
D2f REPOSITORY PASS: full Zig test suite complete
~~~

## Stage meaning

D2a-D2e remain the deterministic reconstruction substrate.

D2f is optional if the only goal is testing that deterministic substrate.
It is **required** before D3 if the goal is the full EvoScene-like experiment,
because otherwise `render_view` can only reproject geometry that already
exists and the scheduling problem is materially easier than EvoScene.
