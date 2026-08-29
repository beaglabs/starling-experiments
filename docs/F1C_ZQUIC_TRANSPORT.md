# F1c — thin zquic transport substrate

F1c replaces the F1b P2Panda candidate with a deliberately thinner transport
boundary. It does not rewrite the F1b result: the recurring P2Panda background
runtime panic remains a measured candidate limitation.

## Frozen transport dependency

F1c pins:

```text
zigstack/zquic
tag: v1.7.48
commit: 4bd041ac95425fb0aa229b74c9d3316c74aaf829
package hash:
zquic-1.7.0-2zRc1PSAFgDCESpm-vZsUr4O02HM0dpzmVJSx5WXW6ES
minimum Zig: 0.16.0
```

zquic's own package pins `zig-varint` v0.1.0. No additional discovery,
replication, actor, database, HTTP, WebSocket, libp2p, or Rust runtime is part
of F1c.

## Ownership boundary

Starlings owns:

- policy and local observations;
- the frozen Stage 7C asynchronous local-clock schedule;
- topology and intended recipients;
- logical envelope identity;
- fact selection and fact IDs;
- idempotent merge;
- logical retries and completion;
- deterministic partition/crash gates;
- attempt/terminal accounting;
- missing-fact attribution;
- experiment configuration and evidence.

zquic owns:

- QUIC packetization;
- TLS 1.3;
- ACK/retransmission;
- loss recovery;
- congestion and flow control;
- UDP transport;
- ordered stream byte delivery.

QUIC retransmissions are not new Starlings attempts. A Starlings attempt is one
logical `(sender, sequence, recipient)` edge.

## Fixed peer graph

There is no discovery layer in F1c.

Each node binds a server to a deterministic loopback port beginning at 47200.
For every directed topology edge, the sender establishes one QUIC connection
to the recipient. Each directed connection uses one persistent
client-initiated unidirectional raw application stream.

For N=8:

```text
ring: 16 directed links
grid: 20 directed links
```

The TLS ALPN is:

```text
starlings/1
```

A frozen self-signed **ECDSA P-256** test certificate/key pair lives under
`src/finalization/fixtures/f1c/`. P-256 is required by the pinned zquic v1.7.48
CertificateVerify implementation. The fixture is test material only.

## Wire framing

Each persistent QUIC stream carries concatenated length-prefixed Starlings
envelopes:

```text
QUIC varint body length
magic
wire version
sender u16
recipient u16
sequence u32
selected-fact count u16
fact IDs [u16; selected]
```

The wire layer is intentionally minimal. Multihash, multicodec, multiaddr and
other Multiformats are reserved for a later protocol-extension layer rather
than being mixed into the F1c transport experiment.

## Accounting

Every intended recipient edge opens a logical attempt before either fault
termination or transport submission.

Terminal identity:

```text
transport_attempts
  = delivered
  + partitioned
  + crashed
  + pending
```

Communication is split into:

```text
attempted_communication_units
communication_units
```

and delivered communication must satisfy:

```text
communication_units = useful + duplicate
```

A QUIC envelope is only marked delivered once the recipient parses the full
Starlings frame and its sender/recipient/sequence/fact payload matches the open
attempt exactly.

Missing collector facts use the F1a cause vocabulary:

- `never_transmitted`
- `delivery_faulted`
- `crashed_before_merge`
- `pending_at_censor`
- `unattributed`

## Frozen asynchronous schedule

F1c preserves the frozen Stage 7C/F1a operator schedule rather than changing
the experiment to synchronous execution.

For every canonical world:

```text
schedule_seed = world seed
clock_jitter = 3
period[node] = 1 + keyed(schedule_seed,node,0,CLOCK) % 3
first_tick[node] = 1 + mix64(clock_key) % period[node]
```

Only operators whose local clock fires on a logical tick make a policy
decision. Each logical attempt is folded into a deterministic
`schedule_hash`, which is emitted in every evidence row. This lets the
verifier distinguish deterministic scheduler/policy behavior from variation in
the real QUIC transport.

## Fault semantics

Faults are injected at the Starlings/transport boundary, not by modifying
zquic internals.

### Partition

During the fixed partition interval, a logical attempt crossing the cut
terminates as `partitioned`. An envelope already in QUIC before the cut is
checked again when it reaches the Starlings receive boundary; if it arrives
during the cut, it is also terminally partitioned rather than merged.

### Crash/restart

The canonical crash target is the collector, node 0, matching F1a.

During the crash interval:

- the crashed node performs no policy work;
- attempts targeting the crashed node terminate as `crashed`;
- in-flight envelopes arriving during the crash are rejected and attributed.

Persistent restart retains knowledge and clears sent/cursor policy metadata.
Reset restart restores the node's deterministic initial knowledge and records
which facts were actually erased.

## Canonical evidence

Run:

```sh
python3 tools/verify_f1c.py
```

The verifier requires Zig 0.16.0 and performs:

1. all core/substrate/F1a/F1c Zig tests;
2. one ReleaseFast build of the installed F1c runner;
3. one real QUIC smoke world;
4. 24 fault-free worlds:
   four frozen profiles × ring/grid × seeds 0,1,2;
5. K=3 reruns of four fixed worlds for full-result determinism;
6. partition, persistent crash/restart and reset crash/restart over two
   representative worlds.

Canonical evidence contains 42 rows:

```text
24 fault-free
12 determinism-audit
 6 contested
--
42 rows
```

Generated output:

```text
trials/f1c-zquic-wired.tsv
```

The trial file remains ignored. Canonical hash/results are frozen back into
`beaglabs/starlings` after the local gate completes.

## Outcome semantics

**PASS** requires:

- all structural accounting identities;
- zero protocol violations;
- zero malformed frames;
- zero failed stream submissions;
- zero unattributed missing facts;
- fault-free convergence;
- no transport panics;
- complete full-row K-rerun stability, including the deterministic
  `schedule_hash` and measured QUIC counters.

**LIMITATION** is a completed scientific result when structural gates hold but
the real zquic candidate shows nondeterminism, fault-free non-convergence,
transport panics, or unattributed loss.

Build/runtime/accounting failures are not scientific negative results and must
be fixed before F1c is complete.

The deterministic Zig substrate remains authoritative in either PASS or
LIMITATION.

## Canonical completion record — 2026-08-29

The authoritative local verifier completed on macOS with Zig 0.16.0.

~~~text
F1c rows: 42
F1c bytes: 8007
F1c sha256:
6ef0b88e5c06c1ceb3ce41ec08e1fcec89a08743e7e2440e5d48a097b3e66ddb

zquic tag: v1.7.48
zquic commit: 4bd041ac95425fb0aa229b74c9d3316c74aaf829
zquic package hash:
zquic-1.7.0-2zRc1PSAFgDCESpm-vZsUr4O02HM0dpzmVJSx5WXW6ES

fault_free_rows: 24
fault_free_successes: 24/24
determinism_audit_rows: 12
determinism_K: 3
contested_rows: 6

envelope_accounting_failures: 0
missing_accounting_failures: 0
communication_accounting_failures: 0
protocol_violations: 0
send_failures: 0
malformed_frames: 0
unattributed_missing: 0
pending_at_censor: 0
transport_panics: 0
udp_datagrams: 37396
backpressure_events: 0
~~~

Full-row K=3 audit:

~~~text
novel_first / grid / 2:
  6d24eee242cc7dbc
  6d24eee242cc7dbc
  6d24eee242cc7dbc
  stable=yes

theta37 / ring / 0:
  45f9f003bc89eab4
  45f9f003bc89eab4
  45f9f003bc89eab4
  stable=yes

theta51 / grid / 1:
  9926f2289bf713c8
  9926f2289bf713c8
  9926f2289bf713c8
  stable=yes

theta93 / ring / 2:
  608d12243989469e
  608d12243989469e
  608d12243989469e
  stable=yes
~~~

Canonical verdict:

~~~text
F1c PASS: pinned zquic transport is stable across the audit and fully
accounted under the contested subset
~~~
