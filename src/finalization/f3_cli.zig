const std = @import("std");
const f3 = @import("f3_inference_policy.zig");
const search = @import("f3_search.zig");
const stage7a = @import("../substrate/stage7/stage7a_policy.zig");
const stage7c = @import("../substrate/stage7/stage7c_async_transfer.zig");

pub fn main(init: std.process.Init) !void {
    const io = init.io;
    const args = try init.minimal.args.toSlice(init.arena.allocator());

    if (args.len == 2 and std.mem.eql(u8, args[1], "validate")) {
        try validate(io);
        return;
    }
    if (args.len == 2 and std.mem.eql(u8, args[1], "corner")) {
        try corner(io);
        return;
    }
    if (args.len == 2 and std.mem.eql(u8, args[1], "search")) {
        try runSearch(io);
        return;
    }

    try std.Io.File.stderr().writeStreamingAll(
        io,
        "usage:\n  f3 validate\n  f3 corner\n  f3 search\n",
    );
    std.process.exit(2);
}

fn validate(io: std.Io) !void {
    const candidates = search.generateCandidates();
    const corner_candidates = search.generateCornerCandidates();

    var invalid: usize = 0;
    var duplicates: usize = 0;
    var gated: usize = 0;

    var i: usize = 0;
    while (i < candidates.len) : (i += 1) {
        candidates.items[i].theta.validate() catch {
            invalid += 1;
        };
        if (candidates.items[i].theta.inference_gating_permille < 1000) {
            gated += 1;
        }

        var j: usize = i + 1;
        while (j < candidates.len) : (j += 1) {
            if (candidates.items[i].theta.eql(candidates.items[j].theta)) {
                duplicates += 1;
            }
        }
    }

    const out = std.Io.File.stdout();
    try writeLine(io, out, "F3 validation\n", .{});
    try writeLine(io, out, "candidate_count: {d}\n", .{candidates.len});
    try writeLine(
        io,
        out,
        "expected_candidate_count: {d}\n",
        .{search.canonical_candidate_count},
    );
    try writeLine(io, out, "corner_candidate_count: {d}\n", .{corner_candidates.len});
    try writeLine(io, out, "gated_candidates: {d}\n", .{gated});
    try writeLine(io, out, "invalid_theta: {d}\n", .{invalid});
    try writeLine(io, out, "duplicate_theta: {d}\n", .{duplicates});
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

    if (candidates.len != search.canonical_candidate_count or
        corner_candidates.len != search.canonical_candidate_count or
        invalid != 0 or duplicates != 0 or gated == 0)
    {
        std.process.exit(1);
    }
}

fn corner(io: std.Io) !void {
    const candidates = search.generateCornerCandidates();
    var mismatches: usize = 0;
    var checks: usize = 0;

    var i: usize = 0;
    while (i < candidates.len) : (i += 1) {
        if (!try search.cornerMatchesReference(i, .training)) {
            mismatches += 1;
        }
        checks += 1;
        if (!try search.cornerMatchesReference(i, .validation)) {
            mismatches += 1;
        }
        checks += 1;
    }

    const out = std.Io.File.stdout();
    try writeLine(io, out, "F3 c=1000 Stage 7B corner audit\n", .{});
    try writeLine(io, out, "candidates: {d}\n", .{candidates.len});
    try writeLine(io, out, "aggregate_checks: {d}\n", .{checks});
    try writeLine(io, out, "mismatches: {d}\n", .{mismatches});

    if (mismatches != 0) std.process.exit(1);
}

fn runSearch(io: std.Io) !void {
    const out = std.Io.File.stdout();
    const candidates = search.generateCandidates();

    var training =
        [_]search.Aggregate{.{}} ** search.max_candidates;
    const all_eligible = search.allEligible(candidates.len);
    var total_violations: u64 = 0;

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
    const validation_evaluated = search.selectedOrControls(
        candidates.len,
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
    const hard_selected = search.selectedOrControls(
        candidates.len,
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

        if (validation_frontier.flags[i] and
            candidates.items[i].theta.inference_gating_permille < 1000)
        {
            var twin = candidates.items[i];
            twin.source = .fixed_profile;
            twin.label = "ungated_twin";
            twin.theta.inference_gating_permille = 1000;
            const twin_metrics = try search.evaluateCandidate(
                twin,
                .validation,
            );
            total_violations +%= twin_metrics.violations;
            try writeRow(
                io,
                out,
                "validation_ungated_twin",
                .validation,
                twin,
                twin_metrics,
                false,
            );
        }
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
            if (!hard_selected[i]) continue;
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

        const frozen = frozenFamily();
        for (frozen) |candidate| {
            const metrics = try search.evaluateCandidate(candidate, split);
            total_violations +%= metrics.violations;
            try writeRow(
                io,
                out,
                "hard_frozen_stage7b",
                split,
                candidate,
                metrics,
                false,
            );
        }
    }

    if (total_violations != 0) std.process.exit(2);
}

fn frozenFamily() [3]search.Candidate {
    return .{
        .{
            .id = 37,
            .source = .fixed_profile,
            .label = "frozen_id37",
            .theta = .{
                .base = stage7c.theta37,
                .inference_gating_permille = 1000,
            },
        },
        .{
            .id = 51,
            .source = .fixed_profile,
            .label = "frozen_id51",
            .theta = .{
                .base = stage7c.theta51,
                .inference_gating_permille = 1000,
            },
        },
        .{
            .id = 93,
            .source = .fixed_profile,
            .label = "frozen_id93",
            .theta = .{
                .base = stage7c.theta93,
                .inference_gating_permille = 1000,
            },
        },
    };
}

fn writeHeader(io: std.Io, out: std.Io.File) !void {
    try out.writeStreamingAll(
        io,
        "phase\tsplit\tid\tsource\tlabel\tn\te\tr\tu\tc\truns\t" ++
            "failures\trounds_sum\tcommunication_sum\tduplicate_sum\t" ++
            "computation_sum\tinference_sum\tuseful_sum\tuseful_per_1000\t" ++
            "duplicate_permille\tviolations\tselected_frontier\n",
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
        "{s}\t{s}\t{d}\t{s}\t{s}\t{d}\t{d}\t{d}\t{d}\t{d}\t" ++
            "{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{s}\n",
        .{
            phase,
            split.name(),
            candidate.id,
            candidate.source.name(),
            candidate.label,
            candidate.theta.base.novelty_permille,
            candidate.theta.base.exploration_permille,
            candidate.theta.base.retry_permille,
            candidate.theta.base.bandwidth_utilization_permille,
            candidate.theta.inference_gating_permille,
            metrics.runs,
            metrics.failures,
            metrics.rounds_sum,
            metrics.communication_sum,
            metrics.duplicate_sum,
            metrics.computation_sum,
            metrics.inference_sum,
            metrics.useful_sum,
            metrics.usefulPerThousand(),
            metrics.duplicatePermille(),
            metrics.violations,
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
