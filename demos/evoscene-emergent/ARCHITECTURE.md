# Architecture

## Design rule

The F4 authority boundary remains unchanged:

~~~text
agent/model
   -> proposed typed action
   -> deterministic parser/validator
   -> deterministic demo runtime
   -> tool invocation
   -> measured artifact/evidence
   -> local state update
~~~

Agents may propose work. They may not fabricate tool results, mutate global scene state directly, bypass validation, or declare success.

## Shared artifacts

Large scene artifacts are content-addressed and referenced by ID rather than broadcast through messages.

Initial artifact classes:

~~~text
InputImage
DepthMap
CameraEstimate
PointCloud
SceneRepresentation
Mesh
RenderedView
SynthesizedView
ConfidenceMap
EvaluationReport
CostRecord
~~~

Every artifact records:

~~~text
artifact_id
kind
producer
parents[]
content_hash
created_step
wall_time_ms
optional model/tool identity
optional seed
metadata
~~~

## Local operator state

Each operator receives only the state it needs.

### Spatial Prior

Knows:

- source image;
- current accepted camera/depth evidence;
- latest confidence map.

May propose:

~~~text
ESTIMATE_DEPTH
ESTIMATE_CAMERA
REFRESH_SPATIAL_PRIOR
PUBLISH_PRIOR
~~~

### Geometry

Knows:

- accepted spatial evidence;
- current scene representation;
- geometry metrics.

May propose:

~~~text
BUILD_GEOMETRY
REFINE_GEOMETRY region=<id>
EXPORT_MESH
REQUEST_EVIDENCE region=<id>
~~~

### View Planner

Knows:

- coverage/confidence summaries;
- geometry uncertainty;
- prior camera poses;
- recent marginal gains.

May propose:

~~~text
PROPOSE_VIEW azimuth=<...> elevation=<...>
PROPOSE_VIEW_SET ...
STOP_VIEW_SEARCH reason=<...>
~~~

### Novel View

Knows:

- accepted view request;
- geometry/depth conditioning;
- source image identity.

May propose:

~~~text
GENERATE_VIEW pose=<...>
RENDER_VIEW pose=<...>
REJECT_VIEW_REQUEST reason=<...>
~~~

### Fusion

Knows:

- new observations;
- spatial prior;
- consistency/confidence scores.

May propose:

~~~text
FUSE_VIEW artifact=<id>
REJECT_OBSERVATION artifact=<id>
REQUEST_REOBSERVATION region=<id>
~~~

### Critic

Knows:

- deterministic evaluation measurements;
- cost trajectory;
- previous quality trajectory.

May propose:

~~~text
QUALITY_IMPROVED delta=<...>
QUALITY_PLATEAU
INCONSISTENCY region=<id>
PROPOSE_TERMINATION
REQUEST_REFINEMENT region=<id>
~~~

The runtime, not the critic, decides whether termination criteria are satisfied.

## Minimal typed coordination vocabulary

Start smaller than the full Starlings protocol vocabulary:

~~~text
OBSERVE
CLAIM
EVIDENCE
PROPOSE
ACCEPT
REJECT
QUERY
DELEGATE
STOP
~~~

Payloads are schema-validated.

Examples:

~~~text
PROPOSE VIEW pose=... expected_gain=...
EVIDENCE COVERAGE region=... score=...
QUERY GEOMETRY region=...
DELEGATE REFINE_GEOMETRY region=...
PROPOSE STOP reason=plateau
~~~

## No central planner

The runtime has a deterministic event loop and safety/resource constraints, but it does not contain a hidden reconstruction plan.

A fixed-schedule baseline may encode:

~~~text
prior
-> geometry
-> views
-> fusion
-> geometry
-> views
-> fusion
-> geometry
-> stop
~~~

The emergent arm may reach any valid sequence, including:

~~~text
prior
-> geometry
-> targeted view
-> fusion
-> targeted geometry refinement
-> stop
~~~

or:

~~~text
prior
-> geometry
-> critic inconsistency
-> alternate view
-> alternate view
-> fusion
-> geometry
-> stop
~~~

## Cost authority

Cost measurement is deterministic.

Every tool invocation emits:

~~~text
tool
start_time
end_time
wall_time_ms
peak_memory_if_available
model_calls
generated_images
generated_pixels
iterations_or_steps
input_artifact_ids
output_artifact_ids
~~~

On macOS, optional machine telemetry may additionally record process RSS and, when available, system power/energy counters. These are secondary metrics; wall-clock and invocation counts are mandatory.

## Quality authority

Agents do not self-score quality.

The evaluator computes deterministic or externally model-scored measurements from artifacts. The first benchmark should use hidden synthetic ground truth so the primary quality gate does not depend entirely on learned preference scores.
