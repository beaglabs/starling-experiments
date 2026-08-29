use std::env;
use std::path::PathBuf;
use std::process::Command;

fn main() {
    let manifest_dir =
        PathBuf::from(env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR"));
    let repo_root = manifest_dir
        .join("../..")
        .canonicalize()
        .expect("resolve starling-experiments root");
    let zig_source = repo_root.join("src/f1b_ffi_root.zig");
    let out_dir = PathBuf::from(env::var("OUT_DIR").expect("OUT_DIR"));
    let output = out_dir.join("starlings_f1b.o");
    let zig = env::var("ZIG").unwrap_or_else(|_| "zig".to_string());

    println!("cargo:rerun-if-changed={}", zig_source.display());
    println!(
        "cargo:rerun-if-changed={}",
        repo_root.join("src/finalization/f1b_policy_ffi.zig").display()
    );
    println!(
        "cargo:rerun-if-changed={}",
        repo_root.join("src/substrate/stage7/stage7a_policy.zig").display()
    );
    println!(
        "cargo:rerun-if-changed={}",
        repo_root.join("src/substrate/stage5/stage5a_scaling.zig").display()
    );

    let status = Command::new(&zig)
        .arg("build-obj")
        .arg(&zig_source)
        .arg("-O")
        .arg("ReleaseFast")
        .arg("-fPIC")
        .arg(format!("-femit-bin={}", output.display()))
        .status()
        .unwrap_or_else(|err| panic!("failed to invoke {zig}: {err}"));

    if !status.success() {
        panic!("Zig F1b policy library build failed: {status}");
    }

    println!("cargo:rustc-link-arg={}", output.display());
}
