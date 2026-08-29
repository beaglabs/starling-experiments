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
