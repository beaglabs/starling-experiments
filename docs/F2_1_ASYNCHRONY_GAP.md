# F2.1 — synchronous-to-asynchronous cost gap

F2.1 measures the cost of the frozen asynchronous execution model relative to
the synchronous Stage 7A execution model under matched local decision budgets.

F2.1 is intentionally separated from F2.2 scaling. No N>8 scaling result is
generated or interpreted in this gate.

## Question

For the same policy, world, topology, seed, fact distribution, redundancy,
bandwidth and per-operator decision budget:

> What changes when execution moves from synchronous rounds to the frozen
> Stage 7C asynchronous local-clock model?

The measured dimensions are:

- completion: synchronous rounds vs asynchronous ticks;
- computation: total policy decisions;
- communication units;
- useful communication;
- duplicate communication;
- success/censor outcome.

## Frozen paired worlds

~~~text
profiles:
  theta37
  theta51
  theta93
  round_robin
  seeded
  novel_first

topologies:
  ring
  grid

seeds:
  0
  1
  2

N = 8
F = 32
R = 2
B = 2
decision budget = 4096 per operator
~~~

This produces exactly 36 paired rows.

## Matched budget

A synchronous Stage 7A round gives every operator one policy decision.
Therefore:

~~~text
max synchronous rounds = 4096
max synchronous decisions/operator = 4096
~~~

The asynchronous side uses the frozen Stage 7C local-clock schedule but adds
one and only one experimental boundary:

~~~text
max asynchronous decisions/operator = 4096
~~~

The async harness runs long enough for the slowest allowed period
(`clock_jitter=3`) to consume all 4096 decisions if convergence has not
occurred:

~~~text
max_ticks = budget * clock_jitter + clock_jitter + 1
~~~

A failed asynchronous row is valid only when every operator has consumed
exactly 4096 decisions. It is then recorded as right-censored.

## Frozen Stage 7C parity

The per-operator cap requires a derived Stage 7C harness; simply increasing
`max_ticks` would allow fast-clock operators to consume more decisions than
slow-clock operators.

To prevent that derived harness from silently changing Stage 7C semantics,
F2.1 runs the frozen Stage 7C engine alongside it.

For every successful async row, where the decision cap did not bind, the
derived harness must exactly match frozen Stage 7C on:

- success and elapsed ticks;
- collector initial/final fact counts;
- local policy ticks;
- actions and rejected actions;
- transport attempts and all terminal categories;
- duplicate/reordering counters;
- communication/useful/duplicate units;
- schedule hash;
- trace hash;
- violations.

Any successful row with `stage7c_parity=no` is an engineering failure, not a
scientific result.

A budget-censored row records `stage7c_parity=budget_bound` because frozen
Stage 7C has no per-operator cap and is no longer the same experiment beyond
that boundary.

## Accounting

Every async row must satisfy:

~~~text
transport_attempts
  = delivered
  + dropped
  + partitioned
  + crashed
  + queue_overflow
  + pending

communication_units
  = useful
  + duplicate
~~~

F2.1 is a no-fault experiment, so any protocol violation or accounting
failure is a hard failure.

## Dataset

Generated but gitignored:

~~~text
trials/f2-gap.tsv
~~~

Columns include both synchronous and asynchronous outcomes and explicit
deltas for:

- communication units;
- duplicate units;
- policy calls;
- async ticks minus sync rounds.

## Canonical verifier

Run:

~~~sh
python3 tools/verify_f2_1.py
~~~

The verifier:

1. requires Zig 0.16.0;
2. runs all repository tests, including F1c and F2.1 parity tests;
3. builds the F2.1 runner in ReleaseFast;
4. executes a 36-world internal validation sweep;
5. generates the 36-row dataset twice;
6. requires byte-identical replay;
7. validates exact world uniqueness and the 4096 decision budget;
8. rejects protocol, accounting, parity, or censoring errors;
9. writes the generated dataset and prints its SHA-256 plus per-profile gap
   summaries.

F2.1 is complete only after the local canonical dataset SHA and findings are
frozen into `beaglabs/starlings`.

F2.2 scaling does not begin until this gate is closed.
