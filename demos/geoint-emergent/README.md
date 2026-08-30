# GEOINT Emergent Operator Trial

This is a separate experiment track from the frozen EvoScene D0-D6 stack.

The purpose is not to build a monolithic GEOINT model. It tests whether a
Starlings population can decide which heterogeneous physical/scene operators
are applicable, combine their typed evidence, preserve competing uncertainty,
and stop without inventing unsupported observations.

## Research question

> Given one visual scene and a heterogeneous evidence/operator vocabulary, can
> local specialists fill a coherent scene-state graph and activate expensive or
> specialized operators only when their prerequisites become available?

Bellingcat ShadowFinder is the first real downstream physics/geolocation tool
in this trial.

## Requested field graph

The schema contains all 54 requested fields:

| Domain | Fields |
| --- | --- |
| Geometry | horizon, vanishing points, scale, camera pose, object height, slope |
| Terrain | elevation, grade, ridgelines, depressions, drainage |
| Water | shoreline, water level, inundation, flow direction, wave state |
| Shadows / illumination | solar angle, shadow object height, shadow length, time-of-day consistency |
| Atmospheric | haze, visibility, cloud base, smoke/plume direction |
| Vegetation | canopy height, health, seasonality, disturbance |
| Built environment | roads, roofs, towers, bridges, utilities, construction |
| Motion | vehicle tracks, vessel wakes, movement vectors, changed objects |
| Temporal | new, removed, expanded, contracted, seasonal change |
| Material / spectral | asphalt, soil, vegetation, water, metal-like |
| Geolocation | candidate region, landmark match, terrain match |
| Uncertainty | confidence, ambiguity, competing hypotheses |

Every field must end as one of:

```text
observed
estimated
derived
not_visible
unavailable
blocked
conflicting
```

STOP is invalid while any field remains `unknown`.

## Perception boundary

The visual/pixel provider is intentionally outside the coordination claim.

A future provider may be:

```text
OpenCV / geometry solver
local multimodal model
specialized remote-sensing model
human annotation
sensor metadata
```

All providers map into the same typed field graph. This keeps the experiment
about coordination rather than about whichever vision model happens to be
attached.

The deterministic fixture in this PR approximates a generic sunlit roadside
scene and is only a protocol/evidence-boundary fixture. It is not asserted to
contain measurements from any user photograph.

## Specialist population

```text
GeometryAgent
TerrainAgent
WaterAgent
IlluminationAgent
AtmosphericAgent
VegetationAgent
BuiltEnvironmentAgent
MotionAgent
TemporalAgent
MaterialSpectralAgent
GeolocationAgent
UncertaintyAgent
```

The first ten specialists are independently eligible from the initial image.
No central order is encoded.

The population uses the same D3-style blind arbitration rule:

```text
minimum BLAKE3(
  seed,
  round,
  role,
  action
)
```

## Matched worlds

The validator runs two acquisition contexts over the same visual fixture.

### `photo_no_datetime`

```text
image evidence exists
datetime missing
trusted object/shadow ratio missing
```

Expected behavior:

```text
IlluminationAgent
  -> marks solar-angle derivation blocked

GeolocationAgent
  -> does NOT invoke ShadowFinder
  -> records candidate region as blocked

UncertaintyAgent
  -> preserves ambiguity
  -> STOP after all 54 fields have explicit states
```

### `photo_shadow_ready`

Same visual evidence, but with a synthetic timestamp/shadow-ratio context.

Expected behavior:

```text
IlluminationAgent
  -> derives sun altitude

GeolocationAgent
  -> ShadowFinder becomes locally eligible
  -> invokes it exactly once
  -> candidate region becomes derived

UncertaintyAgent
  -> reassesses ambiguity
  -> STOP
```

The added shadow values in this matched fixture are synthetic test context.
They are not claimed to be measurements from a supplied photograph.

## ShadowFinder pin

Canonical research adapter:

```text
Bellingcat/ShadowFinder
version: 0.7.0
commit:  f12f8d6d30094f94c8cacb27f3c77db7e443d6fd

src/shadowfinder/shadowfinder.py Git blob:
6dc9781cf3d32e37c0c507929d06cedbd818fe26
```

ShadowFinder accepts either:

```text
object height + shadow length
OR
sun altitude angle
```

plus datetime/time-format and produces a global relative-error surface.

`tools/geoint_shadowfinder.py` turns that surface into a compact deterministic
summary:

```text
candidate cell count
lat/lon bounds
top-N cells by absolute relative solar-altitude error
result SHA-256
```

## Structural gate

No ShadowFinder installation is required:

```sh
python tools/verify_geoint_emergent.py
```

This runs:

```text
Zig 0.16 trial tests
matched context validation
same-seed deterministic replay
different-seed trajectory diversity
state-dependent ShadowFinder activation
epistemic closure guard
ShadowFinder adapter geometry/pin self-test
full repository Zig test suite
```

Expected end:

```text
seed_trajectory_distinct: yes
context_trajectory_distinct: yes
shadowfinder_state_dependent: yes
epistemic_guard: PASS
runtime_invariants: PASS
GEOINT PASS: state-dependent operator emergence complete
GEOINT STRUCTURAL PASS: typed evidence graph + state-dependent ShadowFinder boundary complete
```

## Optional real ShadowFinder run

Clone the pinned ShadowFinder source and install version 0.7.0 in a compatible
Python environment, then provide real acquisition context:

```sh
python tools/verify_geoint_emergent.py \
  --shadowfinder-live \
  --shadowfinder-root /path/to/ShadowFinder \
  --datetime 2026-08-29T16:30:00 \
  --time-format local \
  --object-height 1.8 \
  --shadow-length 1.2
```

or provide `--sun-altitude-deg` directly.

For a real photograph, do not invent those values. If datetime or a trusted
shadow ratio is unavailable, the correct trial outcome is the blocked
ShadowFinder path.

## Scientific claim boundary

A PASS means:

> the Starlings population changed its global operator trajectory when one
> physical inference became locally available, while resolving unsupported
> fields explicitly instead of hallucinating them.

It does not establish GEOINT accuracy. Accuracy belongs to the attached
perception/physics operators and requires separately labeled data.
