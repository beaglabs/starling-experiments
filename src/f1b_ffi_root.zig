pub const ffi = @import("finalization/f1b_policy_ffi.zig");

comptime {
    // Zig 0.16 lazily analyzes imported declarations. Retain the native ABI
    // exports when this root is compiled as an object for the Rust/P2Panda
    // adapter, matching the historical Stage 7C package-root pattern.
    _ = ffi.starlings_stage7c_abi_version;
    _ = ffi.starlings_stage7c_init_state;
    _ = ffi.starlings_stage7c_decide;
    _ = ffi.starlings_stage7c_simulate;
}

test {
    _ = ffi;
}
