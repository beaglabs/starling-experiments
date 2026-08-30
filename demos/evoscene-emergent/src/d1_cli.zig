const std = @import("std");
const artifacts = @import("artifacts.zig");
const fixed = @import("fixed_reference.zig");

pub fn main(init: std.process.Init) !void {
    const io = init.io;
    const args = try init.minimal.args.toSlice(init.arena.allocator());

    if (args.len != 2 or
        !std.mem.eql(u8, args[1], "validate"))
    {
        try std.Io.File.stderr().writeStreamingAll(
            io,
            "usage: run-demo-evoscene-d1 validate\n",
        );
        std.process.exit(2);
    }

    const first = try fixed.runFixed(0);
    const second = try fixed.runFixed(0);
    const replayed = try fixed.replayFixed(
        first.runtime_state.trace[0..first.runtime_state.trace_len],
        0,
    );

    var first_buffer: [32 * 1024]u8 = undefined;
    var second_buffer: [32 * 1024]u8 = undefined;
    var replay_buffer: [32 * 1024]u8 = undefined;

    const first_bytes =
        try first.runtime_state.canonicalTrace(&first_buffer);
    const second_bytes =
        try second.runtime_state.canonicalTrace(&second_buffer);
    const replay_bytes =
        try replayed.canonicalTrace(&replay_buffer);

    const repeat_identical = std.mem.eql(
        u8,
        first_bytes,
        second_bytes,
    );
    const replay_identical = std.mem.eql(
        u8,
        first_bytes,
        replay_bytes,
    );

    const schedule_ok =
        fixed.traceMatchesFrozenSchedule(&first.runtime_state);
    const accounting_ok =
        fixed.exactAccountingHolds(&first.runtime_state);
    const invariants =
        first.runtime_state.invariantsHold() and
        replayed.invariantsHold();

    const trace_digest = artifacts.hashPayload(first_bytes);
    var trace_hex: [64]u8 = undefined;
    hexDigest(trace_digest, &trace_hex);

    var schedule_hex: [64]u8 = undefined;
    hexDigest(first.schedule_digest, &schedule_hex);

    var scene_hex: [64]u8 = undefined;
    hexDigest(first.final_scene, &scene_hex);

    const out = std.Io.File.stdout();

    try writeLine(io, out, "EvoScene-emergent D1 fixed reference validation\n", .{});
    try writeLine(io, out, "arm: fixed\n", .{});
    try writeLine(io, out, "schedule_version: {d}\n", .{fixed.fixed_schedule_version});
    try writeLine(io, out, "schedule_blake3: {s}\n", .{&schedule_hex});
    try writeLine(io, out, "fixed_iterations: {d}\n", .{fixed.fixed_poses.len});
    try writeLine(io, out, "trace_events: {d}\n", .{first.runtime_state.trace_len});
    try writeLine(io, out, "trace_bytes: {d}\n", .{first_bytes.len});
    try writeLine(io, out, "trace_blake3: {s}\n", .{&trace_hex});
    try writeLine(
        io,
        out,
        "byte_identical_repeat: {s}\n",
        .{if (repeat_identical) "yes" else "no"},
    );
    try writeLine(
        io,
        out,
        "byte_identical_replay: {s}\n",
        .{if (replay_identical) "yes" else "no"},
    );
    try writeLine(
        io,
        out,
        "schedule_match: {s}\n",
        .{if (schedule_ok) "yes" else "no"},
    );
    try writeLine(
        io,
        out,
        "proposed_actions: {d}\n",
        .{first.runtime_state.accounting.proposed_actions},
    );
    try writeLine(
        io,
        out,
        "accepted_actions: {d}\n",
        .{first.runtime_state.accounting.accepted_actions},
    );
    try writeLine(
        io,
        out,
        "rejected_actions: {d}\n",
        .{first.runtime_state.accounting.rejected_actions},
    );
    try writeLine(
        io,
        out,
        "tool_invocations: {d}\n",
        .{first.runtime_state.accounting.tool_invocations},
    );
    try writeLine(
        io,
        out,
        "control_actions: {d}\n",
        .{first.runtime_state.accounting.accepted_control_actions},
    );
    try writeLine(
        io,
        out,
        "produced_artifacts: {d}\n",
        .{first.runtime_state.accounting.produced_artifacts},
    );
    try writeLine(
        io,
        out,
        "artifact_store_len: {d}\n",
        .{first.runtime_state.artifacts_store.len},
    );
    try writeLine(
        io,
        out,
        "mock_wall_time_ms: {d}\n",
        .{first.runtime_state.accounting.wall_time_ms},
    );
    try writeLine(
        io,
        out,
        "communication_units: {d}\n",
        .{first.runtime_state.accounting.communication_units},
    );
    try writeLine(io, out, "quality_score: {d}\n", .{first.quality()});
    try writeLine(io, out, "final_scene: {s}\n", .{&scene_hex});
    try writeLine(
        io,
        out,
        "terminated: {s}\n",
        .{if (first.runtime_state.terminated) "yes" else "no"},
    );
    try writeLine(
        io,
        out,
        "accounting_exact: {s}\n",
        .{if (accounting_ok) "PASS" else "FAIL"},
    );
    try writeLine(
        io,
        out,
        "invariants: {s}\n",
        .{if (invariants) "PASS" else "FAIL"},
    );

    if (!repeat_identical or
        !replay_identical or
        !schedule_ok or
        !accounting_ok or
        !invariants or
        !first.runtime_state.terminated)
    {
        try writeLine(
            io,
            out,
            "D1 FAIL: fixed reference gate failed\n",
            .{},
        );
        std.process.exit(2);
    }

    try writeLine(
        io,
        out,
        "D1 PASS: frozen paper-shaped fixed reference complete\n",
        .{},
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
