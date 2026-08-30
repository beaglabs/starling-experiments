const std = @import("std");
const artifacts = @import("artifacts.zig");
const messages = @import("messages.zig");
const accounting_mod = @import("accounting.zig");
const mock_tools = @import("mock_tools.zig");

pub const trace_version: u8 = 1;
pub const no_tool_code: u8 = 0xff;
pub const default_stop_quality_floor: u64 = 700;
pub const fixture_input_payload = "d0-synthetic-scene-input-v1";

pub const Config = struct {
    max_actions: u64 = 64,
    max_tool_invocations: u64 = 32,
    max_wall_time_ms: u64 = 500,
    stop_quality_floor: u64 = default_stop_quality_floor,
};

pub const TraceEvent = struct {
    step: u32,
    operator: artifacts.Operator,
    action: messages.ActionKind,
    inputs: [artifacts.max_parents]artifacts.ArtifactId,
    input_count: u8,
    payload: u64,

    accepted: bool,
    rejection: messages.RejectionReason,

    output: artifacts.ArtifactId = artifacts.zero_id,
    has_output: bool = false,

    tool_code: u8 = no_tool_code,
    wall_time_ms: u64 = 0,

    cumulative_wall_time_ms: u64,
    cumulative_communication_units: u64,
};

pub fn Runtime(
    comptime artifact_capacity: usize,
    comptime trace_capacity: usize,
) type {
    return struct {
        const Self = @This();

        seed: u64,
        config: Config,
        artifacts_store: artifacts.ArtifactStore(artifact_capacity) = .{},
        accounting: accounting_mod.Accounting = .{},

        trace: [trace_capacity]TraceEvent = undefined,
        trace_len: usize = 0,

        step: u32 = 0,
        terminated: bool = false,

        pub fn init(seed: u64, config: Config) Self {
            return .{
                .seed = seed,
                .config = config,
            };
        }

        pub fn addInput(
            self: *Self,
            payload: []const u8,
        ) !artifacts.ArtifactId {
            return self.artifacts_store.addRoot(.input_image, payload);
        }

        pub fn submit(
            self: *Self,
            proposal: messages.Proposal,
        ) !messages.Decision {
            self.step +%= 1;
            self.accounting.recordProposal();

            if (self.terminated) {
                return self.reject(proposal, .runtime_terminated);
            }

            if (self.accounting.proposed_actions > self.config.max_actions) {
                return self.reject(proposal, .budget_exhausted);
            }

            if (!messages.operatorMayPropose(
                proposal.operator,
                proposal.action,
            )) {
                return self.reject(proposal, .operator_not_allowed);
            }

            if (proposal.input_count !=
                messages.requiredInputCount(proposal.action))
            {
                return self.reject(proposal, .invalid_input_count);
            }

            var input_artifacts:
                [artifacts.max_parents]artifacts.Artifact = undefined;

            var i: usize = 0;
            while (i < proposal.input_count) : (i += 1) {
                const input =
                    self.artifacts_store.get(proposal.inputs[i]) orelse
                    return self.reject(proposal, .unknown_input);

                const expected =
                    messages.requiredInputKind(proposal.action, i) orelse
                    return self.reject(proposal, .wrong_input_kind);

                if (input.kind != expected) {
                    return self.reject(proposal, .wrong_input_kind);
                }
                input_artifacts[i] = input.*;
            }

            return switch (proposal.action) {
                .propose_view =>
                    self.acceptViewProposal(proposal, input_artifacts[0]),
                .propose_stop =>
                    self.acceptStopProposal(proposal, input_artifacts[0]),
                else =>
                    self.acceptToolProposal(proposal, input_artifacts),
            };
        }

        fn acceptViewProposal(
            self: *Self,
            proposal: messages.Proposal,
            scene: artifacts.Artifact,
        ) !messages.Decision {
            const payload_hash = mock_tools.viewRequestHash(
                scene,
                self.seed,
                proposal.payload,
            );

            const output = try self.artifacts_store.insertProduced(
                .view_request,
                proposal.operator,
                proposal.inputs,
                proposal.input_count,
                self.step,
                0,
                proposal.payload,
                payload_hash,
            );

            self.accounting.recordControlAction();
            self.accounting.recordAcceptedDecision(true);

            const decision = messages.Decision{
                .accepted = true,
                .rejection = .none,
                .output = output,
                .has_output = true,
            };
            try self.appendTrace(
                proposal,
                decision,
                no_tool_code,
                0,
            );
            return decision;
        }

        fn acceptStopProposal(
            self: *Self,
            proposal: messages.Proposal,
            evaluation: artifacts.Artifact,
        ) !messages.Decision {
            const required_quality = @max(
                self.config.stop_quality_floor,
                proposal.payload,
            );
            if (evaluation.value < required_quality) {
                return self.reject(
                    proposal,
                    .stop_condition_not_met,
                );
            }

            self.terminated = true;
            self.accounting.recordControlAction();
            self.accounting.recordAcceptedDecision(false);

            const decision = messages.Decision{
                .accepted = true,
                .rejection = .none,
            };
            try self.appendTrace(
                proposal,
                decision,
                no_tool_code,
                0,
            );
            return decision;
        }

        fn acceptToolProposal(
            self: *Self,
            proposal: messages.Proposal,
            input_artifacts:
                [artifacts.max_parents]artifacts.Artifact,
        ) !messages.Decision {
            const output = mock_tools.invoke(
                proposal.action,
                input_artifacts,
                proposal.input_count,
                self.seed,
                proposal.payload,
            ) catch {
                return self.reject(proposal, .tool_failure);
            };

            if (self.accounting.tool_invocations + 1 >
                self.config.max_tool_invocations or
                self.accounting.wall_time_ms +
                    output.wall_time_ms >
                    self.config.max_wall_time_ms)
            {
                return self.reject(proposal, .budget_exhausted);
            }

            const output_id = try self.artifacts_store.insertProduced(
                output.kind,
                proposal.operator,
                proposal.inputs,
                proposal.input_count,
                self.step,
                output.wall_time_ms,
                output.value,
                output.payload_hash,
            );

            self.accounting.recordTool(
                output.tool,
                output.wall_time_ms,
            );
            self.accounting.recordAcceptedDecision(true);

            const decision = messages.Decision{
                .accepted = true,
                .rejection = .none,
                .output = output_id,
                .has_output = true,
            };

            try self.appendTrace(
                proposal,
                decision,
                @intFromEnum(output.tool),
                output.wall_time_ms,
            );
            return decision;
        }

        fn reject(
            self: *Self,
            proposal: messages.Proposal,
            reason: messages.RejectionReason,
        ) !messages.Decision {
            self.accounting.recordRejectedDecision();
            const decision = messages.Decision{
                .accepted = false,
                .rejection = reason,
            };
            try self.appendTrace(
                proposal,
                decision,
                no_tool_code,
                0,
            );
            return decision;
        }

        fn appendTrace(
            self: *Self,
            proposal: messages.Proposal,
            decision: messages.Decision,
            tool_code: u8,
            wall_time_ms: u64,
        ) !void {
            if (self.trace_len >= trace_capacity) {
                return error.TraceCapacityExceeded;
            }

            self.trace[self.trace_len] = .{
                .step = self.step,
                .operator = proposal.operator,
                .action = proposal.action,
                .inputs = proposal.inputs,
                .input_count = proposal.input_count,
                .payload = proposal.payload,
                .accepted = decision.accepted,
                .rejection = decision.rejection,
                .output = decision.output,
                .has_output = decision.has_output,
                .tool_code = tool_code,
                .wall_time_ms = wall_time_ms,
                .cumulative_wall_time_ms =
                    self.accounting.wall_time_ms,
                .cumulative_communication_units =
                    self.accounting.communication_units,
            };
            self.trace_len += 1;
        }

        pub fn invariantsHold(self: *const Self) bool {
            return self.accounting.actionAccountingValid() and
                self.accounting.communicationAccountingValid() and
                self.artifacts_store.allHaveValidProvenance() and
                self.trace_len ==
                    @as(usize, @intCast(
                        self.accounting.proposed_actions,
                    ));
        }

        pub fn canonicalTrace(
            self: *const Self,
            out: []u8,
        ) ![]const u8 {
            return encodeTrace(self.trace[0..self.trace_len], out);
        }
    };
}

pub const DemoRuntime = Runtime(64, 64);

pub fn runFixture(seed: u64) !DemoRuntime {
    var runtime = DemoRuntime.init(seed, .{});
    const input = try runtime.addInput(fixture_input_payload);

    // Intentional invalid proposal: role/action mismatch.
    const invalid = try runtime.submit(.{
        .operator = .geometry,
        .action = .estimate_depth,
        .inputs = .{ input, artifacts.zero_id },
        .input_count = 1,
    });
    std.debug.assert(!invalid.accepted);

    const depth = (try runtime.submit(.{
        .operator = .spatial_prior,
        .action = .estimate_depth,
        .inputs = .{ input, artifacts.zero_id },
        .input_count = 1,
    })).output;

    const camera = (try runtime.submit(.{
        .operator = .spatial_prior,
        .action = .estimate_camera,
        .inputs = .{ input, artifacts.zero_id },
        .input_count = 1,
    })).output;

    const scene = (try runtime.submit(.{
        .operator = .geometry,
        .action = .build_geometry,
        .inputs = .{ depth, camera },
        .input_count = 2,
    })).output;

    const view_request = (try runtime.submit(.{
        .operator = .view_planner,
        .action = .propose_view,
        .inputs = .{ scene, artifacts.zero_id },
        .input_count = 1,
        .payload = 35_000,
    })).output;

    const rendered = (try runtime.submit(.{
        .operator = .novel_view,
        .action = .render_view,
        .inputs = .{ scene, view_request },
        .input_count = 2,
    })).output;

    const fused = (try runtime.submit(.{
        .operator = .fusion,
        .action = .fuse_view,
        .inputs = .{ scene, rendered },
        .input_count = 2,
    })).output;

    const refined = (try runtime.submit(.{
        .operator = .geometry,
        .action = .refine_geometry,
        .inputs = .{ scene, fused },
        .input_count = 2,
    })).output;

    const evaluation = (try runtime.submit(.{
        .operator = .critic,
        .action = .evaluate,
        .inputs = .{ refined, artifacts.zero_id },
        .input_count = 1,
    })).output;

    const stop = try runtime.submit(.{
        .operator = .critic,
        .action = .propose_stop,
        .inputs = .{ evaluation, artifacts.zero_id },
        .input_count = 1,
        .payload = default_stop_quality_floor,
    });
    std.debug.assert(stop.accepted);

    return runtime;
}

pub fn replayFixture(
    source: []const TraceEvent,
    seed: u64,
) !DemoRuntime {
    var runtime = DemoRuntime.init(seed, .{});
    _ = try runtime.addInput(fixture_input_payload);

    for (source) |event| {
        const decision = try runtime.submit(.{
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

    return runtime;
}

pub fn encodeTrace(
    events: []const TraceEvent,
    out: []u8,
) ![]const u8 {
    var offset: usize = 0;

    try appendBytes(out, &offset, "EVO-D0");
    try appendU8(out, &offset, trace_version);
    try appendU32(out, &offset, @intCast(events.len));

    for (events) |event| {
        try appendU32(out, &offset, event.step);
        try appendU8(out, &offset, @intFromEnum(event.operator));
        try appendU8(out, &offset, @intFromEnum(event.action));
        try appendU8(out, &offset, event.input_count);

        var i: usize = 0;
        while (i < artifacts.max_parents) : (i += 1) {
            try appendBytes(out, &offset, &event.inputs[i]);
        }

        try appendU64(out, &offset, event.payload);
        try appendU8(
            out,
            &offset,
            if (event.accepted) 1 else 0,
        );
        try appendU8(
            out,
            &offset,
            @intFromEnum(event.rejection),
        );
        try appendU8(
            out,
            &offset,
            if (event.has_output) 1 else 0,
        );
        try appendBytes(out, &offset, &event.output);
        try appendU8(out, &offset, event.tool_code);
        try appendU64(out, &offset, event.wall_time_ms);
        try appendU64(
            out,
            &offset,
            event.cumulative_wall_time_ms,
        );
        try appendU64(
            out,
            &offset,
            event.cumulative_communication_units,
        );
    }

    return out[0..offset];
}

pub fn traceDigest(
    events: []const TraceEvent,
) artifacts.ArtifactId {
    var buffer: [32 * 1024]u8 = undefined;
    const bytes = encodeTrace(events, &buffer) catch unreachable;
    return artifacts.hashPayload(bytes);
}

fn appendBytes(
    out: []u8,
    offset: *usize,
    bytes: []const u8,
) !void {
    if (offset.* + bytes.len > out.len) {
        return error.TraceBufferTooSmall;
    }
    std.mem.copyForwards(
        u8,
        out[offset.* .. offset.* + bytes.len],
        bytes,
    );
    offset.* += bytes.len;
}

fn appendU8(
    out: []u8,
    offset: *usize,
    value: u8,
) !void {
    if (offset.* >= out.len) return error.TraceBufferTooSmall;
    out[offset.*] = value;
    offset.* += 1;
}

fn appendU32(
    out: []u8,
    offset: *usize,
    value: u32,
) !void {
    var bytes: [4]u8 = undefined;
    var i: usize = 0;
    while (i < bytes.len) : (i += 1) {
        const shift: u5 = @intCast(i * 8);
        bytes[i] = @truncate(value >> shift);
    }
    try appendBytes(out, offset, &bytes);
}

fn appendU64(
    out: []u8,
    offset: *usize,
    value: u64,
) !void {
    var bytes: [8]u8 = undefined;
    var i: usize = 0;
    while (i < bytes.len) : (i += 1) {
        const shift: u6 = @intCast(i * 8);
        bytes[i] = @truncate(value >> shift);
    }
    try appendBytes(out, offset, &bytes);
}

test "D0 fixture is exactly reproducible and seed-sensitive" {
    const first = try runFixture(7);
    const second = try runFixture(7);
    const different = try runFixture(8);

    var first_buffer: [32 * 1024]u8 = undefined;
    var second_buffer: [32 * 1024]u8 = undefined;
    var different_buffer: [32 * 1024]u8 = undefined;

    const first_bytes = try first.canonicalTrace(&first_buffer);
    const second_bytes = try second.canonicalTrace(&second_buffer);
    const different_bytes =
        try different.canonicalTrace(&different_buffer);

    try std.testing.expectEqualSlices(
        u8,
        first_bytes,
        second_bytes,
    );
    try std.testing.expect(
        !std.mem.eql(u8, first_bytes, different_bytes),
    );
}

test "D0 replay reproduces the canonical trace byte-for-byte" {
    const original = try runFixture(11);
    const replayed = try replayFixture(
        original.trace[0..original.trace_len],
        11,
    );

    var original_buffer: [32 * 1024]u8 = undefined;
    var replay_buffer: [32 * 1024]u8 = undefined;

    const original_bytes =
        try original.canonicalTrace(&original_buffer);
    const replay_bytes =
        try replayed.canonicalTrace(&replay_buffer);

    try std.testing.expectEqualSlices(
        u8,
        original_bytes,
        replay_bytes,
    );
}

test "D0 accepted actions, communication, provenance, and rejection are exact" {
    const runtime = try runFixture(0);

    try std.testing.expect(runtime.invariantsHold());
    try std.testing.expect(runtime.terminated);
    try std.testing.expectEqual(
        @as(u64, 10),
        runtime.accounting.proposed_actions,
    );
    try std.testing.expectEqual(
        @as(u64, 9),
        runtime.accounting.accepted_actions,
    );
    try std.testing.expectEqual(
        @as(u64, 1),
        runtime.accounting.rejected_actions,
    );
    try std.testing.expectEqual(
        @as(u64, 7),
        runtime.accounting.tool_invocations,
    );
    try std.testing.expectEqual(
        @as(u64, 2),
        runtime.accounting.accepted_control_actions,
    );
    try std.testing.expectEqual(
        @as(u64, 8),
        runtime.accounting.produced_artifacts,
    );
    try std.testing.expectEqual(
        @as(u64, 119),
        runtime.accounting.wall_time_ms,
    );
    try std.testing.expectEqual(
        @as(u64, 28),
        runtime.accounting.communication_units,
    );
    try std.testing.expectEqual(
        messages.RejectionReason.operator_not_allowed,
        runtime.trace[0].rejection,
    );
    try std.testing.expect(
        runtime.artifacts_store.allHaveValidProvenance(),
    );
}

test "D0 critic cannot terminate below deterministic quality floor" {
    var runtime = DemoRuntime.init(0, .{
        .stop_quality_floor = 1_001,
    });
    const input = try runtime.addInput(fixture_input_payload);

    const depth = (try runtime.submit(.{
        .operator = .spatial_prior,
        .action = .estimate_depth,
        .inputs = .{ input, artifacts.zero_id },
        .input_count = 1,
    })).output;
    const camera = (try runtime.submit(.{
        .operator = .spatial_prior,
        .action = .estimate_camera,
        .inputs = .{ input, artifacts.zero_id },
        .input_count = 1,
    })).output;
    const scene = (try runtime.submit(.{
        .operator = .geometry,
        .action = .build_geometry,
        .inputs = .{ depth, camera },
        .input_count = 2,
    })).output;
    const evaluation = (try runtime.submit(.{
        .operator = .critic,
        .action = .evaluate,
        .inputs = .{ scene, artifacts.zero_id },
        .input_count = 1,
    })).output;

    const stop = try runtime.submit(.{
        .operator = .critic,
        .action = .propose_stop,
        .inputs = .{ evaluation, artifacts.zero_id },
        .input_count = 1,
        .payload = 0,
    });

    try std.testing.expect(!stop.accepted);
    try std.testing.expectEqual(
        messages.RejectionReason.stop_condition_not_met,
        stop.rejection,
    );
    try std.testing.expect(!runtime.terminated);
}
