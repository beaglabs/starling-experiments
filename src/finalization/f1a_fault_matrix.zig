const std = @import("std");
const baseline = @import("../substrate/stage7/stage7c_async_transfer.zig");
const stage7a = @import("../substrate/stage7/stage7a_policy.zig");
const scaling = @import("../substrate/stage5/stage5a_scaling.zig");

pub const max_pending_events: usize = 4096;

pub const Error = stage7a.Error || error{
    InvalidTickHorizon,
    InvalidClockJitter,
    InvalidLatency,
    InvalidPermille,
    InvalidPartition,
    InvalidCrash,
    InvalidQueueCapacity,
    InvalidReorderDelay,
};

pub const theta37 = stage7a.Theta{
    .novelty_permille = 244,
    .exploration_permille = 94,
    .retry_permille = 15,
    .bandwidth_utilization_permille = 958,
};

pub const theta51 = stage7a.Theta{
    .novelty_permille = 354,
    .exploration_permille = 141,
    .retry_permille = 0,
    .bandwidth_utilization_permille = 994,
};

pub const theta93 = stage7a.Theta{
    .novelty_permille = 685,
    .exploration_permille = 283,
    .retry_permille = 960,
    .bandwidth_utilization_permille = 344,
};

pub const Profile = struct {
    name: []const u8,
    theta: stage7a.Theta,
};

pub const frozen_profiles = [_]Profile{
    .{ .name = "theta37", .theta = theta37 },
    .{ .name = "theta51", .theta = theta51 },
    .{ .name = "theta93", .theta = theta93 },
    .{ .name = "novel_first", .theta = stage7a.novel_first_theta },
};

pub const canonical_profiles = [_]Profile{
    .{ .name = "theta37", .theta = theta37 },
    .{ .name = "theta51", .theta = theta51 },
    .{ .name = "theta93", .theta = theta93 },
    .{ .name = "round_robin", .theta = stage7a.round_robin_theta },
    .{ .name = "seeded", .theta = stage7a.seeded_theta },
    .{ .name = "novel_first", .theta = stage7a.novel_first_theta },
};

pub const FaultKind = enum {
    no_fault,
    loss_50,
    loss_200,
    duplication_250,
    latency_jitter,
    reordering,
    partition,
    crash_persist,
    crash_reset,
    stale_view,
    queue_capacity,
    combined,

    pub fn name(self: FaultKind) []const u8 {
        return switch (self) {
            .no_fault => "no_fault",
            .loss_50 => "loss_50",
            .loss_200 => "loss_200",
            .duplication_250 => "duplication_250",
            .latency_jitter => "latency_jitter",
            .reordering => "reordering",
            .partition => "partition",
            .crash_persist => "crash_restart_persist",
            .crash_reset => "crash_restart_reset",
            .stale_view => "stale_view",
            .queue_capacity => "queue_capacity",
            .combined => "combined",
        };
    }
};

pub const canonical_faults = [_]FaultKind{
    .no_fault,
    .loss_50,
    .loss_200,
    .duplication_250,
    .latency_jitter,
    .reordering,
    .partition,
    .crash_persist,
    .crash_reset,
    .stale_view,
    .queue_capacity,
    .combined,
};

pub const Config = struct {
    world: stage7a.Config,
    schedule_seed: u64 = 0,
    max_ticks: u32 = 4096,
    /// Node i ticks every 1..clock_jitter ticks, deterministically.
    clock_jitter: u16 = 3,
    latency_min: u16 = 1,
    latency_jitter: u16 = 3,
    loss_permille: u16 = 0,
    duplicate_permille: u16 = 0,
    /// A cut partitions [0, partition_cut) from the remaining nodes.
    partition_start: u32 = 0,
    partition_end: u32 = 0,
    partition_cut: usize = 0,
    /// crash_node == population_size disables crash injection.
    crash_node: usize = 0,
    crash_start: u32 = 0,
    crash_end: u32 = 0,
    persist_knowledge: bool = true,
    /// Effective capacity of the pending-delivery queue.
    queue_capacity: usize = max_pending_events,
    /// Refresh interval for policy-visible knowledge; 0 means current state.
    stale_view_ticks: u16 = 0,
    /// Deterministically delay odd ordinals so later envelopes can overtake.
    force_reordering: bool = false,
    reorder_delay: u16 = 8,

    pub fn validate(self: Config) Error!void {
        try self.world.validate();
        if (self.max_ticks == 0) return error.InvalidTickHorizon;
        if (self.clock_jitter == 0) return error.InvalidClockJitter;
        if (self.latency_min == 0) return error.InvalidLatency;
        if (self.loss_permille > 1000 or self.duplicate_permille > 1000) {
            return error.InvalidPermille;
        }
        if ((self.partition_start == 0) != (self.partition_end == 0)) {
            return error.InvalidPartition;
        }
        if (self.partition_start != 0) {
            if (self.partition_start >= self.partition_end or
                self.partition_end > self.max_ticks or
                self.partition_cut == 0 or
                self.partition_cut >= self.world.population_size)
            {
                return error.InvalidPartition;
            }
        }
        if ((self.crash_start == 0) != (self.crash_end == 0)) {
            return error.InvalidCrash;
        }
        if (self.crash_start != 0) {
            if (self.crash_start >= self.crash_end or
                self.crash_end > self.max_ticks or
                self.crash_node >= self.world.population_size)
            {
                return error.InvalidCrash;
            }
        }
        if (self.queue_capacity == 0 or self.queue_capacity > max_pending_events) {
            return error.InvalidQueueCapacity;
        }
        if (self.force_reordering and self.reorder_delay == 0) {
            return error.InvalidReorderDelay;
        }
    }
};

const Envelope = struct {
    sender: u16,
    recipient: u16,
    sequence: u32,
    copy: u8,
    facts: scaling.BitSet,
    selected: u16,
};

const Delivery = struct {
    due_tick: u32,
    ordinal: u64,
    envelope: Envelope,
};

const FactLedger = struct {
    attempted: [scaling.max_facts]u32 = [_]u32{0} ** scaling.max_facts,
    attempted_to_collector: [scaling.max_facts]u32 = [_]u32{0} ** scaling.max_facts,
    delivery_faulted: [scaling.max_facts]u32 = [_]u32{0} ** scaling.max_facts,
    crashed: [scaling.max_facts]u32 = [_]u32{0} ** scaling.max_facts,
    erased_by_crash: [scaling.max_facts]bool = [_]bool{false} ** scaling.max_facts,
    pending: [scaling.max_facts]u32 = [_]u32{0} ** scaling.max_facts,
};

pub const Result = struct {
    config: Config,
    theta: stage7a.Theta,
    success: bool,
    elapsed_ticks: u32,
    collector_initial_facts: usize,
    collector_final_facts: usize,
    local_policy_ticks: u64,
    actions: u64,
    rejected_actions: u64,
    transport_attempts: u64,
    delivered_envelopes: u64,
    dropped_envelopes: u64,
    partitioned_envelopes: u64,
    crashed_envelopes: u64,
    queue_overflow_envelopes: u64,
    pending_envelopes: u64,
    duplicate_copies: u64,
    reordered_envelopes: u64,
    communication_units: u64,
    useful_deliveries: u64,
    duplicate_deliveries: u64,
    schedule_hash: u64,
    trace_hash: u64,
    violations: u64,
    stale_observations: u64,
    forced_reorder_schedules: u64,
    never_transmitted_missing: usize,
    delivery_faulted_missing: usize,
    crashed_before_merge_missing: usize,
    pending_at_censor_missing: usize,
    unattributed_missing: usize,

    pub fn accounted(self: Result) bool {
        return self.transport_attempts ==
            self.delivered_envelopes +
            self.dropped_envelopes +
            self.partitioned_envelopes +
            self.crashed_envelopes +
            self.queue_overflow_envelopes +
            self.pending_envelopes;
    }

    pub fn missingAccounted(self: Result) bool {
        const missing = self.config.world.fact_count - self.collector_final_facts;
        return missing ==
            self.never_transmitted_missing +
            self.delivery_faulted_missing +
            self.crashed_before_merge_missing +
            self.pending_at_censor_missing +
            self.unattributed_missing;
    }

    pub fn fullyAccounted(self: Result) bool {
        return self.accounted() and self.missingAccounted() and
            self.unattributed_missing == 0 and
            self.communication_units ==
                self.useful_deliveries + self.duplicate_deliveries;
    }
};

pub fn run(config: Config, theta: stage7a.Theta) Error!Result {
    try config.validate();
    try theta.validate();

    var states = [_]scaling.State{.{}} ** scaling.max_operators;
    scaling.initializeStates(&states, config.world.asScaling(.round_robin));
    const initial_states = states;

    var observed_knowledge = [_]scaling.BitSet{.{}} ** scaling.max_operators;
    var last_view_refresh = [_]u32{0} ** scaling.max_operators;
    var init_node: usize = 0;
    while (init_node < config.world.population_size) : (init_node += 1) {
        observed_knowledge[init_node] = states[init_node].knowledge;
    }
    var fact_ledger = FactLedger{};

    const initial_facts =
        states[scaling.collector_index].knowledge.count(config.world.fact_count);
    var result = Result{
        .config = config,
        .theta = theta,
        .success = initial_facts == config.world.fact_count,
        .elapsed_ticks = 0,
        .collector_initial_facts = initial_facts,
        .collector_final_facts = initial_facts,
        .local_policy_ticks = 0,
        .actions = 0,
        .rejected_actions = 0,
        .transport_attempts = 0,
        .delivered_envelopes = 0,
        .dropped_envelopes = 0,
        .partitioned_envelopes = 0,
        .crashed_envelopes = 0,
        .queue_overflow_envelopes = 0,
        .pending_envelopes = 0,
        .duplicate_copies = 0,
        .reordered_envelopes = 0,
        .communication_units = 0,
        .useful_deliveries = 0,
        .duplicate_deliveries = 0,
        .schedule_hash = mix64(config.schedule_seed ^ 0x5343484544554c45),
        .trace_hash = mix64(config.schedule_seed ^ 0x54524143455f3743),
        .violations = 0,
        .stale_observations = 0,
        .forced_reorder_schedules = 0,
        .never_transmitted_missing = 0,
        .delivery_faulted_missing = 0,
        .crashed_before_merge_missing = 0,
        .pending_at_censor_missing = 0,
        .unattributed_missing = 0,
    };
    if (result.success) return result;

    var pending: [max_pending_events]Delivery = undefined;
    var pending_len: usize = 0;
    var next_tick = [_]u32{0} ** scaling.max_operators;
    var periods = [_]u16{0} ** scaling.max_operators;
    var local_round = [_]u32{0} ** scaling.max_operators;
    var sequence = [_]u32{0} ** scaling.max_operators;
    var last_ordinal = [_]u64{0} ** scaling.max_operators;

    var node: usize = 0;
    while (node < config.world.population_size) : (node += 1) {
        const clock_key = keyed(config.schedule_seed, node, 0, 0x434c4f434b);
        periods[node] = @intCast(
            1 + clock_key % @as(u64, config.clock_jitter),
        );
        next_tick[node] = @intCast(
            1 + mix64(clock_key) % @as(u64, periods[node]),
        );
    }

    var tick: u32 = 1;
    while (tick <= config.max_ticks) : (tick += 1) {
        if (config.crash_end != 0 and tick == config.crash_end) {
            const crashed = config.crash_node;
            if (!config.persist_knowledge) {
                const before_reset = states[crashed].knowledge;
                states[crashed].knowledge = initial_states[crashed].knowledge;
                markCrashErasure(
                    before_reset,
                    states[crashed].knowledge,
                    config.world.fact_count,
                    &fact_ledger,
                );
            }
            states[crashed].sent.clear();
            states[crashed].cursor = 0;
            observed_knowledge[crashed] = states[crashed].knowledge;
            last_view_refresh[crashed] = tick;
            result.trace_hash = fold(result.trace_hash, 0x52455354415254, tick, crashed);
        }

        while (findDue(&pending, pending_len, tick)) |index| {
            const delivery = pending[index];
            pending_len -= 1;
            if (index != pending_len) pending[index] = pending[pending_len];
            applyDelivery(
                config,
                delivery,
                tick,
                &states,
                &last_ordinal,
                &result,
                &fact_ledger,
            );
        }

        node = 0;
        while (node < config.world.population_size) : (node += 1) {
            if (next_tick[node] != tick) continue;
            next_tick[node] +%= periods[node];
            if (isCrashed(config, node, tick)) continue;

            local_round[node] +%= 1;
            result.local_policy_ticks +%= 1;
            result.trace_hash = fold(
                result.trace_hash,
                0x504f4c494359,
                tick,
                node,
            );

            var observation_state = states[node];
            if (config.stale_view_ticks != 0) {
                if (tick - last_view_refresh[node] >= config.stale_view_ticks) {
                    observed_knowledge[node] = states[node].knowledge;
                    last_view_refresh[node] = tick;
                } else if (!scaling.BitSet.eql(
                    observed_knowledge[node],
                    states[node].knowledge,
                )) {
                    result.stale_observations +%= 1;
                    result.trace_hash = fold(
                        result.trace_hash,
                        0x5354414c455f5649,
                        tick,
                        node,
                    );
                }
                observation_state.knowledge = observed_knowledge[node];
            }
            const observation = stage7a.Observation.from(
                observation_state,
                node,
                local_round[node],
                config.world,
            );
            const action = stage7a.decide(theta, observation) orelse continue;
            if (!scaling.validateLocalAction(
                action,
                states[node],
                config.world.asScaling(.round_robin),
            )) {
                result.rejected_actions +%= 1;
                result.violations +%= 1;
                continue;
            }

            result.actions +%= 1;
            sequence[node] +%= 1;
            if (action.reset_sent) states[node].sent.clear();
            states[node].sent.unionWithFacts(action.facts, config.world.fact_count);
            states[node].cursor = action.next_cursor;

            scheduleRecipients(
                config,
                node,
                sequence[node],
                action,
                tick,
                &pending,
                &pending_len,
                &result,
                &fact_ledger,
            );
        }

        result.elapsed_ticks = tick;
        result.collector_final_facts =
            states[scaling.collector_index].knowledge.count(config.world.fact_count);
        if (states[scaling.collector_index].knowledge.containsAll(
            config.world.fact_count,
        )) {
            result.success = true;
            break;
        }
    }

    var pending_index: usize = 0;
    while (pending_index < pending_len) : (pending_index += 1) {
        markFactCounts(
            pending[pending_index].envelope.facts,
            config.world.fact_count,
            &fact_ledger.pending,
        );
    }
    result.pending_envelopes = @intCast(pending_len);
    classifyMissing(
        config,
        states[scaling.collector_index].knowledge,
        fact_ledger,
        &result,
    );
    std.debug.assert(result.accounted());
    std.debug.assert(result.missingAccounted());
    std.debug.assert(
        result.communication_units ==
            result.useful_deliveries + result.duplicate_deliveries,
    );
    return result;
}

fn scheduleRecipients(
    config: Config,
    sender: usize,
    sequence: u32,
    action: scaling.Action,
    tick: u32,
    pending: *[max_pending_events]Delivery,
    pending_len: *usize,
    result: *Result,
    fact_ledger: *FactLedger,
) void {
    switch (config.world.topology) {
        .ring => {
            const left =
                (sender + config.world.population_size - 1) %
                config.world.population_size;
            const right = (sender + 1) % config.world.population_size;
            scheduleEnvelope(config, sender, left, sequence, action, tick, pending, pending_len, result, fact_ledger);
            if (right != left) {
                scheduleEnvelope(config, sender, right, sequence, action, tick, pending, pending_len, result, fact_ledger);
            }
        },
        .complete => {
            var recipient: usize = 0;
            while (recipient < config.world.population_size) : (recipient += 1) {
                if (recipient == sender) continue;
                scheduleEnvelope(config, sender, recipient, sequence, action, tick, pending, pending_len, result, fact_ledger);
            }
        },
        .grid => {
            const width = scaling.gridWidth(config.world.population_size);
            const row = sender / width;
            const col = sender % width;
            if (col > 0) scheduleEnvelope(config, sender, sender - 1, sequence, action, tick, pending, pending_len, result, fact_ledger);
            if (col + 1 < width and sender + 1 < config.world.population_size and
                (sender + 1) / width == row)
            {
                scheduleEnvelope(config, sender, sender + 1, sequence, action, tick, pending, pending_len, result, fact_ledger);
            }
            if (sender >= width) scheduleEnvelope(config, sender, sender - width, sequence, action, tick, pending, pending_len, result, fact_ledger);
            if (sender + width < config.world.population_size) {
                scheduleEnvelope(config, sender, sender + width, sequence, action, tick, pending, pending_len, result, fact_ledger);
            }
        },
    }
}

fn scheduleEnvelope(
    config: Config,
    sender: usize,
    recipient: usize,
    sequence: u32,
    action: scaling.Action,
    tick: u32,
    pending: *[max_pending_events]Delivery,
    pending_len: *usize,
    result: *Result,
    fact_ledger: *FactLedger,
) void {
    scheduleCopy(config, sender, recipient, sequence, action, tick, 0, pending, pending_len, result, fact_ledger);
    const duplicate_key = keyed(config.schedule_seed, sender, recipient, sequence) ^ 0x4455504c49434154;
    if (duplicate_key % 1000 < config.duplicate_permille) {
        scheduleCopy(config, sender, recipient, sequence, action, tick, 1, pending, pending_len, result, fact_ledger);
    }
}

fn scheduleCopy(
    config: Config,
    sender: usize,
    recipient: usize,
    sequence: u32,
    action: scaling.Action,
    tick: u32,
    copy: u8,
    pending: *[max_pending_events]Delivery,
    pending_len: *usize,
    result: *Result,
    fact_ledger: *FactLedger,
) void {
    const ordinal = result.transport_attempts + 1;
    result.transport_attempts = ordinal;
    markFactCounts(action.facts, config.world.fact_count, &fact_ledger.attempted);
    if (recipient == scaling.collector_index) {
        markFactCounts(
            action.facts,
            config.world.fact_count,
            &fact_ledger.attempted_to_collector,
        );
    }
    const key = keyed(
        config.schedule_seed ^ @as(u64, copy),
        sender,
        recipient,
        sequence,
    );
    result.schedule_hash = fold(result.schedule_hash, key, tick, ordinal);
    if (key % 1000 < config.loss_permille) {
        result.dropped_envelopes +%= 1;
        markFactCounts(
            action.facts,
            config.world.fact_count,
            &fact_ledger.delivery_faulted,
        );
        return;
    }
    if (pending_len.* >= config.queue_capacity) {
        result.queue_overflow_envelopes +%= 1;
        markFactCounts(
            action.facts,
            config.world.fact_count,
            &fact_ledger.delivery_faulted,
        );
        return;
    }
    const jitter = if (config.latency_jitter == 0)
        0
    else
        mix64(key) % (@as(u64, config.latency_jitter) + 1);
    var due_tick =
        tick +
        @as(u32, config.latency_min) +
        @as(u32, @intCast(jitter));
    if (config.force_reordering and (ordinal & 1) == 1) {
        due_tick +%= config.reorder_delay;
        result.forced_reorder_schedules +%= 1;
        result.schedule_hash = fold(
            result.schedule_hash,
            0x52454f524445525f,
            due_tick,
            ordinal,
        );
    }
    pending[pending_len.*] = .{
        .due_tick = due_tick,
        .ordinal = ordinal,
        .envelope = .{
            .sender = @intCast(sender),
            .recipient = @intCast(recipient),
            .sequence = sequence,
            .copy = copy,
            .facts = action.facts,
            .selected = action.selected,
        },
    };
    pending_len.* += 1;
}

fn markFactCounts(
    facts: scaling.BitSet,
    fact_count: usize,
    counters: *[scaling.max_facts]u32,
) void {
    var fact: usize = 0;
    while (fact < fact_count) : (fact += 1) {
        if (facts.has(fact)) counters[fact] +%= 1;
    }
}

fn markCrashErasure(
    before: scaling.BitSet,
    after: scaling.BitSet,
    fact_count: usize,
    fact_ledger: *FactLedger,
) void {
    var fact: usize = 0;
    while (fact < fact_count) : (fact += 1) {
        if (before.has(fact) and !after.has(fact)) {
            fact_ledger.erased_by_crash[fact] = true;
        }
    }
}

fn classifyMissing(
    config: Config,
    collector_knowledge: scaling.BitSet,
    fact_ledger: FactLedger,
    result: *Result,
) void {
    var fact: usize = 0;
    while (fact < config.world.fact_count) : (fact += 1) {
        if (collector_knowledge.has(fact)) continue;

        if (fact_ledger.pending[fact] != 0) {
            result.pending_at_censor_missing += 1;
        } else if (fact_ledger.erased_by_crash[fact] or
            fact_ledger.crashed[fact] != 0)
        {
            result.crashed_before_merge_missing += 1;
        } else if (fact_ledger.delivery_faulted[fact] != 0) {
            result.delivery_faulted_missing += 1;
        } else if (fact_ledger.attempted_to_collector[fact] == 0) {
            result.never_transmitted_missing += 1;
        } else {
            result.unattributed_missing += 1;
        }
    }
}

fn findDue(
    pending: *const [max_pending_events]Delivery,
    pending_len: usize,
    tick: u32,
) ?usize {
    var found: ?usize = null;
    var i: usize = 0;
    while (i < pending_len) : (i += 1) {
        if (pending[i].due_tick > tick) continue;
        if (found == null or
            pending[i].due_tick < pending[found.?].due_tick or
            (pending[i].due_tick == pending[found.?].due_tick and
                pending[i].ordinal < pending[found.?].ordinal))
        {
            found = i;
        }
    }
    return found;
}

fn applyDelivery(
    config: Config,
    delivery: Delivery,
    tick: u32,
    states: *[scaling.max_operators]scaling.State,
    last_ordinal: *[scaling.max_operators]u64,
    result: *Result,
    fact_ledger: *FactLedger,
) void {
    const envelope = delivery.envelope;
    const sender: usize = @intCast(envelope.sender);
    const recipient: usize = @intCast(envelope.recipient);
    if (isPartitioned(config, sender, recipient, tick)) {
        result.partitioned_envelopes +%= 1;
        markFactCounts(
            envelope.facts,
            config.world.fact_count,
            &fact_ledger.delivery_faulted,
        );
        return;
    }
    if (isCrashed(config, recipient, tick)) {
        result.crashed_envelopes +%= 1;
        markFactCounts(
            envelope.facts,
            config.world.fact_count,
            &fact_ledger.crashed,
        );
        return;
    }
    if (last_ordinal[recipient] > delivery.ordinal) {
        result.reordered_envelopes +%= 1;
    }
    last_ordinal[recipient] = @max(last_ordinal[recipient], delivery.ordinal);
    if (envelope.copy != 0) result.duplicate_copies +%= 1;

    const before = states[recipient].knowledge.count(config.world.fact_count);
    states[recipient].knowledge.unionWithFacts(
        envelope.facts,
        config.world.fact_count,
    );
    const after = states[recipient].knowledge.count(config.world.fact_count);
    const useful: u64 = @intCast(after - before);
    const selected: u64 = envelope.selected;
    result.delivered_envelopes +%= 1;
    result.communication_units +%= selected;
    result.useful_deliveries +%= useful;
    result.duplicate_deliveries +%= selected - useful;
    result.trace_hash = fold(
        result.trace_hash,
        delivery.ordinal,
        tick,
        (@as(u64, envelope.sender) << 32) |
            (@as(u64, envelope.recipient) << 16) |
            envelope.sequence,
    );
}

fn isPartitioned(
    config: Config,
    sender: usize,
    recipient: usize,
    tick: u32,
) bool {
    if (config.partition_start == 0 or
        tick < config.partition_start or tick >= config.partition_end)
    {
        return false;
    }
    return (sender < config.partition_cut) !=
        (recipient < config.partition_cut);
}

fn isCrashed(config: Config, node: usize, tick: u32) bool {
    return config.crash_start != 0 and
        node == config.crash_node and
        tick >= config.crash_start and tick < config.crash_end;
}

fn keyed(seed: u64, a: usize, b: usize, c: anytype) u64 {
    return mix64(
        seed ^
        (@as(u64, @intCast(a)) *% 0x9e3779b97f4a7c15) ^
        (@as(u64, @intCast(b)) *% 0xbf58476d1ce4e5b9) ^
        (@as(u64, @intCast(c)) *% 0x94d049bb133111eb),
    );
}

fn fold(hash: u64, a: u64, b: anytype, c: anytype) u64 {
    return mix64(
        hash ^ a ^
        (@as(u64, @intCast(b)) *% 0x9e3779b97f4a7c15) ^
        (@as(u64, @intCast(c)) *% 0xbf58476d1ce4e5b9),
    );
}

fn mix64(value: u64) u64 {
    var x = value;
    x ^= x >> 30;
    x *%= 0xbf58476d1ce4e5b9;
    x ^= x >> 27;
    x *%= 0x94d049bb133111eb;
    x ^= x >> 31;
    return x;
}

pub fn canonicalConfig(
    seed: u64,
    topology: scaling.TopologyKind,
    fault: FaultKind,
) Config {
    var config = Config{
        .world = .{
            .population_size = 8,
            .fact_count = 32,
            .topology = topology,
            .redundancy = 2,
            .bandwidth = 2,
            .seed = seed,
            .max_rounds = 4096,
        },
        .schedule_seed = seed,
        .max_ticks = 4096,
        .clock_jitter = 3,
        .latency_min = 1,
        .latency_jitter = 4,
    };

    switch (fault) {
        .no_fault => {},
        .loss_50 => config.loss_permille = 50,
        .loss_200 => config.loss_permille = 200,
        .duplication_250 => config.duplicate_permille = 250,
        .latency_jitter => {
            config.latency_min = 4;
            config.latency_jitter = 12;
        },
        .reordering => {
            config.force_reordering = true;
            config.reorder_delay = 12;
        },
        .partition => {
            config.partition_start = 8;
            config.partition_end = 48;
            config.partition_cut = 4;
        },
        .crash_persist, .crash_reset => {
            config.crash_node = scaling.collector_index;
            config.crash_start = 8;
            config.crash_end = 40;
            config.persist_knowledge = fault == .crash_persist;
        },
        .stale_view => config.stale_view_ticks = 8,
        .queue_capacity => config.queue_capacity = 8,
        .combined => {
            config.loss_permille = 100;
            config.duplicate_permille = 250;
            config.latency_min = 3;
            config.latency_jitter = 10;
            config.partition_start = 8;
            config.partition_end = 48;
            config.partition_cut = 4;
        },
    }

    return config;
}

fn toBaselineConfig(config: Config) baseline.Config {
    return .{
        .world = config.world,
        .schedule_seed = config.schedule_seed,
        .max_ticks = config.max_ticks,
        .clock_jitter = config.clock_jitter,
        .latency_min = config.latency_min,
        .latency_jitter = config.latency_jitter,
        .loss_permille = config.loss_permille,
        .duplicate_permille = config.duplicate_permille,
        .partition_start = config.partition_start,
        .partition_end = config.partition_end,
        .partition_cut = config.partition_cut,
        .crash_node = config.crash_node,
        .crash_start = config.crash_start,
        .crash_end = config.crash_end,
        .persist_knowledge = config.persist_knowledge,
    };
}

fn smokeConfig() Config {
    return .{
        .world = .{
            .population_size = 8,
            .fact_count = 32,
            .topology = .ring,
            .redundancy = 2,
            .bandwidth = 2,
            .seed = 0,
            .max_rounds = 4096,
        },
        .schedule_seed = 7,
        .max_ticks = 4096,
        .clock_jitter = 3,
        .latency_min = 1,
        .latency_jitter = 4,
    };
}

test "Stage 7C frozen profiles are exact" {
    try std.testing.expect(theta37.eql(.{
        .novelty_permille = 244,
        .exploration_permille = 94,
        .retry_permille = 15,
        .bandwidth_utilization_permille = 958,
    }));
    try std.testing.expect(theta51.eql(.{
        .novelty_permille = 354,
        .exploration_permille = 141,
        .retry_permille = 0,
        .bandwidth_utilization_permille = 994,
    }));
    try std.testing.expect(theta93.eql(.{
        .novelty_permille = 685,
        .exploration_permille = 283,
        .retry_permille = 960,
        .bandwidth_utilization_permille = 344,
    }));
}

test "Stage 7C schedule and result are deterministic" {
    const config = smokeConfig();
    const a = try run(config, theta51);
    const b = try run(config, theta51);
    try std.testing.expectEqual(a.schedule_hash, b.schedule_hash);
    try std.testing.expectEqual(a.trace_hash, b.trace_hash);
    try std.testing.expectEqual(a.success, b.success);
    try std.testing.expectEqual(a.collector_final_facts, b.collector_final_facts);
    try std.testing.expect(a.accounted());
    try std.testing.expect(b.accounted());
}

test "Stage 7C no-fault smoke converges" {
    const result = try run(smokeConfig(), theta51);
    try std.testing.expect(result.success);
    try std.testing.expectEqual(@as(usize, 32), result.collector_final_facts);
    try std.testing.expectEqual(@as(u64, 0), result.dropped_envelopes);
    try std.testing.expectEqual(@as(u64, 0), result.partitioned_envelopes);
    try std.testing.expectEqual(@as(u64, 0), result.crashed_envelopes);
    try std.testing.expect(result.accounted());
}

test "Stage 7C disruption accounting has no silent loss" {
    var config = smokeConfig();
    config.loss_permille = 100;
    config.duplicate_permille = 250;
    config.partition_start = 8;
    config.partition_end = 24;
    config.partition_cut = 4;
    config.crash_node = 3;
    config.crash_start = 30;
    config.crash_end = 45;
    const result = try run(config, theta51);
    try std.testing.expect(result.dropped_envelopes > 0);
    try std.testing.expect(result.duplicate_copies > 0);
    try std.testing.expect(result.partitioned_envelopes > 0);
    try std.testing.expect(result.crashed_envelopes > 0);
    try std.testing.expect(result.accounted());
    try std.testing.expectEqual(
        result.communication_units,
        result.useful_deliveries + result.duplicate_deliveries,
    );
}

test "Stage 7C schedule seed changes the event schedule" {
    const a = try run(smokeConfig(), theta51);
    var config = smokeConfig();
    config.schedule_seed += 1;
    const b = try run(config, theta51);
    try std.testing.expect(a.schedule_hash != b.schedule_hash);
}


fn expectBaselineEqual(actual: Result, expected: baseline.Result) !void {
    try std.testing.expectEqual(expected.success, actual.success);
    try std.testing.expectEqual(expected.elapsed_ticks, actual.elapsed_ticks);
    try std.testing.expectEqual(expected.collector_initial_facts, actual.collector_initial_facts);
    try std.testing.expectEqual(expected.collector_final_facts, actual.collector_final_facts);
    try std.testing.expectEqual(expected.local_policy_ticks, actual.local_policy_ticks);
    try std.testing.expectEqual(expected.actions, actual.actions);
    try std.testing.expectEqual(expected.rejected_actions, actual.rejected_actions);
    try std.testing.expectEqual(expected.transport_attempts, actual.transport_attempts);
    try std.testing.expectEqual(expected.delivered_envelopes, actual.delivered_envelopes);
    try std.testing.expectEqual(expected.dropped_envelopes, actual.dropped_envelopes);
    try std.testing.expectEqual(expected.partitioned_envelopes, actual.partitioned_envelopes);
    try std.testing.expectEqual(expected.crashed_envelopes, actual.crashed_envelopes);
    try std.testing.expectEqual(expected.queue_overflow_envelopes, actual.queue_overflow_envelopes);
    try std.testing.expectEqual(expected.pending_envelopes, actual.pending_envelopes);
    try std.testing.expectEqual(expected.duplicate_copies, actual.duplicate_copies);
    try std.testing.expectEqual(expected.reordered_envelopes, actual.reordered_envelopes);
    try std.testing.expectEqual(expected.communication_units, actual.communication_units);
    try std.testing.expectEqual(expected.useful_deliveries, actual.useful_deliveries);
    try std.testing.expectEqual(expected.duplicate_deliveries, actual.duplicate_deliveries);
    try std.testing.expectEqual(expected.schedule_hash, actual.schedule_hash);
    try std.testing.expectEqual(expected.trace_hash, actual.trace_hash);
    try std.testing.expectEqual(expected.violations, actual.violations);
}

test "F1a no-fault rows exactly reproduce the frozen Stage 7C suite" {
    const topologies = [_]scaling.TopologyKind{ .ring, .grid };
    for (baseline.frozen_profiles) |profile| {
        for (topologies) |topology| {
            var seed: u64 = 0;
            while (seed < 3) : (seed += 1) {
                const config = canonicalConfig(seed, topology, .no_fault);
                const actual = try run(config, profile.theta);
                const expected = try baseline.run(toBaselineConfig(config), profile.theta);
                try expectBaselineEqual(actual, expected);
                try std.testing.expect(actual.fullyAccounted());
            }
        }
    }
}

test "F1a canonical fault worlds have complete ledgers" {
    for (canonical_faults) |fault| {
        const result = try run(canonicalConfig(0, .ring, fault), theta51);
        try std.testing.expect(result.accounted());
        try std.testing.expect(result.missingAccounted());
        try std.testing.expectEqual(@as(usize, 0), result.unattributed_missing);
        try std.testing.expectEqual(
            result.communication_units,
            result.useful_deliveries + result.duplicate_deliveries,
        );

        switch (fault) {
            .no_fault => try std.testing.expect(result.success),
            .loss_50, .loss_200 => try std.testing.expect(result.dropped_envelopes > 0),
            .duplication_250 => try std.testing.expect(result.duplicate_copies > 0),
            .latency_jitter => {
                try std.testing.expectEqual(@as(u16, 4), result.config.latency_min);
                try std.testing.expectEqual(@as(u16, 12), result.config.latency_jitter);
            },
            .reordering => {
                try std.testing.expect(result.forced_reorder_schedules > 0);
                try std.testing.expect(result.reordered_envelopes > 0);
            },
            .partition => try std.testing.expect(result.partitioned_envelopes > 0),
            .crash_persist, .crash_reset => try std.testing.expect(result.crashed_envelopes > 0),
            .stale_view => try std.testing.expect(result.stale_observations > 0),
            .queue_capacity => try std.testing.expect(result.queue_overflow_envelopes > 0),
            .combined => {
                try std.testing.expect(result.dropped_envelopes > 0);
                try std.testing.expect(result.duplicate_copies > 0);
                try std.testing.expect(result.partitioned_envelopes > 0);
            },
        }
    }
}
