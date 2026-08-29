const std = @import("std");
const scaling = @import("../stage5/stage5a_scaling.zig");

pub const Error = scaling.Error || error{
    InvalidNovelty,
    InvalidExploration,
    InvalidRetry,
    InvalidBandwidthUtilization,
};

pub const Theta = struct {
    /// Preference for facts not yet emitted in the current local memory epoch.
    novelty_permille: u16 = 0,
    /// Blend between cursor order (0) and Stage-5 seeded order (1000).
    exploration_permille: u16 = 0,
    /// Eligibility of previously emitted facts while unsent facts still exist.
    retry_permille: u16 = 1000,
    /// Fraction of the environment-provided bandwidth budget the policy may use.
    bandwidth_utilization_permille: u16 = 1000,

    pub fn validate(self: Theta) Error!void {
        if (self.novelty_permille > 1000) return error.InvalidNovelty;
        if (self.exploration_permille > 1000) return error.InvalidExploration;
        if (self.retry_permille > 1000) return error.InvalidRetry;
        if (self.bandwidth_utilization_permille < 1 or
            self.bandwidth_utilization_permille > 1000)
        {
            return error.InvalidBandwidthUtilization;
        }
    }

    pub fn baselineKind(self: Theta) ?scaling.PolicyKind {
        if (self.eql(round_robin_theta)) return .round_robin;
        if (self.eql(seeded_theta)) return .seeded;
        if (self.eql(novel_first_theta)) return .novel_first;
        return null;
    }

    pub fn eql(a: Theta, b: Theta) bool {
        return a.novelty_permille == b.novelty_permille and
            a.exploration_permille == b.exploration_permille and
            a.retry_permille == b.retry_permille and
            a.bandwidth_utilization_permille ==
                b.bandwidth_utilization_permille;
    }
};

pub const round_robin_theta = Theta{
    .novelty_permille = 0,
    .exploration_permille = 0,
    .retry_permille = 1000,
    .bandwidth_utilization_permille = 1000,
};

pub const seeded_theta = Theta{
    .novelty_permille = 0,
    .exploration_permille = 1000,
    .retry_permille = 1000,
    .bandwidth_utilization_permille = 1000,
};

pub const novel_first_theta = Theta{
    .novelty_permille = 1000,
    .exploration_permille = 0,
    .retry_permille = 0,
    .bandwidth_utilization_permille = 1000,
};

pub const soft_novel_theta = Theta{
    .novelty_permille = 500,
    .exploration_permille = 0,
    .retry_permille = 250,
    .bandwidth_utilization_permille = 1000,
};

pub const exploratory_novel_theta = Theta{
    .novelty_permille = 750,
    .exploration_permille = 500,
    .retry_permille = 250,
    .bandwidth_utilization_permille = 1000,
};

pub const lean_exploratory_theta = Theta{
    .novelty_permille = 750,
    .exploration_permille = 250,
    .retry_permille = 0,
    .bandwidth_utilization_permille = 500,
};

pub const Profile = struct {
    name: []const u8,
    theta: Theta,
};

pub const probe_profiles = [_]Profile{
    .{ .name = "round_robin_corner", .theta = round_robin_theta },
    .{ .name = "seeded_corner", .theta = seeded_theta },
    .{ .name = "novel_first_corner", .theta = novel_first_theta },
    .{ .name = "soft_novel", .theta = soft_novel_theta },
    .{ .name = "exploratory_novel", .theta = exploratory_novel_theta },
    .{ .name = "lean_exploratory", .theta = lean_exploratory_theta },
};

/// Policy-visible state. Everything here is local or immutable environment
/// metadata. There is no collector state, peer knowledge, global completion
/// flag, global novelty table, or global trajectory state.
pub const Observation = struct {
    state: scaling.State,
    operator_index: usize,
    round: u32,
    population_size: usize,
    fact_count: usize,
    topology: scaling.TopologyKind,
    redundancy: usize,
    max_bandwidth: usize,
    seed: u64,
    local_degree: usize,

    pub fn from(
        state: scaling.State,
        operator_index: usize,
        round: u32,
        config: Config,
    ) Observation {
        return .{
            .state = state,
            .operator_index = operator_index,
            .round = round,
            .population_size = config.population_size,
            .fact_count = config.fact_count,
            .topology = config.topology,
            .redundancy = config.redundancy,
            .max_bandwidth = config.bandwidth,
            .seed = config.seed,
            .local_degree = scaling.topologyDegree(
                config.topology,
                operator_index,
                config.population_size,
            ),
        };
    }
};

pub const Config = struct {
    population_size: usize,
    fact_count: usize,
    topology: scaling.TopologyKind,
    redundancy: usize,
    bandwidth: usize,
    seed: u64 = 0,
    max_rounds: u32 = 4096,

    pub fn validate(self: Config) Error!void {
        try self.asScaling(.round_robin).validate();
    }

    pub fn asScaling(
        self: Config,
        policy: scaling.PolicyKind,
    ) scaling.Config {
        return .{
            .population_size = self.population_size,
            .fact_count = self.fact_count,
            .topology = self.topology,
            .redundancy = self.redundancy,
            .bandwidth = self.bandwidth,
            .policy = policy,
            .seed = self.seed,
            .max_rounds = self.max_rounds,
        };
    }
};

pub const Result = struct {
    config: Config,
    theta: Theta,
    success: bool,
    rounds: u32,
    diameter: usize,
    edges: usize,
    collector_initial_facts: usize,
    collector_final_facts: usize,
    policy_calls: u64,
    actions_proposed: u64,
    rejected_actions: u64,
    messages: u64,
    communication_units: u64,
    useful_deliveries: u64,
    duplicate_deliveries: u64,
    violations: u64,

    pub fn usefulPerThousandUnits(self: Result) u64 {
        if (self.communication_units == 0) return 0;
        return (self.useful_deliveries * 1000) / self.communication_units;
    }

    pub fn duplicatePermille(self: Result) u64 {
        if (self.communication_units == 0) return 0;
        return (self.duplicate_deliveries * 1000) /
            self.communication_units;
    }
};

/// Stage 7A intentionally keeps the objective vector unscalarized.
/// Stage 7B may search Pareto fronts or choose weights without changing the
/// underlying experimental measurements.
pub const ObjectiveVector = struct {
    failure: u8,
    rounds: u32,
    communication_units: u64,
    duplicate_deliveries: u64,
    computation_calls: u64,

    pub fn fromResult(result: Result) ObjectiveVector {
        return .{
            .failure = if (result.success) 0 else 1,
            .rounds = result.rounds,
            .communication_units = result.communication_units,
            .duplicate_deliveries = result.duplicate_deliveries,
            .computation_calls = result.policy_calls,
        };
    }

    pub fn weaklyDominates(a: ObjectiveVector, b: ObjectiveVector) bool {
        return a.failure <= b.failure and
            a.rounds <= b.rounds and
            a.communication_units <= b.communication_units and
            a.duplicate_deliveries <= b.duplicate_deliveries and
            a.computation_calls <= b.computation_calls;
    }

    pub fn strictlyDominates(a: ObjectiveVector, b: ObjectiveVector) bool {
        if (!a.weaklyDominates(b)) return false;
        return a.failure < b.failure or
            a.rounds < b.rounds or
            a.communication_units < b.communication_units or
            a.duplicate_deliveries < b.duplicate_deliveries or
            a.computation_calls < b.computation_calls;
    }
};

const Candidate = struct {
    fact: u16,
    score: i64,
};

pub fn effectiveBandwidth(max_bandwidth: usize, theta: Theta) usize {
    std.debug.assert(max_bandwidth >= 1);
    std.debug.assert(theta.bandwidth_utilization_permille >= 1);
    const numerator =
        max_bandwidth *
        @as(usize, theta.bandwidth_utilization_permille);
    const result = (numerator + 999) / 1000;
    return @min(max_bandwidth, @max(@as(usize, 1), result));
}

pub fn decide(
    theta: Theta,
    observation: Observation,
) ?scaling.Action {
    theta.validate() catch return null;

    // The named policies are exact control corners, not approximate rewrites.
    // Delegation guarantees byte-for-byte action semantics at those points.
    if (theta.baselineKind()) |policy| {
        return scaling.decideLocal(
            policy,
            observation.state,
            observation.operator_index,
            observation.round,
            observationConfig(observation, policy),
        );
    }

    const state = observation.state;
    if (state.knowledge.count(observation.fact_count) == 0) return null;

    const bandwidth = effectiveBandwidth(
        observation.max_bandwidth,
        theta,
    );
    const has_unsent = state.knowledge.hasDifference(
        state.sent,
        observation.fact_count,
    );

    var seeded_ranks: [scaling.max_facts]u16 = undefined;
    if (theta.exploration_permille != 0) {
        fillSeededRanks(
            &seeded_ranks,
            observation.operator_index,
            observation.round,
            observation.fact_count,
            observation.seed,
        );
    }

    var heap: [scaling.max_facts]Candidate = undefined;
    var heap_len: usize = 0;

    const cursor_start =
        @as(usize, @intCast(state.cursor)) % observation.fact_count;
    const novelty_scale =
        @as(i64, @intCast(scaling.max_facts + 1));
    const exploration =
        @as(i64, theta.exploration_permille);
    const cursor_weight = 1000 - exploration;

    var fact: usize = 0;
    while (fact < observation.fact_count) : (fact += 1) {
        if (!state.knowledge.has(fact)) continue;

        const was_sent = state.sent.has(fact);
        if (has_unsent and was_sent and
            !retryAllows(theta, observation, fact))
        {
            continue;
        }

        const cursor_rank = (fact + observation.fact_count - cursor_start) %
            observation.fact_count;
        const seeded_rank: usize = if (theta.exploration_permille == 0)
            cursor_rank
        else
            @as(usize, @intCast(seeded_ranks[fact]));

        var score =
            cursor_weight * @as(i64, @intCast(cursor_rank)) +
            exploration * @as(i64, @intCast(seeded_rank));

        if (!was_sent) {
            score -=
                @as(i64, theta.novelty_permille) *
                novelty_scale;
        }

        keepBest(
            &heap,
            &heap_len,
            bandwidth,
            .{ .fact = @intCast(fact), .score = score },
        );
    }

    if (heap_len == 0 and has_unsent) {
        // Intermediate retry gating can theoretically exclude every sent fact
        // while all unsent facts are absent from the traversal due to future
        // policy extensions. Fall back only to currently unsent local facts.
        fact = 0;
        while (fact < observation.fact_count) : (fact += 1) {
            if (!state.knowledge.has(fact) or state.sent.has(fact)) continue;
            const cursor_rank =
                (fact + observation.fact_count - cursor_start) %
                observation.fact_count;
            keepBest(
                &heap,
                &heap_len,
                bandwidth,
                .{
                    .fact = @intCast(fact),
                    .score = @intCast(cursor_rank),
                },
            );
        }
    }

    if (heap_len == 0) return null;

    var selected = scaling.BitSet{};
    var next_cursor = cursor_start;
    var worst_cursor_rank: usize = 0;
    var i: usize = 0;
    while (i < heap_len) : (i += 1) {
        const selected_fact = @as(usize, heap[i].fact);
        selected.set(selected_fact);
        const rank =
            (selected_fact + observation.fact_count - cursor_start) %
            observation.fact_count;
        if (i == 0 or rank >= worst_cursor_rank) {
            worst_cursor_rank = rank;
            next_cursor = (selected_fact + 1) % observation.fact_count;
        }
    }

    return .{
        .facts = selected,
        .selected = @intCast(heap_len),
        .next_cursor = @intCast(next_cursor),
        .reset_sent = false,
    };
}

fn observationConfig(
    observation: Observation,
    policy: scaling.PolicyKind,
) scaling.Config {
    return .{
        .population_size = observation.population_size,
        .fact_count = observation.fact_count,
        .topology = observation.topology,
        .redundancy = observation.redundancy,
        .bandwidth = observation.max_bandwidth,
        .policy = policy,
        .seed = observation.seed,
        .max_rounds = 1,
    };
}

fn retryAllows(
    theta: Theta,
    observation: Observation,
    fact: usize,
) bool {
    if (theta.retry_permille == 1000) return true;
    if (theta.retry_permille == 0) return false;

    const key =
        observation.seed ^
        (@as(u64, @intCast(observation.operator_index)) *%
            0x9e3779b97f4a7c15) ^
        (@as(u64, observation.round) *%
            0xbf58476d1ce4e5b9) ^
        (@as(u64, @intCast(fact)) *%
            0x94d049bb133111eb) ^
        0x52455452595f3741;
    return (mix64(key) % 1000) <
        @as(u64, theta.retry_permille);
}

fn fillSeededRanks(
    ranks: *[scaling.max_facts]u16,
    operator_index: usize,
    round: u32,
    fact_count: usize,
    seed: u64,
) void {
    const salt =
        seed ^
        (@as(u64, @intCast(operator_index)) *%
            0x9e3779b97f4a7c15) ^
        (@as(u64, round) *%
            0xbf58476d1ce4e5b9);
    const start = @as(
        usize,
        @intCast(mix64(salt) % @as(u64, @intCast(fact_count))),
    );

    var step: usize = 1;
    if (fact_count > 2) {
        step = 1 + @as(usize, @intCast(
            mix64(salt ^ 0x94d049bb133111eb) %
                @as(u64, @intCast(fact_count - 1)),
        ));
        while (gcd(step, fact_count) != 1) {
            step += 1;
            if (step >= fact_count) step = 1;
        }
    }

    var rank: usize = 0;
    while (rank < fact_count) : (rank += 1) {
        const fact = (start + rank * step) % fact_count;
        ranks[fact] = @intCast(rank);
    }
}

fn keepBest(
    heap: *[scaling.max_facts]Candidate,
    heap_len: *usize,
    capacity: usize,
    candidate: Candidate,
) void {
    std.debug.assert(capacity >= 1);

    if (heap_len.* < capacity) {
        const index = heap_len.*;
        heap[index] = candidate;
        heap_len.* += 1;
        siftUpWorst(heap, index);
        return;
    }

    // Max-heap root is the worst currently retained candidate.
    if (!candidateBetter(candidate, heap[0])) return;
    heap[0] = candidate;
    siftDownWorst(heap, heap_len.*, 0);
}

fn candidateBetter(a: Candidate, b: Candidate) bool {
    if (a.score != b.score) return a.score < b.score;
    return a.fact < b.fact;
}

fn candidateWorse(a: Candidate, b: Candidate) bool {
    if (a.score != b.score) return a.score > b.score;
    return a.fact > b.fact;
}

fn siftUpWorst(
    heap: *[scaling.max_facts]Candidate,
    start_index: usize,
) void {
    var index = start_index;
    while (index > 0) {
        const parent = (index - 1) / 2;
        if (!candidateWorse(heap[index], heap[parent])) break;
        const tmp = heap[parent];
        heap[parent] = heap[index];
        heap[index] = tmp;
        index = parent;
    }
}

fn siftDownWorst(
    heap: *[scaling.max_facts]Candidate,
    heap_len: usize,
    start_index: usize,
) void {
    var index = start_index;
    while (true) {
        const left = index * 2 + 1;
        if (left >= heap_len) break;
        const right = left + 1;
        var worst = left;
        if (right < heap_len and
            candidateWorse(heap[right], heap[left]))
        {
            worst = right;
        }
        if (!candidateWorse(heap[worst], heap[index])) break;
        const tmp = heap[index];
        heap[index] = heap[worst];
        heap[worst] = tmp;
        index = worst;
    }
}

pub fn run(config: Config, theta: Theta) Error!Result {
    try config.validate();
    try theta.validate();

    if (theta.baselineKind()) |policy| {
        const baseline = try scaling.run(config.asScaling(policy));
        return fromScalingResult(config, theta, baseline);
    }

    var states = [_]scaling.State{.{}} ** scaling.max_operators;
    scaling.initializeStates(
        &states,
        config.asScaling(.round_robin),
    );

    const initial_facts =
        states[scaling.collector_index].knowledge.count(config.fact_count);
    var result = initialResult(config, theta, initial_facts);
    if (result.success) return result;

    var round: u32 = 1;
    while (round <= config.max_rounds) : (round += 1) {
        var actions =
            [_]?scaling.Action{null} ** scaling.max_operators;
        var received =
            [_]scaling.BitSet{.{}} ** scaling.max_operators;

        var operator_index: usize = 0;
        while (operator_index < config.population_size) :
            (operator_index += 1)
        {
            result.policy_calls +%= 1;
            const observation = Observation.from(
                states[operator_index],
                operator_index,
                round,
                config,
            );
            if (decide(theta, observation)) |action| {
                actions[operator_index] = action;
                result.actions_proposed +%= 1;
            }
        }

        var sender: usize = 0;
        while (sender < config.population_size) : (sender += 1) {
            const action = actions[sender] orelse continue;
            if (!scaling.validateLocalAction(
                action,
                states[sender],
                config.asScaling(.round_robin),
            )) {
                result.rejected_actions +%= 1;
                result.violations +%= 1;
                continue;
            }

            if (action.reset_sent) states[sender].sent.clear();
            states[sender].sent.unionWithFacts(
                action.facts,
                config.fact_count,
            );
            states[sender].cursor = action.next_cursor;

            switch (config.topology) {
                .ring => {
                    const left =
                        (sender + config.population_size - 1) %
                        config.population_size;
                    const right =
                        (sender + 1) % config.population_size;
                    deliver(
                        action,
                        states[left].knowledge,
                        &received[left],
                        &result,
                    );
                    if (right != left) {
                        deliver(
                            action,
                            states[right].knowledge,
                            &received[right],
                            &result,
                        );
                    }
                },
                .complete => {
                    var recipient: usize = 0;
                    while (recipient < config.population_size) :
                        (recipient += 1)
                    {
                        if (recipient == sender) continue;
                        deliver(
                            action,
                            states[recipient].knowledge,
                            &received[recipient],
                            &result,
                        );
                    }
                },
                .grid => {
                    const width =
                        scaling.gridWidth(config.population_size);
                    const row = sender / width;
                    const col = sender % width;

                    if (col > 0) {
                        const recipient = sender - 1;
                        deliver(
                            action,
                            states[recipient].knowledge,
                            &received[recipient],
                            &result,
                        );
                    }
                    if (col + 1 < width and
                        sender + 1 < config.population_size)
                    {
                        const recipient = sender + 1;
                        if (recipient / width == row) {
                            deliver(
                                action,
                                states[recipient].knowledge,
                                &received[recipient],
                                &result,
                            );
                        }
                    }
                    if (sender >= width) {
                        const recipient = sender - width;
                        deliver(
                            action,
                            states[recipient].knowledge,
                            &received[recipient],
                            &result,
                        );
                    }
                    if (sender + width < config.population_size) {
                        const recipient = sender + width;
                        deliver(
                            action,
                            states[recipient].knowledge,
                            &received[recipient],
                            &result,
                        );
                    }
                },
            }
        }

        operator_index = 0;
        while (operator_index < config.population_size) :
            (operator_index += 1)
        {
            states[operator_index].knowledge.unionWithFacts(
                received[operator_index],
                config.fact_count,
            );
        }

        result.rounds = round;
        result.collector_final_facts =
            states[scaling.collector_index].knowledge.count(
                config.fact_count,
            );
        if (states[scaling.collector_index].knowledge.containsAll(
            config.fact_count,
        )) {
            result.success = true;
            break;
        }
    }

    std.debug.assert(
        result.communication_units ==
            result.useful_deliveries + result.duplicate_deliveries,
    );
    return result;
}

fn fromScalingResult(
    config: Config,
    theta: Theta,
    baseline: scaling.Result,
) Result {
    return .{
        .config = config,
        .theta = theta,
        .success = baseline.success,
        .rounds = baseline.rounds,
        .diameter = baseline.diameter,
        .edges = baseline.edges,
        .collector_initial_facts = baseline.collector_initial_facts,
        .collector_final_facts = baseline.collector_final_facts,
        .policy_calls = baseline.policy_calls,
        .actions_proposed = baseline.actions_proposed,
        .rejected_actions = baseline.rejected_actions,
        .messages = baseline.messages,
        .communication_units = baseline.communication_units,
        .useful_deliveries = baseline.useful_deliveries,
        .duplicate_deliveries = baseline.duplicate_deliveries,
        .violations = baseline.violations,
    };
}

fn initialResult(
    config: Config,
    theta: Theta,
    initial_facts: usize,
) Result {
    return .{
        .config = config,
        .theta = theta,
        .success = initial_facts == config.fact_count,
        .rounds = 0,
        .diameter = scaling.topologyDiameter(
            config.topology,
            config.population_size,
        ),
        .edges = scaling.topologyEdges(
            config.topology,
            config.population_size,
        ),
        .collector_initial_facts = initial_facts,
        .collector_final_facts = initial_facts,
        .policy_calls = 0,
        .actions_proposed = 0,
        .rejected_actions = 0,
        .messages = 0,
        .communication_units = 0,
        .useful_deliveries = 0,
        .duplicate_deliveries = 0,
        .violations = 0,
    };
}

fn deliver(
    action: scaling.Action,
    snapshot_knowledge: scaling.BitSet,
    received: *scaling.BitSet,
    result: *Result,
) void {
    result.messages +%= 1;
    result.communication_units +%=
        @as(u64, @intCast(action.selected));

    const words = (result.config.fact_count + 63) / 64;
    const remainder = result.config.fact_count % 64;
    const tail_mask = if (remainder == 0)
        ~@as(u64, 0)
    else
        (@as(u64, 1) << @intCast(remainder)) - 1;

    var word_index: usize = 0;
    while (word_index < words) : (word_index += 1) {
        var action_word = action.facts.words[word_index];
        if (word_index + 1 == words) action_word &= tail_mask;
        if (action_word == 0) continue;

        const already_known =
            snapshot_knowledge.words[word_index] |
            received.words[word_index];
        const useful_bits = action_word & ~already_known;
        const duplicate_bits = action_word & already_known;

        result.useful_deliveries +%=
            @as(u64, @intCast(@popCount(useful_bits)));
        result.duplicate_deliveries +%=
            @as(u64, @intCast(@popCount(duplicate_bits)));
        received.words[word_index] |= action_word;
    }
}

fn gcd(a_input: usize, b_input: usize) usize {
    var a = a_input;
    var b = b_input;
    while (b != 0) {
        const t = a % b;
        a = b;
        b = t;
    }
    return a;
}

fn mix64(input: u64) u64 {
    var z = input +% 0x9e3779b97f4a7c15;
    z = (z ^ (z >> 30)) *% 0xbf58476d1ce4e5b9;
    z = (z ^ (z >> 27)) *% 0x94d049bb133111eb;
    return z ^ (z >> 31);
}

fn expectSameAction(
    a: ?scaling.Action,
    b: ?scaling.Action,
) !void {
    try std.testing.expectEqual(a == null, b == null);
    if (a == null) return;
    try std.testing.expect(
        scaling.BitSet.eql(a.?.facts, b.?.facts),
    );
    try std.testing.expectEqual(a.?.selected, b.?.selected);
    try std.testing.expectEqual(a.?.next_cursor, b.?.next_cursor);
    try std.testing.expectEqual(a.?.reset_sent, b.?.reset_sent);
}

test "Stage 7A theta corners map to named baselines" {
    try std.testing.expectEqual(
        scaling.PolicyKind.round_robin,
        round_robin_theta.baselineKind().?,
    );
    try std.testing.expectEqual(
        scaling.PolicyKind.seeded,
        seeded_theta.baselineKind().?,
    );
    try std.testing.expectEqual(
        scaling.PolicyKind.novel_first,
        novel_first_theta.baselineKind().?,
    );
    try std.testing.expect(
        soft_novel_theta.baselineKind() == null,
    );
}

test "Stage 7A baseline corner actions are exact" {
    const config = Config{
        .population_size = 8,
        .fact_count = 16,
        .topology = .ring,
        .redundancy = 2,
        .bandwidth = 3,
        .seed = 42,
        .max_rounds = 64,
    };

    var state = scaling.State{};
    state.knowledge.set(1);
    state.knowledge.set(3);
    state.knowledge.set(5);
    state.knowledge.set(9);
    state.sent.set(1);
    state.sent.set(5);
    state.cursor = 3;

    const fixtures = [_]struct {
        theta: Theta,
        policy: scaling.PolicyKind,
    }{
        .{ .theta = round_robin_theta, .policy = .round_robin },
        .{ .theta = seeded_theta, .policy = .seeded },
        .{ .theta = novel_first_theta, .policy = .novel_first },
    };

    for (fixtures) |fixture| {
        var round: u32 = 1;
        while (round <= 4) : (round += 1) {
            const observation = Observation.from(
                state,
                2,
                round,
                config,
            );
            try expectSameAction(
                scaling.decideLocal(
                    fixture.policy,
                    state,
                    2,
                    round,
                    config.asScaling(fixture.policy),
                ),
                decide(fixture.theta, observation),
            );
        }
    }
}

test "Stage 7A baseline corner runs preserve Stage 5A results" {
    const topologies = [_]scaling.TopologyKind{ .ring, .grid };
    const fixtures = [_]struct {
        theta: Theta,
        policy: scaling.PolicyKind,
    }{
        .{ .theta = round_robin_theta, .policy = .round_robin },
        .{ .theta = seeded_theta, .policy = .seeded },
        .{ .theta = novel_first_theta, .policy = .novel_first },
    };

    for (topologies) |topology| {
        for (fixtures) |fixture| {
            const config = Config{
                .population_size = 32,
                .fact_count = 64,
                .topology = topology,
                .redundancy = 2,
                .bandwidth = 2,
                .seed = 1,
                .max_rounds = 1024,
            };
            const expected = try scaling.run(
                config.asScaling(fixture.policy),
            );
            const actual = try run(config, fixture.theta);

            try std.testing.expectEqual(expected.success, actual.success);
            try std.testing.expectEqual(expected.rounds, actual.rounds);
            try std.testing.expectEqual(
                expected.collector_final_facts,
                actual.collector_final_facts,
            );
            try std.testing.expectEqual(
                expected.communication_units,
                actual.communication_units,
            );
            try std.testing.expectEqual(
                expected.useful_deliveries,
                actual.useful_deliveries,
            );
            try std.testing.expectEqual(
                expected.duplicate_deliveries,
                actual.duplicate_deliveries,
            );
            try std.testing.expectEqual(
                expected.violations,
                actual.violations,
            );
        }
    }
}

test "Stage 7A bandwidth utilization is bounded and ceil-rounded" {
    try std.testing.expectEqual(
        @as(usize, 1),
        effectiveBandwidth(
            4,
            .{ .bandwidth_utilization_permille = 1 },
        ),
    );
    try std.testing.expectEqual(
        @as(usize, 2),
        effectiveBandwidth(
            4,
            .{ .bandwidth_utilization_permille = 500 },
        ),
    );
    try std.testing.expectEqual(
        @as(usize, 4),
        effectiveBandwidth(4, .{}),
    );
}

test "Stage 7A retry zero excludes sent facts while novel facts remain" {
    const config = Config{
        .population_size = 4,
        .fact_count = 8,
        .topology = .ring,
        .redundancy = 1,
        .bandwidth = 4,
    };
    var state = scaling.State{};
    state.knowledge.set(0);
    state.knowledge.set(1);
    state.knowledge.set(2);
    state.sent.set(0);
    state.sent.set(1);

    const theta = Theta{
        .novelty_permille = 500,
        .exploration_permille = 250,
        .retry_permille = 0,
        .bandwidth_utilization_permille = 1000,
    };
    const action = decide(
        theta,
        Observation.from(state, 0, 1, config),
    ).?;

    try std.testing.expect(!action.facts.has(0));
    try std.testing.expect(!action.facts.has(1));
    try std.testing.expect(action.facts.has(2));
}

test "Stage 7A interior policy is deterministic" {
    const config = Config{
        .population_size = 32,
        .fact_count = 64,
        .topology = .grid,
        .redundancy = 2,
        .bandwidth = 2,
        .seed = 7,
        .max_rounds = 512,
    };
    const a = try run(config, exploratory_novel_theta);
    const b = try run(config, exploratory_novel_theta);

    try std.testing.expectEqual(a.success, b.success);
    try std.testing.expectEqual(a.rounds, b.rounds);
    try std.testing.expectEqual(
        a.communication_units,
        b.communication_units,
    );
    try std.testing.expectEqual(
        a.useful_deliveries,
        b.useful_deliveries,
    );
    try std.testing.expectEqual(
        a.duplicate_deliveries,
        b.duplicate_deliveries,
    );
}

test "Stage 7A objective dominance keeps dimensions separate" {
    const a = ObjectiveVector{
        .failure = 0,
        .rounds = 10,
        .communication_units = 100,
        .duplicate_deliveries = 20,
        .computation_calls = 50,
    };
    const b = ObjectiveVector{
        .failure = 0,
        .rounds = 12,
        .communication_units = 120,
        .duplicate_deliveries = 20,
        .computation_calls = 50,
    };
    try std.testing.expect(a.strictlyDominates(b));
    try std.testing.expect(!b.weaklyDominates(a));
}
