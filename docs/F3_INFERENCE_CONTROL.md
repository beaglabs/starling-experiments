# F3 local inference control

F3 extends the frozen Stage 7A/7B policy surface from

```text
theta = (n,e,r,u)
```

to

```text
theta = (n,e,r,u,c)
c = inference-gating permille
```

without changing the frozen four-dimensional policy implementation.

## Gate semantics

Each operator caches the last action computed from a refreshed local
observation.

- refresh/recompute: one inference unit;
- cached-action reuse: zero inference units;
- refresh eligibility is deterministic from seed, operator, and local round;
- the first policy tick always refreshes because no cache exists yet.

The `c=1000` corner delegates directly to the frozen Stage 7A `run` function.
It is not a rewritten approximation. Its inference-unit count equals the
baseline policy-call count.

## Search

The Stage 7B world definitions and selection discipline are re-materialized
from historical blob `e91f88b2ea2dafd6bd51113954ff03aee4330163`.

The new search retains:

- 6 fixed named controls;
- 128 deterministic Latin-hypercube candidates;
- 48 frozen training worlds per candidate;
- 24 untouched validation worlds for training-frontier candidates;
- feasibility first, then resource Pareto selection;
- the six Stage 7B hard-holdout splits.

The Latin hypercube is extended to five dimensions. Pareto dominance adds
`inference_sum` as a minimized dimension while retaining rounds,
communication, duplicates, and computation calls.

## Exact Stage 7B corner audit

A separate 134-candidate `c=1000` corner set is generated directly from the
historical Stage 7B candidate generator. Every candidate aggregate is compared
against the historical reference on both training and validation.

## Frozen family comparison

The known Stage 7B validation-selected family is evaluated ungated on every
hard holdout:

- id 37: `(244,94,15,958,1000)`
- id 51: `(354,141,0,994,1000)`
- id 93: `(685,283,960,344,1000)`

For any gated candidate selected on validation, an otherwise identical
`c=1000` twin is also evaluated on validation.

## Evidence

```sh
python3 tools/verify_f3.py
```

Scientific outcome semantics:

- `PASS`: at least one selected gated theta has zero validation failures and
  strictly fewer inference units than its zero-failure ungated twin;
- `LIMITATION`: the structural/determinism/corner gates pass, but no gated
  theta meets that criterion.

Either is a completed F3 evidence result. Structural mismatches, replay
nondeterminism, protocol violations, or a failed `c=1000` corner audit are
hard verifier failures.
