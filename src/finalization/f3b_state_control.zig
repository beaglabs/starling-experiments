const std = @import("std");
const stage7a = @import("../substrate/stage7/stage7a_policy.zig");
const scaling = @import("../substrate/stage5/stage5a_scaling.zig");

pub const ControllerKind = enum {
    always_refresh,
    knowledge_change,
    knowledge_or_stale,
    knowledge_or_stale_age4,
    knowledge_or_stale_age8,

    pub fn name(self: ControllerKind) []const u8 {
        return switch (self) {
            .always_refresh => "always_refresh",
            .knowledge_change => "knowledge_change",
            .knowledge_or_stale => "knowledge_or_stale",
            .knowledge_or_stale_age4 => "knowledge_or_stale_age4",
            .knowledge_or_stale_age8 => "knowledge_or_stale_age8",
        };
    }

    pub fn usesSemanticStaleness(self: ControllerKind) bool {
        return switch (self) {
            .always_refresh, .knowledge_change => false,
            .knowledge_or_stale,
            .knowledge_or_stale_age4,
            .knowledge_or_stale_age8,
            => true,
        };
    }

    pub fn maxAge(self: ControllerKind) ?u32 {
        return switch (self) {
            .knowledge_or_stale_age4 => 4,
            .knowledge_or_stale_age8 => 8,
            else => null,
        };
    }
};

pub const controller_kinds = [_]ControllerKind{
    .always_refresh,
    .knowledge_change,
    .knowledge_or_stale,
    .knowledge_or_stale_age4,
    .knowledge_or_stale_age8,
};

const RefreshReason = enum {
    first,
    always,
    knowledge,
    invalid_action,
    stale_action,
    age,
    reuse,
};

pub const Result = struct {
    config: stage7a.Config,
    theta: stage7a.Theta,
    controller: ControllerKind,
    success: bool,
    rounds: u32,
    collector_initial_facts: usize,
    collector_final_facts: usize,

    policy_calls: u64,
    inference_units: u64,
    cache_reuses: u64,

    refresh_first: u64,
    refresh_always: u64,
    refresh_knowledge: u64,
    refresh_invalid_action: u64,
    refresh_stale_action: u64,
    refresh_age: u64,

    actions_proposed: u64,
    rejected_actions: u64,
    messages: u64,
    communication_units: u64,
    useful_deliveries: u64,
    duplicate_deliveries: u64,
    violations: u64,

    pub fn inferenceAccounted(self: Result) bool {
        return self.policy_calls ==
            self.inference_units + self.cache_reuses and
            self.inference_units ==
                self.refresh_first +
                    self.refresh_always +
                    self.refresh_knowledge +
                    self.refresh_invalid_action +
                    self.refresh_stale_action +
                    self.refresh_age;
    }

    pub fn communicationAccounted(self: Result) bool {
        return self.communication_units ==
            self.useful_deliveries + self.duplicate_deliveries;
    }
};

pub fn run(
    config: stage7a.Config,
    theta: stage7a.Theta,
    controller: ControllerKind,
) stage7a.Error!Result {
    try config.validate();
    try theta.validate();

    if (controller == .always_refresh) {
        const baseline = try stage7a.run(config, theta);
        return fromBaseline(baseline, controller);
    }

    var states = [_]scaling.State{.{}} ** scaling.max_operators;
    scaling.initializeStates(&states, config.asScaling(.round_robin));

    const initial_facts =
        states[scaling.collector_index].knowledge.count(config.fact_count);
    var result = initialResult(config, theta, controller, initial_facts);
    if (result.success) return result;

    var cache_initialized =
        [_]bool{false} ** scaling.max_operators;
    var cached_actions =
        [_]?scaling.Action{null} ** scaling.max_operators;
    var knowledge_at_refresh =
        [_]scaling.BitSet{.{}} ** scaling.max_operators;
    var last_refresh_round =
        [_]u32{0} ** scaling.max_operators;

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

            const reason = refreshReason(
                controller,
                states[operator_index],
                cache_initialized[operator_index],
                cached_actions[operator_index],
                knowledge_at_refresh[operator_index],
                last_refresh_round[operator_index],
                round,
                config,
            );

            if (reason != .reuse) {
                const observation = stage7a.Observation.from(
                    states[operator_index],
                    operator_index,
                    round,
                    config,
                );
                cached_actions[operator_index] =
                    stage7a.decide(theta, observation);
                cache_initialized[operator_index] = true;
                knowledge_at_refresh[operator_index] =
                    states[operator_index].knowledge;
                last_refresh_round[operator_index] = round;
                recordRefresh(&result, reason);
            } else {
                result.cache_reuses +%= 1;
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

    std.debug.assert(result.inferenceAccounted());
    std.debug.assert(result.communicationAccounted());
    return result;
}

fn refreshReason(
    controller: ControllerKind,
    state: scaling.State,
    cache_initialized: bool,
    cached_action: ?scaling.Action,
    knowledge_snapshot: scaling.BitSet,
    last_refresh_round: u32,
    round: u32,
    config: stage7a.Config,
) RefreshReason {
    if (!cache_initialized) return .first;
    if (controller == .always_refresh) return .always;

    if (!scaling.BitSet.eql(state.knowledge, knowledge_snapshot)) {
        return .knowledge;
    }

    if (cached_action) |action| {
        if (!scaling.validateLocalAction(
            action,
            state,
            config.asScaling(.round_robin),
        )) {
            return .invalid_action;
        }

        if (controller.usesSemanticStaleness() and
            cachedActionIsStale(action, state, config.fact_count))
        {
            return .stale_action;
        }
    } else {
        // A cached null action is safe only until the next policy opportunity.
        // Round-indexed exploration/retry may make a fresh action possible.
        return .invalid_action;
    }

    if (controller.maxAge()) |max_age| {
        if (round - last_refresh_round >= max_age) return .age;
    }

    return .reuse;
}

fn cachedActionIsStale(
    action: scaling.Action,
    state: scaling.State,
    fact_count: usize,
) bool {
    const has_unsent =
        state.knowledge.hasDifference(state.sent, fact_count);
    if (!has_unsent) return false;

    // If every fact in the cached action has already been sent while some
    // locally known fact has not, the action is decision-stale even though it
    // remains structurally valid.
    return action.facts.isSubsetOf(state.sent);
}

fn recordRefresh(result: *Result, reason: RefreshReason) void {
    result.inference_units +%= 1;
    switch (reason) {
        .first => result.refresh_first +%= 1,
        .always => result.refresh_always +%= 1,
        .knowledge => result.refresh_knowledge +%= 1,
        .invalid_action => result.refresh_invalid_action +%= 1,
        .stale_action => result.refresh_stale_action +%= 1,
        .age => result.refresh_age +%= 1,
        .reuse => unreachable,
    }
}

fn fromBaseline(
    baseline: stage7a.Result,
    controller: ControllerKind,
) Result {
    return .{
        .config = baseline.config,
        .theta = baseline.theta,
        .controller = controller,
        .success = baseline.success,
        .rounds = baseline.rounds,
        .collector_initial_facts = baseline.collector_initial_facts,
        .collector_final_facts = baseline.collector_final_facts,
        .policy_calls = baseline.policy_calls,
        .inference_units = baseline.policy_calls,
        .cache_reuses = 0,
        .refresh_first = 0,
        .refresh_always = baseline.policy_calls,
        .refresh_knowledge = 0,
        .refresh_invalid_action = 0,
        .refresh_stale_action = 0,
        .refresh_age = 0,
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
    theta: stage7a.Theta,
    controller: ControllerKind,
    initial_facts: usize,
) Result {
    return .{
        .config = config,
        .theta = theta,
        .controller = controller,
        .success = initial_facts == config.fact_count,
        .rounds = 0,
        .collector_initial_facts = initial_facts,
        .collector_final_facts = initial_facts,
        .policy_calls = 0,
        .inference_units = 0,
        .cache_reuses = 0,
        .refresh_first = 0,
        .refresh_always = 0,
        .refresh_knowledge = 0,
        .refresh_invalid_action = 0,
        .refresh_stale_action = 0,
        .refresh_age = 0,
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

fn expectSame(a: Result, b: stage7a.Result) !void {
    try std.testing.expectEqual(b.success, a.success);
    try std.testing.expectEqual(b.rounds, a.rounds);
    try std.testing.expectEqual(
        b.collector_initial_facts,
        a.collector_initial_facts,
    );
    try std.testing.expectEqual(
        b.collector_final_facts,
        a.collector_final_facts,
    );
    try std.testing.expectEqual(b.policy_calls, a.policy_calls);
    try std.testing.expectEqual(b.policy_calls, a.inference_units);
    try std.testing.expectEqual(@as(u64, 0), a.cache_reuses);
    try std.testing.expectEqual(b.actions_proposed, a.actions_proposed);
    try std.testing.expectEqual(b.rejected_actions, a.rejected_actions);
    try std.testing.expectEqual(b.messages, a.messages);
    try std.testing.expectEqual(
        b.communication_units,
        a.communication_units,
    );
    try std.testing.expectEqual(
        b.useful_deliveries,
        a.useful_deliveries,
    );
    try std.testing.expectEqual(
        b.duplicate_deliveries,
        a.duplicate_deliveries,
    );
    try std.testing.expectEqual(b.violations, a.violations);
    try std.testing.expect(a.inferenceAccounted());
    try std.testing.expect(a.communicationAccounted());
}

test "F3b always-refresh delegates exactly to Stage 7A" {
    const topologies = [_]scaling.TopologyKind{ .ring, .grid };
    const profiles = [_]stage7a.Theta{
        stage7a.soft_novel_theta,
        stage7a.exploratory_novel_theta,
        stage7a.lean_exploratory_theta,
    };

    for (profiles) |theta| {
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
                const actual = try run(
                    config,
                    theta,
                    .always_refresh,
                );
                const expected = try stage7a.run(config, theta);
                try expectSame(actual, expected);
            }
        }
    }
}

test "F3b state-aware controllers preserve exact accounting" {
    const config = stage7a.Config{
        .population_size = 8,
        .fact_count = 32,
        .topology = .ring,
        .redundancy = 2,
        .bandwidth = 2,
        .seed = 1,
        .max_rounds = 512,
    };

    for (controller_kinds[1..]) |controller| {
        const result = try run(
            config,
            stage7a.soft_novel_theta,
            controller,
        );
        try std.testing.expect(result.inferenceAccounted());
        try std.testing.expect(result.communicationAccounted());
        try std.testing.expectEqual(@as(u64, 0), result.violations);
        try std.testing.expect(result.inference_units <= result.policy_calls);
    }
}

test "F3b semantic staleness detects exhausted cached action" {
    var state = scaling.State{};
    state.knowledge.set(0);
    state.knowledge.set(1);
    state.sent.set(0);

    var facts = scaling.BitSet{};
    facts.set(0);
    const action = scaling.Action{
        .facts = facts,
        .selected = 1,
        .next_cursor = 1,
    };
    try std.testing.expect(cachedActionIsStale(action, state, 2));

    state.sent.clear();
    try std.testing.expect(!cachedActionIsStale(action, state, 2));
}


test "F3b refresh reasons detect knowledge change and max age" {
    var state = scaling.State{};
    state.knowledge.set(0);

    var snapshot = scaling.BitSet{};
    var facts = scaling.BitSet{};
    facts.set(0);
    const action = scaling.Action{
        .facts = facts,
        .selected = 1,
        .next_cursor = 1,
    };

    try std.testing.expectEqual(
        RefreshReason.knowledge,
        refreshReason(
            .knowledge_change,
            state,
            true,
            action,
            snapshot,
            1,
            2,
            .{
                .population_size = 8,
                .fact_count = 2,
                .topology = .ring,
                .redundancy = 2,
                .bandwidth = 1,
                .seed = 0,
                .max_rounds = 64,
            },
        ),
    );

    snapshot = state.knowledge;
    try std.testing.expectEqual(
        RefreshReason.age,
        refreshReason(
            .knowledge_or_stale_age4,
            state,
            true,
            action,
            snapshot,
            1,
            5,
            .{
                .population_size = 8,
                .fact_count = 2,
                .topology = .ring,
                .redundancy = 2,
                .bandwidth = 1,
                .seed = 0,
                .max_rounds = 64,
            },
        ),
    );
}
