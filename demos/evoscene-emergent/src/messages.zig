const std = @import("std");
const artifacts = @import("artifacts.zig");

pub const ActionKind = enum(u8) {
    estimate_depth,
    estimate_camera,
    build_geometry,
    refine_geometry,
    propose_view,
    render_view,
    fuse_view,
    evaluate,
    propose_stop,

    pub fn name(self: ActionKind) []const u8 {
        return switch (self) {
            .estimate_depth => "estimate_depth",
            .estimate_camera => "estimate_camera",
            .build_geometry => "build_geometry",
            .refine_geometry => "refine_geometry",
            .propose_view => "propose_view",
            .render_view => "render_view",
            .fuse_view => "fuse_view",
            .evaluate => "evaluate",
            .propose_stop => "propose_stop",
        };
    }
};

pub const MessageKind = enum(u8) {
    propose,
    accept,
    reject,
    evidence,
    observe,
    query,
    delegate,
    stop,
};

pub const RejectionReason = enum(u8) {
    none,
    runtime_terminated,
    operator_not_allowed,
    invalid_input_count,
    unknown_input,
    wrong_input_kind,
    budget_exhausted,
    stop_condition_not_met,
    tool_failure,
};

pub const Proposal = struct {
    operator: artifacts.Operator,
    action: ActionKind,
    inputs: [artifacts.max_parents]artifacts.ArtifactId =
        .{ artifacts.zero_id, artifacts.zero_id },
    input_count: u8 = 0,
    payload: u64 = 0,
};

pub const Decision = struct {
    accepted: bool,
    rejection: RejectionReason,
    output: artifacts.ArtifactId = artifacts.zero_id,
    has_output: bool = false,
};

pub fn operatorMayPropose(
    operator: artifacts.Operator,
    action: ActionKind,
) bool {
    return switch (operator) {
        .spatial_prior =>
            action == .estimate_depth or
            action == .estimate_camera,
        .geometry =>
            action == .build_geometry or
            action == .refine_geometry,
        .view_planner =>
            action == .propose_view,
        .novel_view =>
            action == .render_view,
        .fusion =>
            action == .fuse_view,
        .critic =>
            action == .evaluate or
            action == .propose_stop,
    };
}

pub fn requiredInputCount(action: ActionKind) u8 {
    return switch (action) {
        .estimate_depth,
        .estimate_camera,
        .propose_view,
        .evaluate,
        .propose_stop,
        => 1,

        .build_geometry,
        .refine_geometry,
        .render_view,
        .fuse_view,
        => 2,
    };
}

pub fn requiredInputKind(
    action: ActionKind,
    index: usize,
) ?artifacts.ArtifactKind {
    return switch (action) {
        .estimate_depth,
        .estimate_camera,
        => if (index == 0) .input_image else null,

        .build_geometry => switch (index) {
            0 => .depth_map,
            1 => .camera_estimate,
            else => null,
        },

        .refine_geometry => switch (index) {
            0 => .scene_representation,
            1 => .point_cloud,
            else => null,
        },

        .propose_view =>
            if (index == 0) .scene_representation else null,

        .render_view => switch (index) {
            0 => .scene_representation,
            1 => .view_request,
            else => null,
        },

        .fuse_view => switch (index) {
            0 => .scene_representation,
            1 => .rendered_view,
            else => null,
        },

        .evaluate =>
            if (index == 0) .scene_representation else null,

        .propose_stop =>
            if (index == 0) .evaluation_report else null,
    };
}

test "operator permissions stay role-local" {
    try std.testing.expect(
        operatorMayPropose(.spatial_prior, .estimate_depth),
    );
    try std.testing.expect(
        !operatorMayPropose(.spatial_prior, .build_geometry),
    );
    try std.testing.expect(
        operatorMayPropose(.critic, .propose_stop),
    );
}

test "action schemas require exact artifact kinds" {
    try std.testing.expectEqual(
        @as(u8, 2),
        requiredInputCount(.build_geometry),
    );
    try std.testing.expectEqual(
        artifacts.ArtifactKind.depth_map,
        requiredInputKind(.build_geometry, 0).?,
    );
    try std.testing.expectEqual(
        artifacts.ArtifactKind.camera_estimate,
        requiredInputKind(.build_geometry, 1).?,
    );
}
