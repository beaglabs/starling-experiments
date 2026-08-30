const std = @import("std");
const artifacts = @import("artifacts.zig");
const messages = @import("messages.zig");
const runtime_mod = @import("runtime.zig");
const observation = @import("d3_observation.zig");

const spatial_prior = @import("agents/spatial_prior.zig");
const geometry = @import("agents/geometry.zig");
const view_planner = @import("agents/view_planner.zig");
const novel_view = @import("agents/novel_view.zig");
const fusion = @import("agents/fusion.zig");
const critic = @import("agents/critic.zig");

pub const population_version: u8 = 1;
pub const arbitration_rule =
    "minimum-blake3(seed,round,typed-proposal)";
pub const fixture_input_payload =
    "d3-emergent-specialist-population-input-v1";

pub const Config = struct {
    max_rounds: u32 = 64,
    runtime: runtime_mod.Config = .{
        .max_actions = 48,
        .max_tool_invocations = 32,
        .max_wall_time_ms = 500,
        .stop_quality_floor = runtime_mod.default_stop_quality_floor,
    },
};

pub const Candidate = struct {
    proposal: messages.Proposal,
    rank: artifacts.ArtifactId,
};

pub const Result = struct {
    runtime: runtime_mod.DemoRuntime,
    rounds: u32,
    deadlocked: bool,
    semantic_digest: artifacts.ArtifactId,
    participating_roles: u8,
    final_quality: u64,
    view_count: u8,
};

pub fn run(seed: u64, config: Config) !Result {
    var rt = runtime_mod.DemoRuntime.init(seed, config.runtime);
    _ = try rt.addInput(fixture_input_payload);

    var rounds: u32 = 0;
    var deadlocked = false;

    while (!rt.terminated and rounds < config.max_rounds) {
        var candidates: [6]Candidate = undefined;
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
        .semantic_digest = semanticDigest(
            rt.trace[0..rt.trace_len],
        ),
        .participating_roles = participatingRoleCount(
            rt.trace[0..rt.trace_len],
        ),
        .final_quality = latestEvaluationValue(&rt),
        .view_count = observation.countKind(&rt, .view_request),
    };
}

pub fn collectCandidates(
    rt: *const runtime_mod.DemoRuntime,
    seed: u64,
    round: u32,
    out: *[6]Candidate,
) usize {
    var count: usize = 0;

    const spatial_obs = observation.observe(rt, .spatial_prior);
    if (spatial_prior.propose(spatial_obs, seed)) |proposal| {
        appendCandidate(out, &count, proposal, seed, round);
    }

    const geometry_obs = observation.observe(rt, .geometry);
    if (geometry.propose(geometry_obs, seed)) |proposal| {
        appendCandidate(out, &count, proposal, seed, round);
    }

    const planner_obs = observation.observe(rt, .view_planner);
    if (view_planner.propose(planner_obs, seed)) |proposal| {
        appendCandidate(out, &count, proposal, seed, round);
    }

    const novel_obs = observation.observe(rt, .novel_view);
    if (novel_view.propose(novel_obs, seed)) |proposal| {
        appendCandidate(out, &count, proposal, seed, round);
    }

    const fusion_obs = observation.observe(rt, .fusion);
    if (fusion.propose(fusion_obs, seed)) |proposal| {
        appendCandidate(out, &count, proposal, seed, round);
    }

    const critic_obs = observation.observe(rt, .critic);
    if (critic.propose(rt, critic_obs, seed)) |proposal| {
        appendCandidate(out, &count, proposal, seed, round);
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
            (order == .eq and
                proposalTieBreak(
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
    proposal: messages.Proposal,
    seed: u64,
    round: u32,
) artifacts.ArtifactId {
    var hasher = std.crypto.hash.Blake3.init(.{});
    hasher.update("EVO-D3-ARBITER");
    hasher.update(&[_]u8{population_version});

    var seed_bytes: [8]u8 = undefined;
    var round_bytes: [4]u8 = undefined;
    encodeU64Le(seed, &seed_bytes);
    encodeU32Le(round, &round_bytes);
    hasher.update(&seed_bytes);
    hasher.update(&round_bytes);

    hasher.update(&[_]u8{
        @intFromEnum(proposal.operator),
        @intFromEnum(proposal.action),
        proposal.input_count,
    });

    var i: usize = 0;
    while (i < artifacts.max_parents) : (i += 1) {
        hasher.update(&proposal.inputs[i]);
    }

    var payload_bytes: [8]u8 = undefined;
    encodeU64Le(proposal.payload, &payload_bytes);
    hasher.update(&payload_bytes);

    var digest: artifacts.ArtifactId = undefined;
    hasher.final(&digest);
    return digest;
}

pub fn semanticDigest(
    trace: []const runtime_mod.TraceEvent,
) artifacts.ArtifactId {
    var hasher = std.crypto.hash.Blake3.init(.{});
    hasher.update("EVO-D3-SEMANTIC-TRACE");
    hasher.update(&[_]u8{population_version});

    for (trace) |event| {
        hasher.update(&[_]u8{
            @intFromEnum(event.operator),
            @intFromEnum(event.action),
            if (event.accepted) 1 else 0,
            @intFromEnum(event.rejection),
        });

        var payload_bytes: [8]u8 = undefined;
        encodeU64Le(event.payload, &payload_bytes);
        hasher.update(&payload_bytes);
    }

    var digest: artifacts.ArtifactId = undefined;
    hasher.final(&digest);
    return digest;
}

fn appendCandidate(
    out: *[6]Candidate,
    count: *usize,
    proposal: messages.Proposal,
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

fn proposalTieBreak(
    a: messages.Proposal,
    b: messages.Proposal,
) i8 {
    const ao = @intFromEnum(a.operator);
    const bo = @intFromEnum(b.operator);
    if (ao < bo) return -1;
    if (ao > bo) return 1;

    const aa = @intFromEnum(a.action);
    const ba = @intFromEnum(b.action);
    if (aa < ba) return -1;
    if (aa > ba) return 1;

    if (a.payload < b.payload) return -1;
    if (a.payload > b.payload) return 1;
    return 0;
}

fn participatingRoleCount(
    trace: []const runtime_mod.TraceEvent,
) u8 {
    var seen = [_]bool{false} ** 6;
    for (trace) |event| {
        if (!event.accepted) continue;
        seen[@intFromEnum(event.operator)] = true;
    }

    var count: u8 = 0;
    for (seen) |value| {
        if (value) count +%= 1;
    }
    return count;
}

fn latestEvaluationValue(
    rt: *const runtime_mod.DemoRuntime,
) u64 {
    const evaluation = observation.latestKind(
        rt,
        .evaluation_report,
    ) orelse return 0;
    return evaluation.value;
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

test "D3 arbiter selection is independent of candidate array order" {
    const a = messages.Proposal{
        .operator = .spatial_prior,
        .action = .estimate_depth,
        .input_count = 1,
    };
    const b = messages.Proposal{
        .operator = .spatial_prior,
        .action = .estimate_camera,
        .input_count = 1,
    };

    const ca = Candidate{
        .proposal = a,
        .rank = arbitrationRank(a, 7, 0),
    };
    const cb = Candidate{
        .proposal = b,
        .rank = arbitrationRank(b, 7, 0),
    };

    const forward = [_]Candidate{ ca, cb };
    const reverse = [_]Candidate{ cb, ca };

    const x = chooseCandidate(&forward);
    const y = chooseCandidate(&reverse);

    try std.testing.expectEqual(
        x.proposal.action,
        y.proposal.action,
    );
}

test "D3 same seed produces byte-identical trace" {
    const first = try run(0, .{});
    const second = try run(0, .{});

    var first_buffer: [32 * 1024]u8 = undefined;
    var second_buffer: [32 * 1024]u8 = undefined;
    const a = try first.runtime.canonicalTrace(&first_buffer);
    const b = try second.runtime.canonicalTrace(&second_buffer);

    try std.testing.expectEqualSlices(u8, a, b);
    try std.testing.expect(
        artifacts.eqlId(
            first.semantic_digest,
            second.semantic_digest,
        ),
    );
}

test "D3 seeds produce distinct successful semantic trajectories" {
    const first = try run(0, .{});
    const second = try run(1, .{});

    try std.testing.expect(first.runtime.terminated);
    try std.testing.expect(second.runtime.terminated);
    try std.testing.expect(!first.deadlocked);
    try std.testing.expect(!second.deadlocked);
    try std.testing.expect(first.runtime.invariantsHold());
    try std.testing.expect(second.runtime.invariantsHold());

    try std.testing.expect(
        !artifacts.eqlId(
            first.semantic_digest,
            second.semantic_digest,
        ),
    );
}

test "D3 all six specialists participate in successful run" {
    const result = try run(0, .{});
    try std.testing.expectEqual(
        @as(u8, 6),
        result.participating_roles,
    );
    try std.testing.expect(result.view_count >= 1);
    try std.testing.expect(result.final_quality >= 800);
}
