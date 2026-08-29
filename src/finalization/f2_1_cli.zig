const std = @import("std");
const f2 = @import("f2_1_async_budget.zig");
const stage7a = @import("../substrate/stage7/stage7a_policy.zig");
const frozen_stage7c = @import("../substrate/stage7/stage7c_async_transfer.zig");
const scaling = @import("../substrate/stage5/stage5a_scaling.zig");

const Profile = struct {
    name: []const u8,
    theta: stage7a.Theta,
};

const profiles = [_]Profile{
    .{ .name = "theta37", .theta = f2.theta37 },
    .{ .name = "theta51", .theta = f2.theta51 },
    .{ .name = "theta93", .theta = f2.theta93 },
    .{ .name = "round_robin", .theta = stage7a.round_robin_theta },
    .{ .name = "seeded", .theta = stage7a.seeded_theta },
    .{ .name = "novel_first", .theta = stage7a.novel_first_theta },
};

const topologies = [_]scaling.TopologyKind{ .ring, .grid };
const seeds = [_]u64{ 0, 1, 2 };
const decision_budget: u32 = 4096;

const Pair = struct {
    sync: stage7a.Result,
    async_result: f2.Result,
    frozen_async: frozen_stage7c.Result,

    fn parity(self: Pair) []const u8 {
        if (self.async_result.censored) return "budget_bound";
        return if (f2.matchesFrozen(self.async_result, self.frozen_async))
            "yes"
        else
            "no";
    }
};

pub fn main(init: std.process.Init) !void {
    const io = init.io;
    const args = try init.minimal.args.toSlice(init.arena.allocator());

    if (args.len == 2 and std.mem.eql(u8, args[1], "gap")) {
        try gap(io);
        return;
    }
    if (args.len == 2 and std.mem.eql(u8, args[1], "validate")) {
        try validate(io);
        return;
    }

    try std.Io.File.stderr().writeStreamingAll(
        io,
        "usage:\n  f2.1 gap\n  f2.1 validate\n",
    );
    std.process.exit(2);
}

fn canonicalWorld(
    topology: scaling.TopologyKind,
    seed: u64,
) stage7a.Config {
    return .{
        .population_size = 8,
        .fact_count = 32,
        .topology = topology,
        .redundancy = 2,
        .bandwidth = 2,
        .seed = seed,
        .max_rounds = decision_budget,
    };
}

fn asyncConfig(world: stage7a.Config) f2.Config {
    return .{
        .world = world,
        .schedule_seed = world.seed,
        .max_ticks = f2.horizonForBudget(decision_budget, 3),
        .clock_jitter = 3,
        .latency_min = 1,
        .latency_jitter = 4,
        .decision_budget_per_operator = decision_budget,
    };
}

fn runPair(
    theta: stage7a.Theta,
    topology: scaling.TopologyKind,
    seed: u64,
) !Pair {
    const world = canonicalWorld(topology, seed);
    const async_config = asyncConfig(world);
    return .{
        .sync = try stage7a.run(world, theta),
        .async_result = try f2.run(async_config, theta),
        .frozen_async = try frozen_stage7c.run(
            f2.toFrozenConfig(async_config),
            theta,
        ),
    };
}

fn validate(io: std.Io) !void {
    var rows: usize = 0;
    var violations: u64 = 0;
    var parity_failures: usize = 0;
    var accounting_failures: usize = 0;
    var communication_failures: usize = 0;
    var invalid_censoring: usize = 0;

    for (profiles) |profile| {
        for (topologies) |topology| {
            for (seeds) |seed| {
                const pair = try runPair(profile.theta, topology, seed);
                rows += 1;
                violations +%= pair.sync.violations +
                    pair.async_result.violations;

                if (!pair.async_result.accounted()) {
                    accounting_failures += 1;
                }
                if (pair.async_result.communication_units !=
                    pair.async_result.useful_deliveries +
                        pair.async_result.duplicate_deliveries)
                {
                    communication_failures += 1;
                }

                if (pair.async_result.censored) {
                    if (pair.async_result.success or
                        pair.async_result.min_local_decisions != decision_budget or
                        pair.async_result.max_local_decisions != decision_budget)
                    {
                        invalid_censoring += 1;
                    }
                } else if (!f2.matchesFrozen(
                    pair.async_result,
                    pair.frozen_async,
                )) {
                    parity_failures += 1;
                }
            }
        }
    }

    const out = std.Io.File.stdout();
    try writeLine(io, out, "F2.1 validation\n", .{});
    try writeLine(io, out, "rows: {d}\n", .{rows});
    try writeLine(io, out, "violations: {d}\n", .{violations});
    try writeLine(
        io,
        out,
        "stage7c_parity_failures: {d}\n",
        .{parity_failures},
    );
    try writeLine(
        io,
        out,
        "accounting_failures: {d}\n",
        .{accounting_failures},
    );
    try writeLine(
        io,
        out,
        "communication_failures: {d}\n",
        .{communication_failures},
    );
    try writeLine(
        io,
        out,
        "invalid_censoring: {d}\n",
        .{invalid_censoring},
    );

    if (rows != 36 or violations != 0 or parity_failures != 0 or
        accounting_failures != 0 or communication_failures != 0 or
        invalid_censoring != 0)
    {
        std.process.exit(1);
    }
}

fn gap(io: std.Io) !void {
    const out = std.Io.File.stdout();
    try out.writeStreamingAll(
        io,
        "profile\ttopology\tseed\tdecision_budget\t" ++
            "sync_success\tsync_rounds\tsync_policy_calls\t" ++
            "sync_communication\tsync_useful\tsync_duplicate\t" ++
            "async_success\tasync_ticks\tasync_policy_ticks\t" ++
            "async_min_decisions\tasync_max_decisions\tasync_censored\t" ++
            "async_communication\tasync_useful\tasync_duplicate\t" ++
            "communication_delta\tduplicate_delta\tpolicy_call_delta\t" ++
            "tick_round_delta\tstage7c_parity\tasync_accounted\t" ++
            "async_communication_accounted\tschedule_hash\ttrace_hash\t" ++
            "violations\tasync_transport_attempts\tasync_pending\n",
    );

    for (profiles) |profile| {
        for (topologies) |topology| {
            for (seeds) |seed| {
                const pair = try runPair(profile.theta, topology, seed);
                try writeLine(
                    io,
                    out,
                    "{s}\t{s}\t{d}\t{d}\t{s}\t{d}\t{d}\t{d}\t{d}\t{d}\t" ++
                        "{s}\t{d}\t{d}\t{d}\t{d}\t{s}\t{d}\t{d}\t{d}\t" ++
                        "{d}\t{d}\t{d}\t{d}\t{s}\t{s}\t{s}\t{x}\t{x}\t" ++
                        "{d}\t{d}\t{d}\n",
                    .{
                        profile.name,
                        topology.name(),
                        seed,
                        decision_budget,
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
                        signedDeltaU32(
                            pair.async_result.elapsed_ticks,
                            pair.sync.rounds,
                        ),
                        pair.parity(),
                        yesNo(pair.async_result.accounted()),
                        yesNo(
                            pair.async_result.communication_units ==
                                pair.async_result.useful_deliveries +
                                    pair.async_result.duplicate_deliveries,
                        ),
                        pair.async_result.schedule_hash,
                        pair.async_result.trace_hash,
                        pair.sync.violations + pair.async_result.violations,
                        pair.async_result.transport_attempts,
                        pair.async_result.pending_envelopes,
                    },
                );
            }
        }
    }
}

fn signedDelta(a: u64, b: u64) i128 {
    return @as(i128, @intCast(a)) - @as(i128, @intCast(b));
}

fn signedDeltaU32(a: u32, b: u32) i64 {
    return @as(i64, @intCast(a)) - @as(i64, @intCast(b));
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
