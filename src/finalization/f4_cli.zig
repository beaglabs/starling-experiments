const std = @import("std");
const runtime = @import("f4_runtime.zig");
const replay = @import("f4_replay.zig");

pub fn main(init: std.process.Init) !void {
    const io = init.io;
    const args = try init.minimal.args.toSlice(init.arena.allocator());

    if (args.len == 2 and std.mem.eql(u8, args[1], "validate")) {
        const out = std.Io.File.stdout();
        try writeLine(io, out, "F4 validation\n", .{});
        try writeLine(io, out, "workers: {d}\n", .{runtime.worker_count});
        try writeLine(io, out, "facts: {d}\n", .{runtime.fact_count});
        try writeLine(io, out, "bandwidth: {d}\n", .{runtime.bandwidth});
        try writeLine(io, out, "max_rounds: {d}\n", .{runtime.max_rounds});
        try writeLine(io, out, "deterministic_theta: theta51\n", .{});
        try writeLine(io, out, "mixed_model_workers: 2,3\n", .{});
        try writeLine(
            io,
            out,
            "mixed_essential_fact_seed0: {c}\n",
            .{'A' + @as(u8, @intCast(runtime.essentialFact(0)))},
        );
        try writeLine(
            io,
            out,
            "controllers: always_refresh,knowledge_or_stale\n",
            .{},
        );
        try writeLine(
            io,
            out,
            "decode_modes: typed_unconstrained,cfg_constrained\n",
            .{},
        );
        return;
    }

    if (args.len == 3 and std.mem.eql(u8, args[1], "replay")) {
        const allocator = init.gpa;
        const tsv = try std.Io.Dir.cwd().readFileAlloc(
            io,
            args[2],
            allocator,
            .limited(128 * 1024 * 1024),
        );
        defer allocator.free(tsv);

        const summary = replay.summarizeTsv(tsv);
        if (summary.malformed_records != 0 or
            summary.replay_errors != 0)
        {
            const err = std.Io.File.stderr();
            try writeLine(
                io,
                err,
                "F4 replay rejected: malformed={d} replay_errors={d}\n",
                .{ summary.malformed_records, summary.replay_errors },
            );
            std.process.exit(2);
        }

        try replay.writeSummaryTsv(
            io,
            std.Io.File.stdout(),
            &summary,
        );
        return;
    }

    try std.Io.File.stderr().writeStreamingAll(
        io,
        "usage:\n  run-f4 validate\n  run-f4 replay <raw.tsv>\n",
    );
    std.process.exit(2);
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
