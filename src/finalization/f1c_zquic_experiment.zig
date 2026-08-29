const std = @import("std");
const scaling = @import("../substrate/stage5/stage5a_scaling.zig");
const stage7a = @import("../substrate/stage7/stage7a_policy.zig");
const f1a = @import("f1a_fault_matrix.zig");
const runtime_mod = @import("f1c_zquic_runtime.zig");
const wire = @import("f1c_wire.zig");

pub const FaultKind = enum {
    no_fault,
    partition,
    crash_persist,
    crash_reset,

    pub fn name(self: FaultKind) []const u8 {
        return switch (self) {
            .no_fault => "no_fault",
            .partition => "partition",
            .crash_persist => "crash_restart_persist",
            .crash_reset => "crash_restart_reset",
        };
    }

    pub fn parse(value: []const u8) ?FaultKind {
        if (std.mem.eql(u8, value, "no_fault")) return .no_fault;
        if (std.mem.eql(u8, value, "partition")) return .partition;
        if (std.mem.eql(u8, value, "crash_restart_persist")) return .crash_persist;
        if (std.mem.eql(u8, value, "crash_restart_reset")) return .crash_reset;
        return null;
    }
};

pub const Config = struct {
    world: stage7a.Config,
    fault: FaultKind = .no_fault,
    max_ticks: u32 = 4096,
    partition_start: u32 = 8,
    partition_end: u32 = 48,
    partition_cut: usize = 4,
    crash_node: usize = 3,
    crash_start: u32 = 8,
    crash_end: u32 = 40,

    pub fn validate(self: Config) !void {
        try self.world.validate();
        if (self.max_ticks == 0) return error.InvalidTickHorizon;
        if (self.partition_start >= self.partition_end or
            self.partition_cut == 0 or
            self.partition_cut >= self.world.population_size)
        {
            return error.InvalidPartition;
        }
        if (self.crash_start >= self.crash_end or
            self.crash_node >= self.world.population_size)
        {
            return error.InvalidCrash;
        }
    }
};

const Terminal = enum {
    pending,
    delivered,
    partitioned,
    crashed,
};

const Attempt = struct {
    sender: u16,
    recipient: u16,
    sequence: u32,
    selected: u16,
    facts: scaling.BitSet,
    terminal: Terminal = .pending,
};

const FactLedger = struct {
    attempted_to_collector: [scaling.max_facts]u32 =
        [_]u32{0} ** scaling.max_facts,
    delivery_faulted: [scaling.max_facts]u32 =
        [_]u32{0} ** scaling.max_facts,
    crashed: [scaling.max_facts]u32 =
        [_]u32{0} ** scaling.max_facts,
    erased_by_crash: [scaling.max_facts]bool =
        [_]bool{false} ** scaling.max_facts,
    pending: [scaling.max_facts]u32 =
        [_]u32{0} ** scaling.max_facts,
};

pub const Result = struct {
    profile: []const u8,
    topology: scaling.TopologyKind,
    seed: u64,
    fault: FaultKind,
    fact_count: usize,
    success: bool = false,
    ticks: u32 = 0,
    collector_initial: usize = 0,
    collector_final: usize = 0,
    policy_ticks: u64 = 0,
    actions: u64 = 0,
    rejected_actions: u64 = 0,
    transport_attempts: u64 = 0,
    delivered: u64 = 0,
    partitioned: u64 = 0,
    crashed: u64 = 0,
    pending: u64 = 0,
    attempted_communication_units: u64 = 0,
    communication_units: u64 = 0,
    useful: u64 = 0,
    duplicate: u64 = 0,
    transport_duplicate_deliveries: u64 = 0,
    violations: u64 = 0,
    never_transmitted: usize = 0,
    delivery_faulted: usize = 0,
    crashed_before_merge: usize = 0,
    pending_at_censor: usize = 0,
    unattributed: usize = 0,
    udp_datagrams: u64 = 0,
    network_polls: u64 = 0,
    backpressure_events: u64 = 0,
    send_failures: u64 = 0,
    malformed_frames: u64 = 0,
    result_signature: u64 = 0,

    pub fn accounted(self: Result) bool {
        return self.transport_attempts ==
            self.delivered + self.partitioned + self.crashed + self.pending;
    }

    pub fn missingAccounted(self: Result) bool {
        const missing = self.seedMissing();
        return missing ==
            self.never_transmitted +
            self.delivery_faulted +
            self.crashed_before_merge +
            self.pending_at_censor +
            self.unattributed;
    }

    fn seedMissing(self: Result) usize {
        return self.fact_count - self.collector_final;
    }

    pub fn communicationAccounted(self: Result) bool {
        return self.communication_units == self.useful + self.duplicate;
    }

    pub fn fullyAccounted(self: Result) bool {
        return self.accounted() and
            self.missingAccounted() and
            self.communicationAccounted() and
            self.unattributed == 0;
    }
};

const Engine = struct {
    allocator: std.mem.Allocator,
    config: Config,
    theta: stage7a.Theta,
    states: [scaling.max_operators]scaling.State =
        [_]scaling.State{.{}} ** scaling.max_operators,
    initial_states: [scaling.max_operators]scaling.State =
        [_]scaling.State{.{}} ** scaling.max_operators,
    local_round: [scaling.max_operators]u32 =
        [_]u32{0} ** scaling.max_operators,
    sequence: [scaling.max_operators]u32 =
        [_]u32{0} ** scaling.max_operators,
    current_tick: u32 = 0,
    attempts: std.ArrayList(Attempt) = .empty,
    fact_ledger: FactLedger = .{},
    result: Result,

    fn init(
        allocator: std.mem.Allocator,
        config: Config,
        profile: []const u8,
        theta: stage7a.Theta,
    ) !Engine {
        try config.validate();
        try theta.validate();

        var self = Engine{
            .allocator = allocator,
            .config = config,
            .theta = theta,
            .result = .{
                .profile = profile,
                .topology = config.world.topology,
                .seed = config.world.seed,
                .fault = config.fault,
                .fact_count = config.world.fact_count,
            },
        };
        scaling.initializeStates(
            &self.states,
            config.world.asScaling(.round_robin),
        );
        self.initial_states = self.states;
        self.result.collector_initial =
            self.states[scaling.collector_index].knowledge.count(
                config.world.fact_count,
            );
        self.result.collector_final = self.result.collector_initial;
        self.result.success =
            self.result.collector_initial == config.world.fact_count;
        return self;
    }

    fn deinit(self: *Engine) void {
        self.attempts.deinit(self.allocator);
    }

    fn step(
        self: *Engine,
        runtime: *runtime_mod.Runtime,
        tick: u32,
    ) !void {
        self.current_tick = tick;
        if (self.config.fault == .crash_reset and
            tick == self.config.crash_end)
        {
            self.resetCrashedNode();
        } else if (self.config.fault == .crash_persist and
            tick == self.config.crash_end)
        {
            const node = self.config.crash_node;
            self.states[node].sent.clear();
            self.states[node].cursor = 0;
        }

        try runtime.pump(0);
        try runtime.drainFrames(
            self.config.world.fact_count,
            self,
            deliveryCallback,
        );

        var node: usize = 0;
        while (node < self.config.world.population_size) : (node += 1) {
            if (self.isCrashed(node, tick)) continue;

            self.local_round[node] +%= 1;
            self.result.policy_ticks +%= 1;

            const observation = stage7a.Observation.from(
                self.states[node],
                node,
                self.local_round[node],
                self.config.world,
            );
            const action = stage7a.decide(self.theta, observation) orelse continue;
            if (!scaling.validateLocalAction(
                action,
                self.states[node],
                self.config.world.asScaling(.round_robin),
            )) {
                self.result.rejected_actions +%= 1;
                self.result.violations +%= 1;
                continue;
            }

            self.result.actions +%= 1;
            self.sequence[node] +%= 1;
            if (action.reset_sent) self.states[node].sent.clear();
            self.states[node].sent.unionWithFacts(
                action.facts,
                self.config.world.fact_count,
            );
            self.states[node].cursor = action.next_cursor;

            try self.sendToRecipients(
                runtime,
                node,
                self.sequence[node],
                action,
                tick,
            );
        }

        try runtime.pump(0);
        try runtime.pump(0);
        try runtime.drainFrames(
            self.config.world.fact_count,
            self,
            deliveryCallback,
        );

        self.result.ticks = tick;
        self.result.collector_final =
            self.states[scaling.collector_index].knowledge.count(
                self.config.world.fact_count,
            );
        self.result.success =
            self.states[scaling.collector_index].knowledge.containsAll(
                self.config.world.fact_count,
            );
    }

    fn sendToRecipients(
        self: *Engine,
        runtime: *runtime_mod.Runtime,
        sender: usize,
        sequence: u32,
        action: scaling.Action,
        tick: u32,
    ) !void {
        var recipient: usize = 0;
        while (recipient < self.config.world.population_size) : (recipient += 1) {
            if (recipient == sender or
                !isTopologyEdge(
                    self.config.world.topology,
                    sender,
                    recipient,
                    self.config.world.population_size,
                ))
            {
                continue;
            }

            const attempt_index = try self.openAttempt(
                sender,
                recipient,
                sequence,
                action,
            );

            if (self.isPartitioned(sender, recipient, tick)) {
                self.closeAttempt(attempt_index, .partitioned);
                continue;
            }
            if (self.isCrashed(recipient, tick)) {
                self.closeAttempt(attempt_index, .crashed);
                continue;
            }

            const sent = try runtime.send(
                @intCast(sender),
                @intCast(recipient),
                .{
                    .sender = @intCast(sender),
                    .recipient = @intCast(recipient),
                    .sequence = sequence,
                    .selected = action.selected,
                    .facts = action.facts,
                },
                self.config.world.fact_count,
            );
            if (!sent) {
                // Leave the attempt pending. It remains explicitly visible at
                // censor rather than being converted into an invented loss.
            }
        }
    }

    fn openAttempt(
        self: *Engine,
        sender: usize,
        recipient: usize,
        sequence: u32,
        action: scaling.Action,
    ) !usize {
        const index = self.attempts.items.len;
        try self.attempts.append(self.allocator, .{
            .sender = @intCast(sender),
            .recipient = @intCast(recipient),
            .sequence = sequence,
            .selected = action.selected,
            .facts = action.facts,
        });
        self.result.transport_attempts +%= 1;
        self.result.attempted_communication_units +%= action.selected;
        if (recipient == scaling.collector_index) {
            markFactCounts(
                action.facts,
                self.config.world.fact_count,
                &self.fact_ledger.attempted_to_collector,
            );
        }
        return index;
    }

    fn closeAttempt(
        self: *Engine,
        index: usize,
        terminal: Terminal,
    ) void {
        const attempt = &self.attempts.items[index];
        if (attempt.terminal != .pending) {
            self.result.transport_duplicate_deliveries +%= 1;
            self.result.violations +%= 1;
            return;
        }
        attempt.terminal = terminal;
        switch (terminal) {
            .delivered => self.result.delivered +%= 1,
            .partitioned => {
                self.result.partitioned +%= 1;
                markFactCounts(
                    attempt.facts,
                    self.config.world.fact_count,
                    &self.fact_ledger.delivery_faulted,
                );
            },
            .crashed => {
                self.result.crashed +%= 1;
                markFactCounts(
                    attempt.facts,
                    self.config.world.fact_count,
                    &self.fact_ledger.crashed,
                );
            },
            .pending => {},
        }
    }

    fn onEnvelope(self: *Engine, envelope: wire.Envelope) !void {
        const index = self.findAttempt(
            envelope.sender,
            envelope.recipient,
            envelope.sequence,
        ) orelse {
            self.result.violations +%= 1;
            return error.UnknownTransportDelivery;
        };

        const attempt = &self.attempts.items[index];
        if (attempt.terminal != .pending) {
            self.result.transport_duplicate_deliveries +%= 1;
            self.result.violations +%= 1;
            return;
        }
        if (attempt.selected != envelope.selected or
            !scaling.BitSet.eql(attempt.facts, envelope.facts))
        {
            self.result.violations +%= 1;
            return error.TransportPayloadMismatch;
        }

        const sender: usize = @intCast(envelope.sender);
        const recipient: usize = @intCast(envelope.recipient);
        if (self.isPartitioned(sender, recipient, self.current_tick)) {
            self.closeAttempt(index, .partitioned);
            return;
        }
        if (self.isCrashed(recipient, self.current_tick)) {
            self.closeAttempt(index, .crashed);
            return;
        }
        const before = self.states[recipient].knowledge.count(
            self.config.world.fact_count,
        );
        self.states[recipient].knowledge.unionWithFacts(
            envelope.facts,
            self.config.world.fact_count,
        );
        const after = self.states[recipient].knowledge.count(
            self.config.world.fact_count,
        );
        const useful: u64 = @intCast(after - before);

        self.closeAttempt(index, .delivered);
        self.result.communication_units +%= envelope.selected;
        self.result.useful +%= useful;
        self.result.duplicate +%= envelope.selected - useful;
    }

    fn findAttempt(
        self: *Engine,
        sender: u16,
        recipient: u16,
        sequence: u32,
    ) ?usize {
        var i: usize = 0;
        while (i < self.attempts.items.len) : (i += 1) {
            const attempt = self.attempts.items[i];
            if (attempt.sender == sender and
                attempt.recipient == recipient and
                attempt.sequence == sequence)
            {
                return i;
            }
        }
        return null;
    }

    fn resetCrashedNode(self: *Engine) void {
        const node = self.config.crash_node;
        const before = self.states[node].knowledge;
        self.states[node].knowledge = self.initial_states[node].knowledge;
        self.states[node].sent.clear();
        self.states[node].cursor = 0;

        var fact: usize = 0;
        while (fact < self.config.world.fact_count) : (fact += 1) {
            if (before.has(fact) and !self.states[node].knowledge.has(fact)) {
                self.fact_ledger.erased_by_crash[fact] = true;
            }
        }
    }

    fn isPartitioned(
        self: *const Engine,
        sender: usize,
        recipient: usize,
        tick: u32,
    ) bool {
        if (self.config.fault != .partition or
            tick < self.config.partition_start or
            tick >= self.config.partition_end)
        {
            return false;
        }
        return (sender < self.config.partition_cut) !=
            (recipient < self.config.partition_cut);
    }

    fn isCrashed(
        self: *const Engine,
        node: usize,
        tick: u32,
    ) bool {
        if (self.config.fault != .crash_persist and
            self.config.fault != .crash_reset)
        {
            return false;
        }
        return node == self.config.crash_node and
            tick >= self.config.crash_start and
            tick < self.config.crash_end;
    }

    fn finalize(
        self: *Engine,
        runtime: *runtime_mod.Runtime,
    ) !void {
        var drain: usize = 0;
        while (drain < 64) : (drain += 1) {
            try runtime.pump(0);
            try runtime.drainFrames(
                self.config.world.fact_count,
                self,
                deliveryCallback,
            );
        }

        self.result.collector_final =
            self.states[scaling.collector_index].knowledge.count(
                self.config.world.fact_count,
            );
        self.result.success =
            self.states[scaling.collector_index].knowledge.containsAll(
                self.config.world.fact_count,
            );

        for (self.attempts.items) |attempt| {
            if (attempt.terminal != .pending) continue;
            self.result.pending +%= 1;
            if (attempt.recipient == scaling.collector_index) {
                markFactCounts(
                    attempt.facts,
                    self.config.world.fact_count,
                    &self.fact_ledger.pending,
                );
            }
        }

        classifyMissing(
            self.config,
            self.states[scaling.collector_index].knowledge,
            self.fact_ledger,
            &self.result,
        );

        self.result.udp_datagrams = runtime.counters.udp_datagrams_received;
        self.result.network_polls = runtime.counters.poll_iterations;
        self.result.backpressure_events = runtime.counters.backpressure_events;
        self.result.send_failures = runtime.counters.send_failures;
        self.result.malformed_frames = runtime.counters.malformed_frames;
        self.result.result_signature = resultSignature(self.result);
    }
};

fn deliveryCallback(engine: *Engine, envelope: wire.Envelope) !void {
    try engine.onEnvelope(envelope);
}

pub fn run(
    allocator: std.mem.Allocator,
    config: Config,
    profile: []const u8,
    theta: stage7a.Theta,
) !Result {
    var runtime = try runtime_mod.Runtime.init(
        allocator,
        config.world.population_size,
        config.world.topology,
    );
    defer runtime.deinit();

    var engine = try Engine.init(allocator, config, profile, theta);
    defer engine.deinit();

    if (!engine.result.success) {
        var tick: u32 = 1;
        while (tick <= config.max_ticks) : (tick += 1) {
            try engine.step(&runtime, tick);
            if (engine.result.success) break;
        }
    }

    try engine.finalize(&runtime);
    return engine.result;
}

pub fn profileTheta(name: []const u8) ?stage7a.Theta {
    for (f1a.frozen_profiles) |profile| {
        if (std.mem.eql(u8, name, profile.name)) return profile.theta;
    }
    return null;
}

pub fn canonicalConfig(
    seed: u64,
    topology: scaling.TopologyKind,
    fault: FaultKind,
) Config {
    return .{
        .world = .{
            .population_size = 8,
            .fact_count = 32,
            .topology = topology,
            .redundancy = 2,
            .bandwidth = 2,
            .seed = seed,
            .max_rounds = 4096,
        },
        .fault = fault,
    };
}

fn markFactCounts(
    facts: scaling.BitSet,
    fact_count: usize,
    counts: *[scaling.max_facts]u32,
) void {
    var fact: usize = 0;
    while (fact < fact_count) : (fact += 1) {
        if (facts.has(fact)) counts[fact] +%= 1;
    }
}

fn classifyMissing(
    config: Config,
    collector: scaling.BitSet,
    ledger: FactLedger,
    result: *Result,
) void {
    var fact: usize = 0;
    while (fact < config.world.fact_count) : (fact += 1) {
        if (collector.has(fact)) continue;
        if (ledger.pending[fact] > 0) {
            result.pending_at_censor += 1;
        } else if (ledger.erased_by_crash[fact] or ledger.crashed[fact] > 0) {
            result.crashed_before_merge += 1;
        } else if (ledger.delivery_faulted[fact] > 0) {
            result.delivery_faulted += 1;
        } else if (ledger.attempted_to_collector[fact] == 0) {
            result.never_transmitted += 1;
        } else {
            result.unattributed += 1;
        }
    }
}

fn isTopologyEdge(
    topology: scaling.TopologyKind,
    sender: usize,
    recipient: usize,
    population: usize,
) bool {
    return switch (topology) {
        .complete => sender != recipient,
        .ring => blk: {
            const left = (sender + population - 1) % population;
            const right = (sender + 1) % population;
            break :blk recipient == left or recipient == right;
        },
        .grid => blk: {
            const width = scaling.gridWidth(population);
            const sr = sender / width;
            const sc = sender % width;
            const rr = recipient / width;
            const rc = recipient % width;
            const row_neighbor = sr == rr and
                (if (sc > rc) sc - rc == 1 else rc - sc == 1);
            const col_neighbor = sc == rc and
                (if (sr > rr) sr - rr == 1 else rr - sr == 1);
            break :blk row_neighbor or col_neighbor;
        },
    };
}

fn resultSignature(result: Result) u64 {
    var hash: u64 = 0xcbf29ce484222325;
    const values = [_]u64{
        result.seed,
        @as(u64, @intFromEnum(result.topology)),
        @as(u64, @intFromEnum(result.fault)),
        @as(u64, @intFromBool(result.success)),
        @as(u64, @intCast(result.fact_count)),
        @as(u64, result.ticks),
        @as(u64, @intCast(result.collector_final)),
        result.policy_ticks,
        result.actions,
        result.transport_attempts,
        result.delivered,
        result.partitioned,
        result.crashed,
        result.pending,
        result.attempted_communication_units,
        result.communication_units,
        result.useful,
        result.duplicate,
        result.violations,
        @as(u64, @intCast(result.never_transmitted)),
        @as(u64, @intCast(result.delivery_faulted)),
        @as(u64, @intCast(result.crashed_before_merge)),
        @as(u64, @intCast(result.pending_at_censor)),
        @as(u64, @intCast(result.unattributed)),
        result.udp_datagrams,
        result.network_polls,
        result.backpressure_events,
        result.send_failures,
        result.malformed_frames,
    };
    for (values) |value| {
        var shift: usize = 0;
        while (shift < 64) : (shift += 8) {
            hash ^= @as(u8, @truncate(value >> @intCast(shift)));
            hash *%= 0x100000001b3;
        }
    }
    return hash;
}

test "F1c canonical profiles remain frozen" {
    try std.testing.expect(profileTheta("theta37") != null);
    try std.testing.expect(profileTheta("theta51") != null);
    try std.testing.expect(profileTheta("theta93") != null);
    try std.testing.expect(profileTheta("novel_first") != null);
    try std.testing.expect(profileTheta("unknown") == null);
}

test "F1c fault names round trip" {
    const values = [_]FaultKind{
        .no_fault,
        .partition,
        .crash_persist,
        .crash_reset,
    };
    for (values) |value| {
        try std.testing.expectEqual(value, FaultKind.parse(value.name()).?);
    }
}
