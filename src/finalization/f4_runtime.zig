const std = @import("std");
const stage7a = @import("../substrate/stage7/stage7a_policy.zig");
const scaling = @import("../substrate/stage5/stage5a_scaling.zig");

pub const worker_count: usize = 5;
pub const fact_count: usize = 5;
pub const bandwidth: usize = 2;
pub const collector_index: usize = 0;
pub const max_rounds: u32 = 10;
pub const backend_error_sentinel = "__BACKEND_ERROR__";

pub const theta51 = stage7a.Theta{
    .novelty_permille = 354,
    .exploration_permille = 141,
    .retry_permille = 0,
    .bandwidth_utilization_permille = 994,
};

pub const PopulationMix = enum {
    deterministic_only,
    mixed,
    model_only,

    pub fn name(self: PopulationMix) []const u8 {
        return switch (self) {
            .deterministic_only => "deterministic_only",
            .mixed => "mixed",
            .model_only => "model_only",
        };
    }
};

pub const DecodeMode = enum {
    deterministic,
    typed_unconstrained,
    cfg_constrained,

    pub fn name(self: DecodeMode) []const u8 {
        return switch (self) {
            .deterministic => "deterministic",
            .typed_unconstrained => "typed_unconstrained",
            .cfg_constrained => "cfg_constrained",
        };
    }
};

pub const InferenceController = enum {
    deterministic,
    always_refresh,
    knowledge_or_stale,

    pub fn name(self: InferenceController) []const u8 {
        return switch (self) {
            .deterministic => "deterministic",
            .always_refresh => "always_refresh",
            .knowledge_or_stale => "knowledge_or_stale",
        };
    }
};

pub const OperatorKind = enum {
    deterministic,
    model,

    pub fn name(self: OperatorKind) []const u8 {
        return switch (self) {
            .deterministic => "deterministic",
            .model => "model",
        };
    }
};

pub const DecisionSource = enum {
    deterministic,
    model_call,
    cache,

    pub fn name(self: DecisionSource) []const u8 {
        return switch (self) {
            .deterministic => "deterministic",
            .model_call => "model_call",
            .cache => "cache",
        };
    }
};

pub const ActionKind = enum {
    claim,
    query_evidence,
};

pub const Action = struct {
    kind: ActionKind,
    facts: scaling.BitSet = .{},
    selected: u16 = 0,
    query_fact: u16 = 0,
    next_cursor: u16 = 0,
    reset_sent: bool = false,

    pub fn eql(a: Action, b: Action) bool {
        if (a.kind != b.kind) return false;
        return switch (a.kind) {
            .claim => scaling.BitSet.eql(a.facts, b.facts) and
                a.selected == b.selected and
                a.next_cursor == b.next_cursor and
                a.reset_sent == b.reset_sent,
            .query_evidence => a.query_fact == b.query_fact,
        };
    }
};

pub const ModelCache = struct {
    initialized: bool = false,
    action: ?Action = null,
    knowledge_at_refresh: scaling.BitSet = .{},
};

pub const RoundMetrics = struct {
    messages: u64 = 0,
    communication_units: u64 = 0,
    control_units: u64 = 0,
    useful_deliveries: u64 = 0,
    duplicate_deliveries: u64 = 0,
    essential_reached_deterministic: bool = false,

    pub fn accounted(self: RoundMetrics) bool {
        return self.communication_units ==
            self.control_units +
                self.useful_deliveries +
                self.duplicate_deliveries;
    }
};

pub fn initialStates(environment_seed: u64) [scaling.max_operators]scaling.State {
    var states = [_]scaling.State{.{}} ** scaling.max_operators;
    const offset: usize = @intCast(environment_seed % fact_count);

    var worker: usize = 0;
    while (worker < worker_count) : (worker += 1) {
        states[worker].knowledge.set((worker + offset) % fact_count);
        states[worker].knowledge.set((worker + 1 + offset) % fact_count);
    }
    return states;
}

pub fn essentialFact(environment_seed: u64) usize {
    // Workers 2 and 3 (indices 1 and 2) are the model-backed peers in the
    // mixed arm. Their only shared initial fact has no deterministic copy.
    return (2 + @as(usize, @intCast(environment_seed % fact_count))) %
        fact_count;
}

pub fn operatorKind(mix: PopulationMix, operator_index: usize) OperatorKind {
    std.debug.assert(operator_index < worker_count);
    return switch (mix) {
        .deterministic_only => .deterministic,
        .model_only => .model,
        .mixed => if (operator_index == 1 or operator_index == 2)
            .model
        else
            .deterministic,
    };
}

pub fn topologyNeighbors(
    topology: scaling.TopologyKind,
    operator_index: usize,
    out: *[4]usize,
) usize {
    std.debug.assert(operator_index < worker_count);
    return switch (topology) {
        .ring => blk: {
            const left = (operator_index + worker_count - 1) % worker_count;
            const right = (operator_index + 1) % worker_count;
            out[0] = left;
            if (right == left) break :blk 1;
            out[1] = right;
            break :blk 2;
        },
        .complete => blk: {
            var count: usize = 0;
            var i: usize = 0;
            while (i < worker_count) : (i += 1) {
                if (i == operator_index) continue;
                out[count] = i;
                count += 1;
            }
            break :blk count;
        },
        .grid => blk: {
            const width = scaling.gridWidth(worker_count);
            const row = operator_index / width;
            const col = operator_index % width;
            var count: usize = 0;
            if (col > 0) {
                out[count] = operator_index - 1;
                count += 1;
            }
            if (col + 1 < width and operator_index + 1 < worker_count) {
                const recipient = operator_index + 1;
                if (recipient / width == row) {
                    out[count] = recipient;
                    count += 1;
                }
            }
            if (operator_index >= width) {
                out[count] = operator_index - width;
                count += 1;
            }
            if (operator_index + width < worker_count) {
                out[count] = operator_index + width;
                count += 1;
            }
            break :blk count;
        },
    };
}

pub fn deterministicAction(
    state: scaling.State,
    operator_index: usize,
    round: u32,
    topology: scaling.TopologyKind,
    environment_seed: u64,
) ?Action {
    const config = stage7a.Config{
        .population_size = worker_count,
        .fact_count = fact_count,
        .topology = topology,
        .redundancy = 2,
        .bandwidth = bandwidth,
        .seed = environment_seed,
        .max_rounds = max_rounds,
    };
    const observation = stage7a.Observation.from(
        state,
        operator_index,
        round,
        config,
    );
    const action = stage7a.decide(theta51, observation) orelse return null;
    return .{
        .kind = .claim,
        .facts = action.facts,
        .selected = action.selected,
        .next_cursor = action.next_cursor,
        .reset_sent = action.reset_sent,
    };
}

pub fn parseModelAction(completion: []const u8) !Action {
    const text = std.mem.trim(u8, completion, " \t\r\n");
    var parts = std.mem.splitScalar(u8, text, ' ');
    const first = parts.next() orelse return error.InvalidSyntax;

    if (std.mem.eql(u8, first, "CLAIM")) {
        const list = parts.next() orelse return error.InvalidSyntax;
        if (parts.next() != null) return error.InvalidSyntax;

        var facts = scaling.BitSet{};
        var selected: usize = 0;
        var items = std.mem.splitScalar(u8, list, ',');
        while (items.next()) |item| {
            const fact = try parseFact(item);
            if (!facts.has(fact)) {
                facts.set(fact);
                selected += 1;
            }
        }
        if (selected == 0) return error.InvalidSyntax;

        return .{
            .kind = .claim,
            .facts = facts,
            .selected = @intCast(selected),
        };
    }

    if (std.mem.eql(u8, first, "QUERY")) {
        const second = parts.next() orelse return error.InvalidSyntax;
        const fact_text = parts.next() orelse return error.InvalidSyntax;
        if (parts.next() != null or
            !std.mem.eql(u8, second, "EVIDENCE"))
        {
            return error.InvalidSyntax;
        }

        return .{
            .kind = .query_evidence,
            .query_fact = @intCast(try parseFact(fact_text)),
            .selected = 1,
        };
    }

    return error.InvalidSyntax;
}

fn parseFact(text: []const u8) !usize {
    if (text.len != 1) return error.InvalidFact;
    const upper: u8 = 'A' + @as(u8, @intCast(fact_count));
    if (text[0] < 'A' or text[0] >= upper) {
        return error.InvalidFact;
    }
    return @as(usize, text[0] - 'A');
}

pub fn validateModelAction(action: Action, state: scaling.State) bool {
    return switch (action.kind) {
        .claim => blk: {
            const count = action.facts.count(fact_count);
            break :blk count >= 1 and
                count == @as(usize, action.selected) and
                count <= bandwidth and
                action.facts.isSubsetOf(state.knowledge);
        },
        .query_evidence =>
            @as(usize, action.query_fact) < fact_count,
    };
}

pub fn shouldRefreshModel(
    controller: InferenceController,
    state: scaling.State,
    cache: ModelCache,
) bool {
    return switch (controller) {
        .deterministic => false,
        .always_refresh => true,
        .knowledge_or_stale => blk: {
            if (!cache.initialized) break :blk true;
            if (!scaling.BitSet.eql(
                state.knowledge,
                cache.knowledge_at_refresh,
            )) {
                break :blk true;
            }
            const action = cache.action orelse break :blk true;
            if (!validateModelAction(action, state)) break :blk true;
            if (cachedActionIsStale(action, state)) break :blk true;
            break :blk false;
        },
    };
}

pub fn cachedActionIsStale(
    action: Action,
    state: scaling.State,
) bool {
    return switch (action.kind) {
        .claim => blk: {
            const has_unsent =
                state.knowledge.hasDifference(state.sent, fact_count);
            break :blk has_unsent and action.facts.isSubsetOf(state.sent);
        },
        .query_evidence =>
            state.knowledge.has(@as(usize, action.query_fact)),
    };
}

pub fn applyRound(
    states: *[scaling.max_operators]scaling.State,
    actions: *const [scaling.max_operators]?Action,
    mix: PopulationMix,
    topology: scaling.TopologyKind,
    environment_seed: u64,
) RoundMetrics {
    const snapshot = states.*;
    var received = [_]scaling.BitSet{.{}} ** scaling.max_operators;
    var metrics = RoundMetrics{};
    const essential = essentialFact(environment_seed);

    var sender: usize = 0;
    while (sender < worker_count) : (sender += 1) {
        const action = actions[sender] orelse continue;

        switch (action.kind) {
            .claim => {
                if (action.reset_sent) states[sender].sent.clear();
                states[sender].sent.unionWithFacts(action.facts, fact_count);
                states[sender].cursor = action.next_cursor;

                var neighbors: [4]usize = undefined;
                const neighbor_count =
                    topologyNeighbors(topology, sender, &neighbors);
                var n: usize = 0;
                while (n < neighbor_count) : (n += 1) {
                    const recipient = neighbors[n];
                    metrics.messages +%= 1;
                    deliverFacts(
                        action.facts,
                        snapshot[recipient].knowledge,
                        &received[recipient],
                        &metrics,
                    );
                    if (mix == .mixed and
                        operatorKind(mix, sender) == .model and
                        operatorKind(mix, recipient) == .deterministic and
                        action.facts.has(essential) and
                        !snapshot[recipient].knowledge.has(essential))
                    {
                        metrics.essential_reached_deterministic = true;
                    }
                }
            },
            .query_evidence => {
                var neighbors: [4]usize = undefined;
                const neighbor_count =
                    topologyNeighbors(topology, sender, &neighbors);
                var n: usize = 0;
                while (n < neighbor_count) : (n += 1) {
                    const responder = neighbors[n];
                    metrics.messages +%= 1;
                    metrics.communication_units +%= 1;
                    metrics.control_units +%= 1;

                    const fact = @as(usize, action.query_fact);
                    if (!snapshot[responder].knowledge.has(fact)) continue;

                    metrics.messages +%= 1;
                    var response = scaling.BitSet{};
                    response.set(fact);
                    deliverFacts(
                        response,
                        snapshot[sender].knowledge,
                        &received[sender],
                        &metrics,
                    );

                    if (mix == .mixed and
                        operatorKind(mix, responder) == .model and
                        operatorKind(mix, sender) == .deterministic and
                        fact == essential and
                        !snapshot[sender].knowledge.has(essential))
                    {
                        metrics.essential_reached_deterministic = true;
                    }
                }
            },
        }
    }

    var operator_index: usize = 0;
    while (operator_index < worker_count) : (operator_index += 1) {
        states[operator_index].knowledge.unionWithFacts(
            received[operator_index],
            fact_count,
        );
    }

    std.debug.assert(metrics.accounted());
    return metrics;
}

fn deliverFacts(
    facts: scaling.BitSet,
    snapshot_knowledge: scaling.BitSet,
    received: *scaling.BitSet,
    metrics: *RoundMetrics,
) void {
    var fact: usize = 0;
    while (fact < fact_count) : (fact += 1) {
        if (!facts.has(fact)) continue;

        metrics.communication_units +%= 1;
        if (!snapshot_knowledge.has(fact) and !received.has(fact)) {
            metrics.useful_deliveries +%= 1;
        } else {
            metrics.duplicate_deliveries +%= 1;
        }
        received.set(fact);
    }
}

pub fn collectorSolved(states: *const [scaling.max_operators]scaling.State) bool {
    return states[collector_index].knowledge.containsAll(fact_count);
}

pub fn generationSeed(
    sampling_seed: u64,
    round: u32,
    worker: u8,
) u32 {
    const mixed =
        sampling_seed *% 1_000_003 +
        @as(u64, round) *% 101 +
        @as(u64, worker);
    return @intCast(mixed & 0x7fff_ffff);
}

pub fn maskText(
    mask: scaling.BitSet,
    out: *[32]u8,
) []const u8 {
    var len: usize = 0;
    var fact: usize = 0;
    while (fact < fact_count) : (fact += 1) {
        if (!mask.has(fact)) continue;
        if (len != 0) {
            out[len] = ',';
            len += 1;
        }
        out[len] = 'A' + @as(u8, @intCast(fact));
        len += 1;
    }
    if (len == 0) return "(none)";
    return out[0..len];
}

pub fn canonicalActionText(
    action: ?Action,
    out: *[64]u8,
) ![]const u8 {
    const value = action orelse return "NO_ACTION";
    return switch (value.kind) {
        .query_evidence => std.fmt.bufPrint(
            out,
            "QUERY EVIDENCE {c}",
            .{'A' + @as(u8, @intCast(value.query_fact))},
        ),
        .claim => blk: {
            var len: usize = 0;
            const prefix = "CLAIM ";
            std.mem.copyForwards(u8, out[0..prefix.len], prefix);
            len = prefix.len;

            var fact: usize = 0;
            var first = true;
            while (fact < fact_count) : (fact += 1) {
                if (!value.facts.has(fact)) continue;
                if (!first) {
                    out[len] = ',';
                    len += 1;
                }
                out[len] = 'A' + @as(u8, @intCast(fact));
                len += 1;
                first = false;
            }
            break :blk out[0..len];
        },
    };
}

test "F4 historical overlapping placement preserves one mixed essential fact" {
    const states = initialStates(0);
    try std.testing.expect(states[0].knowledge.has(0));
    try std.testing.expect(states[0].knowledge.has(1));
    try std.testing.expect(states[1].knowledge.has(1));
    try std.testing.expect(states[1].knowledge.has(2));
    try std.testing.expect(states[2].knowledge.has(2));
    try std.testing.expect(states[2].knowledge.has(3));
    try std.testing.expectEqual(@as(usize, 2), essentialFact(0));

    var copies: usize = 0;
    var worker: usize = 0;
    while (worker < worker_count) : (worker += 1) {
        if (states[worker].knowledge.has(essentialFact(0))) copies += 1;
    }
    try std.testing.expectEqual(@as(usize, 2), copies);
    try std.testing.expect(
        operatorKind(.mixed, 1) == .model and
        operatorKind(.mixed, 2) == .model,
    );
}

test "F4 model action parser rejects prose and over-bandwidth semantically" {
    const claim = try parseModelAction("CLAIM A,C");
    try std.testing.expectEqual(ActionKind.claim, claim.kind);
    try std.testing.expectEqual(@as(u16, 2), claim.selected);
    try std.testing.expectError(
        error.InvalidSyntax,
        parseModelAction("I think CLAIM A"),
    );

    var state = scaling.State{};
    state.knowledge.set(0);
    state.knowledge.set(1);
    state.knowledge.set(2);
    const over = try parseModelAction("CLAIM A,B,C");
    try std.testing.expect(!validateModelAction(over, state));
}

test "F4 state-aware model controller refreshes on knowledge change and staleness" {
    var state = scaling.State{};
    state.knowledge.set(0);
    state.knowledge.set(1);

    var cache = ModelCache{};
    try std.testing.expect(
        shouldRefreshModel(.knowledge_or_stale, state, cache),
    );

    var facts = scaling.BitSet{};
    facts.set(0);
    cache.initialized = true;
    cache.action = .{
        .kind = .claim,
        .facts = facts,
        .selected = 1,
    };
    cache.knowledge_at_refresh = state.knowledge;
    try std.testing.expect(
        !shouldRefreshModel(.knowledge_or_stale, state, cache),
    );

    state.sent.set(0);
    try std.testing.expect(
        shouldRefreshModel(.knowledge_or_stale, state, cache),
    );

    cache.knowledge_at_refresh = state.knowledge;
    state.knowledge.set(2);
    try std.testing.expect(
        shouldRefreshModel(.knowledge_or_stale, state, cache),
    );
}

test "F4 generation seed keeps environment and sampling factors separate" {
    try std.testing.expectEqual(
        @as(u32, 102),
        generationSeed(0, 1, 1),
    );
    try std.testing.expect(
        generationSeed(7, 1, 1) != generationSeed(8, 1, 1),
    );
}

test "F4 query accounting includes control plus evidence units" {
    var states = initialStates(0);
    var actions = [_]?Action{null} ** scaling.max_operators;
    actions[0] = .{
        .kind = .query_evidence,
        .query_fact = 2,
        .selected = 1,
    };

    const metrics = applyRound(
        &states,
        &actions,
        .model_only,
        .ring,
        0,
    );
    try std.testing.expect(metrics.control_units > 0);
    try std.testing.expect(metrics.accounted());
}
