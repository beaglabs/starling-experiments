const std = @import("std");
const artifacts = @import("artifacts.zig");
const population = @import("d3_population.zig");

pub fn main(init: std.process.Init) !void {
    const io = init.io;
    const args = try init.minimal.args.toSlice(init.arena.allocator());

    if (args.len != 2 or !std.mem.eql(u8, args[1], "validate")) {
        try std.Io.File.stderr().writeStreamingAll(
            io,
            "usage: run-demo-evoscene-d3 validate\n",
        );
        std.process.exit(2);
    }

    const seed0 = try population.run(0, .{});
    const seed1 = try population.run(1, .{});

    var digest0: [64]u8 = undefined;
    var digest1: [64]u8 = undefined;
    hexDigest(seed0.semantic_digest, &digest0);
    hexDigest(seed1.semantic_digest, &digest1);

    const trajectory_distinct =
        !artifacts.eqlId(
            seed0.semantic_digest,
            seed1.semantic_digest,
        );

    const seed0_ok =
        seed0.runtime.terminated and
        !seed0.deadlocked and
        seed0.runtime.invariantsHold() and
        seed0.participating_roles == 6 and
        seed0.view_count >= 1;

    const seed1_ok =
        seed1.runtime.terminated and
        !seed1.deadlocked and
        seed1.runtime.invariantsHold() and
        seed1.participating_roles == 6 and
        seed1.view_count >= 1;

    const out = std.Io.File.stdout();
    try writeLine(
        io,
        out,
        "EvoScene-emergent D3 specialist population validation\n",
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

    try printRun(io, out, "seed0", seed0, &digest0);
    try printRun(io, out, "seed1", seed1, &digest1);

    try writeLine(
        io,
        out,
        "trajectory_distinct: {s}\n",
        .{if (trajectory_distinct) "yes" else "no"},
    );
    try writeLine(
        io,
        out,
        "all_roles_participate: {s}\n",
        .{if (seed0.participating_roles == 6 and
            seed1.participating_roles == 6)
            "yes"
        else
            "no"},
    );
    try writeLine(
        io,
        out,
        "runtime_invariants: {s}\n",
        .{if (seed0.runtime.invariantsHold() and
            seed1.runtime.invariantsHold())
            "PASS"
        else
            "FAIL"},
    );

    if (!seed0_ok or !seed1_ok or !trajectory_distinct) {
        try writeLine(
            io,
            out,
            "D3 FAIL: emergent specialist population gate failed\n",
            .{},
        );
        std.process.exit(2);
    }

    try writeLine(
        io,
        out,
        "D3 PASS: emergent specialist population complete\n",
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
    try writeLine(
        io,
        out,
        label ++ "_rounds: {d}\n",
        .{result.rounds},
    );
    try writeLine(
        io,
        out,
        label ++ "_views: {d}\n",
        .{result.view_count},
    );
    try writeLine(
        io,
        out,
        label ++ "_final_quality: {d}\n",
        .{result.final_quality},
    );
    try writeLine(
        io,
        out,
        label ++ "_roles: {d}\n",
        .{result.participating_roles},
    );
    try writeLine(
        io,
        out,
        label ++ "_proposals: {d}\n",
        .{result.runtime.accounting.proposed_actions},
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
        label ++ "_wall_time_ms: {d}\n",
        .{result.runtime.accounting.wall_time_ms},
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

fn hexDigest(
    digest: artifacts.ArtifactId,
    out: *[64]u8,
) void {
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
