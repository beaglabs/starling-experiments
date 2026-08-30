# D1 — Frozen Paper-Shaped Fixed Reference

Status: implementation complete on the D1 branch; canonical local validation pending.

D1 establishes the **A arm** of the EvoScene-emergent experiment:

~~~text
same deterministic runtime
same artifact contracts
same mock/local operator interface
same evaluator

A = frozen fixed schedule
~~~

D1 does not attempt to improve reconstruction quality. Real depth, geometry,
fusion, and rendering arrive in D2 behind the same contracts.

## Frozen controller

The schedule is deliberately centralized and non-adaptive:

~~~text
input
  -> estimate depth
  -> estimate camera
  -> build geometry

  -> fixed pose A
  -> render
  -> fuse
  -> refine geometry

  -> fixed pose B
  -> render
  -> fuse
  -> refine geometry

  -> evaluate
  -> STOP
~~~

The two fixed poses are:

~~~text
A: azimuth  35.000 deg, elevation 10.000 deg
B: azimuth 325.000 deg, elevation 10.000 deg
~~~

Pose values are encoded deterministically into the existing D0 proposal
payload. The schedule does not inspect quality, uncertainty, marginal gain, or
remaining budget to decide what runs next.

## Schedule fingerprint

The fixed controller has its own BLAKE3 fingerprint derived from:

- schedule format version;
- exact operator order;
- exact action order;
- exact action payloads;
- fixed STOP quality floor.

The fingerprint is independent of execution seed. This lets later stages prove
that D2 tool substitutions and D3/D4 emergent experiments did not silently
change the A-side controller.

## Expected accounting

With the D0 mock operators, the frozen reference must produce exactly:

~~~text
trace events:             13
accepted actions:         13
rejected actions:          0
tool invocations:         10
control actions:           3
produced artifacts:       12
artifact store size:      13
mock wall time:          184 ms
communication units:      38
terminated:              yes
~~~

Per-tool calls:

~~~text
depth:       1
camera:      1
geometry:    3
view:        2
fusion:      2
evaluator:   1
~~~

## D1 gate

Run:

~~~sh
zig build test-demo-evoscene-d1

zig build -Doptimize=ReleaseFast \
  run-demo-evoscene-d1 -- validate

zig build test
~~~

The validator requires:

~~~text
same seed + same input + frozen schedule
  -> byte-identical repeated execution

canonical trace
  -> replay
  -> byte-identical canonical trace

trace action sequence
  == frozen schedule

all expected cost/accounting identities
  == exact

final artifact
  == SceneRepresentation

evaluation
  == EvaluationReport parented by final scene

STOP
  == accepted by runtime quality authority
~~~

Expected terminal verdict:

~~~text
D1 PASS: frozen paper-shaped fixed reference complete
~~~

Do not begin D2 until this local gate passes.
