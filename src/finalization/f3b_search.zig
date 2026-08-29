const std = @import("std");
const control = @import("f3b_state_control.zig");
const reference = @import("f3_stage7b_reference.zig");
const stage7a = @import("../substrate/stage7/stage7a_policy.zig");
const scaling = @import("../substrate/stage5/stage5a_scaling.zig");

pub const frozen_base_ids = [_]usize{ 37, 51, 93 };
pub const candidate_count: usize =
    frozen_base_ids.len * control.controller_kinds.len;
pub const max_candidates: usize = 32;

pub const Candidate = struct {
    id: usize,
    base_id: usize,
    theta: stage7a.Theta,
    controller: control.ControllerKind,

    pub fn label(self: Candidate) []const u8 {
        return self.controller.name();
    }

    pub fn isBaseline(self: Candidate) bool {
        return self.controller == .always_refresh;
    }
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

    refresh_first: u64 = 0,
    refresh_always: u64 = 0,
    refresh_knowledge: u64 = 0,
    refresh_invalid_action: u64 = 0,
    refresh_stale_action: u64 = 0,
    refresh_age: u64 = 0,

    pub fn add(self: *Aggregate, result: control.Result) void {
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
        self.refresh_first +%= result.refresh_first;
        self.refresh_always +%= result.refresh_always;
        self.refresh_knowledge +%= result.refresh_knowledge;
        self.refresh_invalid_action +%= result.refresh_invalid_action;
        self.refresh_stale_action +%= result.refresh_stale_action;
        self.refresh_age +%= result.refresh_age;
    }

    pub fn inferenceAccounted(self: Aggregate) bool {
        return self.computation_sum ==
            self.inference_sum + self.cache_reuse_sum and
            self.inference_sum ==
                self.refresh_first +
                    self.refresh_always +
                    self.refresh_knowledge +
                    self.refresh_invalid_action +
                    self.refresh_stale_action +
                    self.refresh_age;
    }

    pub fn communicationAccounted(self: Aggregate) bool {
        return self.communication_sum ==
            self.useful_sum + self.duplicate_sum;
    }
};

pub const Frontier = struct {
    flags: [max_candidates]bool = [_]bool{false} ** max_candidates,
    count: usize = 0,
    min_failures: usize = 0,
};

pub fn generateCandidates() CandidateSet {
    const historical = reference.generateCandidates();
    var set = CandidateSet{};

    for (frozen_base_ids) |base_id| {
        const theta = historical.items[base_id].theta;
        for (control.controller_kinds) |controller| {
            set.items[set.len] = .{
                .id = set.len,
                .base_id = base_id,
                .theta = theta,
                .controller = controller,
            };
            set.len += 1;
        }
    }

    return set;
}

pub fn baselineIndexForBase(
    candidates: *const CandidateSet,
    base_id: usize,
) ?usize {
    var i: usize = 0;
    while (i < candidates.len) : (i += 1) {
        if (candidates.items[i].base_id == base_id and
            candidates.items[i].isBaseline())
        {
            return i;
        }
    }
    return null;
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
                        const seeds = [_]u64{ 0, 1 };
                        for (seeds) |seed| {
                            aggregate.add(try runWorld(
                                candidate,
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
                            candidate,
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
                        candidate,
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
                        candidate,
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
                            candidate,
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
                        candidate,
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
                        candidate,
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
                candidate,
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
    candidate: Candidate,
    population: usize,
    facts: usize,
    topology: scaling.TopologyKind,
    redundancy: usize,
    bandwidth: usize,
    seed: u64,
    max_rounds: u32,
) !control.Result {
    return control.run(
        .{
            .population_size = population,
            .fact_count = facts,
            .topology = topology,
            .redundancy = redundancy,
            .bandwidth = bandwidth,
            .seed = seed,
            .max_rounds = max_rounds,
        },
        candidate.theta,
        candidate.controller,
    );
}

pub fn computeFrontier(
    candidate_count_value: usize,
    metrics: *const [max_candidates]Aggregate,
    eligible: *const [max_candidates]bool,
) Frontier {
    var frontier = Frontier{};
    var found = false;

    var i: usize = 0;
    while (i < candidate_count_value) : (i += 1) {
        if (!eligible[i]) continue;
        if (!found or metrics[i].failures < frontier.min_failures) {
            frontier.min_failures = metrics[i].failures;
            found = true;
        }
    }
    if (!found) return frontier;

    i = 0;
    while (i < candidate_count_value) : (i += 1) {
        if (!eligible[i]) continue;
        if (metrics[i].failures != frontier.min_failures) continue;

        var dominated = false;
        var j: usize = 0;
        while (j < candidate_count_value) : (j += 1) {
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

pub fn allEligible(count: usize) [max_candidates]bool {
    var flags = [_]bool{false} ** max_candidates;
    var i: usize = 0;
    while (i < count) : (i += 1) flags[i] = true;
    return flags;
}

pub fn selectedOrBaselines(
    candidates: *const CandidateSet,
    selected: *const [max_candidates]bool,
) [max_candidates]bool {
    var flags = selected.*;
    var i: usize = 0;
    while (i < candidates.len) : (i += 1) {
        if (candidates.items[i].isBaseline()) flags[i] = true;
    }
    return flags;
}

test "F3b candidate set is exactly three frozen bases by five controllers" {
    const candidates = generateCandidates();
    try std.testing.expectEqual(@as(usize, candidate_count), candidates.len);

    var baselines: usize = 0;
    for (candidates.slice()) |candidate| {
        if (candidate.isBaseline()) baselines += 1;
        try candidate.theta.validate();
    }
    try std.testing.expectEqual(@as(usize, 3), baselines);
}

test "F3b paired baselines preserve frozen base theta" {
    const candidates = generateCandidates();
    const historical = reference.generateCandidates();

    for (frozen_base_ids) |base_id| {
        const baseline_index =
            baselineIndexForBase(&candidates, base_id) orelse unreachable;
        try std.testing.expect(
            candidates.items[baseline_index].theta.eql(
                historical.items[base_id].theta,
            ),
        );
    }
}

test "F3b frozen split counts remain exact" {
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

test "F3b feasibility precedes resource Pareto selection" {
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
        .rounds_sum = 100,
        .communication_sum = 100,
        .duplicate_sum = 20,
        .computation_sum = 100,
        .inference_sum = 50,
    };
    eligible[0] = true;
    eligible[1] = true;
    eligible[2] = true;

    const frontier = computeFrontier(3, &metrics, &eligible);
    try std.testing.expectEqual(@as(usize, 0), frontier.min_failures);
    try std.testing.expectEqual(@as(usize, 1), frontier.count);
    try std.testing.expect(frontier.flags[2]);
}
