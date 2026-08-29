const std = @import("std");
const f1c = @import("f1c_zquic_experiment.zig");
const scaling = @import("../substrate/stage5/stage5a_scaling.zig");

pub fn main(init: std.process.Init) !void {
    const io = init.io;
    const args = try init.minimal.args.toSlice(init.arena.allocator());

    if (args.len == 2 and std.mem.eql(u8, args[1], "smoke")) {
        const result = try f1c.run(
            init.arena.allocator(),
            f1c.canonicalConfig(0, .ring, .no_fault),
            "theta51",
            f1c.profileTheta("theta51").?,
        );
        try writeHeader(io);
        try writeResult(io, result);
        if (!result.success or !result.fullyAccounted() or
            result.violations != 0 or result.send_failures != 0 or
            result.malformed_frames != 0)
        {
            std.process.exit(1);
        }
        return;
    }

    if (args.len == 6 and std.mem.eql(u8, args[1], "world")) {
        const profile = args[2];
        const theta = f1c.profileTheta(profile) orelse {
            try usage(io);
            std.process.exit(2);
        };
        const topology = parseTopology(args[3]) orelse {
            try usage(io);
            std.process.exit(2);
        };
        const seed = std.fmt.parseInt(u64, args[4], 10) catch {
            try usage(io);
            std.process.exit(2);
        };
        const fault = f1c.FaultKind.parse(args[5]) orelse {
            try usage(io);
            std.process.exit(2);
        };

        const result = try f1c.run(
            init.arena.allocator(),
            f1c.canonicalConfig(seed, topology, fault),
            profile,
            theta,
        );
        try writeHeader(io);
        try writeResult(io, result);
        return;
    }

    try usage(io);
    std.process.exit(2);
}

fn parseTopology(value: []const u8) ?scaling.TopologyKind {
    if (std.mem.eql(u8, value, "ring")) return .ring;
    if (std.mem.eql(u8, value, "grid")) return .grid;
    return null;
}

fn usage(io: std.Io) !void {
    try std.Io.File.stderr().writeStreamingAll(
        io,
        "usage:\n" ++
            "  f1c smoke\n" ++
            "  f1c world <profile> <ring|grid> <seed> <fault>\n",
    );
}

fn writeHeader(io: std.Io) !void {
    try std.Io.File.stdout().writeStreamingAll(
        io,
        "profile\ttopology\tseed\tfault\tsuccess\tticks\t" ++
            "collector_initial\tcollector_final\tmissing\tpolicy_ticks\t" ++
            "actions\trejected_actions\ttransport_attempts\tdelivered\t" ++
            "partitioned\tcrashed\tpending\tattempted_communication_units\t" ++
            "communication_units\tuseful\tduplicate\ttransport_duplicate_deliveries\t" ++
            "violations\tnever_transmitted\tdelivery_faulted\tcrashed_before_merge\t" ++
            "pending_at_censor\tunattributed\tudp_datagrams\tnetwork_polls\t" ++
            "backpressure_events\tsend_failures\tmalformed_frames\tschedule_hash\t" ++
            "envelope_accounted\tmissing_accounted\tcommunication_accounted\t" ++
            "fully_accounted\tresult_signature\n",
    );
}

fn writeResult(io: std.Io, result: f1c.Result) !void {
    const missing = result.fact_count - result.collector_final;
    var buffer: [8192]u8 = undefined;
    const line = try std.fmt.bufPrint(
        &buffer,
        "{s}\t{s}\t{d}\t{s}\t{s}\t{d}\t{d}\t{d}\t{d}\t{d}\t" ++
            "{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t" ++
            "{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t{d}\t" ++
            "{d}\t{d}\t{d}\t{x}\t{s}\t{s}\t{s}\t{s}\t{x}\n",
        .{
            result.profile,
            result.topology.name(),
            result.seed,
            result.fault.name(),
            yesNo(result.success),
            result.ticks,
            result.collector_initial,
            result.collector_final,
            missing,
            result.policy_ticks,
            result.actions,
            result.rejected_actions,
            result.transport_attempts,
            result.delivered,
            result.partitioned,
            result.crashed,
            result.pending,
            result.attempted_communication_units,
            result.communication_units,
            result.useful,
            result.duplicate,
            result.transport_duplicate_deliveries,
            result.violations,
            result.never_transmitted,
            result.delivery_faulted,
            result.crashed_before_merge,
            result.pending_at_censor,
            result.unattributed,
            result.udp_datagrams,
            result.network_polls,
            result.backpressure_events,
            result.send_failures,
            result.malformed_frames,
            result.schedule_hash,
            yesNo(result.accounted()),
            yesNo(result.missingAccounted()),
            yesNo(result.communicationAccounted()),
            yesNo(result.fullyAccounted()),
            result.result_signature,
        },
    );
    try std.Io.File.stdout().writeStreamingAll(io, line);
}

fn yesNo(value: bool) []const u8 {
    return if (value) "yes" else "no";
}
