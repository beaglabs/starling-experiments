const std = @import("std");
const transfer = @import("stage7c_async_transfer.zig");
const scaling = @import("../stage5/stage5a_scaling.zig");

pub fn main(init: std.process.Init) !void {
    const io = init.io;
    const args = try init.minimal.args.toSlice(init.arena.allocator());

    if (args.len == 2 and std.mem.eql(u8, args[1], "validate")) {
        try validate(io);
        return;
    }
    if (args.len == 2 and std.mem.eql(u8, args[1], "suite")) {
        try suite(io);
        return;
    }
    if (args.len == 10 and std.mem.eql(u8, args[1], "run")) {
        try runOne(io, args[2..]);
        return;
    }

    try usage(io);
    std.process.exit(2);
}

fn usage(io: std.Io) !void {
    try std.Io.File.stderr().writeStreamingAll(
        io,
        "usage:\n" ++
            "  stage7c_cli validate\n" ++
            "  stage7c_cli suite\n" ++
            "  stage7c_cli run <profile> <nodes> <facts> <topology> " ++
            "<redundancy> <bandwidth> <world-seed> <schedule-seed>\n",
    );
}

fn validate(io: std.Io) !void {
    const out = std.Io.File.stdout();
    var failures: usize = 0;
    for (transfer.frozen_profiles) |profile| {
        const result = try transfer.run(smokeConfig(0), profile.theta);
        if (!result.success or !result.accounted() or result.violations != 0) {
            failures += 1;
        }
        try writeLine(
            io,
            out,
            "{s}: success={s} collector={d}/32 ticks={d} accounted={s} violations={d}\n",
            .{
                profile.name,
                yesNo(result.success),
                result.collector_final_facts,
                result.elapsed_ticks,
                yesNo(result.accounted()),
                result.violations,
            },
        );
    }
    if (failures != 0) std.process.exit(1);
}

fn suite(io: std.Io) !void {
    const out = std.Io.File.stdout();
    try out.writeStreamingAll(
        io,
        "profile\ttopology\tworld_seed\tschedule_seed\tsuccess\tticks\t" ++
            "collector_initial\tcollector_final\tpolicy_ticks\tactions\t" ++
            "transport_attempts\tdelivered\tdropped\tpartitioned\tcrashed\t" ++
            "queue_overflow\tpending\tduplicate_copies\treordered\t" ++
            "communication_units\tuseful\tduplicate\tschedule_hash\t" ++
            "trace_hash\tviolations\n",
    );

    const topologies = [_]scaling.TopologyKind{ .ring, .grid };
    for (transfer.frozen_profiles) |profile| {
        for (topologies) |topology| {
            var seed: u64 = 0;
            while (seed < 3) : (seed += 1) {
                var config = smokeConfig(seed);
                config.world.topology = topology;
                config.world.seed = seed;
                const result = try transfer.run(config, profile.theta);
                try writeResult(io, out, profile.name, result);
            }
        }
    }
}

fn runOne(io: std.Io, args: []const []const u8) !void {
    const profile = profileByName(args[0]) orelse {
        try usage(io);
        std.process.exit(2);
    };
    const topology = topologyByName(args[3]) orelse {
        try usage(io);
        std.process.exit(2);
    };
    const world_seed = try std.fmt.parseInt(u64, args[6], 10);
    const schedule_seed = try std.fmt.parseInt(u64, args[7], 10);
    const config = transfer.Config{
        .world = .{
            .population_size = try std.fmt.parseInt(usize, args[1], 10),
            .fact_count = try std.fmt.parseInt(usize, args[2], 10),
            .topology = topology,
            .redundancy = try std.fmt.parseInt(usize, args[4], 10),
            .bandwidth = try std.fmt.parseInt(usize, args[5], 10),
            .seed = world_seed,
            .max_rounds = 4096,
        },
        .schedule_seed = schedule_seed,
        // Match the frozen suite so `run` reproduces suite worlds exactly.
        .latency_jitter = 4,
    };
    const result = try transfer.run(config, profile.theta);
    const out = std.Io.File.stdout();
    try out.writeStreamingAll(
        io,
        "profile\ttopology\tworld_seed\tschedule_seed\tsuccess\tticks\t" ++
            "collector_initial\tcollector_final\tpolicy_ticks\tactions\t" ++
            "transport_attempts\tdelivered\tdropped\tpartitioned\tcrashed\t" ++
            "queue_overflow\tpending\tduplicate_copies\treordered\t" ++
            "communication_units\tuseful\tduplicate\tschedule_hash\t" ++
            "trace_hash\tviolations\n",
    );
    try writeResult(io, out, profile.name, result);
    if (!result.success) std.process.exit(1);
}

fn smokeConfig(seed: u64) transfer.Config {
    return .{
        .world = .{
            .population_size = 8,
            .fact_count = 32,
            .topology = .ring,
            .redundancy = 2,
            .bandwidth = 2,
            .seed = seed,
            .max_rounds = 4096,
        },
        .schedule_seed = seed,
        .max_ticks = 4096,
        .clock_jitter = 3,
        .latency_min = 1,
        .latency_jitter = 4,
    };
}

fn profileByName(name: []const u8) ?transfer.Profile {
    for (transfer.frozen_profiles) |profile| {
        if (std.mem.eql(u8, name, profile.name)) return profile;
    }
    return null;
}

fn topologyByName(name: []const u8) ?scaling.TopologyKind {
    if (std.mem.eql(u8, name, "ring")) return .ring;
    if (std.mem.eql(u8, name, "grid")) return .grid;
    if (std.mem.eql(u8, name, "complete")) return .complete;
    return null;
}

fn writeResult(
    io: std.Io,
    out: std.Io.File,
    profile: []const u8,
    result: transfer.Result,
) !void {
    try writeLine(
        io,
        out,
        "{s}\t{s}\t{d}\t{d}\t{s}\t{d}\t{d}\t{d}\t{d}\t{d}\t" ++
            "{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t" ++
            "{d}\t{d}\t{d}\t{x}\t{x}\t{d}\n",
        .{
            profile,
            result.config.world.topology.name(),
            result.config.world.seed,
            result.config.schedule_seed,
            yesNo(result.success),
            result.elapsed_ticks,
            result.collector_initial_facts,
            result.collector_final_facts,
            result.local_policy_ticks,
            result.actions,
            result.transport_attempts,
            result.delivered_envelopes,
            result.dropped_envelopes,
            result.partitioned_envelopes,
            result.crashed_envelopes,
            result.queue_overflow_envelopes,
            result.pending_envelopes,
            result.duplicate_copies,
            result.reordered_envelopes,
            result.communication_units,
            result.useful_deliveries,
            result.duplicate_deliveries,
            result.schedule_hash,
            result.trace_hash,
            result.violations,
        },
    );
}

fn yesNo(value: bool) []const u8 {
    return if (value) "yes" else "no";
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
