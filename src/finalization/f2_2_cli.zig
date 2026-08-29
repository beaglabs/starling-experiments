const std = @import("std");
const f2 = @import("f2_2_async_scaling.zig");
const stage7a = @import("../substrate/stage7/stage7a_policy.zig");
const scaling = @import("../substrate/stage5/stage5a_scaling.zig");

const Profile = struct {
    name: []const u8,
    theta: stage7a.Theta,
};

pub const profiles = [_]Profile{
    .{ .name = "theta37", .theta = f2.theta37 },
    .{ .name = "theta51", .theta = f2.theta51 },
    .{ .name = "theta93", .theta = f2.theta93 },
    .{ .name = "novel_first", .theta = stage7a.novel_first_theta },
};

pub const populations = [_]usize{ 8, 16, 32, 64, 128 };
pub const densities = [_]usize{ 1, 2 };
pub const topologies = [_]scaling.TopologyKind{ .ring, .grid };
pub const seeds = [_]u64{ 0, 1, 2 };
pub const decision_budget: u32 = 4096;

pub fn main(init: std.process.Init) !void {
    const io = init.io;
    const args = try init.minimal.args.toSlice(init.arena.allocator());

    if (args.len == 2 and std.mem.eql(u8, args[1], "scaling")) {
        try scalingSweep(io);
        return;
    }
    if (args.len == 7 and std.mem.eql(u8, args[1], "world")) {
        try runWorld(io, args[2..]);
        return;
    }

    try std.Io.File.stderr().writeStreamingAll(
        io,
        "usage:\n" ++
            "  f2.2 scaling\n" ++
            "  f2.2 world <profile> <nodes> <facts-per-node> " ++
            "<ring|grid> <seed>\n",
    );
    std.process.exit(2);
}

fn profileByName(name: []const u8) ?Profile {
    for (profiles) |profile| {
        if (std.mem.eql(u8, name, profile.name)) return profile;
    }
    return null;
}

fn topologyByName(name: []const u8) ?scaling.TopologyKind {
    if (std.mem.eql(u8, name, "ring")) return .ring;
    if (std.mem.eql(u8, name, "grid")) return .grid;
    return null;
}

fn canonicalConfig(
    population: usize,
    density: usize,
    topology: scaling.TopologyKind,
    seed: u64,
) f2.Config {
    const latency_min: u16 = 1;
    const latency_jitter: u16 = 4;
    const clock_jitter: u16 = 3;
    return .{
        .world = .{
            .population_size = population,
            .fact_count = population * density,
            .topology = topology,
            .redundancy = 2,
            .bandwidth = 2,
            .seed = seed,
            .max_rounds = decision_budget,
        },
        .schedule_seed = seed,
        .max_ticks = f2.horizonForBudgetAndDrain(
            decision_budget,
            clock_jitter,
            latency_min,
            latency_jitter,
        ),
        .clock_jitter = clock_jitter,
        .latency_min = latency_min,
        .latency_jitter = latency_jitter,
        .decision_budget_per_operator = decision_budget,
    };
}

fn scalingSweep(io: std.Io) !void {
    try writeHeader(io);
    for (populations) |population| {
        for (densities) |density| {
            for (topologies) |topology| {
                for (profiles) |profile| {
                    for (seeds) |seed| {
                        const result = try f2.run(
                            canonicalConfig(
                                population,
                                density,
                                topology,
                                seed,
                            ),
                            profile.theta,
                        );
                        try writeResult(
                            io,
                            profile.name,
                            density,
                            result,
                        );
                    }
                }
            }
        }
    }
}

fn runWorld(io: std.Io, args: []const []const u8) !void {
    const profile = profileByName(args[0]) orelse {
        std.process.exit(2);
    };
    const population = try std.fmt.parseInt(usize, args[1], 10);
    const density = try std.fmt.parseInt(usize, args[2], 10);
    const topology = topologyByName(args[3]) orelse {
        std.process.exit(2);
    };
    const seed = try std.fmt.parseInt(u64, args[4], 10);
    const result = try f2.run(
        canonicalConfig(population, density, topology, seed),
        profile.theta,
    );
    try writeHeader(io);
    try writeResult(io, profile.name, density, result);
}

fn writeHeader(io: std.Io) !void {
    try std.Io.File.stdout().writeStreamingAll(
        io,
        "profile\ttopology\tseed\tnodes\tfacts\tfacts_per_node\t" ++
            "decision_budget\tsuccess\tcensored\tticks\t" ++
            "budget_exhausted_tick\tdrain_ticks\tcollector_initial\t" ++
            "collector_final\tpolicy_ticks\tmin_local_decisions\t" ++
            "max_local_decisions\ttransport_attempts\tdelivered\tdropped\t" ++
            "partitioned\tcrashed\tqueue_overflow\tpending\t" ++
            "duplicate_copies\treordered\tcommunication_units\tuseful\t" ++
            "duplicate\tschedule_hash\ttrace_hash\tviolations\t" ++
            "envelope_accounted\tcommunication_accounted\n",
    );
}

fn writeResult(
    io: std.Io,
    profile: []const u8,
    density: usize,
    result: f2.Result,
) !void {
    var prefix_buffer: [6144]u8 = undefined;
    const prefix = try std.fmt.bufPrint(
        &prefix_buffer,
        "{s}\t{s}\t{d}\t{d}\t{d}\t{d}\t{d}\t{s}\t{s}\t{d}\t" ++
            "{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t" ++
            "{d}\t{d}\t{d}\t{d}\t",
        .{
            profile,
            result.config.world.topology.name(),
            result.config.world.seed,
            result.config.world.population_size,
            result.config.world.fact_count,
            density,
            result.config.decision_budget_per_operator,
            yesNo(result.success),
            yesNo(result.censored),
            result.elapsed_ticks,
            result.budget_exhausted_tick,
            result.drain_ticks,
            result.collector_initial_facts,
            result.collector_final_facts,
            result.local_policy_ticks,
            result.min_local_decisions,
            result.max_local_decisions,
            result.transport_attempts,
            result.delivered_envelopes,
            result.dropped_envelopes,
            result.partitioned_envelopes,
            result.crashed_envelopes,
            result.queue_overflow_envelopes,
            result.pending_envelopes,
        },
    );

    var suffix_buffer: [3072]u8 = undefined;
    const suffix = try std.fmt.bufPrint(
        &suffix_buffer,
        "{d}\t{d}\t{d}\t{d}\t{d}\t{x}\t{x}\t{d}\t{s}\t{s}\n",
        .{
            result.duplicate_copies,
            result.reordered_envelopes,
            result.communication_units,
            result.useful_deliveries,
            result.duplicate_deliveries,
            result.schedule_hash,
            result.trace_hash,
            result.violations,
            yesNo(result.accounted()),
            yesNo(
                result.communication_units ==
                    result.useful_deliveries +
                        result.duplicate_deliveries,
            ),
        },
    );

    try std.Io.File.stdout().writeStreamingAll(io, prefix);
    try std.Io.File.stdout().writeStreamingAll(io, suffix);
}

fn yesNo(value: bool) []const u8 {
    return if (value) "yes" else "no";
}

test "F2.2 canonical matrix has exactly 240 worlds" {
    try std.testing.expectEqual(
        @as(usize, 240),
        populations.len *
            densities.len *
            topologies.len *
            profiles.len *
            seeds.len,
    );
}

test "F2.2 maximum world fits frozen substrate bounds" {
    const population = populations[populations.len - 1];
    const density = densities[densities.len - 1];
    try std.testing.expect(population <= scaling.max_operators);
    try std.testing.expect(population * density <= scaling.max_facts);
}
