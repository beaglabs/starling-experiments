# D0 — Deterministic Runtime Contracts

Status: implementation complete on the D0 branch; canonical local validation pending.

D0 creates the executable authority boundary for the EvoScene-emergent demo
without introducing any real depth, geometry, or novel-view model.

## Scope

Implemented:

- 256-bit BLAKE3 artifact identities;
- causal parent references and provenance validation;
- typed specialist operators;
- role-scoped action permissions;
- exact input artifact schemas;
- deterministic action/communication accounting;
- deterministic mock tool adapters;
- deterministic event runtime;
- runtime-owned resource limits;
- runtime-owned termination validation;
- canonical binary trace encoding;
- byte-for-byte replay;
- an intentional invalid action fixture.

Not implemented in D0:

- the D1 fixed reconstruction schedule;
- real image/depth processing;
- real 3D geometry;
- real novel-view synthesis;
- emergent agents;
- benchmark scenes.

The `runFixture` sequence in `src/runtime.zig` is only a contract/replay test.
It is not the D1 baseline and is not used as an architectural scheduler.

## Authority boundary

~~~text
proposal
  -> role validation
  -> input-schema validation
  -> budget validation
  -> deterministic tool/control execution
  -> content-addressed artifact
  -> accounting
  -> canonical trace
~~~

The critic may propose STOP, but the runtime accepts it only when an
`EvaluationReport` satisfies the deterministic quality floor.

## D0 fixture

The fixture intentionally contains one invalid role/action pair followed by a
complete mock loop:

~~~text
invalid geometry -> estimate_depth        REJECT

spatial_prior -> estimate_depth           ACCEPT
spatial_prior -> estimate_camera          ACCEPT
geometry      -> build_geometry           ACCEPT
view_planner  -> propose_view             ACCEPT
novel_view    -> render_view              ACCEPT
fusion        -> fuse_view                ACCEPT
geometry      -> refine_geometry          ACCEPT
critic        -> evaluate                 ACCEPT
critic        -> propose_stop             ACCEPT
~~~

Expected accounting:

~~~text
trace events:              10
proposed actions:          10
accepted actions:           9
rejected actions:           1
tool invocations:           7
accepted control actions:   2
produced artifacts:         8
artifact store size:        9  # input + 8 produced
mock wall time:           119 ms
communication units:       28
terminated:               yes
~~~

Exact identities:

~~~text
proposed_actions = accepted_actions + rejected_actions

accepted_actions =
  tool_invocations + accepted_control_actions

communication_units =
  proposal_units + decision_units + evidence_units
~~~

## Provenance gate

Every non-root artifact must:

- reference one or two existing parent artifact IDs;
- carry its producing specialist role;
- carry a payload/content hash;
- receive an identity derived from kind, producer, parents, value, and payload
  hash.

Wall-clock timing and creation step are metadata and do not affect the
content/provenance identity.

## Replay gate

The runtime encodes each semantic trace using an explicitly versioned canonical
binary encoding.

D0 requires:

~~~text
same seed + same proposals + same mock tools
  -> byte-identical canonical trace

canonical trace
  -> replay
  -> byte-identical canonical trace
~~~

A different seed must change the canonical trace.

## Local validation

Run:

~~~sh
zig build test-demo-evoscene-d0

zig build -Doptimize=ReleaseFast   run-demo-evoscene-d0 -- validate

zig build test
~~~

The dedicated validator should end with:

~~~text
D0 PASS: deterministic demo runtime contracts complete
~~~

Do not advance to D1 until all three commands pass locally.
