const std = @import("std");
const search = @import("f3b_search.zig");
const reference = @import("f3_stage7b_reference.zig");

pub fn main(init: std.process.Init) !void {
    const io = init.io;
    const args = try init.minimal.args.toSlice(init.arena.allocator());

    if (args.len == 2 and std.mem.eql(u8, args[1], "validate")) {
        try validate(io);
        return;
    }
    if (args.len == 2 and std.mem.eql(u8, args[1], "baseline")) {
        try baselineAudit(io);
        return;
    }
    if (args.len == 2 and std.mem.eql(u8, args[1], "search")) {
        try runSearch(io);
        return;
    }

    try std.Io.File.stderr().writeStreamingAll(
        io,
        "usage:\n  f3b validate\n  f3b baseline\n  f3b search\n",
    );
    std.process.exit(2);
}

fn validate(io: std.Io) !void {
    const candidates = search.generateCandidates();
    var baselines: usize = 0;
    var invalid_theta: usize = 0;
    var duplicate_candidates: usize = 0;

    var i: usize = 0;
    while (i < candidates.len) : (i += 1) {
        candidates.items[i].theta.validate() catch {
            invalid_theta += 1;
        };
        if (candidates.items[i].isBaseline()) baselines += 1;

        var j: usize = i + 1;
        while (j < candidates.len) : (j += 1) {
            if (candidates.items[i].base_id ==
                    candidates.items[j].base_id and
                candidates.items[i].controller ==
                    candidates.items[j].controller)
            {
                duplicate_candidates += 1;
            }
        }
    }

    const out = std.Io.File.stdout();
    try writeLine(io, out, "F3b validation\n", .{});
    try writeLine(
        io,
        out,
        "candidate_count: {d}\n",
        .{candidates.len},
    );
    try writeLine(
        io,
        out,
        "expected_candidate_count: {d}\n",
        .{search.candidate_count},
    );
    try writeLine(io, out, "baseline_candidates: {d}\n", .{baselines});
    try writeLine(io, out, "invalid_theta: {d}\n", .{invalid_theta});
    try writeLine(
        io,
        out,
        "duplicate_candidates: {d}\n",
        .{duplicate_candidates},
    );
    try writeLine(
        io,
        out,
        "training_worlds: {d}\n",
        .{search.worldCount(.training)},
    );
    try writeLine(
        io,
        out,
        "validation_worlds: {d}\n",
        .{search.worldCount(.validation)},
    );

    if (candidates.len != search.candidate_count or
        baselines != search.frozen_base_ids.len or
        invalid_theta != 0 or
        duplicate_candidates != 0)
    {
        std.process.exit(1);
    }
}

fn baselineAudit(io: std.Io) !void {
    const candidates = search.generateCandidates();
    const historical = reference.generateCandidates();

    var checks: usize = 0;
    var mismatches: usize = 0;

    for (search.frozen_base_ids) |base_id| {
        const index = search.baselineIndexForBase(
            &candidates,
            base_id,
        ) orelse {
            mismatches += 1;
            continue;
        };

        const actual_candidate = candidates.items[index];
        const expected_candidate = historical.items[base_id];

        if (!try baselineMatches(
            actual_candidate,
            expected_candidate,
            .training,
            .training,
        )) {
            mismatches += 1;
        }
        checks += 1;

        if (!try baselineMatches(
            actual_candidate,
            expected_candidate,
            .validation,
            .validation,
        )) {
            mismatches += 1;
        }
        checks += 1;
    }

    const out = std.Io.File.stdout();
    try writeLine(io, out, "F3b paired baseline audit\n", .{});
    try writeLine(io, out, "base_ids: 37,51,93\n", .{});
    try writeLine(io, out, "aggregate_checks: {d}\n", .{checks});
    try writeLine(io, out, "mismatches: {d}\n", .{mismatches});

    if (mismatches != 0) std.process.exit(1);
}

fn baselineMatches(
    actual_candidate: search.Candidate,
    expected_candidate: reference.Candidate,
    actual_split: search.SplitKind,
    expected_split: reference.SplitKind,
) !bool {
    const actual = try search.evaluateCandidate(
        actual_candidate,
        actual_split,
    );
    const expected = try reference.evaluateCandidate(
        expected_candidate,
        expected_split,
    );

    return actual.runs == expected.runs and
        actual.failures == expected.failures and
        actual.rounds_sum == expected.rounds_sum and
        actual.communication_sum == expected.communication_sum and
        actual.duplicate_sum == expected.duplicate_sum and
        actual.computation_sum == expected.computation_sum and
        actual.inference_sum == expected.computation_sum and
        actual.cache_reuse_sum == 0 and
        actual.useful_sum == expected.useful_sum and
        actual.violations == expected.violations and
        actual.inferenceAccounted() and
        actual.communicationAccounted();
}

fn runSearch(io: std.Io) !void {
    const out = std.Io.File.stdout();
    const candidates = search.generateCandidates();
    var total_violations: u64 = 0;

    var training =
        [_]search.Aggregate{.{}} ** search.max_candidates;
    const all_eligible = search.allEligible(candidates.len);

    var i: usize = 0;
    while (i < candidates.len) : (i += 1) {
        training[i] = try search.evaluateCandidate(
            candidates.items[i],
            .training,
        );
        total_violations +%= training[i].violations;
    }

    const training_frontier = search.computeFrontier(
        candidates.len,
        &training,
        &all_eligible,
    );

    var validation =
        [_]search.Aggregate{.{}} ** search.max_candidates;
    const validation_evaluated = search.selectedOrBaselines(
        &candidates,
        &training_frontier.flags,
    );

    i = 0;
    while (i < candidates.len) : (i += 1) {
        if (!validation_evaluated[i]) continue;
        validation[i] = try search.evaluateCandidate(
            candidates.items[i],
            .validation,
        );
        total_violations +%= validation[i].violations;
    }

    const validation_frontier = search.computeFrontier(
        candidates.len,
        &validation,
        &training_frontier.flags,
    );
    const hard_evaluated = search.selectedOrBaselines(
        &candidates,
        &validation_frontier.flags,
    );

    try writeHeader(io, out);

    i = 0;
    while (i < candidates.len) : (i += 1) {
        try writeRow(
            io,
            out,
            "training",
            .training,
            candidates.items[i],
            training[i],
            training_frontier.flags[i],
        );
    }

    i = 0;
    while (i < candidates.len) : (i += 1) {
        if (!validation_evaluated[i]) continue;
        try writeRow(
            io,
            out,
            "validation",
            .validation,
            candidates.items[i],
            validation[i],
            validation_frontier.flags[i],
        );
    }

    const hard_splits = [_]search.SplitKind{
        .population_extrapolation,
        .density_extrapolation,
        .redundancy_extrapolation,
        .bandwidth_extrapolation,
        .topology_extrapolation,
        .compound_extrapolation,
    };

    for (hard_splits) |split| {
        i = 0;
        while (i < candidates.len) : (i += 1) {
            if (!hard_evaluated[i]) continue;
            const metrics = try search.evaluateCandidate(
                candidates.items[i],
                split,
            );
            total_violations +%= metrics.violations;
            try writeRow(
                io,
                out,
                "hard",
                split,
                candidates.items[i],
                metrics,
                validation_frontier.flags[i],
            );
        }
    }

    if (total_violations != 0) std.process.exit(2);
}

fn writeHeader(io: std.Io, out: std.Io.File) !void {
    try out.writeStreamingAll(
        io,
        "phase\tsplit\tid\tbase_id\tcontroller\truns\tfailures\t" ++
            "rounds_sum\tcommunication_sum\tduplicate_sum\t" ++
            "computation_sum\tinference_sum\tcache_reuse_sum\tuseful_sum\t" ++
            "violations\trefresh_first\trefresh_always\trefresh_knowledge\t" ++
            "refresh_invalid_action\trefresh_stale_action\trefresh_age\t" ++
            "inference_accounted\tcommunication_accounted\t" ++
            "selected_frontier\n",
    );
}

fn writeRow(
    io: std.Io,
    out: std.Io.File,
    phase: []const u8,
    split: search.SplitKind,
    candidate: search.Candidate,
    metrics: search.Aggregate,
    selected: bool,
) !void {
    try writeLine(
        io,
        out,
        "{s}\t{s}\t{d}\t{d}\t{s}\t{d}\t{d}\t{d}\t{d}\t{d}\t" ++
            "{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t" ++
            "{d}\t{s}\t{s}\t{s}\n",
        .{
            phase,
            split.name(),
            candidate.id,
            candidate.base_id,
            candidate.label(),
            metrics.runs,
            metrics.failures,
            metrics.rounds_sum,
            metrics.communication_sum,
            metrics.duplicate_sum,
            metrics.computation_sum,
            metrics.inference_sum,
            metrics.cache_reuse_sum,
            metrics.useful_sum,
            metrics.violations,
            metrics.refresh_first,
            metrics.refresh_always,
            metrics.refresh_knowledge,
            metrics.refresh_invalid_action,
            metrics.refresh_stale_action,
            metrics.refresh_age,
            yesNo(metrics.inferenceAccounted()),
            yesNo(metrics.communicationAccounted()),
            yesNo(selected),
        },
    );
}

fn yesNo(value: bool) []const u8 {
    return if (value) "yes" else "no";
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
