const std = @import("std");
const stage7a = @import("stage7a_policy.zig");
const scaling = @import("../stage5/stage5a_scaling.zig");

const full_populations = [_]usize{ 32, 64 };
const full_facts = [_]usize{ 32, 128 };
const full_topologies = [_]scaling.TopologyKind{ .ring, .grid };
const full_bandwidths = [_]usize{ 1, 2, 4 };
const full_seeds = [_]u64{ 0, 1, 2 };

pub fn main(init: std.process.Init) !void {
    const io = init.io;
    const args = try init.minimal.args.toSlice(init.arena.allocator());

    if (args.len < 2 or args.len > 3) {
        try usage(io);
        std.process.exit(2);
    }

    if (std.mem.eql(u8, args[1], "validate")) {
        if (args.len != 2) {
            try usage(io);
            std.process.exit(2);
        }
        try validate(io);
        return;
    }

    if (std.mem.eql(u8, args[1], "plan")) {
        if (args.len != 2) {
            try usage(io);
            std.process.exit(2);
        }
        try plan(io);
        return;
    }

    if (std.mem.eql(u8, args[1], "probe")) {
        const mode = if (args.len == 3) args[2] else "smoke";
        if (std.mem.eql(u8, mode, "smoke")) {
            try probeSmoke(io);
            return;
        }
        if (std.mem.eql(u8, mode, "full")) {
            try probeFull(io);
            return;
        }
        try usage(io);
        std.process.exit(2);
    }

    try usage(io);
    std.process.exit(2);
}

fn validate(io: std.Io) !void {
    const out = std.Io.File.stdout();
    const fixtures = [_]struct {
        profile: stage7a.Profile,
        policy: scaling.PolicyKind,
    }{
        .{
            .profile = .{
                .name = "round_robin_corner",
                .theta = stage7a.round_robin_theta,
            },
            .policy = .round_robin,
        },
        .{
            .profile = .{
                .name = "seeded_corner",
                .theta = stage7a.seeded_theta,
            },
            .policy = .seeded,
        },
        .{
            .profile = .{
                .name = "novel_first_corner",
                .theta = stage7a.novel_first_theta,
            },
            .policy = .novel_first,
        },
    };
    const topologies = [_]scaling.TopologyKind{ .ring, .grid };
    const seeds = [_]u64{ 0, 1, 2 };

    var corner_checks: usize = 0;
    var corner_mismatches: usize = 0;

    for (topologies) |topology| {
        for (fixtures) |fixture| {
            for (seeds) |seed| {
                const config = stage7a.Config{
                    .population_size = 32,
                    .fact_count = 64,
                    .topology = topology,
                    .redundancy = 2,
                    .bandwidth = 2,
                    .seed = seed,
                    .max_rounds = 1024,
                };
                const expected = try scaling.run(
                    config.asScaling(fixture.policy),
                );
                const actual = try stage7a.run(
                    config,
                    fixture.profile.theta,
                );
                corner_checks += 1;
                if (!sameBaselineResult(expected, actual)) {
                    corner_mismatches += 1;
                }
            }
        }
    }

    var deterministic_checks: usize = 0;
    var deterministic_mismatches: usize = 0;
    for (topologies) |topology| {
        for (seeds) |seed| {
            const config = stage7a.Config{
                .population_size = 32,
                .fact_count = 64,
                .topology = topology,
                .redundancy = 2,
                .bandwidth = 2,
                .seed = seed,
                .max_rounds = 1024,
            };
            const a = try stage7a.run(
                config,
                stage7a.exploratory_novel_theta,
            );
            const b = try stage7a.run(
                config,
                stage7a.exploratory_novel_theta,
            );
            deterministic_checks += 1;
            if (!sameStage7Result(a, b)) deterministic_mismatches += 1;
        }
    }

    try out.writeStreamingAll(io, "Stage 7A validation\n");
    try writeLine(
        io,
        out,
        "theta_dimensions: 4\n",
        .{},
    );
    try writeLine(
        io,
        out,
        "baseline_corner_checks: {d}\n",
        .{corner_checks},
    );
    try writeLine(
        io,
        out,
        "baseline_corner_mismatches: {d}\n",
        .{corner_mismatches},
    );
    try writeLine(
        io,
        out,
        "interior_determinism_checks: {d}\n",
        .{deterministic_checks},
    );
    try writeLine(
        io,
        out,
        "interior_determinism_mismatches: {d}\n",
        .{deterministic_mismatches},
    );

    if (corner_mismatches != 0 or deterministic_mismatches != 0) {
        std.process.exit(2);
    }
}

fn plan(io: std.Io) !void {
    const out = std.Io.File.stdout();
    const smoke_runs = stage7a.probe_profiles.len;
    const full_runs =
        stage7a.probe_profiles.len *
        full_populations.len *
        full_facts.len *
        full_topologies.len *
        full_bandwidths.len *
        full_seeds.len;

    try out.writeStreamingAll(io, "Stage 7A control-surface probe plan\n");
    try writeLine(io, out, "profiles: {d}\n", .{stage7a.probe_profiles.len});
    try writeLine(io, out, "smoke_runs: {d}\n", .{smoke_runs});
    try writeLine(io, out, "full_runs: {d}\n", .{full_runs});
    try out.writeStreamingAll(
        io,
        "full_grid: N={32,64} F={32,128} G={ring,grid} R=2 B={1,2,4} seed={0,1,2}\n",
    );
    try out.writeStreamingAll(
        io,
        "purpose: parameterization sanity/control-surface probe only; no theta fitting or selection\n",
    );
}

fn probeSmoke(io: std.Io) !void {
    const out = std.Io.File.stdout();
    try writeProbeHeader(io, out);

    for (stage7a.probe_profiles) |profile| {
        const config = stage7a.Config{
            .population_size = 32,
            .fact_count = 32,
            .topology = .ring,
            .redundancy = 2,
            .bandwidth = 2,
            .seed = 0,
            .max_rounds = 1024,
        };
        const result = try stage7a.run(config, profile.theta);
        try writeProbeRow(io, out, profile, result);
    }
}

fn probeFull(io: std.Io) !void {
    const out = std.Io.File.stdout();
    try writeProbeHeader(io, out);

    for (stage7a.probe_profiles) |profile| {
        for (full_populations) |population| {
            for (full_facts) |facts| {
                for (full_topologies) |topology| {
                    for (full_bandwidths) |bandwidth| {
                        for (full_seeds) |seed| {
                            const config = stage7a.Config{
                                .population_size = population,
                                .fact_count = facts,
                                .topology = topology,
                                .redundancy = 2,
                                .bandwidth = bandwidth,
                                .seed = seed,
                                .max_rounds = 4096,
                            };
                            const result = try stage7a.run(
                                config,
                                profile.theta,
                            );
                            try writeProbeRow(
                                io,
                                out,
                                profile,
                                result,
                            );
                        }
                    }
                }
            }
        }
    }
}

fn writeProbeHeader(io: std.Io, out: std.Io.File) !void {
    try out.writeStreamingAll(
        io,
        "profile\tpopulation\tfacts\ttopology\tdiameter\tredundancy\tbandwidth\tseed\tnovelty_permille\texploration_permille\tretry_permille\tbandwidth_utilization_permille\tsuccess\trounds\tcollector_initial\tcollector_final\tpolicy_calls\tactions\tmessages\tcommunication_units\tuseful\tduplicate\tuseful_per_1000\tduplicate_permille\tviolations\n",
    );
}

fn writeProbeRow(
    io: std.Io,
    out: std.Io.File,
    profile: stage7a.Profile,
    result: stage7a.Result,
) !void {
    try writeLine(
        io,
        out,
        "{s}\t{d}\t{d}\t{s}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{s}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\n",
        .{
            profile.name,
            result.config.population_size,
            result.config.fact_count,
            result.config.topology.name(),
            result.diameter,
            result.config.redundancy,
            result.config.bandwidth,
            result.config.seed,
            result.theta.novelty_permille,
            result.theta.exploration_permille,
            result.theta.retry_permille,
            result.theta.bandwidth_utilization_permille,
            if (result.success) "yes" else "no",
            result.rounds,
            result.collector_initial_facts,
            result.collector_final_facts,
            result.policy_calls,
            result.actions_proposed,
            result.messages,
            result.communication_units,
            result.useful_deliveries,
            result.duplicate_deliveries,
            result.usefulPerThousandUnits(),
            result.duplicatePermille(),
            result.violations,
        },
    );
}

fn sameBaselineResult(
    a: scaling.Result,
    b: stage7a.Result,
) bool {
    return a.success == b.success and
        a.rounds == b.rounds and
        a.collector_initial_facts == b.collector_initial_facts and
        a.collector_final_facts == b.collector_final_facts and
        a.policy_calls == b.policy_calls and
        a.actions_proposed == b.actions_proposed and
        a.rejected_actions == b.rejected_actions and
        a.messages == b.messages and
        a.communication_units == b.communication_units and
        a.useful_deliveries == b.useful_deliveries and
        a.duplicate_deliveries == b.duplicate_deliveries and
        a.violations == b.violations;
}

fn sameStage7Result(
    a: stage7a.Result,
    b: stage7a.Result,
) bool {
    return a.success == b.success and
        a.rounds == b.rounds and
        a.collector_initial_facts == b.collector_initial_facts and
        a.collector_final_facts == b.collector_final_facts and
        a.policy_calls == b.policy_calls and
        a.actions_proposed == b.actions_proposed and
        a.messages == b.messages and
        a.communication_units == b.communication_units and
        a.useful_deliveries == b.useful_deliveries and
        a.duplicate_deliveries == b.duplicate_deliveries and
        a.violations == b.violations;
}

fn usage(io: std.Io) !void {
    try std.Io.File.stderr().writeStreamingAll(
        io,
        "usage:\n" ++
            "  zig run src/stage7a_cli.zig -- validate\n" ++
            "  zig run src/stage7a_cli.zig -- plan\n" ++
            "  zig run -O ReleaseFast src/stage7a_cli.zig -- probe [smoke|full]\n",
    );
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
