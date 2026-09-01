# Daytona agentic benchmark campaign

This repository owns the executable benchmark scaffolding for Starlings. The
protocol/core remains in `beaglabs/starlings`.

## First suite

The launcher pins the initial architecture-oriented suite to:

| key | Harbor dataset |
| --- | --- |
| `skillsbench` | `benchflow/skillsbench` |
| `harbor-index` | `harbor-index/harbor-index-1.0` |
| `tau3` | `sierra-research/tau3-bench` |
| `frontier-bench` | `frontier-bench/frontier-bench` |

Daytona is used twice:

1. one GPU sandbox hosts the frozen model, vLLM, Harbor, and the external agent;
2. Harbor provisions each benchmark task as a separate Daytona sandbox.

No local Docker daemon is required.

## Local prerequisite

Install the Daytona Python SDK and export an API key:

```sh
python3 -m pip install -U daytona
export DAYTONA_API_KEY='...'
```

`HF_TOKEN` is optional for gated Hugging Face models.

## Smoke all four environments

This provisions a Daytona controller, runs one oracle task from each dataset,
downloads all Harbor jobs, then deletes the controller:

```sh
python3 tools/daytona_agentic_bench.py \
  --conditions oracle \
  --n-tasks 1 \
  --concurrency 4
```

Results are downloaded as:

```text
starlings-agentic-results.tar.gz
```

The archive contains Harbor job directories plus a `runs/manifest.json`.

## Condition A: frozen-model conventional baseline

```sh
python3 tools/daytona_agentic_bench.py \
  --conditions a \
  --n-tasks 10 \
  --concurrency 10 \
  --keep-controller
```

Condition A defaults to:

```text
model: Qwen/Qwen2.5-Coder-1.5B-Instruct
served as: starlings-frozen-1.5b
agent: Terminus-2
temperature: 0
max turns: 50
```

The GPU preference order is:

```text
RTX 5090 -> RTX Pro 6000 -> H100
```

Override it with `--gpu-types`.

## Reuse the GPU controller

The launcher prints `controller_id=...`. Reuse it without paying model startup
again:

```sh
python3 tools/daytona_agentic_bench.py \
  --controller-id '<id>' \
  --skip-setup \
  --conditions a \
  --n-tasks 82 \
  --concurrency 32 \
  --keep-controller
```

If `--keep-controller` is omitted, the launcher deletes the GPU sandbox after
downloading the result archive.

## Conditions B and C

B and C intentionally require explicit Harbor agent import paths so the launcher
cannot silently substitute another scaffold for Starlings:

```sh
python3 tools/daytona_agentic_bench.py \
  --conditions b,c \
  --agent-b benchmarks.harbor_agents:StarlingsAgent \
  --agent-c benchmarks.harbor_agents:DeterministicStarlingsAgent \
  --n-tasks 10 \
  --concurrency 10
```

- B must use the exact same frozen model endpoint and model name as A.
- C receives no `-m` model argument at all.
- The custom agent layer is the only intended architectural difference between
  A and B once the matched baseline adapter replaces Terminus-2 for publication
  runs.

## Dry-run command validation

No Daytona resources are created:

```sh
python3 tools/daytona_agentic_bench.py \
  --conditions oracle,a \
  --n-tasks 10 \
  --dry-run
```

Run the command-generation tests with:

```sh
python3 tools/test_daytona_agentic_bench.py
```

## Full first-suite counts

After smoke and matched-task A/B/C runs have passed, omit `--n-tasks` to run
the complete registered dataset. The current suite sizes are:

```text
SkillsBench 1.1        87
Harbor Index 1.0       82
tau3-bench            375
Frontier-Bench         74
```

The launcher deliberately keeps outputs separated by condition and benchmark so
the resulting Harbor jobs can be compared without mixing trials.
