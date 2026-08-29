# F2.2 — asynchronous scaling boundary

F2.2 measures the feasibility boundary of the frozen asynchronous Starlings
execution model under a fixed local decision budget.

F2.1 already established the N=8 synchronous-to-asynchronous cost gap. F2.2
does not repeat the synchronous arm and does not change the frozen policy
surface.

## Question

For a fixed 4096 decisions/operator budget:

> How far can each frozen asynchronous policy scale as population and fact
> density increase, and where does convergence become right-censored?

## Frozen matrix

~~~text
N:
  8
  16
  32
  64
  128

F/N:
  1
  2

topology:
  ring
  grid

profiles:
  theta37
  theta51
  theta93
  novel_first

seeds:
  0
  1
  2

redundancy = 2
bandwidth = 2
decision budget/operator = 4096
clock_jitter = 3
latency_min = 1
latency_jitter = 4
~~~

Exactly:

~~~text
5 populations
× 2 fact densities
× 2 topologies
× 4 profiles
× 3 seeds
= 240 worlds
~~~

Maximum canonical world:

~~~text
N = 128
F = 256
~~~

which remains inside the frozen substrate bounds
(`max_operators=1024`, `max_facts=2048`).

## F2.1 continuity

F2.2 uses its own harness rather than modifying the frozen F2.1 code.

Before interpreting any scaling result, the F2.2 test suite requires exact
agreement with the F2.1 capped async harness over the complete N=8 scaling
box:

~~~text
4 profiles × 2 densities × 2 topologies × 3 seeds = 48 worlds
~~~

The compared measurements include outcome, elapsed ticks, collector state,
policy ticks/actions, transport attempts, pending deliveries, reordering,
communication/useful/duplicate units, schedule hash, trace hash and
violations.

This proves that the F2.2 harness differs only where the scaling experiment
requires new censoring semantics.

## Budget exhaustion and delivery drain

F2.1 had no budget-bound canonical rows, so it never had to interpret
in-flight messages after all operators exhausted their local budgets.

F2.2 may reach that boundary.

A world must **not** be declared censored merely because every operator has
spent decision 4096 if envelopes produced by those decisions are still in
flight.

F2.2 therefore uses:

~~~text
phase 1:
  policy decisions + deterministic asynchronous delivery

when every operator reaches 4096 decisions:
  record budget_exhausted_tick
  prohibit all further policy decisions

phase 2:
  continue deterministic delivery only

if collector converges:
  success

else when pending queue becomes empty:
  censored
~~~

The maximum logical horizon additionally includes the maximum configured
delivery latency.

A censored world is valid only if:

~~~text
success = no
censored = yes
min_local_decisions = 4096
max_local_decisions = 4096
budget_exhausted_tick > 0
pending = 0
collector_final < facts
~~~

This makes the scaling boundary a decision-budget boundary rather than a
scheduler or in-flight-delivery artifact.

## No-fault structural gate

F2.2 is a no-fault experiment. Every row must have:

~~~text
dropped = 0
partitioned = 0
crashed = 0
queue_overflow = 0
duplicate_copies = 0
violations = 0
~~~

Reordering is allowed because deterministic latency jitter can reorder
delivery ordinals.

Accounting must satisfy:

~~~text
transport_attempts = delivered + pending

communication_units = useful + duplicate
~~~

Successful worlds may still contain pending envelopes after the collector
reaches completion; those envelopes remain explicitly accounted as pending.

Censored worlds must drain pending to zero before censoring.

## Dataset

Generated and gitignored:

~~~text
trials/f2-scaling.tsv
~~~

Per-world measurements include:

- success / censored;
- elapsed ticks;
- budget-exhausted tick and post-budget drain ticks;
- collector initial/final facts;
- total/min/max local decisions;
- transport attempts/delivered/pending;
- no-fault terminal counters;
- deterministic reordering count;
- communication/useful/duplicate units;
- schedule hash;
- trace hash;
- protocol violations;
- accounting flags.

## Feasibility boundary

There are three seeds at every:

~~~text
profile × topology × F/N × N
~~~

So F2.2 does not collapse the boundary to one seed.

For each `profile × topology × F/N` group, the verifier reports:

~~~text
pattern:
  N:successful_seeds/3

largest_all_success_N:
  largest N where all 3 seeds converge

first_any_censored_N:
  smallest N where fewer than 3 seeds converge

first_all_censored_N:
  smallest N where 0 of 3 seeds converge

monotone_success_counts:
  whether seed-success count never increases as N increases
~~~

`first_any_censored_N` is the first observed feasibility loss.
`first_all_censored_N` is the stronger all-seeds boundary.

Non-monotone success counts are reported as evidence rather than silently
forced into a monotone threshold.

## Canonical verifier

Run:

~~~sh
python3 tools/verify_f2_2.py
~~~

The verifier:

1. requires Zig 0.16.0;
2. runs the full repository test suite, including the 48-world F2.1
   continuity gate;
3. builds the F2.2 runner in ReleaseFast;
4. generates all 240 worlds;
5. generates all 240 worlds a second time;
6. requires byte-identical full-dataset replay;
7. requires exact matrix membership with no duplicates/missing worlds;
8. validates decision-budget, censoring, delivery-drain, no-fault and
   accounting invariants;
9. writes `trials/f2-scaling.tsv`;
10. prints its SHA-256, profile summaries and all 16 feasibility-boundary
    groups.

## Outcome semantics

F2.2 **PASS** means the 240-world scaling experiment is deterministic and
structurally valid. It does not require every world to converge.

Budget-exhausted censoring is the scientific quantity being measured.

Build failures, queue overflow, silent pending delivery at censor, protocol
violations, accounting failure or replay nondeterminism are engineering
failures and must be fixed before any scaling conclusion is accepted.

After PASS, the dataset SHA and boundary table are frozen into
`beaglabs/starlings`. F2 is then complete and its experiment scaffold is
deleted only after the documentation-of-record merge.
