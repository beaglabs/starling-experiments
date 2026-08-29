const std = @import("std");
const stage7a = @import("../substrate/stage7/stage7a_policy.zig");
const scaling = @import("../substrate/stage5/stage5a_scaling.zig");

pub const Error = stage7a.Error || error{InvalidInferenceGate};

pub const Theta = struct {
    base: stage7a.Theta,
    inference_gating_permille: u16 = 1000,

    pub fn validate(self: Theta) Error!void {
        try self.base.validate();
        if (self.inference_gating_permille > 1000) {
            return error.InvalidInferenceGate;
        }
    }

    pub fn eql(a: Theta, b: Theta) bool {
        return a.base.eql(b.base) and
            a.inference_gating_permille == b.inference_gating_permille;
    }
};

pub const Result = struct {
    config: stage7a.Config,
    theta: Theta,
    success: bool,
    rounds: u32,
    diameter: usize,
    edges: usize,
    collector_initial_facts: usize,
    collector_final_facts: usize,
    policy_calls: u64,
    inference_units: u64,
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

pub fn run(config: stage7a.Config, theta: Theta) Error!Result {
    try config.validate();
    try theta.validate();

    if (theta.inference_gating_permille == 1000) {
        const baseline = try stage7a.run(config, theta.base);
        return fromBaseline(baseline, theta);
    }

    var states = [_]scaling.State{.{}} ** scaling.max_operators;
    scaling.initializeStates(&states, config.asScaling(.round_robin));

    const initial_facts =
        states[scaling.collector_index].knowledge.count(config.fact_count);
    var result = initialResult(config, theta, initial_facts);
    if (result.success) return result;

    var cache_initialized =
        [_]bool{false} ** scaling.max_operators;
    var cached_actions =
        [_]?scaling.Action{null} ** scaling.max_operators;

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

            const refresh =
                !cache_initialized[operator_index] or
                inferenceAllows(
                    theta.inference_gating_permille,
                    config.seed,
                    operator_index,
                    round,
                );

            if (refresh) {
                const observation = stage7a.Observation.from(
                    states[operator_index],
                    operator_index,
                    round,
                    config,
                );
                cached_actions[operator_index] =
                    stage7a.decide(theta.base, observation);
                cache_initialized[operator_index] = true;
                result.inference_units +%= 1;
            }

            if (cached_actions[operator_index]) |action| {
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
    std.debug.assert(result.inference_units <= result.policy_calls);
    return result;
}

fn fromBaseline(baseline: stage7a.Result, theta: Theta) Result {
    return .{
        .config = baseline.config,
        .theta = theta,
        .success = baseline.success,
        .rounds = baseline.rounds,
        .diameter = baseline.diameter,
        .edges = baseline.edges,
        .collector_initial_facts = baseline.collector_initial_facts,
        .collector_final_facts = baseline.collector_final_facts,
        .policy_calls = baseline.policy_calls,
        .inference_units = baseline.policy_calls,
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
    config: stage7a.Config,
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
        .inference_units = 0,
        .actions_proposed = 0,
        .rejected_actions = 0,
        .messages = 0,
        .communication_units = 0,
        .useful_deliveries = 0,
        .duplicate_deliveries = 0,
        .violations = 0,
    };
}

fn inferenceAllows(
    gating_permille: u16,
    seed: u64,
    operator_index: usize,
    round: u32,
) bool {
    if (gating_permille == 1000) return true;
    if (gating_permille == 0) return false;

    const key =
        seed ^
        (@as(u64, @intCast(operator_index)) *%
            0x9e3779b97f4a7c15) ^
        (@as(u64, round) *%
            0xbf58476d1ce4e5b9) ^
        0x494e4645525f4633;
    return (mix64(key) % 1000) < @as(u64, gating_permille);
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

fn mix64(input: u64) u64 {
    var z = input +% 0x9e3779b97f4a7c15;
    z = (z ^ (z >> 30)) *% 0xbf58476d1ce4e5b9;
    z = (z ^ (z >> 27)) *% 0x94d049bb133111eb;
    return z ^ (z >> 31);
}

fn expectSame(a: Result, b: stage7a.Result) !void {
    try std.testing.expectEqual(b.success, a.success);
    try std.testing.expectEqual(b.rounds, a.rounds);
    try std.testing.expectEqual(b.collector_final_facts, a.collector_final_facts);
    try std.testing.expectEqual(b.policy_calls, a.policy_calls);
    try std.testing.expectEqual(b.actions_proposed, a.actions_proposed);
    try std.testing.expectEqual(b.rejected_actions, a.rejected_actions);
    try std.testing.expectEqual(b.messages, a.messages);
    try std.testing.expectEqual(b.communication_units, a.communication_units);
    try std.testing.expectEqual(b.useful_deliveries, a.useful_deliveries);
    try std.testing.expectEqual(b.duplicate_deliveries, a.duplicate_deliveries);
    try std.testing.expectEqual(b.violations, a.violations);
    try std.testing.expectEqual(b.policy_calls, a.inference_units);
}

test "F3 c=1000 delegates exactly to Stage 7A" {
    const profiles = [_]stage7a.Theta{
        stage7a.round_robin_theta,
        stage7a.seeded_theta,
        stage7a.novel_first_theta,
        stage7a.soft_novel_theta,
        stage7a.exploratory_novel_theta,
        stage7a.lean_exploratory_theta,
    };
    const topologies = [_]scaling.TopologyKind{ .ring, .grid };

    for (profiles) |base| {
        for (topologies) |topology| {
            var seed: u64 = 0;
            while (seed < 3) : (seed += 1) {
                const config = stage7a.Config{
                    .population_size = 8,
                    .fact_count = 32,
                    .topology = topology,
                    .redundancy = 2,
                    .bandwidth = 2,
                    .seed = seed,
                    .max_rounds = 4096,
                };
                const actual = try run(config, .{
                    .base = base,
                    .inference_gating_permille = 1000,
                });
                const expected = try stage7a.run(config, base);
                try expectSame(actual, expected);
            }
        }
    }
}

test "F3 gated inference never exceeds policy calls" {
    const config = stage7a.Config{
        .population_size = 8,
        .fact_count = 32,
        .topology = .ring,
        .redundancy = 2,
        .bandwidth = 2,
        .seed = 0,
        .max_rounds = 256,
    };
    const result = try run(config, .{
        .base = stage7a.soft_novel_theta,
        .inference_gating_permille = 250,
    });
    try std.testing.expect(result.inference_units <= result.policy_calls);
    try std.testing.expect(result.inference_units > 0);
}
