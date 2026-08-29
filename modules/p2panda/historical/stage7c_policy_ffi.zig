const stage7a = @import("stage7a_policy.zig");
const scaling = @import("../stage5/stage5a_scaling.zig");

pub const abi_version: u32 = 1;

pub const FfiAction = extern struct {
    selected: u16,
    next_cursor: u16,
    reset_sent: u8,
    _padding: [3]u8,
};

pub const FfiSimulation = extern struct {
    success: u8,
    _padding: [7]u8,
    rounds: u32,
    _padding2: u32,
    communication_units: u64,
    useful_deliveries: u64,
    duplicate_deliveries: u64,
    policy_calls: u64,
    violations: u64,
};

fn topologyFromInt(value: u8) ?scaling.TopologyKind {
    return switch (value) {
        0 => .ring,
        1 => .complete,
        2 => .grid,
        else => null,
    };
}

fn configFromArgs(
    population_size: u16,
    fact_count: u16,
    topology: u8,
    redundancy: u16,
    bandwidth: u16,
    seed: u64,
    max_rounds: u32,
) ?stage7a.Config {
    const topology_kind = topologyFromInt(topology) orelse return null;
    return .{
        .population_size = population_size,
        .fact_count = fact_count,
        .topology = topology_kind,
        .redundancy = redundancy,
        .bandwidth = bandwidth,
        .seed = seed,
        .max_rounds = max_rounds,
    };
}

pub export fn starlings_stage7c_abi_version() u32 {
    return abi_version;
}

/// Initialize one operator's exact Stage 5/7 deterministic fact placement.
///
/// Returns 0 on success, negative on invalid arguments.
pub export fn starlings_stage7c_init_state(
    population_size: u16,
    fact_count: u16,
    topology: u8,
    redundancy: u16,
    bandwidth: u16,
    seed: u64,
    operator_index: u16,
    out_knowledge: [*]u64,
    out_words: usize,
) i32 {
    const config = configFromArgs(
        population_size,
        fact_count,
        topology,
        redundancy,
        bandwidth,
        seed,
        1,
    ) orelse return -1;

    config.validate() catch return -2;
    if (operator_index >= population_size) return -3;

    const active_words = (@as(usize, fact_count) + 63) / 64;
    if (out_words < active_words) return -4;

    var states = [_]scaling.State{.{}} ** scaling.max_operators;
    scaling.initializeStates(
        &states,
        config.asScaling(.round_robin),
    );

    var word: usize = 0;
    while (word < active_words) : (word += 1) {
        out_knowledge[word] =
            states[@as(usize, operator_index)].knowledge.words[word];
    }
    while (word < out_words) : (word += 1) {
        out_knowledge[word] = 0;
    }

    return 0;
}

/// Execute the exact Stage 7A local policy against externally-held local
/// state. The policy is not reimplemented by the distributed harness.
///
/// Returns:
///   1 -> action produced
///   0 -> no action
///  <0 -> invalid input
pub export fn starlings_stage7c_decide(
    population_size: u16,
    fact_count: u16,
    topology: u8,
    redundancy: u16,
    bandwidth: u16,
    seed: u64,
    operator_index: u16,
    round: u32,
    cursor: u16,
    novelty_permille: u16,
    exploration_permille: u16,
    retry_permille: u16,
    bandwidth_utilization_permille: u16,
    knowledge_words: [*]const u64,
    sent_words: [*]const u64,
    input_words: usize,
    out_fact_words: [*]u64,
    out_words: usize,
    out_action: *FfiAction,
) i32 {
    const config = configFromArgs(
        population_size,
        fact_count,
        topology,
        redundancy,
        bandwidth,
        seed,
        1,
    ) orelse return -1;

    config.validate() catch return -2;
    if (operator_index >= population_size) return -3;

    const active_words = (@as(usize, fact_count) + 63) / 64;
    if (input_words < active_words or out_words < active_words) return -4;

    const theta = stage7a.Theta{
        .novelty_permille = novelty_permille,
        .exploration_permille = exploration_permille,
        .retry_permille = retry_permille,
        .bandwidth_utilization_permille = bandwidth_utilization_permille,
    };
    theta.validate() catch return -5;

    var state = scaling.State{ .cursor = cursor };
    var word: usize = 0;
    while (word < active_words) : (word += 1) {
        state.knowledge.words[word] = knowledge_words[word];
        state.sent.words[word] = sent_words[word];
        out_fact_words[word] = 0;
    }
    while (word < out_words) : (word += 1) {
        out_fact_words[word] = 0;
    }

    const observation = stage7a.Observation.from(
        state,
        operator_index,
        round,
        config,
    );
    const action = stage7a.decide(theta, observation) orelse {
        out_action.* = .{
            .selected = 0,
            .next_cursor = cursor,
            .reset_sent = 0,
            ._padding = .{ 0, 0, 0 },
        };
        return 0;
    };

    word = 0;
    while (word < active_words) : (word += 1) {
        out_fact_words[word] = action.facts.words[word];
    }

    out_action.* = .{
        .selected = action.selected,
        .next_cursor = action.next_cursor,
        .reset_sent = if (action.reset_sent) 1 else 0,
        ._padding = .{ 0, 0, 0 },
    };
    return 1;
}

pub export fn starlings_stage7c_simulate(
    population_size: u16,
    fact_count: u16,
    topology: u8,
    redundancy: u16,
    bandwidth: u16,
    seed: u64,
    max_rounds: u32,
    novelty_permille: u16,
    exploration_permille: u16,
    retry_permille: u16,
    bandwidth_utilization_permille: u16,
    out_simulation: *FfiSimulation,
) i32 {
    const config = configFromArgs(
        population_size,
        fact_count,
        topology,
        redundancy,
        bandwidth,
        seed,
        max_rounds,
    ) orelse return -1;

    config.validate() catch return -2;

    const theta = stage7a.Theta{
        .novelty_permille = novelty_permille,
        .exploration_permille = exploration_permille,
        .retry_permille = retry_permille,
        .bandwidth_utilization_permille = bandwidth_utilization_permille,
    };
    theta.validate() catch return -3;

    const result = stage7a.run(config, theta) catch return -4;
    out_simulation.* = .{
        .success = if (result.success) 1 else 0,
        ._padding = .{ 0, 0, 0, 0, 0, 0, 0 },
        .rounds = result.rounds,
        ._padding2 = 0,
        .communication_units = result.communication_units,
        .useful_deliveries = result.useful_deliveries,
        .duplicate_deliveries = result.duplicate_deliveries,
        .policy_calls = result.policy_calls,
        .violations = result.violations,
    };
    return 0;
}

test "Stage 7C FFI uses the exact Stage 7A policy" {
    const config = stage7a.Config{
        .population_size = 8,
        .fact_count = 32,
        .topology = .ring,
        .redundancy = 2,
        .bandwidth = 2,
        .seed = 7,
        .max_rounds = 64,
    };

    var states = [_]scaling.State{.{}} ** scaling.max_operators;
    scaling.initializeStates(
        &states,
        config.asScaling(.round_robin),
    );

    const state = states[3];
    const theta = stage7a.Theta{
        .novelty_permille = 354,
        .exploration_permille = 141,
        .retry_permille = 0,
        .bandwidth_utilization_permille = 994,
    };
    const expected = stage7a.decide(
        theta,
        stage7a.Observation.from(state, 3, 5, config),
    ).?;

    var output = [_]u64{0} ** scaling.word_count;
    var ffi_action = FfiAction{
        .selected = 0,
        .next_cursor = 0,
        .reset_sent = 0,
        ._padding = .{ 0, 0, 0 },
    };

    const status = starlings_stage7c_decide(
        8,
        32,
        0,
        2,
        2,
        7,
        3,
        5,
        state.cursor,
        354,
        141,
        0,
        994,
        state.knowledge.words[0..].ptr,
        state.sent.words[0..].ptr,
        scaling.word_count,
        output[0..].ptr,
        scaling.word_count,
        &ffi_action,
    );

    try @import("std").testing.expectEqual(@as(i32, 1), status);
    try @import("std").testing.expectEqual(
        expected.selected,
        ffi_action.selected,
    );
    try @import("std").testing.expectEqual(
        expected.next_cursor,
        ffi_action.next_cursor,
    );

    var word: usize = 0;
    while (word < scaling.word_count) : (word += 1) {
        try @import("std").testing.expectEqual(
            expected.facts.words[word],
            output[word],
        );
    }
}


test "Stage 7C ABI structs have fixed C layout" {
    try @import("std").testing.expectEqual(
        @as(usize, 8),
        @sizeOf(FfiAction),
    );
    try @import("std").testing.expectEqual(
        @as(usize, 56),
        @sizeOf(FfiSimulation),
    );
}
