const std = @import("std");
const f1a = @import("f1a_fault_matrix.zig");
const scaling = @import("../substrate/stage5/stage5a_scaling.zig");

pub fn main(init: std.process.Init) !void {
    const io = init.io;
    const args = try init.minimal.args.toSlice(init.arena.allocator());

    if (args.len == 2 and std.mem.eql(u8, args[1], "validate")) {
        try validate(io);
        return;
    }
    if (args.len == 2 and std.mem.eql(u8, args[1], "matrix")) {
        try matrix(io);
        return;
    }

    try std.Io.File.stderr().writeStreamingAll(
        io,
        "usage:\n  f1a validate\n  f1a matrix\n",
    );
    std.process.exit(2);
}

fn validate(io: std.Io) !void {
    var rows: usize = 0;
    var successes: usize = 0;
    var accounting_failures: usize = 0;
    var missing_accounting_failures: usize = 0;
    var unattributed: usize = 0;
    var violations: u64 = 0;

    const topologies = [_]scaling.TopologyKind{ .ring, .grid };
    for (f1a.canonical_profiles) |profile| {
        for (topologies) |topology| {
            var seed: u64 = 0;
            while (seed < 3) : (seed += 1) {
                for (f1a.canonical_faults) |fault| {
                    const result = try f1a.run(
                        f1a.canonicalConfig(seed, topology, fault),
                        profile.theta,
                    );
                    rows += 1;
                    if (result.success) successes += 1;
                    if (!result.accounted()) accounting_failures += 1;
                    if (!result.missingAccounted()) {
                        missing_accounting_failures += 1;
                    }
                    unattributed += result.unattributed_missing;
                    violations +%= result.violations;
                }
            }
        }
    }

    const out = std.Io.File.stdout();
    try writeLine(io, out, "F1a canonical fault-matrix validation\n", .{});
    try writeLine(io, out, "rows: {d}\n", .{rows});
    try writeLine(io, out, "successes: {d}\n", .{successes});
    try writeLine(
        io,
        out,
        "envelope_accounting_failures: {d}\n",
        .{accounting_failures},
    );
    try writeLine(
        io,
        out,
        "missing_accounting_failures: {d}\n",
        .{missing_accounting_failures},
    );
    try writeLine(io, out, "unattributed_missing: {d}\n", .{unattributed});
    try writeLine(io, out, "violations: {d}\n", .{violations});

    if (rows != 432 or
        accounting_failures != 0 or
        missing_accounting_failures != 0 or
        unattributed != 0 or
        violations != 0)
    {
        std.process.exit(1);
    }
}

fn matrix(io: std.Io) !void {
    const out = std.Io.File.stdout();
    try out.writeStreamingAll(
        io,
        "profile\ttopology\tworld_seed\tschedule_seed\tfault\tsuccess\t" ++
            "ticks\tcollector_initial\tcollector_final\tmissing\tpolicy_ticks\t" ++
            "actions\trejected_actions\ttransport_attempts\tdelivered\tdropped\t" ++
            "partitioned\tcrashed\tqueue_overflow\tpending\tduplicate_copies\t" ++
            "reordered\tstale_observations\tforced_reorder_schedules\t" ++
            "communication_units\tuseful\tduplicate\tschedule_hash\ttrace_hash\t" ++
            "violations\tnever_transmitted\tdelivery_faulted\tcrashed_before_merge\t" ++
            "pending_at_censor\tunattributed\tenvelope_accounted\t" ++
            "missing_accounted\tfully_accounted\n",
    );

    const topologies = [_]scaling.TopologyKind{ .ring, .grid };
    for (f1a.canonical_profiles) |profile| {
        for (topologies) |topology| {
            var seed: u64 = 0;
            while (seed < 3) : (seed += 1) {
                for (f1a.canonical_faults) |fault| {
                    const result = try f1a.run(
                        f1a.canonicalConfig(seed, topology, fault),
                        profile.theta,
                    );
                    try writeResult(io, out, profile.name, fault, result);
                }
            }
        }
    }
}

fn writeResult(
    io: std.Io,
    out: std.Io.File,
    profile: []const u8,
    fault: f1a.FaultKind,
    result: f1a.Result,
) !void {
    const missing =
        result.config.world.fact_count - result.collector_final_facts;

    try writeLine(
        io,
        out,
        "{s}\t{s}\t{d}\t{d}\t{s}\t{s}\t{d}\t{d}\t{d}\t{d}\t" ++
            "{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t",
        .{
            profile,
            result.config.world.topology.name(),
            result.config.world.seed,
            result.config.schedule_seed,
            fault.name(),
            yesNo(result.success),
            result.elapsed_ticks,
            result.collector_initial_facts,
            result.collector_final_facts,
            missing,
            result.local_policy_ticks,
            result.actions,
            result.rejected_actions,
            result.transport_attempts,
            result.delivered_envelopes,
            result.dropped_envelopes,
            result.partitioned_envelopes,
            result.crashed_envelopes,
            result.queue_overflow_envelopes,
            result.pending_envelopes,
        },
    );
    try writeLine(
        io,
        out,
        "{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{x}\t{x}\t{d}\t" ++
            "{d}\t{d}\t{d}\t{d}\t{d}\t{s}\t{s}\t{s}\n",
        .{
            result.duplicate_copies,
            result.reordered_envelopes,
            result.stale_observations,
            result.forced_reorder_schedules,
            result.communication_units,
            result.useful_deliveries,
            result.duplicate_deliveries,
            result.schedule_hash,
            result.trace_hash,
            result.violations,
            result.never_transmitted_missing,
            result.delivery_faulted_missing,
            result.crashed_before_merge_missing,
            result.pending_at_censor_missing,
            result.unattributed_missing,
            yesNo(result.accounted()),
            yesNo(result.missingAccounted()),
            yesNo(result.fullyAccounted()),
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
