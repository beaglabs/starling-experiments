const std = @import("std");

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{});
    const optimize = b.standardOptimizeOption(.{});

    const starlings = b.dependency("starlings", .{
        .target = target,
        .optimize = optimize,
    });

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

    const test_step = b.step(
        "test",
        "Run protocol-core, frozen-substrate, and finalization tests",
    );
    test_step.dependOn(&run_core_tests.step);
    test_step.dependOn(&run_substrate_tests.step);
    test_step.dependOn(&run_f1a_tests.step);

    addRunStep(b, target, optimize, "run-stage5a", "Run frozen Stage 5A CLI", "src/substrate/stage5a_run.zig");
    addRunStep(b, target, optimize, "run-stage7a", "Run frozen Stage 7A CLI", "src/substrate/stage7a_run.zig");
    addRunStep(b, target, optimize, "run-stage7c", "Run frozen Stage 7C CLI", "src/substrate/stage7c_run.zig");
    addRunStep(b, target, optimize, "run-f1a", "Run F1a canonical fault matrix", "src/f1a_run.zig");
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
