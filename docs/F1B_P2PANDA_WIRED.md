# F1b — audited P2Panda-wired candidate transport

F1b re-evaluates the historical P2Panda attempt only after F1a established a
complete deterministic transport/fact ledger.

The historical candidate remains preserved byte-for-byte under
`modules/p2panda/historical/`. The active adapter lives separately under
`modules/p2panda/`.

## Ownership boundary

P2Panda provides only in-process network wiring and replication.

Starlings owns:

- theta/policy execution through the Zig FFI;
- initial fact placement;
- topology and intended recipients;
- stable envelope identity `(run_nonce, sender, sequence)`;
- per-recipient logical attempt identity
  `(sender, sequence, recipient)`;
- deterministic partition/crash gates;
- fact merge and completion;
- terminal transport ledger;
- collector-missing attribution;
- deterministic result signatures and evidence classification.

No external-host sockets are part of the canonical F1b validation design.

## Ledger

An intended recipient attempt is recorded before publication.

At an intended recipient's P2Panda `Processed` event the attempt closes as:

- `delivered`;
- `partitioned`;
- `crashed`.

Any attempt without a terminal receive event at censor remains `pending`.

The required identity is:

```text
attempts = delivered + partitioned + crashed + pending
```

Collector-missing facts are classified as:

- `pending_at_censor`;
- `crashed_before_merge`;
- `delivery_faulted`;
- `never_transmitted`;
- otherwise `unattributed`.

A crash-reset may erase a previously delivered collector fact; that is
classified as `crashed_before_merge`.

## Evaluation

`tools/verify_f1b.py` runs:

1. the Zig/FFI test gate;
2. a 24-world fault-free transfer sweep:
   - theta37/theta51/theta93/novel_first;
   - ring/grid;
   - seeds 0,1,2;
3. a K=3 fixed-configuration determinism audit over four representative
   worlds;
4. a contested subset for partition, crash/restart with persistent knowledge,
   and crash/restart with reset knowledge.

The audit compares a deterministic result signature that includes convergence,
collector facts, policy progress, attempts/terminals, communication/useful/
duplicate units, P2Panda operation/session counts, sync/policy errors, missing
fact causes, and accounting status. Wall-clock elapsed time is intentionally
excluded.

## Outcome semantics

`PASS` requires:

- every interface/accounting identity is complete;
- every fault-free world converges;
- no unattributed collector-missing facts;
- no P2Panda sync errors;
- byte-stable result signatures across each K-rerun group.

`LIMITATION` is a completed scientific result when the interface accounting
is structurally valid but P2Panda exhibits measurable nondeterminism,
fault-free non-convergence, sync errors, or unattributed loss.

The deterministic Zig substrate remains authoritative in either case.
