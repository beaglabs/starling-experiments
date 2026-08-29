# F1b P2Panda-wired candidate evaluation

F1b re-evaluates the 2026-08-28 P2Panda candidate only after F1a established
the deterministic contested-environment accounting model.

## Historical source

The rejected candidate is preserved unchanged in
`modules/p2panda/historical/` from:

```text
starlings commit:
b7fd4571f7e4d770542f7d9b69b89d00d353099b

P2Panda fork revision:
80051611b7b41250815a40c945ae7bece84aa249
```

See `modules/p2panda/HISTORICAL_SOURCE.md` for source blob identities.

## Active candidate

The active adapter:

- links the exact Stage 7A Zig policy through a C ABI;
- uses P2Panda only for real topic-stream movement between in-process nodes;
- records a Starlings logical attempt before every recipient edge is
  published;
- records delivered/partitioned/crashed/pending terminal categories;
- exposes P2Panda local/remote operation counts, sync sessions, sync errors,
  and duplicate envelope observations;
- maintains per-fact collector attribution;
- distinguishes attempted communication from delivered communication;
- requires `communication_units = useful + duplicate`;
- rejects an unpaired P2Panda receive as a ledger-interface violation.

No relay or bootstrap server is configured. A run-specific P2Panda network ID
isolates the validation group from unrelated local nodes.

## Fault semantics

The contested subset is injected at the Starlings/P2Panda application
boundary, not inside P2Panda internals.

### Partition

A real P2Panda-delivered envelope whose intended Starlings edge crosses the
frozen cut during the partition window is terminally rejected as
`delivery_faulted`.

### Crash/restart

The designated Starlings node performs no policy work and rejects incoming
merges during the frozen crash window.

- persistent restart retains knowledge;
- reset restart restores deterministic initial knowledge and clears sent/cursor
  metadata;
- collector facts actually erased by reset are explicitly marked, so
  `crashed_before_merge` cannot be used as a generic explanation for state
  loss.

## Determinism

The verifier compares the **entire result row** across K=3 reruns for four
fixed fault-free worlds. `result_signature` is reported as a compact digest,
but signature equality alone is not sufficient.

The row deliberately contains P2Panda operational counters
(`p2panda_local_ops`, `p2panda_remote_ops`, `sync_sessions`,
`sync_errors`). If the real candidate transport varies in these measured
results, F1b records LIMITATION rather than normalizing the variation away.

## Evidence command

```sh
python3 tools/verify_f1b.py
```

Generated dataset:

```text
trials/f1b-p2panda-wired.tsv
```

The dataset is gitignored. After a completed PASS or LIMITATION run, its
SHA-256, audit result, fault-free convergence result, and contested summary are
frozen into the `starlings` documentation of record.
