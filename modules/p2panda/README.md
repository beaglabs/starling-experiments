# F1b — audited P2Panda candidate transport

This directory contains the active F1b adapter. The original rejected
2026-08-28 candidate is preserved byte-for-byte under `historical/`; do not
edit that snapshot.

## What P2Panda owns

P2Panda provides the real eventually-consistent topic-stream wiring between
the eight in-process Starlings nodes. The pinned dependency is:

```text
beaglabs/p2panda
rev 80051611b7b41250815a40c945ae7bece84aa249
```

The validation network configures **no relay and no bootstrap**. The pinned
P2Panda API therefore remains local-area-only. Every run also derives a unique
network ID from the frozen world configuration, preventing unrelated local
P2Panda processes from joining the experiment.

## What Starlings owns

The exact Zig Stage 7A policy is linked through
`src/finalization/f1b_policy_ffi.zig`. Rust does not reimplement policy,
topology, fact selection, idempotent merge, completion, or the scientific
accounting rules.

For every logical sender/recipient edge Starlings records an attempt before
publishing through P2Panda. An attempt must terminate as exactly one of:

- delivered;
- partitioned at the deterministic F1b boundary;
- crashed at the deterministic F1b boundary;
- pending at censor.

The collector also maintains fact-level attribution using the F1a vocabulary:

- `never_transmitted`
- `delivery_faulted`
- `crashed_before_merge`
- `pending_at_censor`
- `unattributed` (candidate limitation)

A P2Panda receive which cannot be tied to a prior Starlings logical attempt is
a **ledger interface violation** and is a hard verifier failure.

## Communication accounting

Two quantities are intentionally separate:

- `attempted_communication_units`: facts carried by all Starlings logical
  recipient attempts, including attempts later faulted or pending;
- `communication_units`: facts on logical recipient edges actually delivered
  to the application.

The delivered identity must hold exactly:

```text
communication_units = useful + duplicate
```

## Contested worlds

F1b does not modify P2Panda's internal QUIC/sync implementation. Instead it
places deterministic Starlings fault gates around the real P2Panda movement
path:

- `partition`: cross-cut deliveries during the frozen partition window are
  rejected and attributed after P2Panda surfaces the envelope;
- `crash_restart_persist`: the designated Starlings node stops policy work and
  rejects deliveries during the crash window, then restarts with knowledge
  retained;
- `crash_restart_reset`: same, but knowledge is restored to its initial
  deterministic state. Collector facts erased by the reset are recorded
  explicitly before they may receive `crashed_before_merge` attribution.

This evaluates whether the P2Panda wiring can support a fully audited
Starlings transport boundary. It is not a claim that F1b injects faults inside
P2Panda itself.

## Canonical validation

From the repository root:

```sh
python3 tools/verify_f1b.py
```

The verifier runs:

1. all Zig tests including the exact FFI-policy equivalence test;
2. Rust formatting/tests and the release build;
3. 24 fault-free worlds (four frozen profiles × ring/grid × seeds 0,1,2);
4. K=3 full-result reruns on four representative fixed worlds;
5. partition, persistent crash/restart, and reset crash/restart on two
   representative worlds.

Outcome semantics:

- **PASS**: all fault-free worlds converge, K-reruns are byte-equivalent at the
  result-row level, no sync error/unattributed loss appears, and the contested
  subset is fully accounted;
- **LIMITATION**: structural/accounting gates hold but the candidate exhibits
  nondeterminism, fault-free non-convergence, P2Panda sync errors, or
  unattributed loss;
- **hard failure**: broken build/FFI, accounting identity failure, unexercised
  configured fault, or ledger interface violation.

The deterministic Zig substrate remains authoritative in either PASS or
LIMITATION.
