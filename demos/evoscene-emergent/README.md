# Emergent 3D Scene Evolution

A real-world Starlings demonstration inspired by:

> **Self-Evolving 3D Scene Generation from a Single Image**  
> Kaizhi Zheng et al., arXiv:2512.08905 / CVPR 2026.

Paper: https://arxiv.org/abs/2512.08905  
Reference implementation: https://github.com/UCSB-AI/EvoScene

## Goal

Given one RGB image, construct a usable 3D scene through a population of specialist operators that decide **when**, **where**, and **whether** expensive 3D/vision tools should run.

The demo does **not** require the original EvoScene implementation or its H100 hardware envelope.

Instead it recreates the paper's core self-evolving structure with locally runnable substitutes:

~~~text
single image
    |
    v
spatial prior
    |
    v
geometry / scene state
    ^
    |              feedback
novel views <-> fusion / evaluation
    |
    +---- targeted refinement until convergence
~~~

The Starlings version removes the fixed global loop:

~~~text
specialist local state
        +
typed proposals / evidence
        +
deterministic validation
        +
cost-aware local decisions
        =
emergent reconstruction schedule
~~~

## Primary research question

> Can a heterogeneous population of specialist operators reach a target 3D reconstruction quality with fewer expensive operator invocations than the same operators executed under a fixed EvoScene-shaped schedule?

## What counts as the baseline

We deliberately do **not** compare local execution cost to the paper's H100 runtime.

The controlled baseline is:

~~~text
same local operator implementations
same input
same seeds
same quality evaluator
same machine

A: fixed paper-shaped schedule
B: emergent Starlings schedule
~~~

This isolates savings attributable to coordination.

## Operator population

- **Spatial Prior** — estimate depth/camera information and initialize spatial evidence.
- **Geometry** — maintain/refine the current 3D representation.
- **View Planner** — identify uncertain or poorly observed regions and propose camera poses.
- **Novel View** — synthesize or render candidate new observations.
- **Fusion** — fuse accepted observations into spatial state.
- **Critic** — measure consistency, coverage, and convergence.
- **Budget/Telemetry** — deterministic accounting only; it does not centrally schedule work.

Heavy numerical/model kernels remain tools. Agents decide whether to invoke them; they do not replace geometry algorithms with language-model arithmetic.

## Scientific output

For each scene:

~~~text
quality trajectory
operator invocation trace
wall-clock cost
per-operator cost
generated-view count
rejected proposals
communication units
termination cause
final artifact
~~~

The headline metric is quality-matched savings:

~~~text
Savings(Q*) =
  1 - cost_emergent(reaches Q*) / cost_fixed(reaches Q*)
~~~

See [PLAN.md](./PLAN.md) for implementation stages and [ARCHITECTURE.md](./ARCHITECTURE.md) for operator/message contracts.
