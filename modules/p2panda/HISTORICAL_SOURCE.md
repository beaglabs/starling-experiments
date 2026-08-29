# F1b historical P2Panda provenance

F1b re-evaluates the rejected 2026-08-28 P2Panda transport candidate with the
deterministic accounting discipline established by F1a.

The historical source is re-materialized from the exact parent of the removal
commit `39cf3e9`:

```text
source commit:
b7fd4571f7e4d770542f7d9b69b89d00d353099b
```

The following files under `modules/p2panda/historical/` are byte-identical
Git blobs from that source commit:

| Historical path | Source blob |
| --- | --- |
| `.gitignore` | `b83d22266ac8aa2f8df2edef68082c789727841d` |
| `Cargo.toml` | `00e8d31c1cb9fe5f3ef978c810f7acfeb458ef80` |
| `README.md` | `93f9f43219adb337116d64d85cdfe7254c86e96e` |
| `build.rs` | `ea32b248f1c829c109c84c8ce660b66aa03099cb` |
| `run_suite.sh` | `bab3b06407037fc0cc8c94cbfc65444eaabc17f6` |
| `rust-toolchain.toml` | `d1ad785747eb8330d0e6226ee0d781d82336aca0` |
| `src/main.rs` | `bb441b0043890b0355ef0be5df0f3ddba370b470` |
| `stage7c_policy_ffi.zig` | `6afd110ad2cd410854ac56f49d51bbe27501dcf6` |

Historical dependency:

```text
Rust edition: 2024
Rust: 1.98.0
P2Panda fork: https://github.com/beaglabs/p2panda
revision: 80051611b7b41250815a40c945ae7bece84aa249
upstream basis: v0.7.0
```

## F1b delta rule

The active F1b adapter must be implemented outside `historical/`. The
historical snapshot is immutable evidence and is never edited.

The active adapter must add:

- complete logical attempt accounting at the P2Panda boundary;
- collector-directed per-fact attempt attribution;
- explicit receive/duplicate/fault accounting;
- deterministic partition and crash/restart contested worlds;
- fixed-seed K-rerun determinism audit;
- explicit PASS/LIMITATION outcome.

Any collector-missing fact that was attempted toward the collector but cannot
be attributed to an injected fault or crash remains `unattributed` and causes
F1b to record LIMITATION rather than silently treating the run as successful.
