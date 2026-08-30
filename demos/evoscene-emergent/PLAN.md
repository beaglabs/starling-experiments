# Implementation Plan

## Objective

Build a locally runnable proof of concept that recreates the **operator structure** of EvoScene rather than its exact H100 implementation.

The experiment compares:

~~~text
FIXED
paper-shaped deterministic schedule
using local operators

vs

EMERGENT
same local operators
scheduled by specialist Starlings agents
~~~

The first claim we want to earn is:

> At matched reconstruction quality, emergent scheduling reduces expensive operator work relative to a fixed schedule built from the same tools.

Not:

> We reproduce EvoScene's published quality.

That distinction keeps the experiment runnable and the savings claim causal.

---

# Experimental design

## Inputs

Use two input classes.

### A. Hidden-ground-truth synthetic benchmark — primary

Create or select small 3D scenes for which the complete geometry is known.

For each scene:

1. render one RGB input image;
2. hide the original 3D scene from the reconstruction population;
3. reconstruct from only that image;
4. compare the result against the hidden source geometry and held-out renders.

This gives an objective quality target on local hardware.

Initial benchmark:

~~~text
5 scenes smoke
20 scenes canonical
~~~

Scene complexity should vary:

~~~text
single dominant object
multi-object tabletop
room-like layout
occlusion-heavy scene
thin / difficult geometry
~~~

### B. Real photographs — qualitative

Use a small set of ordinary photographs after the synthetic benchmark works.

They are useful for demos but not the primary savings gate because ground-truth 3D is unavailable.

---

# Quality metrics

Primary synthetic metrics should be computable without an LLM judging its own work.

Candidate core metrics:

~~~text
held-out depth render error
silhouette IoU
point/mesh Chamfer distance
F-score at fixed geometric thresholds
input-view reprojection error
held-out-view image similarity
coverage/completeness
~~~

Secondary metrics may include:

~~~text
LPIPS
CLIP/DINO similarity
texture consistency
learned perceptual preference
~~~

Before canonical benchmarking, freeze one scalar quality score or a clearly defined Pareto gate.

Example:

~~~text
Q =
  weighted(
    normalized geometry F-score,
    held-out depth consistency,
    held-out render similarity
  )
~~~

Do not tune the emergent policy against canonical test scenes.

---

# Cost metrics

Mandatory:

~~~text
wall_time_ms
total tool invocations
invocations by operator
depth calls
geometry-build calls
geometry-refine calls
novel-view calls
generated-view count
fusion calls
evaluation calls
model/agent inference calls
communication units
~~~

Optional:

~~~text
peak process RSS
peak unified memory estimate
energy/power telemetry
generated pixels
diffusion steps
~~~

The primary savings metric is quality matched:

~~~text
Savings(Q*) =
    1 - C_emergent(Q >= Q*) / C_fixed(Q >= Q*)
~~~

Report both:

~~~text
wall-clock savings
expensive-operator savings
~~~

The latter is often more portable across machines.

---

# Local operator strategy

The operator API must be implementation-independent.

Do not hard-code EvoScene's H100-scale models into the coordination layer.

Each tool implements a contract such as:

~~~text
DepthTool.estimate(image) -> DepthMap
GeometryTool.build(evidence) -> SceneRepresentation
GeometryTool.refine(scene, region) -> SceneRepresentation
ViewTool.generate(scene, source, pose) -> View
FusionTool.fuse(scene, view) -> Evidence
Evaluator.measure(scene) -> EvaluationReport
~~~

## Stage-one implementations

Start with the cheapest implementations that exercise the loop.

Initial choices:

- spatial prior: pinned MoGe-2 ViT-S normal, persisting only metric depth, validity mask, and camera intrinsics;
- scene representation: explicit backprojection into a point cloud owned by the geometry stage;
- fusion: deterministic point/evidence fusion;
- geometry refinement: deterministic cleanup/refinement before any learned geometry replacement;
- novel view: deterministic reprojection/rendering first, generative view synthesis later;
- finalization: one deterministic mesh generation/export pass after convergence in both arms;
- evaluation: hidden-ground-truth render/geometry metrics.

MoGe's native point-map output is deliberately not used as the D2b scene
representation. D2b must reconstruct geometry from the D2a depth and camera
artifacts so the SpatialPrior and Geometry operators remain separable.

This lets us validate the coordination experiment before depending on a particular 3D foundation model.

## Stage-two replacements

Once the loop is correct, swap real learned operators behind the same interfaces one at a time.

The replacement decision belongs to a later PR and should be based on actual Mac compatibility, memory envelope, and output quality.

---

# Agent design

A small local model may be used for specialist decisions, but deterministic policies should exist for every agent so the demo can distinguish:

~~~text
tool quality
coordination policy
model-agent quality
~~~

Initial population:

~~~text
SpatialPriorAgent
GeometryAgent
ViewPlannerAgent
NovelViewAgent
FusionAgent
CriticAgent
~~~

BudgetTelemetry is deterministic infrastructure, not an agent.

## Agent observation rule

Each agent receives:

~~~text
its local artifact references
small metric summaries
relevant recent messages
remaining resource budget
~~~

It does **not** receive a full global orchestration trace by default.

This preserves the local-information character of the Starlings experiments.

## Agent action rule

Agent output is constrained to typed proposals.

No shell commands or arbitrary Python are emitted by agents.

The runtime maps accepted actions to known tool adapters.

---

# Fixed reference schedule

The local fixed baseline uses the same tools and roughly mirrors the paper's three-stage evolving loop.

Initial frozen baseline candidate:

~~~text
1. spatial prior
2. full geometry build

3. choose predetermined view set A
4. generate/render views A
5. fuse A
6. full geometry refinement

7. choose predetermined complementary view set B
8. generate/render views B
9. fuse B
10. full geometry refinement

11. final evaluation/export
~~~

The exact number of poses/views is frozen only after the cheap tool loop is working.

This is **not** claimed to be EvoScene's official implementation. It is the paper-shaped fixed control for the local experiment.

---

# Emergent schedule

The emergent arm has no hard-coded iteration count.

It terminates when deterministic termination rules accept a STOP proposal or a resource ceiling is reached.

Initial termination rules:

~~~text
minimum evidence requirements satisfied
AND
quality >= Q_min
AND
(
  marginal quality gain < epsilon for K accepted expensive actions
  OR
  all uncertainty regions below threshold
)
~~~

Hard ceiling:

~~~text
max wall time
max novel views
max geometry refinements
max agent inference calls
~~~

The ceiling prevents pathological loops from becoming an unbounded demo.

---

# Stage gates / PR stack

## D0 — Demo scaffold and deterministic contracts

**Purpose:** create the executable experiment boundary before any heavy models.

Deliverables:

~~~text
demos/evoscene-emergent/
  README.md
  PLAN.md
  ARCHITECTURE.md
  src/
    artifacts.*
    messages.*
    runtime.*
    accounting.*
  tests/
~~~

Implement:

- typed artifact IDs;
- operator/action enums;
- deterministic event loop;
- trace format;
- cost accounting;
- mock tools;
- replay tests.

Gate:

~~~text
same seed + mock tools => byte-identical trace
every accepted action accounted
every produced artifact has provenance
invalid action => deterministic rejection
~~~

**PR:** demo: add emergent 3D runtime contracts

---

## D1 — Paper-shaped fixed local reference

**Purpose:** establish the controlled cost baseline using mock/lightweight real operators.

Implement:

- single-image input;
- fixed schedule runner;
- frozen view poses;
- scene-state transitions;
- evaluator;
- per-operator telemetry.

Gate:

~~~text
fixed schedule completes end-to-end
produces final scene artifact
quality + cost metrics emitted
exact run configuration frozen
~~~

**PR:** demo: add fixed 3D scene evolution reference

---

## D2 — Real local reconstruction operators

**Purpose:** replace mocks with the minimum useful local 3D pipeline.

Order:

~~~text
D2a real metric depth + camera prior
D2b explicit backprojection + initial point-cloud scene state
D2c real fusion + geometry refinement
D2d deterministic novel-view rendering/reprojection (existing geometry only)
D2e deterministic final mesh generation/export
D2f learned novel-view generator (required for EvoScene-faithful unseen-region evidence)
~~~

The mesh finalizer runs only after convergence/STOP and runs exactly once in
both fixed and emergent arms. Mesh generation therefore remains a controlled
post-convergence cost rather than another scheduling degree of freedom.

Do not merge a tool merely because it runs. Each adapter needs:

~~~text
content-hash identity
seed/config capture
failure category
latency accounting
artifact validation
~~~

Gate:

~~~text
one real input -> inspectable 3D artifact
hidden synthetic scene -> nontrivial measurable quality
no unaccounted tool failure
~~~

**PR stack:** one adapter per PR where practical.

---

## D3 — Emergent specialist population

**Purpose:** replace the fixed schedule with local specialist decisions.

Implement:

- six agent roles;
- constrained action grammar/schema;
- local observation projections;
- proposal/accept/reject flow;
- deterministic resource enforcement;
- deterministic STOP validation.

First use deterministic specialist policies.

Then add model-backed specialists behind the same boundary.

Gate:

~~~text
emergent arm reaches a valid final artifact
no central schedule encoded in runtime
at least two distinct successful semantic trajectories
all tool invocations attributable to accepted proposals
~~~

**PR:** demo: add emergent 3D specialist population

---

## D4 — Cost-aware adaptation

**Purpose:** test the real hypothesis.

Agents receive local marginal-gain and budget evidence.

Examples:

~~~text
ViewPlanner:
  region 7 remains low coverage

Critic:
  last novel view improved Q by 0.004

Geometry:
  full rebuild predicted high cost; local refinement sufficient

NovelView:
  proposed view overlaps 92% with existing evidence
~~~

The system may learn/choose to:

- skip redundant views;
- target only uncertain regions;
- defer geometry rebuilds;
- terminate early;
- spend more compute on hard scenes.

Gate:

~~~text
emergent scheduling differs from fixed schedule
savings arise from fewer/skipped/targeted expensive actions
quality remains measurable and non-degenerate
~~~

**PR:** demo: add cost-aware emergent resource allocation

---

## D5 — Frozen benchmark

**Purpose:** turn the demo into evidence.

Freeze before interpreting:

~~~text
scene set
single input render per scene
held-out views
tool versions/hashes
agent model/hash if used
seeds
budgets
Q* definition
fixed schedule
termination thresholds
~~~

Canonical comparison:

~~~text
20 scenes
>=3 scheduling/model seeds where nondeterminism exists

fixed
vs
emergent
~~~

Report:

~~~text
success at Q*
median and mean quality
median and mean cost
quality-matched savings
per-operator invocation savings
wall-clock savings
trajectory diversity
failure attribution
~~~

Gate for a strong demo result:

~~~text
emergent reaches Q* on >= fixed success rate - tolerance
AND
median expensive-operator cost is lower
AND
savings survives scene-level paired comparison
~~~

Do not require a positive result. A clean negative result is still evidence.

**PR:** demo: freeze emergent 3D benchmark evidence

---

## D6 — Real-photo demonstration

**Purpose:** make the research result visually legible.

Add:

- 3–5 real photographs;
- input image;
- emergent trace visualization;
- final mesh/point cloud;
- before/after view coverage;
- cost timeline.

No new scientific claim should depend solely on this qualitative stage.

**PR:** demo: add emergent 3D visual showcase

---

# Required trace schema

Each semantic step should eventually emit one row:

~~~text
run_id
scene_id
seed
arm
step
operator
observation_hash
action
action_payload_hash
accepted
rejection_reason
input_artifact_ids
output_artifact_ids
tool
tool_config_hash
wall_time_ms
cumulative_cost
quality_before
quality_after
termination_state
~~~

Large artifacts stay out of git.

Canonical hashes and summaries go into documentation, following the finalization-run discipline.

---

# Suggested repository layout

~~~text
demos/
  README.md
  evoscene-emergent/
    README.md
    PLAN.md
    ARCHITECTURE.md

    src/
      artifacts.zig
      messages.zig
      runtime.zig
      accounting.zig
      evaluator.zig

      agents/
        spatial_prior.zig
        geometry.zig
        view_planner.zig
        novel_view.zig
        fusion.zig
        critic.zig

      tools/
        depth/
        geometry/
        view/
        fusion/

    runners/
      fixed.py
      emergent.py

    tests/
    assets/
      README.md

    trials/
      README.md
~~~

Zig should own coordination/replay/accounting.

Python may own ML/3D tool adapters where ecosystem support requires it.

The boundary between them should be explicit and record-oriented, just as F4 kept llama.cpp I/O outside the authoritative deterministic runtime.

---

# First implementation target

Do **not** begin with a generative novel-view model.

The first executable target should be:

~~~text
single image
-> local depth estimate
-> backprojected point cloud
-> simple scene representation
-> deterministic candidate camera views
-> render/reproject observations
-> fuse
-> evaluate
~~~

with both fixed and emergent scheduling.

That gives us the full scientific harness cheaply.

Only after the deterministic D2a–D2e pipeline succeeds should a learned
novel-view generator be inserted. That keeps failures attributable and prevents
model setup from hiding a coordination bug. The terminal tangible artifact is a
mesh/export (GLB primary, with PLY/OBJ where useful), generated by the same
finalizer in both experiment arms.

---

# Definition of done

The demo is complete when we can truthfully show:

~~~text
one RGB image
     |
     v
heterogeneous specialist population
     |
     +-- decides where new evidence is needed
     +-- decides which reconstruction tools to invoke
     +-- decides when refinement is no longer worth its cost
     |
     v
3D artifact
~~~

and produce a paired table such as:

~~~text
scene    fixed_Q   emergent_Q   fixed_cost   emergent_cost   savings
-----    -------   ----------   ----------   -------------   -------
...
~~~

where every value is reproducible from an authoritative trace.

## Deterministic D2 completion boundary

The deterministic D2 operator pipeline is complete when D2a–D2e pass:

~~~text
D2a  real metric depth + camera prior
D2b  explicit backprojection + point-cloud scene state
D2c  deterministic fusion + geometry refinement
D2d  deterministic novel-view render/reprojection
D2e  deterministic post-convergence mesh finalization/export
~~~

D2a-D2e remain the complete deterministic operator substrate. D2f is optional
only when testing that substrate in isolation. For the full EvoScene-faithful
fixed-vs-emergent experiment, D2f is required before D3 because it is the
operator that can add genuinely unseen-region image evidence.
