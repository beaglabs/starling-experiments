const std = @import("std");
const stage7a = @import("../substrate/stage7/stage7a_policy.zig");
const scaling = @import("../substrate/stage5/stage5a_scaling.zig");

pub const Error = stage7a.Error || error{
    InvalidInferenceGate,
};

pub const Theta = struct {
    policy: stage7a.Theta,
    inference_gate_permille: u16,

    pub fn validate(self: Theta) Error!void {
        try self.policy.validate();
        if (self.inference_gate_permille > 1000) {
            return error.InvalidInferenceGate;
        }
    }
};

pub const Result = struct {
    config: stage7a.Config,
    theta: Theta,
    success: bool,
    rounds: u32,
    collector_initial_facts: usize,
    collector_final_facts: usize,
    policy_calls: u64,
    inference_units: u64,
    cache_hits: u64,
    actions_proposed: u64,
    rejected_actions: u64,
    messages: u64,
    communication_units: u64,
    useful_deliveries: u64,
    duplicate_deliveries: u64,
    violations: u64,

    pub fn inferenceSaved(self: Result) u64 {
        return self.policy_calls - self.inference_units;
    }
};

pub fn refreshEligible(
    seed: u64,
    operator_index: usize,
    local_round: u32,
    gate_permille: u16,
) bool {
    if (gate_permille == 1000) return true;
    if (gate_permille == 0) return false;
    const key =
        seed ^
        (@as(u64, @intCast(operator_index)) *% 0x9e3779b97f4a7c15) ^
        (@as(u64, local_round) *% 0xbf58476d1ce4e5b9) ^
        0x494e4645525f4633;
    return (mix64(key) % 1000) < @as(u64, gate_permille);
}

pub fn run(config: stage7a.Config, theta: Theta) Error!Result {
    try config.validate();
    try theta.validate();

    var states = [_]scaling.State{.{}} ** scaling.max_operators;
    scaling.initializeStates(&states, config.asScaling(.round_robin));

    const initial_facts =
        states[scaling.collector_index].knowledge.count(config.fact_count);
    var result = Result{
        .config = config,
        .theta = theta,
        .success = initial_facts == config.fact_count,
        .rounds = 0,
        .collector_initial_facts = initial_facts,
        .collector_final_facts = initial_facts,
        .policy_calls = 0,
        .inference_units = 0,
        .cache_hits = 0,
        .actions_proposed = 0,
        .rejected_actions = 0,
        .messages = 0,
        .communication_units = 0,
        .useful_deliveries = 0,
        .duplicate_deliveries = 0,
        .violations = 0,
    };
    if (result.success) return result;

    var cached_actions =
        [_]?scaling.Action{null} ** scaling.max_operators;
    var cache_initialized =
        [_]bool{false} ** scaling.max_operators;

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
                refreshEligible(
                    config.seed,
                    operator_index,
                    round,
                    theta.inference_gate_permille,
                );

            if (refresh) {
                const observation = stage7a.Observation.from(
                    states[operator_index],
                    operator_index,
                    round,
                    config,
                );
                cached_actions[operator_index] =
                    stage7a.decide(theta.policy, observation);
                cache_initialized[operator_index] = true;
                result.inference_units +%= 1;
            } else {
                result.cache_hits +%= 1;
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
            states[sender].sent.unionWithFacts(action.facts, config.fact_count);
            states[sender].cursor = action.next_cursor;

            switch (config.topology) {
                .ring => {
                    const left =
                        (sender + config.population_size - 1) %
                        config.population_size;
                    const right =
                        (sender + 1) % config.population_size;
                    deliver(action, states[left].knowledge, &received[left], &result);
                    if (right != left) {
                        deliver(action, states[right].knowledge, &received[right], &result);
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
                    const width = scaling.gridWidth(config.population_size);
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
            states[scaling.collector_index].knowledge.count(config.fact_count);
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
    std.debug.assert(
        result.policy_calls ==
            result.inference_units + result.cache_hits,
    );
    return result;
}

fn deliver(
    action: scaling.Action,
    snapshot_knowledge: scaling.BitSet,
    received: *scaling.BitSet,
    result: *Result,
) void {
    result.messages +%= 1;
    result.communication_units +%= @as(u64, @intCast(action.selected));

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

fn expectEquivalent(a: Result, b: stage7a.Result) !void {
    try std.testing.expectEqual(b.success, a.success);
    try std.testing.expectEqual(b.rounds, a.rounds);
    try std.testing.expectEqual(b.collector_initial_facts, a.collector_initial_facts);
    try std.testing.expectEqual(b.collector_final_facts, a.collector_final_facts);
    try std.testing.expectEqual(b.policy_calls, a.policy_calls);
    try std.testing.expectEqual(b.actions_proposed, a.actions_proposed);
    try std.testing.expectEqual(b.rejected_actions, a.rejected_actions);
    try std.testing.expectEqual(b.messages, a.messages);
    try std.testing.expectEqual(b.communication_units, a.communication_units);
    try std.testing.expectEqual(b.useful_deliveries, a.useful_deliveries);
    try std.testing.expectEqual(b.duplicate_deliveries, a.duplicate_deliveries);
    try std.testing.expectEqual(b.violations, a.violations);
    try std.testing.expectEqual(a.policy_calls, a.inference_units);
    try std.testing.expectEqual(@as(u64, 0), a.cache_hits);
}

test "F3 c=1000 reproduces Stage 7A exactly" {
    const profiles = [_]stage7a.Theta{
        stage7a.Theta{
            .novelty_permille = 244,
            .exploration_permille = 94,
            .retry_permille = 15,
            .bandwidth_utilization_permille = 958,
        },
        stage7a.Theta{
            .novelty_permille = 354,
            .exploration_permille = 141,
            .retry_permille = 0,
            .bandwidth_utilization_permille = 994,
        },
        stage7a.Theta{
            .novelty_permille = 685,
            .exploration_permille = 283,
            .retry_permille = 960,
            .bandwidth_utilization_permille = 344,
        },
        stage7a.round_robin_theta,
        stage7a.seeded_theta,
        stage7a.novel_first_theta,
    };
    const topologies = [_]scaling.TopologyKind{ .ring, .grid };

    for (profiles) |policy| {
        for (topologies) |topology| {
            var seed: u64 = 0;
            while (seed < 3) : (seed += 1) {
                const config = stage7a.Config{
                    .population_size = 32,
                    .fact_count = 64,
                    .topology = topology,
                    .redundancy = 2,
                    .bandwidth = 2,
                    .seed = seed,
                    .max_rounds = 2048,
                };
                const actual = try run(config, .{
                    .policy = policy,
                    .inference_gate_permille = 1000,
                });
                const expected = try stage7a.run(config, policy);
                try expectEquivalent(actual, expected);
            }
        }
    }
}

test "F3 refresh gate is deterministic" {
    var a: usize = 0;
    var round: u32 = 1;
    while (round <= 1000) : (round += 1) {
        if (refreshEligible(42, 3, round, 500)) a += 1;
    }
    var b: usize = 0;
    round = 1;
    while (round <= 1000) : (round += 1) {
        if (refreshEligible(42, 3, round, 500)) b += 1;
    }
    try std.testing.expectEqual(a, b);
    try std.testing.expect(a > 0 and a < 1000);
}
