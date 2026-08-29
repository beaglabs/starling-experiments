# F2 asynchrony cost and scaling scaffold

F2 is intentionally disposable. The working tree contains the experiment only
until both canonical dataset hashes and summaries are frozen in
`beaglabs/starlings`.

## F2.1 paired gap

36 paired worlds:

- six profiles: theta37, theta51, theta93, round_robin, seeded, novel_first
- ring and grid
- seeds 0, 1, 2
- N=8, F=32, R=2, B=2
- synchronous Stage 7A vs asynchronous Stage 7C semantics

The asynchronous side uses the same exact per-operator decision budget as the
synchronous maximum-round budget.

Dataset: `trials/f2-gap.tsv`.

## F2.2 asynchronous scaling

240 worlds:

- N in 8, 16, 32, 64, 128
- F/N in 1, 2
- ring and grid
- theta37, theta51, theta93, novel_first
- seeds 0, 1, 2
- exact local decision budget: 4096 decisions per operator

A failed async run is validly right-censored only after every operator has
consumed all 4096 local decisions. The derived F2 async harness extends the
frozen Stage 7C engine only with this budget/censor boundary.

Dataset: `trials/f2-scaling.tsv`.

## Evidence

```sh
python3 tools/verify_f2.py
```

The verifier runs both datasets twice byte-for-byte, freezes their SHA-256
values, reports paired resource deltas, and reports the smallest censored N for
each profile/topology/fact-density combination.

After the hashes and summaries are merged into `starlings`, the F2 source and
runner are deleted from this repository in a follow-up commit on this stage.
