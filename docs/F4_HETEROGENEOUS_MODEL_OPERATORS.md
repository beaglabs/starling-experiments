# F4 — heterogeneous deterministic/model-backed operators

F4 is the final stage of the Starlings finalization run.

Question:

> Does the operator-neutral Starlings substrate support heterogeneous
> populations containing both validated deterministic policies and live
> language-model-backed operators without changing protocol semantics?

The execution boundary is:

~~~text
llama.cpp
  -> raw proposed completion
  -> Zig parse / semantic validation
  -> Starlings action
  -> deterministic topology + state transition
  -> authoritative Zig replay
~~~

Invalid model output is rejected and counted. It is never silently repaired.

## Historical adapter provenance

The external runner preserves controls from historical Stage 3F.0:

~~~text
historical file:
  tools/stage3f0_llama_cpp.py

historical blob:
  f84098b579fda8c472eac0f16e9e05e3d534bf16

historical parent:
  176a0f95b3d2c668b3d8cb6b3231d5d59609999e
~~~

Preserved controls:

- environment seed and sampling seed are independent factors;
- paired typed_unconstrained and cfg_constrained treatments;
- deterministic per-turn model seeds;
- cache_prompt=false;
- reasoning_effort=none;
- thinking disabled;
- raw record output followed by independent Zig replay;
- trajectory-hash diversity rather than canonical-trajectory matching.

F4 adds true heterogeneous populations and the validated F3 state-aware
inference controller.

## Model

Canonical target:

~~~text
weights:
  ~/Downloads/models/gemma-4-E2B-it-Q4_K_M.gguf

server:
  llama-server on 127.0.0.1:8080
~~~

The runner SHA-256 hashes the complete GGUF before the live experiment and
records that digest in the metadata sidecar. Weights are never committed.

## Population environment

F2 already owns scaling evidence, so F4 deliberately uses a small live-model
box:

~~~text
N = 5
F = 5
facts = A,B,C,D,E
redundancy = 2
bandwidth = 2 facts/claim
collector = Worker 1
max rounds = 10
topologies = ring, grid
~~~

Initial information uses the historical overlapping placement. Seed 0 is:

~~~text
Worker 1: A,B
Worker 2: B,C
Worker 3: C,D
Worker 4: D,E
Worker 5: E,A
~~~

Fact labels rotate by environment_seed modulo 5.

## Population mixes

### deterministic_only

All five workers use frozen Stage 7B theta51:

~~~text
theta51 = (354,141,0,994)
~~~

### mixed

~~~text
Worker 1: deterministic theta51 collector
Worker 2: model-backed
Worker 3: model-backed
Worker 4: deterministic theta51
Worker 5: deterministic theta51
~~~

Workers 2 and 3 share one fact whose only initial copies are on those two
model-backed workers. For seed 0 the essential fact is C. Its identity rotates
with the environment seed.

Therefore a successful mixed run cannot be explained by deterministic peers
carrying non-participating model workers. Authoritative replay rejects a
successful mixed record without attributed transfer of that essential fact
from the model-only initial holder set into a deterministic worker.

### model_only

All five workers are model-backed. This arm is diagnostic and is not the
primary promotion gate.

## Common action language

Model-backed operators may propose:

~~~text
CLAIM <facts>
QUERY EVIDENCE <fact>
~~~

CLAIM:
- one or two facts;
- facts must already be known by the sender;
- accepted facts go to every topology neighbor;
- accepted claims update local sent state.

QUERY EVIDENCE:
- asks every topology neighbor for one fact;
- neighbors that know it return deterministic evidence;
- the model chooses the query;
- Starlings determines and applies evidence.

Syntax-valid output can still be semantically rejected.

## Communication accounting

Queries introduce explicit control traffic. Every run must satisfy:

~~~text
communication_units
  = control_units
  + useful_deliveries
  + duplicate_deliveries
~~~

## Decode treatments

Every canonical model-backed state-aware run pairs:

~~~text
typed_unconstrained
cfg_constrained
~~~

on the same mix, topology, environment seed, sampling seed, controller, model,
sampler settings, token budget, and round budget.

Grammar:

~~~text
grammars/f4.gbnf
~~~

CFG constrains syntax only. Semantic validation stays in Zig.

## Inference control

F3 established:

~~~text
blind probabilistic gating:
  LIMITATION

knowledge/staleness-aware gating:
  PASS
~~~

F4 therefore uses knowledge_or_stale as the canonical model inference
controller.

A model call occurs if:

- no accepted cached action exists;
- local knowledge changed since the last model inference;
- the cached action became semantically invalid;
- a cached CLAIM is stale because unsent facts remain while all selected facts
  are already sent;
- a cached QUERY is stale because the requested fact is now locally known.

Otherwise the accepted cached action is reused with no model call.

A smaller matched always_refresh subset remains as a control.

## Raw record

Every operator decision records:

~~~text
mix
topology
environment_seed
sampling_seed
mode
controller
round
worker
operator_kind
source
knowledge_before
sent_before
cursor_before
model_seed
completion_tokens
latency_us
token_budget
completion
~~~

source is exactly one of:

~~~text
deterministic
model_call
cache
~~~

## Authoritative replay

The Zig replayer independently:

- reconstructs initial local states;
- recomputes every deterministic theta51 action;
- checks every recorded pre-state;
- checks every deterministic action string;
- verifies expected model-call versus cache-reuse decisions;
- verifies every live model generation seed;
- parses model output;
- applies semantic validation;
- applies synchronous topology transitions;
- checks the essential-fact condition in successful mixed populations;
- measures all communication;
- reconstructs collector success;
- produces the canonical per-population summary;
- hashes semantic decision trajectories rather than raw model wording.

The Python state mirror exists only to construct the next closed-loop prompt.
Any Python/Zig disagreement rejects the record.

## Sampling controls

~~~text
temperature = 0.7
top_p = 0.9
top_k = 40
max_tokens = 32
cache_prompt = false
reasoning_effort = none
enable_thinking = false
~~~

Environment and sampling seeds remain separate.

Per-turn model seed:

~~~text
sampling_seed * 1,000,003
+ round * 101
+ worker
~~~

reduced into the llama.cpp seed range.

## Smoke plan

Smoke is a plumbing gate, not canonical evidence.

~~~text
deterministic controls: 1

mixed:
  ring
  env=0
  sampling=0
  typed + CFG
  knowledge_or_stale + always_refresh
  4 runs

model_only:
  ring
  env=0
  sampling=0
  typed + CFG
  knowledge_or_stale
  2 runs

total:
  7 population runs
  <= 180 live model calls
~~~

Smoke PASS means the full repository tests, runner self-test, raw replay,
backend/token-budget gates, deterministic control, and at least one accepted
model action all pass. Smoke is never frozen as the final F4 result.

## Canonical plan

Deterministic controls:

~~~text
environment seeds = 0,1,2
topologies = ring,grid

6 runs
~~~

Canonical state-aware model matrix:

~~~text
environment seeds = 0,1,2
sampling seeds = 0,1,2
topologies = ring,grid
mixes = mixed,model_only
modes = typed_unconstrained,cfg_constrained
controller = knowledge_or_stale

72 runs
~~~

Matched always-refresh audit:

~~~text
environment seed = 0
sampling seed = 0
topologies = ring,grid
mixes = mixed,model_only
modes = typed_unconstrained,cfg_constrained

8 runs
~~~

Canonical summary shape:

~~~text
6 + 72 + 8 = 86 runs
~~~

Worst-case live-model ceiling:

~~~text
2800 calls
~~~

State-aware cache reuse may lower the observed count.

## Trajectory diversity

For each fixed mixed/topology/environment/mode/state-aware configuration, the
three sampling seeds provide repeated populations from the same initial state
and treatment.

The trajectory hash records semantic decision events rather than raw completion
wording. Accepted actions, cache reuses, syntax rejection, semantic rejection,
and backend failure are distinct events; irrelevant wording differences cannot
inflate diversity.

Healthy controlled emergence can appear as:

~~~text
multiple successful semantic trajectory hashes
same correct collector outcome
~~~

No claim is made from one trajectory.

## Canonical promotion criterion

Engineering gates first:

- Zig 0.16.0;
- full repository tests;
- runner self-test and dry-run plans;
- valid metadata and SHA-256 identities;
- exact 86-run matrix;
- byte-identical deterministic replay summary;
- zero replay/accounting failures;
- zero llama.cpp backend failures;
- zero token-budget violations;
- every deterministic control succeeds;
- CFG produces zero syntactically invalid actions;
- every successful mixed run has attributed essential-fact transfer.

After those gates, F4 PASS requires:

1. for each topology, at least one decode mode succeeds in at least 6 of its 9
   state-aware mixed runs; and
2. at least one fixed mixed environment/treatment has at least two successful
   sampling-seed runs with at least two distinct trajectory hashes.

F4 LIMITATION means every structural/engineering gate passes but this
heterogeneous success/diversity criterion is not met.

The model-only arm remains diagnostic.

## Runbook

Structural gate:

~~~sh
python3 tools/f4_llama_cpp.py --self-test
python3 tools/f4_llama_cpp.py --plan smoke --dry-run
python3 tools/f4_llama_cpp.py --plan canonical --dry-run
zig build test
zig build -Doptimize=ReleaseFast run-f4 -- validate
~~~

Start llama.cpp:

~~~sh
llama-server \
  -m ~/Downloads/models/gemma-4-E2B-it-Q4_K_M.gguf \
  --port 8080 \
  -np 1 \
  -c 4096
~~~

Live smoke:

~~~sh
python3 tools/f4_llama_cpp.py \
  --plan smoke \
  --output trials/f4-smoke-raw.tsv

python3 tools/verify_f4.py \
  --raw trials/f4-smoke-raw.tsv
~~~

Canonical replication, only after smoke passes:

~~~sh
python3 tools/f4_llama_cpp.py \
  --plan canonical \
  --output trials/f4-raw.tsv

python3 tools/verify_f4.py \
  --raw trials/f4-raw.tsv
~~~

Canonical verifier output:

~~~text
trials/f4-heterogeneous.tsv
~~~

It prints summary SHA-256, raw SHA-256, GGUF SHA-256, grouped outcomes and
communication, model-call/cache-reuse totals, rejection counts, trajectory
diversity, and the final PASS/LIMITATION verdict.

No canonical F4 result is claimed until the canonical local run completes.
