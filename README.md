<div align="center">

<img src="assets/starlings-logo-placeholder.svg" alt="Starlings logo placeholder" width="520">

# Starlings Experiments

**Evidence-backed coordination without a central schedule.**

[![Zig](https://img.shields.io/badge/Zig-0.16.0-F7A41D?logo=zig&logoColor=white)](https://ziglang.org/)
[![Protocol](https://img.shields.io/badge/protocol-operator--neutral-655eb6)](https://github.com/beaglabs/starlings)
[![Replay](https://img.shields.io/badge/replay-deterministic-2ea44f)](#verification)
[![Status](https://img.shields.io/badge/status-active%20research-444444)](#research-status)

</div>

---

Starlings is an experimental framework for studying **decentralized coordination, controlled emergence, and local decision-making in heterogeneous agent populations**.

The core protocol lives in [`beaglabs/starlings`](https://github.com/beaglabs/starlings). This repository contains the executable experiments that stress that protocol under faults, asynchronous transport, local inference control, heterogeneous operators, and application-level emergence.

The central question is simple:

> Can useful global workflows emerge from local observations, typed proposals, causal evidence, and neutral arbitration—without encoding the workflow in a central controller?

## Current capabilities

- **Operator-neutral coordination** — specialists interact through typed proposals rather than a global task graph.
- **Deterministic replay** — fixed seeds and canonical trace encodings make coordination paths reproducible.
- **Content-addressed provenance** — artifacts and causal evidence are pinned with BLAKE3 identities.
- **Runtime-enforced admissibility** — permissions, schemas, budgets, provenance, and STOP conditions are checked outside the agents.
- **Fault experimentation** — loss, duplication, latency, reordering, partition, crash/restart, stale state, and transport behavior.
- **Local inference control** — agents can gate expensive inference based on state rather than blind refresh cadence.
- **Heterogeneous populations** — deterministic and model-backed operators can share the same protocol boundary.
- **Emergent workflow trials** — specialist populations assemble valid application workflows without a centrally encoded action sequence.
- **Epistemic closure** — unsupported conclusions can remain explicitly blocked, unavailable, conflicting, or uncertain instead of being fabricated.

## The coordination loop

Starlings separates **what an operator can do** from **who should act next**.

```text
              local observations
                     │
       ┌─────────────┼─────────────┐
       ▼             ▼             ▼
  Specialist A  Specialist B  Specialist C
       │             │             │
       └────── typed proposals ────┘
                     │
                     ▼
            neutral arbitration
                     │
                     ▼
            protocol/runtime gate
              │             │
          accepted       rejected
              │
              ▼
       tools / state transition
              │
              ▼
       artifacts + provenance
              │
              └───────────────► new local observations
```

The population driver does not need to contain a hidden sequence such as “depth, then geometry, then view planning, then evaluation.” Specialists become eligible because the evidence they depend on exists.

## Research status

| Area | Evidence | Status |
| --- | --- | --- |
| Deterministic protocol substrate | Frozen Stage 5A / 7A / 7C reproduction | Validated |
| Fault behavior | F1 fault matrices and transport trials | Validated |
| Local inference control | Blind vs state-aware gating | Validated |
| Heterogeneous operators | Shared protocol across different operator types | Validated |
| Emergent workflow formation | EvoScene D3 specialist population | Validated |
| State-dependent tool activation | GEOINT + ShadowFinder trial | Validated |
| Explicit mathematical model | Formal system, invariants, theorem candidates | **Next** |
| Rogue / Byzantine operators | Falsification of formal robustness claims | Planned |

### Application-level emergence

The EvoScene D3 trial produced two successful trajectories from the same specialist population without a centrally encoded workflow:

| Run | Views | Tool calls | Mock wall time | Final quality |
| --- | ---: | ---: | ---: | ---: |
| Seed 0 | 4 | 16 | 282 ms | 834 |
| Seed 1 | 1 | 7 | 119 ms | 940 |

Both runs:

- involved all six specialist roles,
- terminated without deadlock,
- satisfied runtime accounting and provenance invariants,
- replayed deterministically for the same seed,
- and produced distinct semantic traces for different seeds.

This is evidence of **trajectory emergence**, not yet a claim of optimality.

### State-dependent tool emergence

The GEOINT trial uses 12 specialists and a 54-field evidence graph.

With missing acquisition time and shadow geometry, the population reaches epistemic closure while leaving ShadowFinder inactive. When those physical prerequisites are added in a matched context, the geolocation specialist independently becomes eligible to invoke ShadowFinder exactly once.

```text
same visual scene
      │
      ├── missing prerequisites
      │      └── ShadowFinder dormant
      │          candidate region = blocked
      │
      └── prerequisites available
             └── ShadowFinder active
                 candidate region = derived
```

The global operator trajectory changes because **local evidence changed**, not because a controller selected a different pipeline.

## Formal model

The protocol core already exposes an operator-neutral population abstraction:

```text
P = (A, G, X, M, F, Π, C, Φ, J)
```

where the implementation separates agents, communication topology, state, messages, transition behavior, policies, costs, aggregation/evaluation, and objectives.

The next research step is to make that abstraction explicit enough to support proofs and falsifiable predictions around:

- safety and admissibility,
- causal provenance,
- progress and deadlock freedom,
- convergence,
- state-dependent workflow emergence,
- communication cost,
- robustness to missing or faulty operators,
- and the conditions under which no individual agent needs to encode the final global workflow.

The formal specification will be developed alongside the empirical traces in this repository rather than inferred from a toy system after the fact.

## Requirements

For the deterministic experiment suite:

- Zig **0.16.0**
- Python 3.10+ for verification/adapters
- macOS or Linux

Some optional experiments have additional model or tool dependencies documented in their own directories.

## Quick start

Clone and run the full deterministic gate:

```bash
git clone https://github.com/beaglabs/starling-experiments.git
cd starling-experiments

zig version
zig build test
```

Run the two current emergence validators:

```bash
zig build -Doptimize=ReleaseFast \
  run-demo-evoscene-d3 -- validate

python tools/verify_geoint_emergent.py
```

Reproduce the frozen historical substrate:

```bash
python3 tools/verify_s0.py
```

## Experiment map

### Frozen substrate

```text
S0
└── reproduces frozen Stage 5A / 7A / 7C evidence
```

S0 fails closed unless the historical substrate, protocol package, regenerated data, and frozen hashes match.

### F-series

The F-series moves from deterministic substrate validation into more realistic coordination conditions:

```text
F1   faults / transport
F2   asynchronous execution and scaling
F3   local inference control
F4   heterogeneous operators
```

The exact experiment gate available in the current branch is always visible through:

```bash
zig build --help
```

### EvoScene emergence trial

[`demos/evoscene-emergent/`](demos/evoscene-emergent/) uses a 3D reconstruction workload to test whether specialists can assemble and terminate a workflow without a central schedule.

The important research boundary is D3:

```text
SpatialPriorAgent
GeometryAgent
ViewPlannerAgent
NovelViewAgent
FusionAgent
CriticAgent
```

Each role emits typed proposals from local state. A neutral BLAKE3-derived arbiter selects among simultaneous proposals.

See [D3 Emergent Population](demos/evoscene-emergent/D3_EMERGENT_POPULATION.md).

### GEOINT emergence trial

[`demos/geoint-emergent/`](demos/geoint-emergent/) tests the same coordination pattern over a very different workload: geometry, terrain, water, illumination, atmosphere, vegetation, built environment, motion, temporal evidence, materials, geolocation, and uncertainty.

The trial includes a pinned adapter for [Bellingcat ShadowFinder](https://github.com/bellingcat/ShadowFinder) and explicitly tests when that operator should **not** run.

See [GEOINT Emergent Operator Trial](demos/geoint-emergent/README.md).

## Verification

The full repository test surface is:

```bash
zig build test
```

Useful focused gates include:

```bash
# Frozen substrate
python3 tools/verify_s0.py

# Fault matrix
python3 tools/verify_f1a.py

# D3 emergent specialist population
zig build test-demo-evoscene-d3
zig build -Doptimize=ReleaseFast \
  run-demo-evoscene-d3 -- validate

# GEOINT operator emergence
python tools/verify_geoint_emergent.py
```

Generated trial outputs remain untracked unless they are intentionally frozen as canonical evidence. Scientific conclusions should point to reproducible commands, seeds, hashes, and trace digests.

## Repository layout

```text
src/
  substrate/                  frozen historical experiment substrate
  f1* / f3* / f4*            finalization experiment implementations

demos/
  evoscene-emergent/          emergent workflow / 3D reconstruction trial
  geoint-emergent/            emergent GEOINT evidence/operator trial

tools/
  verify_*.py                 fail-closed experiment gates
  evoscene_*.py               real reconstruction adapters
  geoint_*.py                 GEOINT / ShadowFinder adapters

trials/                       generated outputs; generally untracked
assets/                       project visual assets / logo placeholder
```

The protocol implementation and architecture decisions live in [`beaglabs/starlings`](https://github.com/beaglabs/starlings).

## Documentation

- [Protocol core](https://github.com/beaglabs/starlings) — operator-neutral protocol, provenance, grammar, and formal population substrate
- [EvoScene plan](demos/evoscene-emergent/PLAN.md) — reconstruction workload and emergence experiment structure
- [D3 emergent population](demos/evoscene-emergent/D3_EMERGENT_POPULATION.md) — decentralized workflow assembly and validation
- [GEOINT emergence trial](demos/geoint-emergent/README.md) — typed evidence graph, epistemic closure, and conditional ShadowFinder activation
- **Formal Starlings model** — next research artifact on this branch

## Scope

Starlings is a research system for studying coordination mechanisms. A passing experiment demonstrates the specific invariant or behavior named by that experiment; it does not imply that every emergent trajectory is optimal, truthful, safe under arbitrary adversaries, or suitable for autonomous deployment.

The upcoming formalization and Byzantine-agent work are intended to make those boundaries increasingly precise.

---

<div align="center">

**Starlings** · local rules, causal evidence, global coordination

</div>
