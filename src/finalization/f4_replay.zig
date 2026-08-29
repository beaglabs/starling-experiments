const std = @import("std");

pub const worker_count: usize = 5;
pub const fact_count: usize = 5;
pub const collector_index: usize = 0;
pub const full_mask: u8 = (1 << fact_count) - 1;
pub const canonical_max_rounds: u32 = 5;
const max_runs: usize = 128;
const max_completion_bytes: usize = 4096;
const fnv_offset: u64 = 0xcbf29ce484222325;
const fnv_prime: u64 = 0x100000001b3;
const backend_error_sentinel = "__BACKEND_ERROR__";

pub const Population = enum {
    deterministic_only,
    mixed,
    model_only,

    fn parse(text: []const u8) !Population {
        if (std.mem.eql(u8, text, "deterministic_only")) return .deterministic_only;
        if (std.mem.eql(u8, text, "mixed")) return .mixed;
        if (std.mem.eql(u8, text, "model_only")) return .model_only;
        return error.UnknownPopulation;
    }

    fn name(self: Population) []const u8 {
        return switch (self) {
            .deterministic_only => "deterministic_only",
            .mixed => "mixed",
            .model_only => "model_only",
        };
    }
};

pub const Topology = enum {
    ring,
    grid,

    fn parse(text: []const u8) !Topology {
        if (std.mem.eql(u8, text, "ring")) return .ring;
        if (std.mem.eql(u8, text, "grid")) return .grid;
        return error.UnknownTopology;
    }

    fn name(self: Topology) []const u8 {
        return switch (self) {
            .ring => "ring",
            .grid => "grid",
        };
    }
};

pub const Mode = enum {
    typed_unconstrained,
    cfg_constrained,

    fn parse(text: []const u8) !Mode {
        if (std.mem.eql(u8, text, "typed_unconstrained")) return .typed_unconstrained;
        if (std.mem.eql(u8, text, "cfg_constrained")) return .cfg_constrained;
        return error.UnknownMode;
    }

    fn name(self: Mode) []const u8 {
        return switch (self) {
            .typed_unconstrained => "typed_unconstrained",
            .cfg_constrained => "cfg_constrained",
        };
    }
};

pub const OperatorType = enum {
    deterministic,
    model,

    fn parse(text: []const u8) !OperatorType {
        if (std.mem.eql(u8, text, "deterministic")) return .deterministic;
        if (std.mem.eql(u8, text, "model")) return .model;
        return error.UnknownOperatorType;
    }
};

const ActionKind = enum {
    claim,
    query_evidence,
};

const Action = struct {
    kind: ActionKind,
    facts: u8,
};

const RunKey = struct {
    population: Population,
    topology: Topology,
    environment_seed: u64,
    sampling_seed: u64,
    mode: Mode,
};

pub const RunResult = struct {
    key: RunKey,
    success: bool,
    rounds: u32,
    model_calls: usize,
    deterministic_calls: usize,
    protocol_actions: usize,
    invalid_actions: usize,
    backend_errors: usize,
    semantic_violations: usize,
    network_messages: usize,
    communication_units: usize,
    useful_fact_deliveries: usize,
    duplicate_fact_transmissions: usize,
    completion_tokens: usize,
    generated_bytes: usize,
    latency_us: u64,
    trajectory_hash: u64,
    budget_compliant: bool,
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
    operator_type: OperatorType,
    knowledge_before: u8,
    generation_seed: u32,
    completion_tokens: usize,
    latency_us: u64,
    escaped_completion: []const u8,
};

const NeighborSet = struct {
    items: [4]usize = undefined,
    len: usize = 0,

    fn slice(self: *const NeighborSet) []const usize {
        return self.items[0..self.len];
    }
};

const RoundMetrics = struct {
    semantic_violations: usize = 0,
    network_messages: usize = 0,
    communication_units: usize = 0,
    useful_fact_deliveries: usize = 0,
    duplicate_fact_transmissions: usize = 0,
};

const RunAccumulator = struct {
    key: RunKey,
    knowledge: [worker_count]u8,
    current_round: u32 = 1,
    seen_workers: [worker_count]bool = [_]bool{false} ** worker_count,
    actions: [worker_count]?Action = [_]?Action{null} ** worker_count,
    model_calls: usize = 0,
    deterministic_calls: usize = 0,
    protocol_actions: usize = 0,
    invalid_actions: usize = 0,
    backend_errors: usize = 0,
    semantic_violations: usize = 0,
    rounds: u32 = 0,
    network_messages: usize = 0,
    communication_units: usize = 0,
    useful_fact_deliveries: usize = 0,
    duplicate_fact_transmissions: usize = 0,
    completion_tokens: usize = 0,
    generated_bytes: usize = 0,
    latency_us: u64 = 0,
    trajectory_hash: u64 = fnv_offset,
    solved: bool = false,

    fn init(key: RunKey) RunAccumulator {
        return .{
            .key = key,
            .knowledge = initialKnowledge(key.environment_seed),
        };
    }

    fn ingest(
        self: *RunAccumulator,
        record: RawRecord,
        completion: []const u8,
    ) !void {
        if (self.solved) return error.RecordAfterSuccess;
        if (!keyEql(record.key, self.key)) return error.WrongRun;
        if (record.round != self.current_round) return error.UnexpectedRound;
        if (record.round > canonical_max_rounds) return error.BudgetExceeded;
        if (record.worker == 0 or @as(usize, record.worker) > worker_count) {
            return error.InvalidWorker;
        }

        const worker_index: usize = @intCast(record.worker - 1);
        if (self.seen_workers[worker_index]) return error.DuplicateWorker;
        if (record.knowledge_before != self.knowledge[worker_index]) {
            return error.KnowledgeMismatch;
        }

        const expected_type = expectedOperator(
            self.key.population,
            worker_index,
        );
        if (record.operator_type != expected_type) {
            return error.OperatorTypeMismatch;
        }

        if (record.operator_type == .model) {
            const expected_seed = generationSeed(
                record.key.sampling_seed,
                record.round,
                record.worker,
            );
            if (record.generation_seed != expected_seed) {
                return error.GenerationSeedMismatch;
            }
            self.model_calls += 1;
            self.completion_tokens += record.completion_tokens;
            self.latency_us +%= record.latency_us;
        } else {
            if (record.generation_seed != 0 or record.completion_tokens != 0 or
                record.latency_us != 0)
            {
                return error.DeterministicRecordHasModelCost;
            }
            self.deterministic_calls += 1;
        }

        hashByte(&self.trajectory_hash, @intCast(record.round & 0xff));
        hashByte(&self.trajectory_hash, record.worker);
        hashByte(
            &self.trajectory_hash,
            if (record.operator_type == .model) 1 else 0,
        );
        hashSlice(&self.trajectory_hash, completion);
        hashByte(&self.trajectory_hash, 0xff);

        if (std.mem.eql(u8, completion, backend_error_sentinel)) {
            if (record.operator_type != .model) {
                return error.DeterministicBackendError;
            }
            self.backend_errors += 1;
            self.seen_workers[worker_index] = true;
            try self.finishRoundIfComplete();
            return;
        }

        const action = parseAction(completion) catch {
            if (record.operator_type == .deterministic) {
                return error.InvalidDeterministicAction;
            }
            self.invalid_actions += 1;
            self.generated_bytes += completion.len;
            self.seen_workers[worker_index] = true;
            try self.finishRoundIfComplete();
            return;
        };

        if (record.operator_type == .deterministic) {
            if (action.kind != .claim or
                action.facts != self.knowledge[worker_index])
            {
                return error.DeterministicPolicyMismatch;
            }
        } else {
            self.generated_bytes += completion.len;
        }

        self.protocol_actions += 1;
        self.actions[worker_index] = action;
        self.seen_workers[worker_index] = true;
        try self.finishRoundIfComplete();
    }

    fn finishRoundIfComplete(self: *RunAccumulator) !void {
        for (self.seen_workers) |seen| {
            if (!seen) return;
        }

        const metrics = applyRound(
            &self.knowledge,
            self.actions,
            self.key.topology,
        );
        self.semantic_violations += metrics.semantic_violations;
        self.network_messages += metrics.network_messages;
        self.communication_units += metrics.communication_units;
        self.useful_fact_deliveries += metrics.useful_fact_deliveries;
        self.duplicate_fact_transmissions += metrics.duplicate_fact_transmissions;
        self.rounds += 1;
        self.solved = self.knowledge[collector_index] == full_mask;
        self.current_round += 1;
        self.seen_workers = [_]bool{false} ** worker_count;
        self.actions = [_]?Action{null} ** worker_count;
    }

    fn complete(self: *const RunAccumulator) bool {
        for (self.seen_workers) |seen| {
            if (seen) return false;
        }
        return self.model_calls + self.deterministic_calls != 0;
    }

    fn result(self: *const RunAccumulator) RunResult {
        return .{
            .key = self.key,
            .success = self.solved,
            .rounds = self.rounds,
            .model_calls = self.model_calls,
            .deterministic_calls = self.deterministic_calls,
            .protocol_actions = self.protocol_actions,
            .invalid_actions = self.invalid_actions,
            .backend_errors = self.backend_errors,
            .semantic_violations = self.semantic_violations,
            .network_messages = self.network_messages,
            .communication_units = self.communication_units,
            .useful_fact_deliveries = self.useful_fact_deliveries,
            .duplicate_fact_transmissions = self.duplicate_fact_transmissions,
            .completion_tokens = self.completion_tokens,
            .generated_bytes = self.generated_bytes,
            .latency_us = self.latency_us,
            .trajectory_hash = self.trajectory_hash,
            .budget_compliant = self.rounds <= canonical_max_rounds,
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
        } else if (!keyEql(active.?.key, record.key)) {
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
    if (summary.run_count >= max_runs) return error.TooManyRuns;
    for (summary.runs[0..summary.run_count]) |prior| {
        if (keyEql(prior.key, run.key)) return error.DuplicateRun;
    }

    summary.runs[summary.run_count] = run.result();
    summary.run_count += 1;
}

fn keyEql(a: RunKey, b: RunKey) bool {
    return a.population == b.population and
        a.topology == b.topology and
        a.environment_seed == b.environment_seed and
        a.sampling_seed == b.sampling_seed and
        a.mode == b.mode;
}

fn expectedOperator(
    population: Population,
    worker_index: usize,
) OperatorType {
    return switch (population) {
        .deterministic_only => .deterministic,
        .model_only => .model,
        .mixed => if (worker_index == 0 or worker_index == 2)
            .model
        else
            .deterministic,
    };
}

fn initialKnowledge(seed: u64) [worker_count]u8 {
    const offset: usize = @intCast(seed % fact_count);
    var result = [_]u8{0} ** worker_count;
    var i: usize = 0;
    while (i < worker_count) : (i += 1) {
        result[i] =
            factBit((i + offset) % fact_count) |
            factBit((i + 1 + offset) % fact_count);
    }
    return result;
}

fn generationSeed(
    sampling_seed: u64,
    round: u32,
    worker: u8,
) u32 {
    const mixed =
        sampling_seed *% 1_000_003 +%
        @as(u64, round) *% 101 +%
        @as(u64, worker);
    return @intCast(mixed & 0x7fff_ffff);
}

fn neighbors(topology: Topology, worker_index: usize) NeighborSet {
    var set = NeighborSet{};
    switch (topology) {
        .ring => {
            const left = (worker_index + worker_count - 1) % worker_count;
            const right = (worker_index + 1) % worker_count;
            set.items[0] = left;
            set.len = 1;
            if (right != left) {
                set.items[1] = right;
                set.len = 2;
            }
        },
        .grid => {
            const width: usize = 3;
            const row = worker_index / width;
            const col = worker_index % width;
            if (col > 0) {
                set.items[set.len] = worker_index - 1;
                set.len += 1;
            }
            if (col + 1 < width and worker_index + 1 < worker_count) {
                const recipient = worker_index + 1;
                if (recipient / width == row) {
                    set.items[set.len] = recipient;
                    set.len += 1;
                }
            }
            if (worker_index >= width) {
                set.items[set.len] = worker_index - width;
                set.len += 1;
            }
            if (worker_index + width < worker_count) {
                set.items[set.len] = worker_index + width;
                set.len += 1;
            }
        },
    }
    return set;
}

fn parseAction(text: []const u8) !Action {
    const trimmed = std.mem.trim(u8, text, " \t\r\n");
    if (trimmed.len == 0) return error.InvalidAction;

    var tokens = std.mem.tokenizeScalar(u8, trimmed, ' ');
    const first = tokens.next() orelse return error.InvalidAction;

    if (std.mem.eql(u8, first, "CLAIM")) {
        const facts_text = tokens.next() orelse return error.InvalidAction;
        if (tokens.next() != null) return error.InvalidAction;
        return .{ .kind = .claim, .facts = try parseFactList(facts_text) };
    }

    if (std.mem.eql(u8, first, "QUERY")) {
        const second = tokens.next() orelse return error.InvalidAction;
        if (!std.mem.eql(u8, second, "EVIDENCE")) return error.InvalidAction;
        const fact_text = tokens.next() orelse return error.InvalidAction;
        if (tokens.next() != null) return error.InvalidAction;
        return .{
            .kind = .query_evidence,
            .facts = try parseSingleFact(fact_text),
        };
    }

    return error.InvalidAction;
}

fn applyRound(
    knowledge: *[worker_count]u8,
    actions: [worker_count]?Action,
    topology: Topology,
) RoundMetrics {
    const snapshot = knowledge.*;
    var next = snapshot;
    var metrics = RoundMetrics{};

    var sender: usize = 0;
    while (sender < worker_count) : (sender += 1) {
        const action = actions[sender] orelse continue;
        const peers = neighbors(topology, sender);

        switch (action.kind) {
            .claim => {
                if (action.facts == 0 or
                    (action.facts & ~snapshot[sender]) != 0)
                {
                    metrics.semantic_violations += 1;
                    continue;
                }

                for (peers.slice()) |recipient| {
                    metrics.network_messages += 1;
                    const selected: usize = @intCast(@popCount(action.facts));
                    metrics.communication_units += selected;
                    const unseen = action.facts & ~next[recipient];
                    const duplicate = action.facts & next[recipient];
                    metrics.useful_fact_deliveries += @intCast(@popCount(unseen));
                    metrics.duplicate_fact_transmissions += @intCast(@popCount(duplicate));
                    next[recipient] |= action.facts;
                }
            },
            .query_evidence => {
                for (peers.slice()) |recipient| {
                    metrics.network_messages += 1;
                    metrics.communication_units += 1;
                    if ((snapshot[recipient] & action.facts) == 0) continue;

                    metrics.network_messages += 1;
                    metrics.communication_units += 1;
                    if ((next[sender] & action.facts) == 0) {
                        metrics.useful_fact_deliveries += 1;
                    } else {
                        metrics.duplicate_fact_transmissions += 1;
                    }
                    next[sender] |= action.facts;
                }
            },
        }
    }

    knowledge.* = next;
    return metrics;
}

fn parseFactList(text: []const u8) !u8 {
    var result: u8 = 0;
    var parts = std.mem.splitScalar(u8, text, ',');
    var count: usize = 0;
    while (parts.next()) |part| {
        if (part.len == 0) return error.InvalidFact;
        result |= try parseSingleFact(part);
        count += 1;
    }
    if (count == 0 or result == 0) return error.InvalidFact;
    return result;
}

fn parseSingleFact(text: []const u8) !u8 {
    if (text.len != 1) return error.InvalidFact;
    return switch (text[0]) {
        'A' => factBit(0),
        'B' => factBit(1),
        'C' => factBit(2),
        'D' => factBit(3),
        'E' => factBit(4),
        else => error.InvalidFact,
    };
}

fn factBit(index: usize) u8 {
    return @as(u8, 1) << @intCast(index);
}

fn parseRawLine(line: []const u8) !RawRecord {
    var fields: [13][]const u8 = undefined;
    var count: usize = 0;
    var iterator = std.mem.splitScalar(u8, line, '\t');

    while (iterator.next()) |field| {
        if (count >= fields.len) return error.InvalidRecord;
        fields[count] = field;
        count += 1;
    }
    if (count != fields.len) return error.InvalidRecord;
    for (fields[0..12]) |field| {
        if (field.len == 0) return error.InvalidRecord;
    }

    return .{
        .key = .{
            .population = try Population.parse(fields[0]),
            .topology = try Topology.parse(fields[1]),
            .environment_seed = try std.fmt.parseInt(u64, fields[2], 10),
            .sampling_seed = try std.fmt.parseInt(u64, fields[3], 10),
            .mode = try Mode.parse(fields[4]),
        },
        .round = try std.fmt.parseInt(u32, fields[5], 10),
        .worker = try std.fmt.parseInt(u8, fields[6], 10),
        .operator_type = try OperatorType.parse(fields[7]),
        .knowledge_before = try std.fmt.parseInt(u8, fields[8], 10),
        .generation_seed = try std.fmt.parseInt(u32, fields[9], 10),
        .completion_tokens = try std.fmt.parseInt(usize, fields[10], 10),
        .latency_us = try std.fmt.parseInt(u64, fields[11], 10),
        .escaped_completion = fields[12],
    };
}

fn unescapeCompletion(
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

pub fn main(init: std.process.Init) !void {
    const io = init.io;
    const allocator = init.gpa;
    const args = try init.minimal.args.toSlice(init.arena.allocator());

    if (args.len != 2) {
        try std.Io.File.stderr().writeStreamingAll(
            io,
            "usage: f4-replay <raw.tsv>\n",
        );
        std.process.exit(2);
    }

    const tsv = try std.Io.Dir.cwd().readFileAlloc(
        io,
        args[1],
        allocator,
        .limited(64 * 1024 * 1024),
    );
    defer allocator.free(tsv);

    const summary = summarizeTsv(tsv);
    if (summary.malformed_records != 0 or summary.replay_errors != 0) {
        var buffer: [256]u8 = undefined;
        const msg = try std.fmt.bufPrint(
            &buffer,
            "F4 replay rejected data: malformed={d} replay_errors={d}\n",
            .{ summary.malformed_records, summary.replay_errors },
        );
        try std.Io.File.stderr().writeStreamingAll(io, msg);
        std.process.exit(2);
    }

    const out = std.Io.File.stdout();
    try out.writeStreamingAll(
        io,
        "population\ttopology\tenvironment_seed\tsampling_seed\tmode\t" ++
            "success\trounds\tmodel_calls\tdeterministic_calls\tprotocol_actions\t" ++
            "invalid_actions\tbackend_errors\tsemantic_violations\tnetwork_messages\t" ++
            "communication_units\tuseful\tduplicate\tcompletion_tokens\tgenerated_bytes\t" ++
            "trajectory_hash\tbudget_compliant\n",
    );

    for (summary.runs[0..summary.run_count]) |run| {
        var buffer: [1024]u8 = undefined;
        const line = try std.fmt.bufPrint(
            &buffer,
            "{s}\t{s}\t{d}\t{d}\t{s}\t{s}\t{d}\t{d}\t{d}\t{d}\t" ++
                "{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{x}\t{s}\n",
            .{
                run.key.population.name(),
                run.key.topology.name(),
                run.key.environment_seed,
                run.key.sampling_seed,
                run.key.mode.name(),
                if (run.success) "yes" else "no",
                run.rounds,
                run.model_calls,
                run.deterministic_calls,
                run.protocol_actions,
                run.invalid_actions,
                run.backend_errors,
                run.semantic_violations,
                run.network_messages,
                run.communication_units,
                run.useful_fact_deliveries,
                run.duplicate_fact_transmissions,
                run.completion_tokens,
                run.generated_bytes,
                run.trajectory_hash,
                if (run.budget_compliant) "yes" else "no",
            },
        );
        try out.writeStreamingAll(io, line);
    }
}

test "F4 deterministic population converges on ring" {
    var knowledge = initialKnowledge(0);
    var round: u32 = 0;

    while (round < canonical_max_rounds and
        knowledge[collector_index] != full_mask) : (round += 1)
    {
        var actions = [_]?Action{null} ** worker_count;
        var worker: usize = 0;
        while (worker < worker_count) : (worker += 1) {
            actions[worker] = .{
                .kind = .claim,
                .facts = knowledge[worker],
            };
        }
        _ = applyRound(&knowledge, actions, .ring);
    }

    try std.testing.expectEqual(full_mask, knowledge[collector_index]);
}

test "F4 grid topology is connected for five workers" {
    const n0 = neighbors(.grid, 0);
    try std.testing.expectEqualSlices(usize, &.{ 1, 3 }, n0.slice());

    const n1 = neighbors(.grid, 1);
    try std.testing.expectEqualSlices(usize, &.{ 0, 2, 4 }, n1.slice());
}

test "F4 parser rejects prose and accepts protocol vocabulary" {
    const claim = try parseAction("CLAIM A,C,E");
    try std.testing.expectEqual(ActionKind.claim, claim.kind);
    try std.testing.expectEqual(@as(u8, 0b10101), claim.facts);

    try std.testing.expectError(
        error.InvalidAction,
        parseAction("I think CLAIM A"),
    );
}

test "F4 operator mix is frozen" {
    try std.testing.expectEqual(
        OperatorType.model,
        expectedOperator(.mixed, 0),
    );
    try std.testing.expectEqual(
        OperatorType.deterministic,
        expectedOperator(.mixed, 1),
    );
    try std.testing.expectEqual(
        OperatorType.model,
        expectedOperator(.mixed, 2),
    );
}
