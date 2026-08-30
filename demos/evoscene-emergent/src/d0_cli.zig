const std = @import("std");
const artifacts = @import("artifacts.zig");
const runtime = @import("runtime.zig");

pub fn main(init: std.process.Init) !void {
    const io = init.io;
    const args = try init.minimal.args.toSlice(init.arena.allocator());

    if (args.len != 2 or
        !std.mem.eql(u8, args[1], "validate"))
    {
        try std.Io.File.stderr().writeStreamingAll(
            io,
            "usage: run-demo-evoscene-d0 validate\n",
        );
        std.process.exit(2);
    }

    const first = try runtime.runFixture(0);
    const replayed = try runtime.replayFixture(
        first.trace[0..first.trace_len],
        0,
    );

    var first_buffer: [32 * 1024]u8 = undefined;
    var replay_buffer: [32 * 1024]u8 = undefined;

    const first_bytes = try first.canonicalTrace(&first_buffer);
    const replay_bytes =
        try replayed.canonicalTrace(&replay_buffer);

    const byte_identical = std.mem.eql(
        u8,
        first_bytes,
        replay_bytes,
    );
    const invariants = first.invariantsHold() and
        replayed.invariantsHold();

    const digest = artifacts.hashPayload(first_bytes);
    var digest_hex: [64]u8 = undefined;
    hexDigest(digest, &digest_hex);

    const out = std.Io.File.stdout();
    try writeLine(io, out, "EvoScene-emergent D0 validation\n", .{});
    try writeLine(
        io,
        out,
        "trace_events: {d}\n",
        .{first.trace_len},
    );
    try writeLine(
        io,
        out,
        "trace_bytes: {d}\n",
        .{first_bytes.len},
    );
    try writeLine(
        io,
        out,
        "trace_sha256: {s}\n",
        .{&digest_hex},
    );
    try writeLine(
        io,
        out,
        "byte_identical_replay: {s}\n",
        .{if (byte_identical) "yes" else "no"},
    );
    try writeLine(
        io,
        out,
        "proposed_actions: {d}\n",
        .{first.accounting.proposed_actions},
    );
    try writeLine(
        io,
        out,
        "accepted_actions: {d}\n",
        .{first.accounting.accepted_actions},
    );
    try writeLine(
        io,
        out,
        "rejected_actions: {d}\n",
        .{first.accounting.rejected_actions},
    );
    try writeLine(
        io,
        out,
        "tool_invocations: {d}\n",
        .{first.accounting.tool_invocations},
    );
    try writeLine(
        io,
        out,
        "control_actions: {d}\n",
        .{first.accounting.accepted_control_actions},
    );
    try writeLine(
        io,
        out,
        "produced_artifacts: {d}\n",
        .{first.accounting.produced_artifacts},
    );
    try writeLine(
        io,
        out,
        "artifact_store_len: {d}\n",
        .{first.artifacts_store.len},
    );
    try writeLine(
        io,
        out,
        "mock_wall_time_ms: {d}\n",
        .{first.accounting.wall_time_ms},
    );
    try writeLine(
        io,
        out,
        "communication_units: {d}\n",
        .{first.accounting.communication_units},
    );
    try writeLine(
        io,
        out,
        "terminated: {s}\n",
        .{if (first.terminated) "yes" else "no"},
    );
    try writeLine(
        io,
        out,
        "invariants: {s}\n",
        .{if (invariants) "PASS" else "FAIL"},
    );

    if (!byte_identical or !invariants or !first.terminated) {
        try writeLine(
            io,
            out,
            "D0 FAIL: deterministic runtime contract gate failed\n",
            .{},
        );
        std.process.exit(2);
    }

    try writeLine(
        io,
        out,
        "D0 PASS: deterministic demo runtime contracts complete\n",
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
