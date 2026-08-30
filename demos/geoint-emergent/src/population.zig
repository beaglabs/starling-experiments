const std = @import("std");
const schema = @import("schema.zig");
const protocol = @import("protocol.zig");
const context_mod = @import("context.zig");
const runtime_mod = @import("runtime.zig");
const observation = @import("observation.zig");
const agents = @import("agents.zig");

pub const population_version: u8 = 1;
pub const arbitration_rule =
    "minimum-blake3(seed,round,typed-geoint-proposal)";

pub const Candidate = struct {
    proposal: protocol.Proposal,
    rank: [32]u8,
};

pub const Result = struct {
    runtime: runtime_mod.Runtime,
    rounds: u32,
    deadlocked: bool,
    semantic_digest: [32]u8,
    participating_roles: u8,
    shadowfinder_calls: u8,
    resolved_fields: u8,
};

pub fn run(
    context: context_mod.AcquisitionContext,
    seed: u64,
) !Result {
    var rt = runtime_mod.Runtime.init(context);
    var rounds: u32 = 0;
    var deadlocked = false;

    while (!rt.terminated and rounds < 32) {
        var candidates: [12]Candidate = undefined;
        const count = collectCandidates(
            &rt,
            seed,
            rounds,
            &candidates,
        );

        if (count == 0) {
            deadlocked = true;
            break;
        }

        const selected = chooseCandidate(candidates[0..count]);
        _ = try rt.submit(selected.proposal);
        rounds +%= 1;
    }

    return .{
        .runtime = rt,
        .rounds = rounds,
        .deadlocked = deadlocked,
        .semantic_digest = semanticDigest(rt.trace[0..rt.trace_len]),
        .participating_roles = participatingRoleCount(
            rt.trace[0..rt.trace_len],
        ),
        .shadowfinder_calls = acceptedActionCount(
            rt.trace[0..rt.trace_len],
            .run_shadowfinder,
        ),
        .resolved_fields = rt.facts.resolvedCount(),
    };
}

pub fn collectCandidates(
    rt: *const runtime_mod.Runtime,
    seed: u64,
    round: u32,
    out: *[12]Candidate,
) usize {
    var count: usize = 0;
    const roles = [_]protocol.Role{
        .geometry,
        .terrain,
        .water,
        .illumination,
        .atmospheric,
        .vegetation,
        .built_environment,
        .motion,
        .temporal,
        .material_spectral,
        .geolocation,
        .uncertainty,
    };
    for (roles) |role| {
        const obs = observation.observe(rt, role);
        if (agents.propose(obs)) |proposal| {
            appendCandidate(
                out,
                &count,
                proposal,
                seed,
                round,
            );
        }
    }
    return count;
}

pub fn chooseCandidate(candidates: []const Candidate) Candidate {
    std.debug.assert(candidates.len > 0);
    var best = candidates[0];

    var i: usize = 1;
    while (i < candidates.len) : (i += 1) {
        const order = std.mem.order(
            u8,
            &candidates[i].rank,
            &best.rank,
        );
        if (order == .lt or
            (order == .eq and tieBreak(
                candidates[i].proposal,
                best.proposal,
            ) < 0))
        {
            best = candidates[i];
        }
    }

    return best;
}

pub fn arbitrationRank(
    proposal: protocol.Proposal,
    seed: u64,
    round: u32,
) [32]u8 {
    var hasher = std.crypto.hash.Blake3.init(.{});
    hasher.update("GEOINT-EMERGENT-ARBITER");
    hasher.update(&[_]u8{population_version});

    var seed_bytes: [8]u8 = undefined;
    var round_bytes: [4]u8 = undefined;
    encodeU64Le(seed, &seed_bytes);
    encodeU32Le(round, &round_bytes);
    hasher.update(&seed_bytes);
    hasher.update(&round_bytes);
    hasher.update(&[_]u8{
        @intFromEnum(proposal.role),
        @intFromEnum(proposal.action),
    });

    var digest: [32]u8 = undefined;
    hasher.final(&digest);
    return digest;
}

pub fn semanticDigest(
    trace: []const runtime_mod.TraceEvent,
) [32]u8 {
    var hasher = std.crypto.hash.Blake3.init(.{});
    hasher.update("GEOINT-EMERGENT-SEMANTIC-TRACE");
    hasher.update(&[_]u8{population_version});

    for (trace) |event| {
        hasher.update(&[_]u8{
            @intFromEnum(event.role),
            @intFromEnum(event.action),
            if (event.accepted) 1 else 0,
            @intFromEnum(event.rejection),
            event.fields_written,
        });
    }

    var digest: [32]u8 = undefined;
    hasher.final(&digest);
    return digest;
}

fn appendCandidate(
    out: *[12]Candidate,
    count: *usize,
    proposal: protocol.Proposal,
    seed: u64,
    round: u32,
) void {
    std.debug.assert(count.* < out.len);
    out[count.*] = .{
        .proposal = proposal,
        .rank = arbitrationRank(proposal, seed, round),
    };
    count.* += 1;
}

fn participatingRoleCount(
    trace: []const runtime_mod.TraceEvent,
) u8 {
    var seen = [_]bool{false} ** 12;
    for (trace) |event| {
        if (!event.accepted) continue;
        seen[@intFromEnum(event.role)] = true;
    }

    var count: u8 = 0;
    for (seen) |value| {
        if (value) count +|= 1;
    }
    return count;
}

fn acceptedActionCount(
    trace: []const runtime_mod.TraceEvent,
    action: protocol.Action,
) u8 {
    var count: u8 = 0;
    for (trace) |event| {
        if (event.accepted and event.action == action) {
            count +|= 1;
        }
    }
    return count;
}

fn tieBreak(a: protocol.Proposal, b: protocol.Proposal) i8 {
    const ar = @intFromEnum(a.role);
    const br = @intFromEnum(b.role);
    if (ar < br) return -1;
    if (ar > br) return 1;

    const aa = @intFromEnum(a.action);
    const ba = @intFromEnum(b.action);
    if (aa < ba) return -1;
    if (aa > ba) return 1;
    return 0;
}

fn encodeU64Le(value: u64, out: *[8]u8) void {
    var i: usize = 0;
    while (i < 8) : (i += 1) {
        const shift: u6 = @intCast(i * 8);
        out[i] = @truncate(value >> shift);
    }
}

fn encodeU32Le(value: u32, out: *[4]u8) void {
    var i: usize = 0;
    while (i < 4) : (i += 1) {
        const shift: u5 = @intCast(i * 8);
        out[i] = @truncate(value >> shift);
    }
}

test "same GEOINT world and seed replay exactly" {
    const a = try run(
        context_mod.AcquisitionContext.photoNoDatetime(),
        0,
    );
    const b = try run(
        context_mod.AcquisitionContext.photoNoDatetime(),
        0,
    );

    try std.testing.expectEqual(a.rounds, b.rounds);
    try std.testing.expectEqualSlices(
        u8,
        &a.semantic_digest,
        &b.semantic_digest,
    );
}

test "GEOINT arbitration does not depend on candidate array order" {
    const pa = protocol.Proposal{
        .role = .geometry,
        .action = .inspect_geometry,
    };
    const pb = protocol.Proposal{
        .role = .terrain,
        .action = .inspect_terrain,
    };
    const ca = Candidate{
        .proposal = pa,
        .rank = arbitrationRank(pa, 7, 0),
    };
    const cb = Candidate{
        .proposal = pb,
        .rank = arbitrationRank(pb, 7, 0),
    };

    const forward = [_]Candidate{ ca, cb };
    const reverse = [_]Candidate{ cb, ca };
    const x = chooseCandidate(&forward);
    const y = chooseCandidate(&reverse);

    try std.testing.expectEqual(x.proposal.role, y.proposal.role);
    try std.testing.expectEqual(x.proposal.action, y.proposal.action);
}

test "missing datetime blocks ShadowFinder without blocking workflow" {
    const result = try run(
        context_mod.AcquisitionContext.photoNoDatetime(),
        0,
    );

    try std.testing.expect(result.runtime.terminated);
    try std.testing.expect(!result.deadlocked);
    try std.testing.expectEqual(@as(u8, 0), result.shadowfinder_calls);
    try std.testing.expectEqual(
        schema.Status.blocked,
        result.runtime.facts.status(.candidate_region),
    );
    try std.testing.expectEqual(
        schema.Status.unavailable,
        result.runtime.facts.status(.new_objects),
    );
    try std.testing.expect(result.runtime.facts.allResolved());
    try std.testing.expect(result.runtime.invariantsHold());
}

test "shadow-ready context activates ShadowFinder path" {
    const result = try run(
        context_mod.AcquisitionContext.photoShadowReady(),
        0,
    );

    try std.testing.expect(result.runtime.terminated);
    try std.testing.expect(!result.deadlocked);
    try std.testing.expectEqual(@as(u8, 1), result.shadowfinder_calls);
    try std.testing.expectEqual(
        schema.Status.derived,
        result.runtime.facts.status(.candidate_region),
    );
    try std.testing.expect(result.runtime.facts.allResolved());
    try std.testing.expect(result.runtime.invariantsHold());
}

test "different seeds yield distinct GEOINT coordination trajectories" {
    const a = try run(
        context_mod.AcquisitionContext.photoNoDatetime(),
        0,
    );
    const b = try run(
        context_mod.AcquisitionContext.photoNoDatetime(),
        1,
    );

    try std.testing.expect(
        !std.mem.eql(u8, &a.semantic_digest, &b.semantic_digest),
    );
}
