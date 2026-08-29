const std = @import("std");
const f2 = @import("f2_async_budget.zig");
const stage7a = @import("../substrate/stage7/stage7a_policy.zig");
const scaling = @import("../substrate/stage5/stage5a_scaling.zig");

const Profile = struct {
    name: []const u8,
    theta: stage7a.Theta,
};

const gap_profiles = [_]Profile{
    .{ .name = "theta37", .theta = f2.theta37 },
    .{ .name = "theta51", .theta = f2.theta51 },
    .{ .name = "theta93", .theta = f2.theta93 },
    .{ .name = "round_robin", .theta = stage7a.round_robin_theta },
    .{ .name = "seeded", .theta = stage7a.seeded_theta },
    .{ .name = "novel_first", .theta = stage7a.novel_first_theta },
};

const scaling_profiles = [_]Profile{
    .{ .name = "theta37", .theta = f2.theta37 },
    .{ .name = "theta51", .theta = f2.theta51 },
    .{ .name = "theta93", .theta = f2.theta93 },
    .{ .name = "novel_first", .theta = stage7a.novel_first_theta },
};

const topologies = [_]scaling.TopologyKind{ .ring, .grid };
const populations = [_]usize{ 8, 16, 32, 64, 128 };
const densities = [_]usize{ 1, 2 };
const seeds = [_]u64{ 0, 1, 2 };
const decision_budget: u32 = 4096;

pub fn main(init: std.process.Init) !void {
    const io = init.io;
    const args = try init.minimal.args.toSlice(init.arena.allocator());

    if (args.len == 2 and std.mem.eql(u8, args[1], "gap")) {
        try gap(io);
        return;
    }
    if (args.len == 2 and std.mem.eql(u8, args[1], "scaling")) {
        try scalingSweep(io);
        return;
    }
    if (args.len == 2 and std.mem.eql(u8, args[1], "validate")) {
        try validate(io);
        return;
    }

    try std.Io.File.stderr().writeStreamingAll(
        io,
        "usage:\n  f2 gap\n  f2 scaling\n  f2 validate\n",
    );
    std.process.exit(2);
}

fn validate(io: std.Io) !void {
    var gap_rows: usize = 0;
    var scaling_rows: usize = 0;
    var violations: u64 = 0;
    var censored_with_short_budget: usize = 0;

    for (gap_profiles) |profile| {
        for (topologies) |topology| {
            for (seeds) |seed| {
                const pair = try runPair(profile.theta, topology, seed);
                gap_rows += 1;
                violations +%= pair.sync.violations + pair.async_result.violations;
                if (!pair.async_result.success and
                    (pair.async_result.min_local_decisions != decision_budget or
                        pair.async_result.max_local_decisions != decision_budget))
                {
                    censored_with_short_budget += 1;
                }
            }
        }
    }

    for (populations) |population| {
        for (densities) |density| {
            for (topologies) |topology| {
                for (scaling_profiles) |profile| {
                    for (seeds) |seed| {
                        const result = try runScaling(
                            population,
                            density,
                            topology,
                            profile.theta,
                            seed,
                        );
                        scaling_rows += 1;
                        violations +%= result.violations;
                        if (!result.success and
                            (result.min_local_decisions != decision_budget or
                                result.max_local_decisions != decision_budget))
                        {
                            censored_with_short_budget += 1;
                        }
                    }
                }
            }
        }
    }

    const out = std.Io.File.stdout();
    try writeLine(io, out, "F2 validation\n", .{});
    try writeLine(io, out, "gap_rows: {d}\n", .{gap_rows});
    try writeLine(io, out, "scaling_rows: {d}\n", .{scaling_rows});
    try writeLine(io, out, "violations: {d}\n", .{violations});
    try writeLine(
        io,
        out,
        "censored_with_short_budget: {d}\n",
        .{censored_with_short_budget},
    );

    if (gap_rows != 36 or scaling_rows != 240 or
        violations != 0 or censored_with_short_budget != 0)
    {
        std.process.exit(1);
    }
}

const Pair = struct {
    sync: stage7a.Result,
    async_result: f2.Result,
};

fn runPair(
    theta: stage7a.Theta,
    topology: scaling.TopologyKind,
    seed: u64,
) !Pair {
    const world = stage7a.Config{
        .population_size = 8,
        .fact_count = 32,
        .topology = topology,
        .redundancy = 2,
        .bandwidth = 2,
        .seed = seed,
        .max_rounds = decision_budget,
    };
    const sync = try stage7a.run(world, theta);
    const async_result = try f2.run(.{
        .world = world,
        .schedule_seed = seed,
        .max_ticks = f2.horizonForBudget(decision_budget, 3),
        .clock_jitter = 3,
        .latency_min = 1,
        .latency_jitter = 4,
        .decision_budget_per_operator = decision_budget,
    }, theta);
    return .{ .sync = sync, .async_result = async_result };
}

fn runScaling(
    population: usize,
    density: usize,
    topology: scaling.TopologyKind,
    theta: stage7a.Theta,
    seed: u64,
) !f2.Result {
    const fact_count = population * density;
    const world = stage7a.Config{
        .population_size = population,
        .fact_count = fact_count,
        .topology = topology,
        .redundancy = 2,
        .bandwidth = 2,
        .seed = seed,
        .max_rounds = decision_budget,
    };
    return f2.run(.{
        .world = world,
        .schedule_seed = seed,
        .max_ticks = f2.horizonForBudget(decision_budget, 3),
        .clock_jitter = 3,
        .latency_min = 1,
        .latency_jitter = 4,
        .decision_budget_per_operator = decision_budget,
    }, theta);
}

fn gap(io: std.Io) !void {
    const out = std.Io.File.stdout();
    try out.writeStreamingAll(
        io,
        "profile\ttopology\tseed\tsync_success\tsync_rounds\t" ++
            "sync_policy_calls\tsync_communication\tsync_useful\tsync_duplicate\t" ++
            "async_success\tasync_ticks\tasync_policy_ticks\tasync_min_decisions\t" ++
            "async_max_decisions\tasync_censored\tasync_communication\t" ++
            "async_useful\tasync_duplicate\tcommunication_delta\t" ++
            "duplicate_delta\tpolicy_call_delta\tschedule_hash\ttrace_hash\tviolations\n",
    );

    for (gap_profiles) |profile| {
        for (topologies) |topology| {
            for (seeds) |seed| {
                const pair = try runPair(profile.theta, topology, seed);
                try writeLine(
                    io,
                    out,
                    "{s}\t{s}\t{d}\t{s}\t{d}\t{d}\t{d}\t{d}\t{d}\t" ++
                        "{s}\t{d}\t{d}\t{d}\t{d}\t{s}\t{d}\t{d}\t{d}\t" ++
                        "{d}\t{d}\t{d}\t{x}\t{x}\t{d}\n",
                    .{
                        profile.name,
                        topology.name(),
                        seed,
                        yesNo(pair.sync.success),
                        pair.sync.rounds,
                        pair.sync.policy_calls,
                        pair.sync.communication_units,
                        pair.sync.useful_deliveries,
                        pair.sync.duplicate_deliveries,
                        yesNo(pair.async_result.success),
                        pair.async_result.elapsed_ticks,
                        pair.async_result.local_policy_ticks,
                        pair.async_result.min_local_decisions,
                        pair.async_result.max_local_decisions,
                        yesNo(pair.async_result.censored),
                        pair.async_result.communication_units,
                        pair.async_result.useful_deliveries,
                        pair.async_result.duplicate_deliveries,
                        signedDelta(
                            pair.async_result.communication_units,
                            pair.sync.communication_units,
                        ),
                        signedDelta(
                            pair.async_result.duplicate_deliveries,
                            pair.sync.duplicate_deliveries,
                        ),
                        signedDelta(
                            pair.async_result.local_policy_ticks,
                            pair.sync.policy_calls,
                        ),
                        pair.async_result.schedule_hash,
                        pair.async_result.trace_hash,
                        pair.sync.violations + pair.async_result.violations,
                    },
                );
            }
        }
    }
}

fn scalingSweep(io: std.Io) !void {
    const out = std.Io.File.stdout();
    try out.writeStreamingAll(
        io,
        "profile\ttopology\tseed\tnodes\tfacts\tfacts_per_node\t" ++
            "decision_budget\tsuccess\tcensored\tticks\tcollector_initial\t" ++
            "collector_final\tpolicy_ticks\tmin_local_decisions\t" ++
            "max_local_decisions\tcommunication_units\tuseful\tduplicate\t" ++
            "schedule_hash\ttrace_hash\tviolations\n",
    );

    for (populations) |population| {
        for (densities) |density| {
            for (topologies) |topology| {
                for (scaling_profiles) |profile| {
                    for (seeds) |seed| {
                        const result = try runScaling(
                            population,
                            density,
                            topology,
                            profile.theta,
                            seed,
                        );
                        try writeLine(
                            io,
                            out,
                            "{s}\t{s}\t{d}\t{d}\t{d}\t{d}\t{d}\t{s}\t{s}\t" ++
                                "{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t" ++
                                "{x}\t{x}\t{d}\n",
                            .{
                                profile.name,
                                topology.name(),
                                seed,
                                population,
                                population * density,
                                density,
                                decision_budget,
                                yesNo(result.success),
                                yesNo(result.censored),
                                result.elapsed_ticks,
                                result.collector_initial_facts,
                                result.collector_final_facts,
                                result.local_policy_ticks,
                                result.min_local_decisions,
                                result.max_local_decisions,
                                result.communication_units,
                                result.useful_deliveries,
                                result.duplicate_deliveries,
                                result.schedule_hash,
                                result.trace_hash,
                                result.violations,
                            },
                        );
                    }
                }
            }
        }
    }
}

fn signedDelta(a: u64, b: u64) i128 {
    return @as(i128, a) - @as(i128, b);
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
    var buffer: [8192]u8 = undefined;
    const line = try std.fmt.bufPrint(&buffer, format, args);
    try out.writeStreamingAll(io, line);
}
