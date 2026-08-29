# F3 — local inference control

F3 extends the frozen Stage 7A/7B policy surface from:

~~~text
theta = (n, e, r, u)
~~~

to:

~~~text
theta = (n, e, r, u, c)

c = inference-gating permille
~~~

The scientific question is:

> Can an operator reduce local inference/recomputation while preserving
> feasibility and competitive resource behavior?

F3 is intentionally evaluated on the deterministic synchronous Stage 7A/7B
substrate. F2 already quantified the synchronous/asynchronous gap; keeping F3
synchronous isolates the inference-control mechanism before any later
cross-product with transport/asynchrony.

## Historical Stage 7B provenance

The Stage 7B search harness is re-materialized from the exact historical blob:

~~~text
e91f88b2ea2dafd6bd51113954ff03aee4330163
~~~

The re-materialized file differs only in the two import paths required by the
experiments repository layout.

The canonical Stage 7B report identity is:

~~~text
SHA-256:
e3d27eec1f7bb78d5cabf869fc5172c3746a356f7f4cd9db4cc91f657e01ff2f
~~~

F3 separately anchors the historical reference to the frozen validation
frontier:

~~~text
id 37 = (244, 94, 15, 958)
  validation:
    failures      = 0
    rounds        = 1046
    communication = 258389
    duplicates    = 170485
    computation   = 55936

id 51 = (354, 141, 0, 994)
  validation:
    failures      = 0
    rounds        = 1054
    communication = 255319
    duplicates    = 167211
    computation   = 56576

id 93 = (685, 283, 960, 344)
  validation:
    failures      = 0
    rounds        = 1435
    communication = 250805
    duplicates    = 162945
    computation   = 76704
~~~

A mismatch is an engineering failure.

## Inference-gating semantics

Each operator maintains a cached action produced from its most recently
refreshed local observation.

Every policy opportunity is exactly one of:

~~~text
refresh:
  rebuild local observation
  run the frozen Stage 7A decision rule
  update cached action
  inference cost = 1

reuse:
  reuse cached action
  inference cost = 0
~~~

The first policy opportunity for every operator always refreshes because no
cache exists yet.

For `0 < c < 1000`, refresh eligibility is deterministic from:

~~~text
(world seed, operator index, local round)
~~~

with `c` applied only as a threshold. Therefore the refresh set is monotone:
if a tick refreshes at a lower c, it also refreshes at every higher c.

Corners:

~~~text
c = 0:
  first opportunity refreshes
  all later opportunities reuse cache

c = 1000:
  always refresh
~~~

## Exact inference accounting

F3 records:

~~~text
policy_calls
inference_units
cache_reuses
~~~

and requires:

~~~text
policy_calls = inference_units + cache_reuses
~~~

for every run and every emitted aggregate row.

This makes inference savings an audited partition of policy opportunities.

## c=1000 delegation rule

The `c=1000` corner does not pass through a rewritten approximation.

It delegates directly to the frozen Stage 7A `run` implementation and maps:

~~~text
inference_units = baseline policy_calls
cache_reuses = 0
~~~

F3 then performs an aggregate corner audit for all 134 historical Stage 7B
candidates on both:

~~~text
training   48 worlds/candidate
validation 24 worlds/candidate
~~~

Expected:

~~~text
134 candidates × 2 splits = 268 aggregate checks
mismatches = 0
~~~

## Five-dimensional search

F3 preserves Stage 7B's deterministic search discipline:

~~~text
6 fixed Stage 7A probe profiles
128 Latin-hypercube candidates
134 total candidates
~~~

The 128 space-filling candidates extend the historical four-dimensional
stratification with a fifth independent deterministic permutation for c.

Ranges:

~~~text
n in [0,1000]
e in [0,1000]
r in [0,1000]
u in [250,1000]
c in [0,1000]
~~~

No runtime entropy is used.

The six exact fixed controls remain at `c=1000`.

## Training and validation

Training is unchanged from Stage 7B:

~~~text
N={32,64}
F/N={1,2}
topology={ring,grid}
R=2
B={1,2,4}
seed={0,1}
H=2048

48 worlds/candidate
~~~

Validation is untouched during training:

~~~text
same structural box
seed=2
H=2048

24 worlds/evaluated candidate
~~~

Selection remains feasibility-first:

~~~text
1. minimum failure count
2. discard less-feasible candidates
3. compute resource Pareto frontier among equally feasible candidates
~~~

The minimized resource dimensions are:

~~~text
rounds
communication
duplicates
policy/computation calls
inference units
~~~

`cache_reuses` is not a separate Pareto dimension because it is exactly
dependent on computation and inference:

~~~text
cache_reuses = computation - inference
~~~

## Ungated twin comparison

For every validation-selected candidate with `c < 1000`, F3 evaluates an
otherwise identical validation twin with:

~~~text
same (n,e,r,u)
c = 1000
~~~

This isolates the effect of inference gating after candidate selection.

The primary success criterion is:

~~~text
selected gated candidate:
  validation failures = 0

ungated twin:
  validation failures = 0

and:
  gated inference_units < ungated inference_units
~~~

Other resource deltas versus the twin are reported rather than hidden.

## Hard holdouts

Only the validation-selected frontier plus the three exact named controls is
evaluated on the Stage 7B hard sets:

~~~text
population_N_128       36 worlds
density_F_over_N_4     36 worlds
redundancy_R_4         72 worlds
bandwidth_B_8          24 worlds
topology_complete      36 worlds
compound                9 worlds
~~~

Hard results never participate in selection.

The frozen Stage 7B selected family IDs 37/51/93 is also evaluated ungated on
all six hard splits for direct comparison.

## Dataset

Generated and gitignored:

~~~text
trials/f3-inference-control.tsv
~~~

The dataset includes:

- training rows for all 134 candidates;
- validation rows for the training-frontier candidates plus diagnostics;
- ungated validation twins for selected gated candidates;
- hard-holdout rows for selected candidates and named controls;
- hard-holdout rows for frozen Stage 7B IDs 37/51/93.

Every row includes:

~~~text
n e r u c
runs / failures
rounds
communication
duplicates
computation
inference
cache reuse
useful
violations
inference-accounting flag
selection flag
~~~

## Canonical verifier

Run:

~~~sh
python3 tools/verify_f3.py
~~~

The verifier:

1. requires Zig 0.16.0;
2. runs the complete repository test suite;
3. validates historical Stage 7B anchor values;
4. validates F3 candidate uniqueness and split shape;
5. runs the 268-check `c=1000` Stage 7B corner audit;
6. runs the full F3 search twice;
7. requires byte-identical evidence replay;
8. validates exact inference/cache accounting on every row;
9. validates frontier/twin/hard-holdout structure;
10. writes the generated dataset and prints its SHA-256 plus the selected
    validation frontier and gated-vs-ungated deltas.

## Outcome semantics

F3 **PASS**:

- all structural, provenance, corner and replay gates pass; and
- at least one validation-selected gated candidate preserves zero failures
  while strictly reducing inference units relative to its zero-failure
  ungated twin.

F3 **LIMITATION**:

- all structural, provenance, corner and replay gates pass; but
- no selected gated candidate meets that inference-reduction criterion.

Either PASS or LIMITATION is a completed scientific result.

The following are engineering failures, not scientific outcomes:

- Stage 7B provenance/anchor mismatch;
- `c=1000` corner mismatch;
- invalid or duplicate candidate theta;
- protocol violations;
- inference accounting failure;
- missing required twin/hard rows;
- replay nondeterminism;
- build/runtime failure.

No canonical F3 result is claimed until the local verifier completes.
