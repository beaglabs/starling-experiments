# F1a — canonical deterministic fault matrix

F1a extends the frozen Stage 7C asynchronous substrate into the contested
environment without modifying the S0 substrate files.

## Frozen matrix

- profiles: `theta37`, `theta51`, `theta93`, `round_robin`, `seeded`,
  `novel_first`
- topologies: `ring`, `grid`
- world/schedule seeds: `0`, `1`, `2`
- `N=8`, `F=32`, `R=2`, `B=2`, `max_ticks=4096`
- 12 fault worlds
- 432 canonical rows

Fault parameterization:

| World | Deterministic injection |
| --- | --- |
| `no_fault` | frozen Stage 7C settings |
| `loss_50` | 50 permille loss |
| `loss_200` | 200 permille loss |
| `duplication_250` | 250 permille duplicate-copy eligibility |
| `latency_jitter` | base latency 4, jitter 12 |
| `reordering` | odd transport ordinals delayed by 12 ticks |
| `partition` | cut at node 4 from ticks 8–47, then reconnect |
| `crash_restart_persist` | collector crash ticks 8–39, knowledge persists |
| `crash_restart_reset` | collector crash ticks 8–39, learned knowledge reset |
| `stale_view` | policy-visible knowledge refreshed every 8 ticks |
| `queue_capacity` | pending queue capacity reduced to 8 envelopes |
| `combined` | 100 permille loss + 250 duplication + latency/jitter + partition |

## Attribution

Every fact absent from the collector at censor is assigned exactly one primary
terminal cause in priority order:

1. `pending_at_censor`: a containing envelope remains pending;
2. `crashed_before_merge`: the fact was rejected by a crashed recipient or
   erased by non-persistent restart;
3. `delivery_faulted`: a containing envelope was lost, partitioned, or
   rejected by queue capacity;
4. `never_transmitted`: no containing envelope was ever attempted toward the
   collector;
5. otherwise `unattributed`, which fails the F1a gate.

The ledger also records envelope terminal categories, duplicate copies,
reordered deliveries, stale observations, and forced-reordering schedules.

## Equivalence gate

The 24 `no_fault` rows for the four frozen Stage 7C profiles are compared
field-for-field against the authoritative S0 engine, including schedule and
trace hashes.

## Evidence command

```sh
python3 tools/verify_f1a.py
```

The verifier runs all tests, validates the 432-world matrix, emits the dataset
twice and requires byte identity, checks that every configured fault is
actually exercised, and writes the ignored canonical dataset to
`trials/f1a-fault-matrix.tsv`.

The dataset SHA-256 and per-profile summary are frozen into the Starlings
documentation of record only after this gate passes.
