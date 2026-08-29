const std = @import("std");
const f3 = @import("f3_inference_policy.zig");
const reference = @import("f3_stage7b_reference.zig");
const stage7a = @import("../substrate/stage7/stage7a_policy.zig");
const scaling = @import("../substrate/stage5/stage5a_scaling.zig");

pub const latin_candidate_count: usize = 128;
pub const fixed_candidate_count: usize = stage7a.probe_profiles.len;
pub const canonical_candidate_count: usize =
    latin_candidate_count + fixed_candidate_count;
pub const max_candidates: usize = 160;

pub const CandidateSource = enum {
    fixed_profile,
    latin_hypercube,

    pub fn name(self: CandidateSource) []const u8 {
        return switch (self) {
            .fixed_profile => "fixed_profile",
            .latin_hypercube => "latin_hypercube",
        };
    }
};

pub const Candidate = struct {
    id: usize,
    source: CandidateSource,
    label: []const u8,
    theta: f3.Theta,
};

pub const CandidateSet = struct {
    items: [max_candidates]Candidate = undefined,
    len: usize = 0,

    pub fn slice(self: *const CandidateSet) []const Candidate {
        return self.items[0..self.len];
    }
};

pub const SplitKind = enum {
    training,
    validation,
    population_extrapolation,
    density_extrapolation,
    redundancy_extrapolation,
    bandwidth_extrapolation,
    topology_extrapolation,
    compound_extrapolation,

    pub fn name(self: SplitKind) []const u8 {
        return switch (self) {
            .training => "training",
            .validation => "validation",
            .population_extrapolation => "population_N_128",
            .density_extrapolation => "density_F_over_N_4",
            .redundancy_extrapolation => "redundancy_R_4",
            .bandwidth_extrapolation => "bandwidth_B_8",
            .topology_extrapolation => "topology_complete",
            .compound_extrapolation => "compound",
        };
    }

    pub fn maxRounds(self: SplitKind) u32 {
        return switch (self) {
            .training, .validation => 2048,
            else => 4096,
        };
    }
};

pub const Aggregate = struct {
    runs: usize = 0,
    failures: usize = 0,
    rounds_sum: u64 = 0,
    communication_sum: u64 = 0,
    duplicate_sum: u64 = 0,
    computation_sum: u64 = 0,
    inference_sum: u64 = 0,
    cache_reuse_sum: u64 = 0,
    useful_sum: u64 = 0,
    violations: u64 = 0,

    pub fn successCount(self: Aggregate) usize {
        return self.runs - self.failures;
    }

    pub fn add(self: *Aggregate, result: f3.Result) void {
        self.runs += 1;
        if (!result.success) self.failures += 1;
        self.rounds_sum +%= @as(u64, result.rounds);
        self.communication_sum +%= result.communication_units;
        self.duplicate_sum +%= result.duplicate_deliveries;
        self.computation_sum +%= result.policy_calls;
        self.inference_sum +%= result.inference_units;
        self.cache_reuse_sum +%= result.cache_reuses;
        self.useful_sum +%= result.useful_deliveries;
        self.violations +%= result.violations;
    }

    pub fn usefulPerThousand(self: Aggregate) u64 {
        if (self.communication_sum == 0) return 0;
        return (self.useful_sum * 1000) / self.communication_sum;
    }

    pub fn duplicatePermille(self: Aggregate) u64 {
        if (self.communication_sum == 0) return 0;
        return (self.duplicate_sum * 1000) / self.communication_sum;
    }

    pub fn inferenceAccounted(self: Aggregate) bool {
        return self.computation_sum ==
            self.inference_sum + self.cache_reuse_sum;
    }
};

pub const Frontier = struct {
    flags: [max_candidates]bool = [_]bool{false} ** max_candidates,
    count: usize = 0,
    min_failures: usize = 0,
};

pub fn generateCornerCandidates() CandidateSet {
    const baseline = reference.generateCandidates();
    var set = CandidateSet{};
    var i: usize = 0;
    while (i < baseline.len) : (i += 1) {
        const item = baseline.items[i];
        set.items[i] = .{
            .id = item.id,
            .source = switch (item.source) {
                .fixed_profile => .fixed_profile,
                .latin_hypercube => .latin_hypercube,
            },
            .label = item.label,
            .theta = .{
                .base = item.theta,
                .inference_gating_permille = 1000,
            },
        };
    }
    set.len = baseline.len;
    return set;
}

pub fn cornerMatchesReference(
    candidate_index: usize,
    split: SplitKind,
) !bool {
    const f3_candidates = generateCornerCandidates();
    const reference_candidates = reference.generateCandidates();
    if (candidate_index >= f3_candidates.len or
        candidate_index >= reference_candidates.len)
    {
        return false;
    }

    const actual = try evaluateCandidate(
        f3_candidates.items[candidate_index],
        split,
    );
    const expected = try reference.evaluateCandidate(
        reference_candidates.items[candidate_index],
        switch (split) {
            .training => .training,
            .validation => .validation,
            .population_extrapolation => .population_extrapolation,
            .density_extrapolation => .density_extrapolation,
            .redundancy_extrapolation => .redundancy_extrapolation,
            .bandwidth_extrapolation => .bandwidth_extrapolation,
            .topology_extrapolation => .topology_extrapolation,
            .compound_extrapolation => .compound_extrapolation,
        },
    );

    return actual.runs == expected.runs and
        actual.failures == expected.failures and
        actual.rounds_sum == expected.rounds_sum and
        actual.communication_sum == expected.communication_sum and
        actual.duplicate_sum == expected.duplicate_sum and
        actual.computation_sum == expected.computation_sum and
        actual.inference_sum == expected.computation_sum and
        actual.cache_reuse_sum == 0 and
        actual.useful_sum == expected.useful_sum and
        actual.violations == expected.violations;
}

pub fn generateCandidates() CandidateSet {
    var set = CandidateSet{};

    for (stage7a.probe_profiles) |profile| {
        addUnique(&set, .{
            .id = set.len,
            .source = .fixed_profile,
            .label = profile.name,
            .theta = .{
                .base = profile.theta,
                .inference_gating_permille = 1000,
            },
        });
    }

    var index: usize = 0;
    while (index < latin_candidate_count) : (index += 1) {
        const n_stratum = index;
        const e_stratum = (index * 37 + 17) % latin_candidate_count;
        const r_stratum = (index * 73 + 43) % latin_candidate_count;
        const u_stratum = (index * 101 + 61) % latin_candidate_count;
        const c_stratum = (index * 53 + 29) % latin_candidate_count;

        const theta = f3.Theta{
            .base = .{
                .novelty_permille = scale1000(n_stratum),
                .exploration_permille = scale1000(e_stratum),
                .retry_permille = scale1000(r_stratum),
                .bandwidth_utilization_permille =
                    250 + scale750(u_stratum),
            },
            .inference_gating_permille = scale1000(c_stratum),
        };

        addUnique(&set, .{
            .id = set.len,
            .source = .latin_hypercube,
            .label = "latin",
            .theta = theta,
        });
    }

    return set;
}

fn addUnique(set: *CandidateSet, candidate: Candidate) void {
    var i: usize = 0;
    while (i < set.len) : (i += 1) {
        if (set.items[i].theta.eql(candidate.theta)) return;
    }
    std.debug.assert(set.len < max_candidates);
    var value = candidate;
    value.id = set.len;
    set.items[set.len] = value;
    set.len += 1;
}

fn scale1000(stratum: usize) u16 {
    std.debug.assert(stratum < latin_candidate_count);
    return @intCast((stratum * 1000) / (latin_candidate_count - 1));
}

fn scale750(stratum: usize) u16 {
    std.debug.assert(stratum < latin_candidate_count);
    return @intCast((stratum * 750) / (latin_candidate_count - 1));
}

pub fn worldCount(split: SplitKind) usize {
    return switch (split) {
        .training => 48,
        .validation => 24,
        .population_extrapolation => 36,
        .density_extrapolation => 36,
        .redundancy_extrapolation => 72,
        .bandwidth_extrapolation => 24,
        .topology_extrapolation => 36,
        .compound_extrapolation => 9,
    };
}

pub fn evaluateCandidate(
    candidate: Candidate,
    split: SplitKind,
) !Aggregate {
    return switch (split) {
        .training => evaluateTrainingLike(candidate, .training),
        .validation => evaluateTrainingLike(candidate, .validation),
        .population_extrapolation => evaluatePopulation(candidate),
        .density_extrapolation => evaluateDensity(candidate),
        .redundancy_extrapolation => evaluateRedundancy(candidate),
        .bandwidth_extrapolation => evaluateBandwidth(candidate),
        .topology_extrapolation => evaluateTopology(candidate),
        .compound_extrapolation => evaluateCompound(candidate),
    };
}

fn evaluateTrainingLike(
    candidate: Candidate,
    split: SplitKind,
) !Aggregate {
    std.debug.assert(split == .training or split == .validation);
    const populations = [_]usize{ 32, 64 };
    const ratios = [_]usize{ 1, 2 };
    const topologies = [_]scaling.TopologyKind{ .ring, .grid };
    const bandwidths = [_]usize{ 1, 2, 4 };
    var aggregate = Aggregate{};
    for (populations) |population| {
        for (ratios) |ratio| {
            for (topologies) |topology| {
                for (bandwidths) |bandwidth| {
                    if (split == .training) {
                        const training_seeds = [_]u64{ 0, 1 };
                        for (training_seeds) |seed| {
                            aggregate.add(try runWorld(
                                candidate.theta,
                                population,
                                population * ratio,
                                topology,
                                2,
                                bandwidth,
                                seed,
                                split.maxRounds(),
                            ));
                        }
                    } else {
                        aggregate.add(try runWorld(
                            candidate.theta,
                            population,
                            population * ratio,
                            topology,
                            2,
                            bandwidth,
                            2,
                            split.maxRounds(),
                        ));
                    }
                }
            }
        }
    }
    return aggregate;
}

fn evaluatePopulation(candidate: Candidate) !Aggregate {
    const ratios = [_]usize{ 1, 2 };
    const topologies = [_]scaling.TopologyKind{ .ring, .grid };
    const bandwidths = [_]usize{ 1, 2, 4 };
    const seeds = [_]u64{ 0, 1, 2 };

    var aggregate = Aggregate{};
    for (ratios) |ratio| {
        for (topologies) |topology| {
            for (bandwidths) |bandwidth| {
                for (seeds) |seed| {
                    aggregate.add(try runWorld(
                        candidate.theta,
                        128,
                        128 * ratio,
                        topology,
                        2,
                        bandwidth,
                        seed,
                        SplitKind.population_extrapolation.maxRounds(),
                    ));
                }
            }
        }
    }
    return aggregate;
}

fn evaluateDensity(candidate: Candidate) !Aggregate {
    const populations = [_]usize{ 32, 64 };
    const topologies = [_]scaling.TopologyKind{ .ring, .grid };
    const bandwidths = [_]usize{ 1, 2, 4 };
    const seeds = [_]u64{ 0, 1, 2 };

    var aggregate = Aggregate{};
    for (populations) |population| {
        for (topologies) |topology| {
            for (bandwidths) |bandwidth| {
                for (seeds) |seed| {
                    aggregate.add(try runWorld(
                        candidate.theta,
                        population,
                        population * 4,
                        topology,
                        2,
                        bandwidth,
                        seed,
                        SplitKind.density_extrapolation.maxRounds(),
                    ));
                }
            }
        }
    }
    return aggregate;
}

fn evaluateRedundancy(candidate: Candidate) !Aggregate {
    const populations = [_]usize{ 32, 64 };
    const ratios = [_]usize{ 1, 2 };
    const topologies = [_]scaling.TopologyKind{ .ring, .grid };
    const bandwidths = [_]usize{ 1, 2, 4 };
    const seeds = [_]u64{ 0, 1, 2 };

    var aggregate = Aggregate{};
    for (populations) |population| {
        for (ratios) |ratio| {
            for (topologies) |topology| {
                for (bandwidths) |bandwidth| {
                    for (seeds) |seed| {
                        aggregate.add(try runWorld(
                            candidate.theta,
                            population,
                            population * ratio,
                            topology,
                            4,
                            bandwidth,
                            seed,
                            SplitKind.redundancy_extrapolation.maxRounds(),
                        ));
                    }
                }
            }
        }
    }
    return aggregate;
}

fn evaluateBandwidth(candidate: Candidate) !Aggregate {
    const populations = [_]usize{ 32, 64 };
    const ratios = [_]usize{ 1, 2 };
    const topologies = [_]scaling.TopologyKind{ .ring, .grid };
    const seeds = [_]u64{ 0, 1, 2 };

    var aggregate = Aggregate{};
    for (populations) |population| {
        for (ratios) |ratio| {
            for (topologies) |topology| {
                for (seeds) |seed| {
                    aggregate.add(try runWorld(
                        candidate.theta,
                        population,
                        population * ratio,
                        topology,
                        2,
                        8,
                        seed,
                        SplitKind.bandwidth_extrapolation.maxRounds(),
                    ));
                }
            }
        }
    }
    return aggregate;
}

fn evaluateTopology(candidate: Candidate) !Aggregate {
    const populations = [_]usize{ 32, 64 };
    const ratios = [_]usize{ 1, 2 };
    const bandwidths = [_]usize{ 1, 2, 4 };
    const seeds = [_]u64{ 0, 1, 2 };

    var aggregate = Aggregate{};
    for (populations) |population| {
        for (ratios) |ratio| {
            for (bandwidths) |bandwidth| {
                for (seeds) |seed| {
                    aggregate.add(try runWorld(
                        candidate.theta,
                        population,
                        population * ratio,
                        .complete,
                        2,
                        bandwidth,
                        seed,
                        SplitKind.topology_extrapolation.maxRounds(),
                    ));
                }
            }
        }
    }
    return aggregate;
}

fn evaluateCompound(candidate: Candidate) !Aggregate {
    const topologies = [_]scaling.TopologyKind{
        .ring,
        .grid,
        .complete,
    };
    const seeds = [_]u64{ 0, 1, 2 };

    var aggregate = Aggregate{};
    for (topologies) |topology| {
        for (seeds) |seed| {
            aggregate.add(try runWorld(
                candidate.theta,
                128,
                512,
                topology,
                4,
                8,
                seed,
                SplitKind.compound_extrapolation.maxRounds(),
            ));
        }
    }
    return aggregate;
}

fn runWorld(
    theta: f3.Theta,
    population: usize,
    facts: usize,
    topology: scaling.TopologyKind,
    redundancy_count: usize,
    bandwidth: usize,
    seed: u64,
    max_rounds: u32,
) !f3.Result {
    return f3.run(
        .{
            .population_size = population,
            .fact_count = facts,
            .topology = topology,
            .redundancy = redundancy_count,
            .bandwidth = bandwidth,
            .seed = seed,
            .max_rounds = max_rounds,
        },
        theta,
    );
}

pub fn computeFrontier(
    candidate_count: usize,
    metrics: *const [max_candidates]Aggregate,
    eligible: *const [max_candidates]bool,
) Frontier {
    var frontier = Frontier{};
    var found = false;

    var i: usize = 0;
    while (i < candidate_count) : (i += 1) {
        if (!eligible[i]) continue;
        if (!found or metrics[i].failures < frontier.min_failures) {
            frontier.min_failures = metrics[i].failures;
            found = true;
        }
    }
    if (!found) return frontier;

    i = 0;
    while (i < candidate_count) : (i += 1) {
        if (!eligible[i]) continue;
        if (metrics[i].failures != frontier.min_failures) continue;

        var dominated = false;
        var j: usize = 0;
        while (j < candidate_count) : (j += 1) {
            if (i == j or !eligible[j]) continue;
            if (metrics[j].failures != frontier.min_failures) continue;
            if (resourceStrictlyDominates(metrics[j], metrics[i])) {
                dominated = true;
                break;
            }
        }

        if (!dominated) {
            frontier.flags[i] = true;
            frontier.count += 1;
        }
    }

    return frontier;
}

pub fn resourceWeaklyDominates(a: Aggregate, b: Aggregate) bool {
    return a.rounds_sum <= b.rounds_sum and
        a.communication_sum <= b.communication_sum and
        a.duplicate_sum <= b.duplicate_sum and
        a.computation_sum <= b.computation_sum and
        a.inference_sum <= b.inference_sum;
}

pub fn resourceStrictlyDominates(a: Aggregate, b: Aggregate) bool {
    if (!resourceWeaklyDominates(a, b)) return false;
    return a.rounds_sum < b.rounds_sum or
        a.communication_sum < b.communication_sum or
        a.duplicate_sum < b.duplicate_sum or
        a.computation_sum < b.computation_sum or
        a.inference_sum < b.inference_sum;
}

pub fn allEligible(candidate_count: usize) [max_candidates]bool {
    var flags = [_]bool{false} ** max_candidates;
    var i: usize = 0;
    while (i < candidate_count) : (i += 1) flags[i] = true;
    return flags;
}

pub fn selectedOrControls(
    candidate_count: usize,
    selected: *const [max_candidates]bool,
) [max_candidates]bool {
    var flags = selected.*;
    var i: usize = 0;
    while (i < @min(candidate_count, @as(usize, 3))) : (i += 1) {
        flags[i] = true;
    }
    return flags;
}

test "Stage 7B deterministic candidate set is canonical and unique" {
    const candidates = generateCandidates();
    try std.testing.expectEqual(
        @as(usize, canonical_candidate_count),
        candidates.len,
    );

    var i: usize = 0;
    while (i < candidates.len) : (i += 1) {
        try candidates.items[i].theta.validate();
        var j: usize = i + 1;
        while (j < candidates.len) : (j += 1) {
            try std.testing.expect(
                !candidates.items[i].theta.eql(candidates.items[j].theta),
            );
        }
    }

    try std.testing.expect(
        candidates.items[0].theta.base.eql(stage7a.round_robin_theta) and
        candidates.items[0].theta.inference_gating_permille == 1000,
    );
    try std.testing.expect(
        candidates.items[1].theta.base.eql(stage7a.seeded_theta) and
        candidates.items[1].theta.inference_gating_permille == 1000,
    );
    try std.testing.expect(
        candidates.items[2].theta.base.eql(stage7a.novel_first_theta) and
        candidates.items[2].theta.inference_gating_permille == 1000,
    );
}

test "Stage 7B frozen split counts are exact" {
    try std.testing.expectEqual(@as(usize, 48), worldCount(.training));
    try std.testing.expectEqual(@as(usize, 24), worldCount(.validation));
    try std.testing.expectEqual(
        @as(usize, 36),
        worldCount(.population_extrapolation),
    );
    try std.testing.expectEqual(
        @as(usize, 36),
        worldCount(.density_extrapolation),
    );
    try std.testing.expectEqual(
        @as(usize, 72),
        worldCount(.redundancy_extrapolation),
    );
    try std.testing.expectEqual(
        @as(usize, 24),
        worldCount(.bandwidth_extrapolation),
    );
    try std.testing.expectEqual(
        @as(usize, 36),
        worldCount(.topology_extrapolation),
    );
    try std.testing.expectEqual(
        @as(usize, 9),
        worldCount(.compound_extrapolation),
    );
}

test "Stage 7B feasibility precedes resource Pareto selection" {
    var metrics = [_]Aggregate{.{}} ** max_candidates;
    var eligible = [_]bool{false} ** max_candidates;

    metrics[0] = .{
        .runs = 10,
        .failures = 0,
        .rounds_sum = 100,
        .communication_sum = 100,
        .duplicate_sum = 20,
        .computation_sum = 100,
        .inference_sum = 100,
    };
    metrics[1] = .{
        .runs = 10,
        .failures = 1,
        .rounds_sum = 1,
        .communication_sum = 1,
        .duplicate_sum = 0,
        .computation_sum = 1,
        .inference_sum = 1,
    };
    metrics[2] = .{
        .runs = 10,
        .failures = 0,
        .rounds_sum = 90,
        .communication_sum = 90,
        .duplicate_sum = 20,
        .computation_sum = 90,
        .inference_sum = 90,
    };
    eligible[0] = true;
    eligible[1] = true;
    eligible[2] = true;

    const frontier = computeFrontier(3, &metrics, &eligible);
    try std.testing.expectEqual(@as(usize, 0), frontier.min_failures);
    try std.testing.expectEqual(@as(usize, 1), frontier.count);
    try std.testing.expect(!frontier.flags[0]);
    try std.testing.expect(!frontier.flags[1]);
    try std.testing.expect(frontier.flags[2]);
}

test "Stage 7B Latin candidate bandwidth utilization stays bounded" {
    const candidates = generateCandidates();
    var i: usize = fixed_candidate_count;
    while (i < candidates.len) : (i += 1) {
        try std.testing.expect(
            candidates.items[i].theta.base.bandwidth_utilization_permille >= 250,
        );
        try std.testing.expect(
            candidates.items[i].theta.base.bandwidth_utilization_permille <= 1000,
        );
    }
}


test "F3 Latin inference gating stays in permille bounds" {
    const candidates = generateCandidates();
    var i: usize = fixed_candidate_count;
    while (i < candidates.len) : (i += 1) {
        try std.testing.expect(
            candidates.items[i].theta.inference_gating_permille <= 1000,
        );
    }
}


test "F3 c=1000 corner candidate set exactly matches Stage 7B" {
    const actual = generateCornerCandidates();
    const expected = reference.generateCandidates();
    try std.testing.expectEqual(expected.len, actual.len);

    var i: usize = 0;
    while (i < expected.len) : (i += 1) {
        try std.testing.expect(
            actual.items[i].theta.base.eql(expected.items[i].theta),
        );
        try std.testing.expectEqual(
            @as(u16, 1000),
            actual.items[i].theta.inference_gating_permille,
        );
    }
}


test "F3 aggregate inference accounting is exact" {
    const candidate = Candidate{
        .id = 0,
        .source = .fixed_profile,
        .label = "accounting_probe",
        .theta = .{
            .base = stage7a.soft_novel_theta,
            .inference_gating_permille = 500,
        },
    };
    const aggregate = try evaluateCandidate(candidate, .validation);
    try std.testing.expectEqual(
        aggregate.computation_sum,
        aggregate.inference_sum + aggregate.cache_reuse_sum,
    );
}
