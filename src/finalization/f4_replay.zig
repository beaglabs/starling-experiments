const std = @import("std");
const runtime = @import("f4_runtime.zig");
const scaling = @import("../substrate/stage5/stage5a_scaling.zig");

pub const max_completion_bytes: usize = 4096;
pub const max_runs: usize = 256;
const fnv_offset: u64 = 0xcbf29ce484222325;
const fnv_prime: u64 = 0x100000001b3;

pub const RunKey = struct {
    mix: runtime.PopulationMix,
    topology: scaling.TopologyKind,
    environment_seed: u64,
    sampling_seed: u64,
    mode: runtime.DecodeMode,
    controller: runtime.InferenceController,

    pub fn eql(a: RunKey, b: RunKey) bool {
        return a.mix == b.mix and
            a.topology == b.topology and
            a.environment_seed == b.environment_seed and
            a.sampling_seed == b.sampling_seed and
            a.mode == b.mode and
            a.controller == b.controller;
    }
};

pub const RunResult = struct {
    key: RunKey,
    success: bool,
    rounds: u32,
    model_calls: u64,
    cache_reuses: u64,
    deterministic_decisions: u64,
    protocol_actions: u64,
    accepted_model_actions: u64,
    invalid_actions: u64,
    backend_errors: u64,
    semantic_rejections: u64,
    token_budget_violations: u64,
    messages: u64,
    communication_units: u64,
    control_units: u64,
    useful_deliveries: u64,
    duplicate_deliveries: u64,
    completion_tokens: u64,
    latency_us: u64,
    essential_fact: usize,
    essential_reached_deterministic: bool,
    trajectory_hash: u64,

    pub fn communicationAccounted(self: RunResult) bool {
        return self.communication_units ==
            self.control_units +
                self.useful_deliveries +
                self.duplicate_deliveries;
    }
};

pub const Summary = struct {
    records: usize = 0,
    malformed_records: usize = 0,
    replay_errors: usize = 0,
    runs: [max_runs]RunResult = undefined,
    run_count: usize = 0,
};

const RawRecord = struct {
    key: RunKey,
    round: u32,
    worker: u8,
    operator_kind: runtime.OperatorKind,
    source: runtime.DecisionSource,
    knowledge_before: u8,
    sent_before: u8,
    cursor_before: u16,
    model_seed: u32,
    completion_tokens: u32,
    latency_us: u64,
    token_budget: u32,
    escaped_completion: []const u8,
};

const RunAccumulator = struct {
    key: RunKey,
    states: [scaling.max_operators]scaling.State,
    caches: [scaling.max_operators]runtime.ModelCache =
        [_]runtime.ModelCache{.{}} ** scaling.max_operators,
    current_round: u32 = 1,
    seen_workers: [runtime.worker_count]bool =
        [_]bool{false} ** runtime.worker_count,
    actions: [scaling.max_operators]?runtime.Action =
        [_]?runtime.Action{null} ** scaling.max_operators,

    model_calls: u64 = 0,
    cache_reuses: u64 = 0,
    deterministic_decisions: u64 = 0,
    protocol_actions: u64 = 0,
    accepted_model_actions: u64 = 0,
    invalid_actions: u64 = 0,
    backend_errors: u64 = 0,
    semantic_rejections: u64 = 0,
    token_budget_violations: u64 = 0,

    messages: u64 = 0,
    communication_units: u64 = 0,
    control_units: u64 = 0,
    useful_deliveries: u64 = 0,
    duplicate_deliveries: u64 = 0,
    completion_tokens: u64 = 0,
    latency_us: u64 = 0,

    rounds: u32 = 0,
    essential_reached_deterministic: bool = false,
    trajectory_hash: u64 = fnv_offset,
    solved: bool = false,

    fn init(key: RunKey) RunAccumulator {
        return .{
            .key = key,
            .states = runtime.initialStates(key.environment_seed),
        };
    }

    fn ingest(
        self: *RunAccumulator,
        record: RawRecord,
        completion: []const u8,
    ) !void {
        if (self.solved) return error.RecordAfterSuccess;
        if (!RunKey.eql(record.key, self.key)) return error.WrongRun;
        if (record.round != self.current_round) {
            return error.UnexpectedRound;
        }
        if (record.round == 0 or record.round > runtime.max_rounds) {
            return error.InvalidRound;
        }
        if (record.worker == 0 or
            @as(usize, record.worker) > runtime.worker_count)
        {
            return error.InvalidWorker;
        }

        const operator_index: usize = @intCast(record.worker - 1);
        if (self.seen_workers[operator_index]) {
            return error.DuplicateWorker;
        }

        const expected_kind =
            runtime.operatorKind(self.key.mix, operator_index);
        if (record.operator_kind != expected_kind) {
            return error.OperatorKindMismatch;
        }

        const state = self.states[operator_index];
        if (record.knowledge_before != maskToU8(state.knowledge) or
            record.sent_before != maskToU8(state.sent) or
            record.cursor_before != state.cursor)
        {
            return error.StateMismatch;
        }

        hashByte(&self.trajectory_hash, @intCast(record.round & 0xff));
        hashByte(&self.trajectory_hash, record.worker);
        hashByte(
            &self.trajectory_hash,
            @intCast(@intFromEnum(record.source)),
        );

        switch (expected_kind) {
            .deterministic => try self.ingestDeterministic(
                operator_index,
                record,
                completion,
            ),
            .model => try self.ingestModel(
                operator_index,
                record,
                completion,
            ),
        }

        self.seen_workers[operator_index] = true;
        try self.finishRoundIfComplete();
    }

    fn ingestDeterministic(
        self: *RunAccumulator,
        operator_index: usize,
        record: RawRecord,
        completion: []const u8,
    ) !void {
        if (record.source != .deterministic or
            record.model_seed != 0 or
            record.completion_tokens != 0 or
            record.latency_us != 0)
        {
            return error.InvalidDeterministicRecord;
        }

        const expected = runtime.deterministicAction(
            self.states[operator_index],
            operator_index,
            record.round,
            self.key.topology,
            self.key.environment_seed,
        );

        var buffer: [64]u8 = undefined;
        const expected_text =
            try runtime.canonicalActionText(expected, &buffer);
        if (!std.mem.eql(u8, expected_text, completion)) {
            return error.DeterministicActionMismatch;
        }

        self.hashSemanticDecision(expected_text);
        self.deterministic_decisions +%= 1;
        self.actions[operator_index] = expected;
    }

    fn ingestModel(
        self: *RunAccumulator,
        operator_index: usize,
        record: RawRecord,
        completion: []const u8,
    ) !void {
        if (self.key.mode == .deterministic or
            self.key.controller == .deterministic)
        {
            return error.InvalidModelRunDimensions;
        }

        const should_refresh = runtime.shouldRefreshModel(
            self.key.controller,
            self.states[operator_index],
            self.caches[operator_index],
        );

        if (should_refresh) {
            if (record.source != .model_call) {
                return error.ExpectedModelCall;
            }
            const expected_seed = runtime.generationSeed(
                self.key.sampling_seed,
                record.round,
                record.worker,
            );
            if (record.model_seed != expected_seed) {
                return error.GenerationSeedMismatch;
            }

            self.model_calls +%= 1;
            self.completion_tokens +%= record.completion_tokens;
            self.latency_us +%= record.latency_us;
            if (record.completion_tokens > record.token_budget) {
                self.token_budget_violations +%= 1;
            }

            self.caches[operator_index].initialized = true;
            self.caches[operator_index].knowledge_at_refresh =
                self.states[operator_index].knowledge;
            self.caches[operator_index].action = null;

            if (std.mem.eql(
                u8,
                completion,
                runtime.backend_error_sentinel,
            )) {
                self.hashSemanticDecision("BACKEND_ERROR");
                self.backend_errors +%= 1;
                return;
            }

            const action = runtime.parseModelAction(completion) catch {
                self.hashSemanticDecision("INVALID_SYNTAX");
                self.invalid_actions +%= 1;
                return;
            };
            self.protocol_actions +%= 1;

            if (!runtime.validateModelAction(
                action,
                self.states[operator_index],
            )) {
                var rejected_buffer: [64]u8 = undefined;
                const rejected_text =
                    try runtime.canonicalActionText(
                        action,
                        &rejected_buffer,
                    );
                hashSlice(&self.trajectory_hash, "SEMANTIC_REJECT:");
                self.hashSemanticDecision(rejected_text);
                self.semantic_rejections +%= 1;
                return;
            }

            var accepted_buffer: [64]u8 = undefined;
            const accepted_text =
                try runtime.canonicalActionText(
                    action,
                    &accepted_buffer,
                );
            self.hashSemanticDecision(accepted_text);
            self.accepted_model_actions +%= 1;
            self.caches[operator_index].action = action;
            self.actions[operator_index] = action;
            return;
        }

        if (record.source != .cache or
            record.model_seed != 0 or
            record.completion_tokens != 0 or
            record.latency_us != 0)
        {
            return error.InvalidCacheRecord;
        }

        const cached = self.caches[operator_index].action orelse
            return error.MissingCachedAction;
        var buffer: [64]u8 = undefined;
        const expected_text =
            try runtime.canonicalActionText(cached, &buffer);
        if (!std.mem.eql(u8, expected_text, completion)) {
            return error.CachedActionMismatch;
        }

        self.hashSemanticDecision(expected_text);
        self.cache_reuses +%= 1;
        self.actions[operator_index] = cached;
    }

    fn hashSemanticDecision(
        self: *RunAccumulator,
        text: []const u8,
    ) void {
        hashSlice(&self.trajectory_hash, text);
        hashByte(&self.trajectory_hash, 0xff);
    }

    fn finishRoundIfComplete(self: *RunAccumulator) !void {
        for (self.seen_workers) |seen| {
            if (!seen) return;
        }

        const metrics = runtime.applyRound(
            &self.states,
            &self.actions,
            self.key.mix,
            self.key.topology,
            self.key.environment_seed,
        );
        if (!metrics.accounted()) return error.AccountingFailure;

        self.messages +%= metrics.messages;
        self.communication_units +%= metrics.communication_units;
        self.control_units +%= metrics.control_units;
        self.useful_deliveries +%= metrics.useful_deliveries;
        self.duplicate_deliveries +%= metrics.duplicate_deliveries;
        self.essential_reached_deterministic =
            self.essential_reached_deterministic or
            metrics.essential_reached_deterministic;

        self.rounds = self.current_round;
        self.solved = runtime.collectorSolved(&self.states);

        self.current_round += 1;
        self.seen_workers =
            [_]bool{false} ** runtime.worker_count;
        self.actions =
            [_]?runtime.Action{null} ** scaling.max_operators;
    }

    fn complete(self: *const RunAccumulator) bool {
        for (self.seen_workers) |seen| {
            if (seen) return false;
        }
        return self.rounds > 0;
    }

    fn result(self: *const RunAccumulator) RunResult {
        return .{
            .key = self.key,
            .success = self.solved,
            .rounds = self.rounds,
            .model_calls = self.model_calls,
            .cache_reuses = self.cache_reuses,
            .deterministic_decisions = self.deterministic_decisions,
            .protocol_actions = self.protocol_actions,
            .accepted_model_actions = self.accepted_model_actions,
            .invalid_actions = self.invalid_actions,
            .backend_errors = self.backend_errors,
            .semantic_rejections = self.semantic_rejections,
            .token_budget_violations = self.token_budget_violations,
            .messages = self.messages,
            .communication_units = self.communication_units,
            .control_units = self.control_units,
            .useful_deliveries = self.useful_deliveries,
            .duplicate_deliveries = self.duplicate_deliveries,
            .completion_tokens = self.completion_tokens,
            .latency_us = self.latency_us,
            .essential_fact = runtime.essentialFact(
                self.key.environment_seed,
            ),
            .essential_reached_deterministic =
                self.essential_reached_deterministic,
            .trajectory_hash = self.trajectory_hash,
        };
    }
};

pub fn summarizeTsv(tsv: []const u8) Summary {
    var summary = Summary{};
    var active: ?RunAccumulator = null;
    var lines = std.mem.splitScalar(u8, tsv, '\n');

    while (lines.next()) |raw_line| {
        const line = std.mem.trim(u8, raw_line, "\r");
        if (line.len == 0 or line[0] == '#') continue;
        if (std.mem.startsWith(u8, line, "mix\ttopology\t")) continue;

        summary.records += 1;
        const record = parseRawLine(line) catch {
            summary.malformed_records += 1;
            continue;
        };

        var completion_buffer: [max_completion_bytes]u8 = undefined;
        const completion = unescapeCompletion(
            record.escaped_completion,
            &completion_buffer,
        ) catch {
            summary.malformed_records += 1;
            continue;
        };

        if (active == null) {
            active = RunAccumulator.init(record.key);
        } else if (!RunKey.eql(active.?.key, record.key)) {
            if (active) |*run| {
                finishRun(&summary, run) catch {
                    summary.replay_errors += 1;
                };
            }
            active = RunAccumulator.init(record.key);
        }

        if (active) |*run| {
            run.ingest(record, completion) catch {
                summary.replay_errors += 1;
            };
        }
    }

    if (active) |*run| {
        finishRun(&summary, run) catch {
            summary.replay_errors += 1;
        };
    }

    return summary;
}

fn finishRun(summary: *Summary, run: *RunAccumulator) !void {
    if (!run.complete()) return error.IncompleteRun;
    if (!run.solved and run.rounds != runtime.max_rounds) {
        return error.EarlyUnsolvedTermination;
    }
    if (summary.run_count >= max_runs) return error.TooManyRuns;

    const result = run.result();
    if (!result.communicationAccounted()) return error.AccountingFailure;

    var i: usize = 0;
    while (i < summary.run_count) : (i += 1) {
        if (RunKey.eql(summary.runs[i].key, result.key)) {
            return error.DuplicateRun;
        }
    }

    if (result.key.mix == .mixed and result.success and
        !result.essential_reached_deterministic)
    {
        return error.MixedSuccessWithoutEssentialTransfer;
    }

    summary.runs[summary.run_count] = result;
    summary.run_count += 1;
}

fn parseRawLine(line: []const u8) !RawRecord {
    var fields: [18][]const u8 = undefined;
    var count: usize = 0;
    var iterator = std.mem.splitScalar(u8, line, '\t');

    while (iterator.next()) |field| {
        if (count >= fields.len) return error.InvalidRecord;
        fields[count] = field;
        count += 1;
    }
    if (count != fields.len) return error.InvalidRecord;
    for (fields[0..17]) |field| {
        if (field.len == 0) return error.InvalidRecord;
    }

    return .{
        .key = .{
            .mix = try parseMix(fields[0]),
            .topology = try parseTopology(fields[1]),
            .environment_seed =
                try std.fmt.parseInt(u64, fields[2], 10),
            .sampling_seed =
                try std.fmt.parseInt(u64, fields[3], 10),
            .mode = try parseMode(fields[4]),
            .controller = try parseController(fields[5]),
        },
        .round = try std.fmt.parseInt(u32, fields[6], 10),
        .worker = try std.fmt.parseInt(u8, fields[7], 10),
        .operator_kind = try parseOperatorKind(fields[8]),
        .source = try parseSource(fields[9]),
        .knowledge_before = try std.fmt.parseInt(u8, fields[10], 10),
        .sent_before = try std.fmt.parseInt(u8, fields[11], 10),
        .cursor_before = try std.fmt.parseInt(u16, fields[12], 10),
        .model_seed = try std.fmt.parseInt(u32, fields[13], 10),
        .completion_tokens =
            try std.fmt.parseInt(u32, fields[14], 10),
        .latency_us = try std.fmt.parseInt(u64, fields[15], 10),
        .token_budget = try std.fmt.parseInt(u32, fields[16], 10),
        .escaped_completion = fields[17],
    };
}

fn parseMix(text: []const u8) !runtime.PopulationMix {
    inline for (.{ runtime.PopulationMix.deterministic_only,
        runtime.PopulationMix.mixed,
        runtime.PopulationMix.model_only }) |value|
    {
        if (std.mem.eql(u8, text, value.name())) return value;
    }
    return error.UnknownMix;
}

fn parseTopology(text: []const u8) !scaling.TopologyKind {
    if (std.mem.eql(u8, text, "ring")) return .ring;
    if (std.mem.eql(u8, text, "grid")) return .grid;
    return error.UnknownTopology;
}

fn parseMode(text: []const u8) !runtime.DecodeMode {
    inline for (.{ runtime.DecodeMode.deterministic,
        runtime.DecodeMode.typed_unconstrained,
        runtime.DecodeMode.cfg_constrained }) |value|
    {
        if (std.mem.eql(u8, text, value.name())) return value;
    }
    return error.UnknownMode;
}

fn parseController(text: []const u8) !runtime.InferenceController {
    inline for (.{ runtime.InferenceController.deterministic,
        runtime.InferenceController.always_refresh,
        runtime.InferenceController.knowledge_or_stale }) |value|
    {
        if (std.mem.eql(u8, text, value.name())) return value;
    }
    return error.UnknownController;
}

fn parseOperatorKind(text: []const u8) !runtime.OperatorKind {
    if (std.mem.eql(u8, text, "deterministic")) return .deterministic;
    if (std.mem.eql(u8, text, "model")) return .model;
    return error.UnknownOperatorKind;
}

fn parseSource(text: []const u8) !runtime.DecisionSource {
    if (std.mem.eql(u8, text, "deterministic")) return .deterministic;
    if (std.mem.eql(u8, text, "model_call")) return .model_call;
    if (std.mem.eql(u8, text, "cache")) return .cache;
    return error.UnknownSource;
}

pub fn maskToU8(mask: scaling.BitSet) u8 {
    var value: u8 = 0;
    var fact: usize = 0;
    while (fact < runtime.fact_count) : (fact += 1) {
        if (mask.has(fact)) {
            value |= @as(u8, 1) << @intCast(fact);
        }
    }
    return value;
}

pub fn u8ToMask(value: u8) scaling.BitSet {
    var mask = scaling.BitSet{};
    var fact: usize = 0;
    while (fact < runtime.fact_count) : (fact += 1) {
        if ((value & (@as(u8, 1) << @intCast(fact))) != 0) {
            mask.set(fact);
        }
    }
    return mask;
}

pub fn unescapeCompletion(
    input: []const u8,
    out: *[max_completion_bytes]u8,
) ![]const u8 {
    var src: usize = 0;
    var dst: usize = 0;

    while (src < input.len) {
        if (dst >= out.len) return error.CompletionTooLarge;

        if (input[src] != '\\') {
            out[dst] = input[src];
            dst += 1;
            src += 1;
            continue;
        }

        src += 1;
        if (src >= input.len) return error.InvalidEscape;
        out[dst] = switch (input[src]) {
            '\\' => '\\',
            't' => '\t',
            'r' => '\r',
            'n' => '\n',
            else => return error.InvalidEscape,
        };
        dst += 1;
        src += 1;
    }

    return out[0..dst];
}

fn hashByte(hash: *u64, byte: u8) void {
    hash.* ^= byte;
    hash.* *%= fnv_prime;
}

fn hashSlice(hash: *u64, bytes: []const u8) void {
    for (bytes) |byte| hashByte(hash, byte);
}

pub fn writeSummaryTsv(
    io: std.Io,
    out: std.Io.File,
    summary: *const Summary,
) !void {
    try out.writeStreamingAll(
        io,
        "mix\ttopology\tenvironment_seed\tsampling_seed\tmode\tcontroller\t" ++
            "success\trounds\tmodel_calls\tcache_reuses\t" ++
            "deterministic_decisions\tprotocol_actions\taccepted_model_actions\t" ++
            "invalid_actions\tbackend_errors\tsemantic_rejections\t" ++
            "token_budget_violations\tmessages\tcommunication_units\t" ++
            "control_units\tuseful_deliveries\tduplicate_deliveries\t" ++
            "completion_tokens\tlatency_us\tessential_fact\t" ++
            "essential_reached_deterministic\ttrajectory_hash\n",
    );

    for (summary.runs[0..summary.run_count]) |run| {
        try writeLine(
            io,
            out,
            "{s}\t{s}\t{d}\t{d}\t{s}\t{s}\t{s}\t{d}\t{d}\t{d}\t" ++
                "{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t" ++
                "{d}\t{d}\t{d}\t{d}\t{d}\t{c}\t{s}\t{x}\n",
            .{
                run.key.mix.name(),
                run.key.topology.name(),
                run.key.environment_seed,
                run.key.sampling_seed,
                run.key.mode.name(),
                run.key.controller.name(),
                if (run.success) "yes" else "no",
                run.rounds,
                run.model_calls,
                run.cache_reuses,
                run.deterministic_decisions,
                run.protocol_actions,
                run.accepted_model_actions,
                run.invalid_actions,
                run.backend_errors,
                run.semantic_rejections,
                run.token_budget_violations,
                run.messages,
                run.communication_units,
                run.control_units,
                run.useful_deliveries,
                run.duplicate_deliveries,
                run.completion_tokens,
                run.latency_us,
                'A' + @as(u8, @intCast(run.essential_fact)),
                if (run.essential_reached_deterministic) "yes" else "no",
                run.trajectory_hash,
            },
        );
    }
}

fn writeLine(
    io: std.Io,
    out: std.Io.File,
    comptime format: []const u8,
    args: anytype,
) !void {
    var buffer: [8192]u8 = undefined;
    const line = try std.fmt.bufPrint(&buffer, format, args);
    try out.writeStreamingAll(io, line);
}

test "F4 raw replay accepts deterministic control fixture" {
    const tsv =
        "deterministic_only\tring\t0\t0\tdeterministic\tdeterministic\t" ++
        "1\t1\tdeterministic\tdeterministic\t3\t0\t0\t0\t0\t0\t32\tCLAIM A,B\n";

    // The one-line fixture is intentionally incomplete and must therefore
    // fail replay rather than be treated as evidence.
    const summary = summarizeTsv(tsv);
    try std.testing.expect(summary.replay_errors != 0);
}

test "F4 raw completion escaping round-trips protocol text" {
    var buffer: [max_completion_bytes]u8 = undefined;
    const value = try unescapeCompletion(
        "CLAIM A,B\\n",
        &buffer,
    );
    try std.testing.expectEqualStrings("CLAIM A,B\n", value);
}
