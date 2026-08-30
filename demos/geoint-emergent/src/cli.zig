const std = @import("std");
const schema = @import("schema.zig");
const context_mod = @import("context.zig");
const population = @import("population.zig");

pub fn main(init: std.process.Init) !void {
    const io = init.io;
    const args = try init.minimal.args.toSlice(init.arena.allocator());

    if (args.len != 2 or !std.mem.eql(u8, args[1], "validate")) {
        try std.Io.File.stderr().writeStreamingAll(
            io,
            "usage: run-demo-geoint-emergent validate\n",
        );
        std.process.exit(2);
    }

    const base0 = try population.run(
        context_mod.AcquisitionContext.photoNoDatetime(),
        0,
    );
    const base1 = try population.run(
        context_mod.AcquisitionContext.photoNoDatetime(),
        1,
    );
    const shadow0 = try population.run(
        context_mod.AcquisitionContext.photoShadowReady(),
        0,
    );

    var base0_hex: [64]u8 = undefined;
    var base1_hex: [64]u8 = undefined;
    var shadow0_hex: [64]u8 = undefined;
    hexDigest(base0.semantic_digest, &base0_hex);
    hexDigest(base1.semantic_digest, &base1_hex);
    hexDigest(shadow0.semantic_digest, &shadow0_hex);

    const seed_distinct = !std.mem.eql(
        u8,
        &base0.semantic_digest,
        &base1.semantic_digest,
    );
    const context_distinct = !std.mem.eql(
        u8,
        &base0.semantic_digest,
        &shadow0.semantic_digest,
    );

    const base_ok =
        base0.runtime.terminated and
        !base0.deadlocked and
        base0.participating_roles == 12 and
        base0.shadowfinder_calls == 0 and
        base0.resolved_fields == @as(u8, @intCast(schema.field_count)) and
        base0.runtime.facts.status(.candidate_region) == .blocked and
        base0.runtime.invariantsHold();

    const shadow_ok =
        shadow0.runtime.terminated and
        !shadow0.deadlocked and
        shadow0.participating_roles == 12 and
        shadow0.shadowfinder_calls == 1 and
        shadow0.resolved_fields == @as(u8, @intCast(schema.field_count)) and
        shadow0.runtime.facts.status(.candidate_region) == .derived and
        shadow0.runtime.invariantsHold();

    const epistemic_guard =
        base0.runtime.facts.status(.solar_angle) == .blocked and
        base0.runtime.facts.status(.movement_vectors) == .unavailable and
        base0.runtime.facts.status(.new_objects) == .unavailable and
        base0.runtime.facts.status(.water_level) == .unavailable;

    const out = std.Io.File.stdout();
    try writeLine(
        io,
        out,
        "Starlings GEOINT operator-population validation\n",
        .{},
    );
    try writeLine(
        io,
        out,
        "population_version: {d}\n",
        .{population.population_version},
    );
    try writeLine(
        io,
        out,
        "arbitration_rule: {s}\n",
        .{population.arbitration_rule},
    );

    try printRun(io, out, "base_seed0", base0, &base0_hex);
    try printRun(io, out, "base_seed1", base1, &base1_hex);
    try printRun(io, out, "shadow_seed0", shadow0, &shadow0_hex);

    try writeLine(
        io,
        out,
        "seed_trajectory_distinct: {s}\n",
        .{if (seed_distinct) "yes" else "no"},
    );
    try writeLine(
        io,
        out,
        "context_trajectory_distinct: {s}\n",
        .{if (context_distinct) "yes" else "no"},
    );
    try writeLine(
        io,
        out,
        "shadowfinder_state_dependent: {s}\n",
        .{if (base0.shadowfinder_calls == 0 and
            shadow0.shadowfinder_calls == 1)
            "yes"
        else
            "no"},
    );
    try writeLine(
        io,
        out,
        "epistemic_guard: {s}\n",
        .{if (epistemic_guard) "PASS" else "FAIL"},
    );
    try writeLine(
        io,
        out,
        "runtime_invariants: {s}\n",
        .{if (base0.runtime.invariantsHold() and
            base1.runtime.invariantsHold() and
            shadow0.runtime.invariantsHold())
            "PASS"
        else
            "FAIL"},
    );

    if (!base_ok or !shadow_ok or !seed_distinct or
        !context_distinct or !epistemic_guard)
    {
        try writeLine(
            io,
            out,
            "GEOINT FAIL: operator-population emergence gate failed\n",
            .{},
        );
        std.process.exit(2);
    }

    try writeLine(
        io,
        out,
        "GEOINT PASS: state-dependent operator emergence complete\n",
        .{},
    );
}

fn printRun(
    io: std.Io,
    out: std.Io.File,
    comptime label: []const u8,
    result: population.Result,
    digest: *const [64]u8,
) !void {
    try writeLine(io, out, label ++ "_rounds: {d}\n", .{result.rounds});
    try writeLine(
        io,
        out,
        label ++ "_roles: {d}\n",
        .{result.participating_roles},
    );
    try writeLine(
        io,
        out,
        label ++ "_resolved_fields: {d}\n",
        .{result.resolved_fields},
    );
    try writeLine(
        io,
        out,
        label ++ "_shadowfinder_calls: {d}\n",
        .{result.shadowfinder_calls},
    );
    try writeLine(
        io,
        out,
        label ++ "_tool_invocations: {d}\n",
        .{result.runtime.accounting.tool_invocations},
    );
    try writeLine(
        io,
        out,
        label ++ "_fields_written: {d}\n",
        .{result.runtime.accounting.fields_written},
    );
    try writeLine(
        io,
        out,
        label ++ "_communication_units: {d}\n",
        .{result.runtime.accounting.communication_units},
    );
    try writeLine(
        io,
        out,
        label ++ "_candidate_region: {s}\n",
        .{@tagName(result.runtime.facts.status(.candidate_region))},
    );
    try writeLine(
        io,
        out,
        label ++ "_terminated: {s}\n",
        .{if (result.runtime.terminated) "yes" else "no"},
    );
    try writeLine(
        io,
        out,
        label ++ "_deadlocked: {s}\n",
        .{if (result.deadlocked) "yes" else "no"},
    );
    try writeLine(
        io,
        out,
        label ++ "_semantic_blake3: {s}\n",
        .{digest},
    );
}

fn hexDigest(digest: [32]u8, out: *[64]u8) void {
    const alphabet = "0123456789abcdef";
    var i: usize = 0;
    while (i < digest.len) : (i += 1) {
        out[i * 2] = alphabet[digest[i] >> 4];
        out[i * 2 + 1] = alphabet[digest[i] & 0x0f];
    }
}

fn writeLine(
    io: std.Io,
    out: std.Io.File,
    comptime format: []const u8,
    args: anytype,
) !void {
    var buffer: [4096]u8 = undefined;
    const line = try std.fmt.bufPrint(&buffer, format, args);
    try out.writeStreamingAll(io, line);
}
