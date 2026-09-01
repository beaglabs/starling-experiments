# Starling Experiments

Finalization experiments for [beaglabs/starlings](https://github.com/beaglabs/starlings).

The `starlings` repository is the protocol core and documentation of record.
This repository contains executable experimental scaffolds and generated-trial
workflows for the finalization run. Canonical evidence is frozen back into
`beaglabs/starlings`; generated trial outputs remain untracked here.

## Finalization order

```text
S0 -> F1a -> F1b / F2 / F3 -> F4
```

The dependency and evidence gates are intentional: F1a does not begin until S0
has reproduced the frozen Stage 7C first-suite dataset byte-for-byte.

## S0

S0 re-materializes the historical Stage 5A, Stage 7A, and Stage 7C engines
without semantic changes. Their source is the parent of `176a0f9`:

```text
e943ea84a71bbf0189a54d0a703052b1515629cf
```

The protocol core is consumed directly as a public immutable HTTPS archive
pinned to:

```text
beaglabs/starlings
7c1152b82f540fafe072bcf64ef99904a05be044
starlings-0.1.0-3sAXWncEAgDdDV5wfBEssJhtUpW2Spm-u8gDHJOFPaKE
```

The archive URL embeds the exact commit SHA and Zig verifies the frozen package
content hash before building.

Run the complete fail-closed S0 gate:

```sh
python3 tools/verify_s0.py
```

That performs:

1. byte-identity checks on all frozen historical source blobs;
2. `zig build test` across the protocol package and frozen substrate;
3. Stage 5A validation;
4. Stage 7A named-control/corner validation;
5. Stage 7C validation;
6. Stage 7C first-suite regeneration in `ReleaseFast`;
7. exact SHA-256 comparison against:

```text
c89d1985af0479191126fca91265b1fe7f49e7b34db471e13c74e8bb28195a36
```

The generated `trials/s0-stage7c.tsv` remains ignored. S0 is not complete
unless the verifier prints `S0 PASS`.


## Agentic benchmark campaign

The first architecture-oriented benchmark suite runs SkillsBench 1.1, Harbor
Index 1.0, tau3-bench, and Frontier-Bench entirely on Daytona. One GPU Daytona
sandbox hosts the frozen model and Harbor controller; Harbor provisions task
sandboxes through Daytona, so no local Docker daemon is required.

See [Daytona agentic benchmark campaign](docs/DAYTONA_AGENTIC_BENCHMARKS.md).
