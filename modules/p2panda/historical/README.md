# P2Panda module

Native Rust P2Panda adapter used by Stage 7C.

The module does not reimplement the Starlings policy. Its build script compiles `src/experiments/stage7/stage7c_policy_ffi.zig` and links the exact Zig policy into the Rust harness.

## Toolchains

- Zig: the repository's authoritative Zig toolchain
- Rust: pinned by `rust-toolchain.toml`
- P2Panda: pinned to the exact upstream v0.7.0 Git revision in `Cargo.toml`

## Run

```sh
cargo test
cargo run --release -- --profile theta51 --nodes 8 --facts 32 --topology ring --redundancy 2 --bandwidth 2 --seed 0
```

Use `run_suite.sh` for the small transfer suite.
