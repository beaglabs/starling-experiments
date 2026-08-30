const std = @import("std");
const artifacts = @import("artifacts.zig");
const messages = @import("messages.zig");
const accounting_mod = @import("accounting.zig");
const mock_tools = @import("mock_tools.zig");
const runtime_mod = @import("runtime.zig");

pub const fixed_schedule_version: u8 = 1;
pub const fixed_input_payload = "d1-fixed-scene-input-v1";
pub const fixed_stop_quality_floor: u64 =
    runtime_mod.default_stop_quality_floor;

pub const FixedPose = struct {
    azimuth_mdeg: u32,
    elevation_mdeg: u32,

    pub fn payload(self: FixedPose) u64 {
        return (@as(u64, self.azimuth_mdeg) << 32) |
            @as(u64, self.elevation_mdeg);
    }
};

pub const fixed_poses = [_]FixedPose{
    .{
        .azimuth_mdeg = 35_000,
        .elevation_mdeg = 10_000,
    },
    .{
        .azimuth_mdeg = 325_000,
        .elevation_mdeg = 10_000,
    },
};

pub const FixedStep = struct {
    operator: artifacts.Operator,
    action: messages.ActionKind,
    payload: u64 = 0,
};

pub const fixed_steps = [_]FixedStep{
    .{
        .operator = .spatial_prior,
        .action = .estimate_depth,
    },
    .{
        .operator = .spatial_prior,
        .action = .estimate_camera,
    },
    .{
        .operator = .geometry,
        .action = .build_geometry,
    },

    .{
        .operator = .view_planner,
        .action = .propose_view,
        .payload = fixed_poses[0].payload(),
    },
    .{
        .operator = .novel_view,
        .action = .render_view,
    },
    .{
        .operator = .fusion,
        .action = .fuse_view,
    },
    .{
        .operator = .geometry,
        .action = .refine_geometry,
    },

    .{
        .operator = .view_planner,
        .action = .propose_view,
        .payload = fixed_poses[1].payload(),
    },
    .{
        .operator = .novel_view,
        .action = .render_view,
    },
    .{
        .operator = .fusion,
        .action = .fuse_view,
    },
    .{
        .operator = .geometry,
        .action = .refine_geometry,
    },

    .{
        .operator = .critic,
        .action = .evaluate,
    },
    .{
        .operator = .critic,
        .action = .propose_stop,
        .payload = fixed_stop_quality_floor,
    },
};

pub const expected_trace_events: u64 = @intCast(fixed_steps.len);
pub const expected_tool_invocations: u64 = 10;
pub const expected_control_actions: u64 = 3;
pub const expected_produced_artifacts: u64 = 12;
pub const expected_artifact_store_len: u64 = 13;
pub const expected_wall_time_ms: u64 = 184;
pub const expected_communication_units: u64 = 38;

pub const FixedResult = struct {
    runtime_state: runtime_mod.DemoRuntime,
    final_scene: artifacts.ArtifactId,
    evaluation: artifacts.ArtifactId,
    schedule_digest: artifacts.ArtifactId,
    run_config_digest: artifacts.ArtifactId,

    pub fn quality(self: *const FixedResult) u64 {
        const artifact =
            self.runtime_state.artifacts_store.get(self.evaluation).?;
        return artifact.value;
    }
};

pub fn scheduleDigest() artifacts.ArtifactId {
    var hasher = std.crypto.hash.Blake3.init(.{});
    hasher.update("EVO-D1-SCHEDULE");

    const header = [_]u8{
        fixed_schedule_version,
        @intCast(fixed_steps.len),
    };
    hasher.update(&header);

    for (fixed_steps) |step| {
        const step_header = [_]u8{
            @intFromEnum(step.operator),
            @intFromEnum(step.action),
        };
        hasher.update(&step_header);

        var payload_bytes: [8]u8 = undefined;
        encodeU64Le(step.payload, &payload_bytes);
        hasher.update(&payload_bytes);
    }

    var digest: artifacts.ArtifactId = undefined;
    hasher.final(&digest);
    return digest;
}

pub fn runConfigDigest(seed: u64) artifacts.ArtifactId {
    var hasher = std.crypto.hash.Blake3.init(.{});
    hasher.update("EVO-D1-RUN-CONFIG");

    const schedule_digest = scheduleDigest();
    hasher.update(&schedule_digest);
    hasher.update(fixed_input_payload);

    const versions = [_]u8{
        fixed_schedule_version,
        runtime_mod.trace_version,
        mock_tools.mock_tool_version,
    };
    hasher.update(&versions);

    var seed_bytes: [8]u8 = undefined;
    encodeU64Le(seed, &seed_bytes);
    hasher.update(&seed_bytes);

    var digest: artifacts.ArtifactId = undefined;
    hasher.final(&digest);
    return digest;
}

pub fn runFixed(seed: u64) !FixedResult {
    var runtime_state = runtime_mod.DemoRuntime.init(seed, .{
        .stop_quality_floor = fixed_stop_quality_floor,
    });

    const input = try runtime_state.addInput(fixed_input_payload);

    const depth = try submitOutput(&runtime_state, .{
        .operator = .spatial_prior,
        .action = .estimate_depth,
        .inputs = .{ input, artifacts.zero_id },
        .input_count = 1,
    });

    const camera = try submitOutput(&runtime_state, .{
        .operator = .spatial_prior,
        .action = .estimate_camera,
        .inputs = .{ input, artifacts.zero_id },
        .input_count = 1,
    });

    var scene = try submitOutput(&runtime_state, .{
        .operator = .geometry,
        .action = .build_geometry,
        .inputs = .{ depth, camera },
        .input_count = 2,
    });

    for (fixed_poses) |pose| {
        const view_request = try submitOutput(&runtime_state, .{
            .operator = .view_planner,
            .action = .propose_view,
            .inputs = .{ scene, artifacts.zero_id },
            .input_count = 1,
            .payload = pose.payload(),
        });

        const rendered = try submitOutput(&runtime_state, .{
            .operator = .novel_view,
            .action = .render_view,
            .inputs = .{ scene, view_request },
            .input_count = 2,
        });

        const fused = try submitOutput(&runtime_state, .{
            .operator = .fusion,
            .action = .fuse_view,
            .inputs = .{ scene, rendered },
            .input_count = 2,
        });

        scene = try submitOutput(&runtime_state, .{
            .operator = .geometry,
            .action = .refine_geometry,
            .inputs = .{ scene, fused },
            .input_count = 2,
        });
    }

    const evaluation = try submitOutput(&runtime_state, .{
        .operator = .critic,
        .action = .evaluate,
        .inputs = .{ scene, artifacts.zero_id },
        .input_count = 1,
    });

    const stop = try runtime_state.submit(.{
        .operator = .critic,
        .action = .propose_stop,
        .inputs = .{ evaluation, artifacts.zero_id },
        .input_count = 1,
        .payload = fixed_stop_quality_floor,
    });
    if (!stop.accepted) return error.FixedScheduleRejected;
    if (stop.has_output) return error.UnexpectedStopOutput;

    return .{
        .runtime_state = runtime_state,
        .final_scene = scene,
        .evaluation = evaluation,
        .schedule_digest = scheduleDigest(),
        .run_config_digest = runConfigDigest(seed),
    };
}

pub fn replayFixed(
    source: []const runtime_mod.TraceEvent,
    seed: u64,
) !runtime_mod.DemoRuntime {
    var runtime_state = runtime_mod.DemoRuntime.init(seed, .{
        .stop_quality_floor = fixed_stop_quality_floor,
    });
    _ = try runtime_state.addInput(fixed_input_payload);

    for (source) |event| {
        const decision = try runtime_state.submit(.{
            .operator = event.operator,
            .action = event.action,
            .inputs = event.inputs,
            .input_count = event.input_count,
            .payload = event.payload,
        });

        if (decision.accepted != event.accepted or
            decision.rejection != event.rejection or
            decision.has_output != event.has_output)
        {
            return error.ReplayMismatch;
        }

        if (decision.has_output and
            !artifacts.eqlId(decision.output, event.output))
        {
            return error.ReplayMismatch;
        }
    }

    return runtime_state;
}

pub fn traceMatchesFrozenSchedule(
    runtime_state: *const runtime_mod.DemoRuntime,
) bool {
    if (runtime_state.trace_len != fixed_steps.len) return false;

    for (fixed_steps, 0..) |step, i| {
        const event = runtime_state.trace[i];

        if (!event.accepted or
            event.rejection != .none or
            event.operator != step.operator or
            event.action != step.action or
            event.payload != step.payload)
        {
            return false;
        }
    }

    return true;
}

pub fn exactAccountingHolds(
    runtime_state: *const runtime_mod.DemoRuntime,
) bool {
    const accounting = runtime_state.accounting;

    return accounting.proposed_actions == expected_trace_events and
        accounting.accepted_actions == expected_trace_events and
        accounting.rejected_actions == 0 and
        accounting.tool_invocations == expected_tool_invocations and
        accounting.accepted_control_actions == expected_control_actions and
        accounting.produced_artifacts == expected_produced_artifacts and
        runtime_state.artifacts_store.len ==
            @as(usize, @intCast(expected_artifact_store_len)) and
        accounting.wall_time_ms == expected_wall_time_ms and
        accounting.communication_units == expected_communication_units and
        accounting.tool_invocations_by_kind[
            @intFromEnum(accounting_mod.ToolKind.depth)
        ] == 1 and
        accounting.tool_invocations_by_kind[
            @intFromEnum(accounting_mod.ToolKind.camera)
        ] == 1 and
        accounting.tool_invocations_by_kind[
            @intFromEnum(accounting_mod.ToolKind.geometry)
        ] == 3 and
        accounting.tool_invocations_by_kind[
            @intFromEnum(accounting_mod.ToolKind.view)
        ] == 2 and
        accounting.tool_invocations_by_kind[
            @intFromEnum(accounting_mod.ToolKind.fusion)
        ] == 2 and
        accounting.tool_invocations_by_kind[
            @intFromEnum(accounting_mod.ToolKind.evaluator)
        ] == 1;
}

fn submitOutput(
    runtime_state: *runtime_mod.DemoRuntime,
    proposal: messages.Proposal,
) !artifacts.ArtifactId {
    const decision = try runtime_state.submit(proposal);
    if (!decision.accepted) return error.FixedScheduleRejected;
    if (!decision.has_output) return error.FixedScheduleMissingOutput;
    return decision.output;
}

fn encodeU64Le(value: u64, out: *[8]u8) void {
    var i: usize = 0;
    while (i < 8) : (i += 1) {
        const shift: u6 = @intCast(i * 8);
        out[i] = @truncate(value >> shift);
    }
}

test "D1 fixed schedule is byte deterministic and replayable" {
    const first = try runFixed(17);
    const second = try runFixed(17);
    const replayed = try replayFixed(
        first.runtime_state.trace[0..first.runtime_state.trace_len],
        17,
    );

    var first_buffer: [32 * 1024]u8 = undefined;
    var second_buffer: [32 * 1024]u8 = undefined;
    var replay_buffer: [32 * 1024]u8 = undefined;

    const first_bytes =
        try first.runtime_state.canonicalTrace(&first_buffer);
    const second_bytes =
        try second.runtime_state.canonicalTrace(&second_buffer);
    const replay_bytes =
        try replayed.canonicalTrace(&replay_buffer);

    try std.testing.expectEqualSlices(u8, first_bytes, second_bytes);
    try std.testing.expectEqualSlices(u8, first_bytes, replay_bytes);
    try std.testing.expect(
        artifacts.eqlId(first.final_scene, second.final_scene),
    );
    try std.testing.expect(
        artifacts.eqlId(first.evaluation, second.evaluation),
    );
}

test "D1 frozen schedule and exact accounting are enforced" {
    const result = try runFixed(0);

    try std.testing.expect(result.runtime_state.invariantsHold());
    try std.testing.expect(result.runtime_state.terminated);
    try std.testing.expect(
        traceMatchesFrozenSchedule(&result.runtime_state),
    );
    try std.testing.expect(
        exactAccountingHolds(&result.runtime_state),
    );

    const final_scene =
        result.runtime_state.artifacts_store.get(result.final_scene).?;
    const evaluation =
        result.runtime_state.artifacts_store.get(result.evaluation).?;

    try std.testing.expectEqual(
        artifacts.ArtifactKind.scene_representation,
        final_scene.kind,
    );
    try std.testing.expectEqual(
        artifacts.ArtifactKind.evaluation_report,
        evaluation.kind,
    );
    try std.testing.expect(
        artifacts.eqlId(evaluation.parents[0], result.final_scene),
    );
    try std.testing.expect(result.quality() >= fixed_stop_quality_floor);
}

test "D1 schedule fingerprint is seed independent while execution is not" {
    const first = try runFixed(1);
    const second = try runFixed(2);

    try std.testing.expect(
        artifacts.eqlId(first.schedule_digest, second.schedule_digest),
    );
    try std.testing.expect(
        !artifacts.eqlId(
            first.run_config_digest,
            second.run_config_digest,
        ),
    );

    var first_buffer: [32 * 1024]u8 = undefined;
    var second_buffer: [32 * 1024]u8 = undefined;
    const first_bytes =
        try first.runtime_state.canonicalTrace(&first_buffer);
    const second_bytes =
        try second.runtime_state.canonicalTrace(&second_buffer);

    try std.testing.expect(!std.mem.eql(u8, first_bytes, second_bytes));
}
