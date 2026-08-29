# F3b — state-aware local inference control

F3a tested blind deterministic/probabilistic refresh gating and produced a
valid limitation: the validation frontier preferred c=1000 always-refresh
policies.

F3b changes the experiment rather than simply widening that search.

The base communication policy is held fixed at the frozen Stage 7B selected
family:

~~~text
id37 = (244,94,15,958)
id51 = (354,141,0,994)
id93 = (685,283,960,344)
~~~

Only the local inference controller varies.

## Question

> Can a controller reuse prior local computation when decision-relevant state
> is unchanged, while preserving zero-failure validation and hard-holdout
> behavior?

## Why F3b is paired

F3a jointly varied:

~~~text
(n,e,r,u,c)
~~~

so most gated candidates were not exact gated versions of the known strong
Stage 7B policies.

F3b removes that confound.

For each frozen base theta, every controller is evaluated against an exact
always-refresh twin with the same:

~~~text
n
e
r
u
world
seed
topology
redundancy
bandwidth
round budget
~~~

The only changed variable is refresh control.

## Controllers

Five deterministic controller modes are frozen before execution.

### always_refresh

Exact baseline.

Every policy opportunity refreshes and delegates to frozen Stage 7A.

~~~text
inference_units = policy_calls
cache_reuses = 0
~~~

### knowledge_change

Refresh when:

- no cached action exists;
- local knowledge differs from the knowledge snapshot at the last inference;
- the cached action is structurally invalid under current local state.

Otherwise reuse the cached action.

The invalid-action condition is mandatory safety, not an optimization axis.

### knowledge_or_stale

Includes all `knowledge_change` triggers plus semantic action staleness.

A cached action is stale when:

~~~text
some locally known fact is still unsent
AND
every fact selected by the cached action has already been sent
~~~

This means a still-valid cached action is not reused when it would merely
repeat already-sent facts while locally novel work remains.

### knowledge_or_stale_age4

Same as `knowledge_or_stale`, plus forced refresh after four rounds without a
fresh inference.

### knowledge_or_stale_age8

Same as `knowledge_or_stale`, plus forced refresh after eight rounds without
a fresh inference.

The age variants test whether a bounded safety refresh can recover useful
round-dependent Stage 7A exploration/retry behavior without returning all the
way to always-refresh.

## Exact accounting

Each policy opportunity belongs to exactly one category:

~~~text
fresh inference
cached reuse
~~~

Therefore:

~~~text
policy_calls = inference_units + cache_reuses
~~~

Every fresh inference also has exactly one attributed reason:

~~~text
first
always
knowledge_change
invalid_cached_action
semantic_staleness
max_age
~~~

Therefore:

~~~text
inference_units
  = refresh_first
  + refresh_always
  + refresh_knowledge
  + refresh_invalid_action
  + refresh_stale_action
  + refresh_age
~~~

Communication remains:

~~~text
communication_units = useful + duplicate
~~~

Any accounting mismatch or protocol violation is an engineering failure.

## Candidate matrix

~~~text
3 frozen Stage 7B base policies
×
5 inference controllers
=
15 paired candidates
~~~

There is no new search over `n,e,r,u`.

## Historical baseline audit

Before interpreting any controller result, all three `always_refresh`
candidates must exactly reproduce the historical Stage 7B reference on both
training and validation.

~~~text
3 base policies × 2 splits = 6 aggregate checks
expected mismatches = 0
~~~

This is separate from the F3a 134-candidate c=1000 audit.

## Training and validation

The Stage 7B split definitions remain frozen.

Training:

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

Validation:

~~~text
same structural box
seed=2
H=2048

24 worlds/evaluated candidate
~~~

All 15 candidates are evaluated on training.

Selection remains:

~~~text
1. minimum failure count
2. discard less-feasible candidates
3. Pareto frontier over:
   rounds
   communication
   duplicates
   policy/computation calls
   inference units
~~~

Only the training frontier can enter validation selection.

All three paired always-refresh baselines are evaluated on validation for
diagnostics even if absent from the training frontier. A baseline outside the
training frontier cannot re-enter selection.

## Hard holdouts

Validation-selected candidates plus the three paired baselines are evaluated
on all six frozen Stage 7B hard sets:

~~~text
population_N_128       36 worlds
density_F_over_N_4     36 worlds
redundancy_R_4         72 worlds
bandwidth_B_8          24 worlds
topology_complete      36 worlds
compound                9 worlds
~~~

Hard results never participate in training or validation selection.

## F3b promotion criterion

A state-aware controller counts as an F3b PASS candidate only if:

~~~text
controller != always_refresh

validation:
  selected_frontier = yes
  failures = 0

paired always-refresh validation baseline:
  failures = 0

inference:
  candidate inference < paired baseline inference

hard holdouts:
  candidate failures across all six splits = 0
~~~

Because the candidate is already on the feasibility-first validation Pareto
frontier, a PASS establishes that inference savings are competitive with the
other measured resource dimensions rather than being accepted as an isolated
trade.

Other deltas against the exact paired baseline are printed explicitly.

If a candidate reduces validation inference but fails a hard holdout, that is
reported as a validation improvement with a hard-generalization limitation,
not promoted as PASS.

## Dataset

Generated and gitignored:

~~~text
trials/f3b-state-aware.tsv
~~~

Rows include:

- phase and split;
- candidate id and frozen base id;
- controller name;
- runs/failures;
- rounds;
- communication/useful/duplicate;
- computation/inference/cache reuse;
- all refresh-reason counters;
- accounting flags;
- frontier selection.

## Canonical verifier

Run:

~~~sh
python3 tools/verify_f3b.py
~~~

The verifier:

1. requires Zig 0.16.0;
2. runs the full repository test suite, including F3a regression tests;
3. validates the exact 15-candidate paired matrix;
4. runs the 6-check historical paired-baseline audit;
5. runs the complete F3b search twice;
6. requires byte-identical evidence replay;
7. validates inference and communication accounting on every row;
8. validates training/validation selection discipline;
9. validates exact paired-baseline presence;
10. validates hard coverage for every selected candidate and baseline;
11. writes `trials/f3b-state-aware.tsv`;
12. reports validation inference savings, hard-holdout behavior, dataset
    SHA-256, and PASS/LIMITATION.

## Outcome semantics

**PASS**

At least one state-aware controller satisfies the full promotion criterion.

**LIMITATION**

All structural/provenance/determinism/accounting gates pass, but no
state-aware controller satisfies the full promotion criterion.

Either is a completed F3b result.

F3 itself remains open after an F3b limitation; the next distinct mechanism
would be uncertainty/novelty-triggered or adaptive-budget inference control.

No canonical F3b result is claimed until the local verifier completes.

## Canonical completion record — 2026-08-29

The authoritative local verifier completed on macOS with Zig 0.16.0.

~~~text
F3b rows: 85
F3b bytes: 11329
F3b sha256:
eb4237fdf5e6ac309b29f01c16345f9ff6507b8806ab986b15fbb3c9e080347a

F3b byte_identical_replay: yes
F3b violations: 0
F3b inference_accounting_failures: 0
F3b communication_accounting_failures: 0
F3b paired_baseline_mismatches: 0
~~~

Candidate and baseline gates:

~~~text
candidate_count: 15
expected_candidate_count: 15
baseline_candidates: 3
invalid_theta: 0
duplicate_candidates: 0
training_worlds: 48
validation_worlds: 24

paired baseline ids: 37,51,93
aggregate_checks: 6
mismatches: 0
~~~

### Validation frontier

~~~text
id=2 base=37 controller=knowledge_or_stale
  failures=0
  rounds=1046
  communication=257666
  duplicates=169774
  computation=55936
  inference=54769
  reuse=1167
  hard_failures=0

id=4 base=37 controller=knowledge_or_stale_age8
  failures=0
  rounds=1046
  communication=257666
  duplicates=169774
  computation=55936
  inference=54769
  reuse=1167
  hard_failures=0

id=7 base=51 controller=knowledge_or_stale
  failures=0
  rounds=1054
  communication=253171
  duplicates=165064
  computation=56576
  inference=55017
  reuse=1559
  hard_failures=0

id=9 base=51 controller=knowledge_or_stale_age8
  failures=0
  rounds=1054
  communication=253171
  duplicates=165064
  computation=56576
  inference=55017
  reuse=1559
  hard_failures=0

id=10 base=93 controller=always_refresh
  failures=0
  rounds=1435
  communication=250805
  duplicates=162945
  computation=76704
  inference=76704
  reuse=0
  hard_failures=0

id=12 base=93 controller=knowledge_or_stale
  failures=0
  rounds=1437
  communication=251061
  duplicates=163160
  computation=76832
  inference=75701
  reuse=1131
  hard_failures=0

id=13 base=93 controller=knowledge_or_stale_age4
  failures=0
  rounds=1437
  communication=251061
  duplicates=163160
  computation=76832
  inference=75701
  reuse=1131
  hard_failures=0

id=14 base=93 controller=knowledge_or_stale_age8
  failures=0
  rounds=1437
  communication=251061
  duplicates=163160
  computation=76832
  inference=75701
  reuse=1131
  hard_failures=0
~~~

### Paired validation improvements

Against the exact always-refresh twin for the same frozen Stage 7B theta:

~~~text
base 37 / knowledge_or_stale:
  saved inference = 1167
  rounds delta = 0
  communication delta = -723
  duplicate delta = -711
  computation delta = 0
  hard failures = 0

base 37 / knowledge_or_stale_age8:
  same canonical validation measurements

base 51 / knowledge_or_stale:
  saved inference = 1559
  rounds delta = 0
  communication delta = -2148
  duplicate delta = -2147
  computation delta = 0
  hard failures = 0

base 51 / knowledge_or_stale_age8:
  same canonical validation measurements

base 93 / knowledge_or_stale:
  saved inference = 1003
  rounds delta = +2
  communication delta = +256
  duplicate delta = +215
  computation delta = +128
  hard failures = 0

base 93 / knowledge_or_stale_age4:
  same canonical validation measurements

base 93 / knowledge_or_stale_age8:
  same canonical validation measurements
~~~

Every promoted state-aware controller remained zero-failure across all six
hard holdout families.

The paired always-refresh hard baselines also remained zero-failure:

~~~text
id37:
  failures=0
  communication=28280472
  inference=1103040

id51:
  failures=0
  communication=27458552
  inference=1100576

id93:
  failures=0
  communication=29195088
  inference=1615264
~~~

### Interpretation

F3b closes as a **PASS**.

The decisive result is that `knowledge_or_stale` is promoted for all three
frozen Stage 7B base policies:

- it preserves zero validation failures;
- it uses strictly fewer inference units than the exact always-refresh twin;
- it lies on the validation feasibility/resource Pareto frontier;
- it remains zero-failure across all six hard holdout families.

For bases 37 and 51, the controller is strictly better on the measured
validation resource vector except that total policy/computation calls remain
equal:

~~~text
same rounds
same computation calls
less inference
less communication
fewer duplicates
~~~

Base 51 shows the largest validation inference reduction:

~~~text
1559 fewer inference units
2148 fewer communication units
2147 fewer duplicate units
0 extra rounds
0 extra computation calls
0 hard failures
~~~

Base 93 demonstrates the tradeoff boundary: state-aware reuse still saves 1003
inference units and generalizes with zero hard failures, but costs two rounds,
256 communication units, 215 duplicate units and 128 computation calls on the
validation aggregate. Both the always-refresh and state-aware variants remain
on the Pareto frontier.

The age-bounded variants do not improve the canonical validation measurements
over plain `knowledge_or_stale` in this experiment. Therefore the simplest
validated controller is:

~~~text
refresh if:
  no cached action exists
  OR local knowledge changed since last inference
  OR cached action became structurally invalid
  OR cached action is semantically stale:
       unsent local facts remain
       AND cached selected facts are already sent

otherwise:
  reuse cached action
~~~

### F3 conclusion

F3a and F3b together answer the broader local-inference-control question:

~~~text
blind probabilistic/round-indexed gating:
  LIMITATION

state-aware knowledge/staleness gating:
  PASS
~~~

The evidence supports retaining the validated communication policy surface:

~~~text
theta = (n,e,r,u)
~~~

and treating inference control as a separate deterministic local controller
rather than adding a probabilistic `c` dimension to theta.

Canonical verdict:

~~~text
F3b PASS: state-aware inference-control evidence complete
~~~
