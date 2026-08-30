# D3 — Emergent Specialist Population

Status: implementation complete on the D3 branch; local Zig validation pending.

D3 is the first stage whose primary subject is **Starlings coordination** rather
than the 3D operator substrate.

## Claim boundary

D3 asks:

> Can six role-local specialists assemble a valid reconstruction workflow,
> select views, react to produced evidence, and terminate without a central
> schedule encoding the action sequence?

D3 does **not** yet ask whether the emergent schedule is cheaper or better than
the fixed controller. That is D4/D5.

## Population

~~~text
SpatialPriorAgent
GeometryAgent
ViewPlannerAgent
NovelViewAgent
FusionAgent
CriticAgent
~~~

Every specialist emits only an existing typed `messages.Proposal`. No new
protocol action is introduced.

## Local observations

`d3_observation.zig` projects the artifact store differently for each role.

Examples:

~~~text
SpatialPriorAgent:
  input image
  whether depth exists
  whether camera exists

GeometryAgent:
  depth
  camera
  latest scene
  latest fused point cloud

ViewPlannerAgent:
  latest scene
  pending rendered/fused evidence
  latest evaluation
  view/refinement counts

CriticAgent:
  latest refined scene
  latest evaluation
  view/refinement counts
~~~

Every observation includes deterministic remaining action/tool/wall-time
budget, but agents do not receive a central next-step instruction.

## No central schedule

The population driver does only three generic operations:

~~~text
1. ask every specialist for at most one typed proposal
2. rank emitted proposals
3. submit the selected proposal to the existing D0 runtime
~~~

The arbiter never switches on action kind or operator role.

Frozen arbitration rule:

~~~text
minimum BLAKE3(
  seed,
  round,
  operator,
  action,
  inputs,
  payload
)
~~~

The lowest digest wins. A deterministic tie-break exists only for the
cryptographic-collision case.

Candidate-array order is explicitly tested not to affect selection.

## Causal specialist policies

The initial D3 agents are deterministic policies so coordination behavior can
be separated from language-model behavior.

Spatial prior:

~~~text
if depth/camera missing:
  propose one missing prior
~~~

Geometry:

~~~text
depth + camera and no scene:
  build geometry

newer fused point cloud:
  refine current scene
~~~

View planner:

~~~text
scene ready:
  choose a seed/state-dependent pose

pending render/fusion/refinement:
  do nothing

new refined scene:
  wait for Critic evaluation

evaluation below target:
  request another view
~~~

Novel view:

~~~text
new view request:
  render it
~~~

Fusion:

~~~text
new rendered observation:
  fuse it
~~~

Critic:

~~~text
refined scene without evaluation:
  evaluate

quality >= 900:
  propose STOP

four views used:
  propose STOP at runtime quality floor
~~~

The critic is forbidden by policy from evaluating/stopping on the initial
unrefined scene. At least one render -> fusion -> refinement cycle must occur.

## View diversity

The D3 view policy chooses among:

~~~text
+35 deg / +10 deg
-35 deg / +10 deg
+55 deg / +15 deg
-55 deg / +15 deg
~~~

using the current scene artifact identity, seed, and local view count.

This gives reproducible but state/seed-dependent semantic trajectories.

## D3 gate

~~~sh
zig build test-demo-evoscene-d3
zig build -Doptimize=ReleaseFast run-demo-evoscene-d3 -- validate
zig build test
~~~

The validator runs seeds 0 and 1 and requires:

~~~text
both populations terminate
neither population deadlocks
all six specialist roles participate
>= 1 view is requested
runtime accounting identities hold
artifact provenance holds
same seed => byte-identical trace
different seeds => distinct semantic trace digest
candidate-list order does not change arbitration
~~~

Expected final verdict:

~~~text
trajectory_distinct: yes
all_roles_participate: yes
runtime_invariants: PASS
D3 PASS: emergent specialist population complete
~~~

## What passing D3 means

A D3 pass supports the narrow claim that a decentralized specialist population
can produce more than one valid workflow trajectory under the same protocol
without a central action schedule.

It does **not** establish:

~~~text
quality-matched savings
optimal view choice
better reconstruction quality
model-driven intelligence
~~~

Those are deliberately deferred.

## Next top-level stage

~~~text
D4  cost-aware adaptive scheduling
~~~

D4 will expose marginal gain, uncertainty, and budget evidence to the same
specialist boundary and compare the resulting choices against the frozen D1
fixed schedule.
