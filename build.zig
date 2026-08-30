const std = @import("std");

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{});
    const optimize = b.standardOptimizeOption(.{});
    const needs_libc = switch (target.result.os.tag) {
        .macos, .ios, .tvos, .watchos, .visionos, .driverkit => true,
        else => false,
    };

    const starlings = b.dependency("starlings", .{
        .target = target,
        .optimize = optimize,
    });
    const zquic = b.dependency("zquic", .{
        .target = target,
        .optimize = optimize,
    }).module("zquic");

    const core_tests = b.addTest(.{
        .root_module = starlings.module("starlings"),
    });
    const run_core_tests = b.addRunArtifact(core_tests);

    const substrate_tests = b.addTest(.{
        .root_module = b.createModule(.{
            .root_source_file = b.path("src/substrate/root.zig"),
            .target = target,
            .optimize = optimize,
        }),
    });
    const run_substrate_tests = b.addRunArtifact(substrate_tests);

    const f1a_tests = b.addTest(.{
        .root_module = b.createModule(.{
            .root_source_file = b.path("src/f1a_test_root.zig"),
            .target = target,
            .optimize = optimize,
        }),
    });
    const run_f1a_tests = b.addRunArtifact(f1a_tests);

    const f1c_test_module = b.createModule(.{
        .root_source_file = b.path("src/f1c_test_root.zig"),
        .target = target,
        .optimize = optimize,
        .link_libc = needs_libc,
    });
    f1c_test_module.addImport("zquic", zquic);
    const f1c_tests = b.addTest(.{
        .root_module = f1c_test_module,
    });
    const run_f1c_tests = b.addRunArtifact(f1c_tests);

    const f3_tests = b.addTest(.{
        .root_module = b.createModule(.{
            .root_source_file = b.path("src/f3_test_root.zig"),
            .target = target,
            .optimize = optimize,
        }),
    });
    const run_f3_tests = b.addRunArtifact(f3_tests);

    const f3b_tests = b.addTest(.{
        .root_module = b.createModule(.{
            .root_source_file = b.path("src/f3b_test_root.zig"),
            .target = target,
            .optimize = optimize,
        }),
    });
    const run_f3b_tests = b.addRunArtifact(f3b_tests);

    const f4_tests = b.addTest(.{
        .root_module = b.createModule(.{
            .root_source_file = b.path("src/f4_test_root.zig"),
            .target = target,
            .optimize = optimize,
        }),
    });
    const run_f4_tests = b.addRunArtifact(f4_tests);

    const test_step = b.step(
        "test",
        "Run protocol-core, frozen-substrate, and finalization tests",
    );
    test_step.dependOn(&run_core_tests.step);
    test_step.dependOn(&run_substrate_tests.step);
    test_step.dependOn(&run_f1a_tests.step);
    test_step.dependOn(&run_f1c_tests.step);
    test_step.dependOn(&run_f3_tests.step);
    test_step.dependOn(&run_f3b_tests.step);
    test_step.dependOn(&run_f4_tests.step);

    addRunStep(b, target, optimize, "run-stage5a", "Run frozen Stage 5A CLI", "src/substrate/stage5a_run.zig");
    addRunStep(b, target, optimize, "run-stage7a", "Run frozen Stage 7A CLI", "src/substrate/stage7a_run.zig");
    addRunStep(b, target, optimize, "run-stage7c", "Run frozen Stage 7C CLI", "src/substrate/stage7c_run.zig");
    addRunStep(b, target, optimize, "run-f1a", "Run F1a canonical fault matrix", "src/f1a_run.zig");
    addRunStep(b, target, optimize, "run-f3", "Run F3a blind inference-gating experiment", "src/f3_run.zig");
    addRunStep(b, target, optimize, "run-f3b", "Run F3b state-aware inference-control experiment", "src/f3b_run.zig");
    addRunStep(b, target, optimize, "run-f4", "Run F4 heterogeneous operator validation/replay", "src/f4_run.zig");

    const f1c_run_module = b.createModule(.{
        .root_source_file = b.path("src/f1c_run.zig"),
        .target = target,
        .optimize = optimize,
        .link_libc = needs_libc,
    });
    f1c_run_module.addImport("zquic", zquic);
    const f1c_exe = b.addExecutable(.{
        .name = "run-f1c",
        .root_module = f1c_run_module,
    });
    b.installArtifact(f1c_exe);
    const f1c_run = b.addRunArtifact(f1c_exe);
    if (b.args) |args| f1c_run.addArgs(args);
    const f1c_step = b.step("run-f1c", "Run F1c zquic transport candidate");
    f1c_step.dependOn(&f1c_run.step);
}

fn addRunStep(
    b: *std.Build,
    target: std.Build.ResolvedTarget,
    optimize: std.builtin.OptimizeMode,
    step_name: []const u8,
    description: []const u8,
    source: []const u8,
) void {
    const exe = b.addExecutable(.{
        .name = step_name,
        .root_module = b.createModule(.{
            .root_source_file = b.path(source),
            .target = target,
            .optimize = optimize,
        }),
    });
    const run = b.addRunArtifact(exe);
    if (b.args) |args| run.addArgs(args);

    const step = b.step(step_name, description);
    step.dependOn(&run.step);
}
