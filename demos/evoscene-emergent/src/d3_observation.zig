const std = @import("std");
const artifacts = @import("artifacts.zig");
const runtime_mod = @import("runtime.zig");

pub const LocalObservation = struct {
    operator: artifacts.Operator,

    input: ?artifacts.Artifact = null,
    depth: ?artifacts.Artifact = null,
    camera: ?artifacts.Artifact = null,
    scene: ?artifacts.Artifact = null,
    point_cloud: ?artifacts.Artifact = null,
    view_request: ?artifacts.Artifact = null,
    rendered_view: ?artifacts.Artifact = null,
    evaluation: ?artifacts.Artifact = null,

    view_request_count: u8 = 0,
    refinement_count: u8 = 0,

    remaining_actions: u64 = 0,
    remaining_tools: u64 = 0,
    remaining_wall_ms: u64 = 0,
    terminated: bool = false,
};

pub fn observe(
    rt: *const runtime_mod.DemoRuntime,
    operator: artifacts.Operator,
) LocalObservation {
    var result = LocalObservation{
        .operator = operator,
        .remaining_actions = remaining(
            rt.config.max_actions,
            rt.accounting.proposed_actions,
        ),
        .remaining_tools = remaining(
            rt.config.max_tool_invocations,
            rt.accounting.tool_invocations,
        ),
        .remaining_wall_ms = remaining(
            rt.config.max_wall_time_ms,
            rt.accounting.wall_time_ms,
        ),
        .terminated = rt.terminated,
    };

    switch (operator) {
        .spatial_prior => {
            result.input = latestKind(rt, .input_image);
            result.depth = latestKind(rt, .depth_map);
            result.camera = latestKind(rt, .camera_estimate);
        },
        .geometry => {
            result.depth = latestKind(rt, .depth_map);
            result.camera = latestKind(rt, .camera_estimate);
            result.scene = latestKind(rt, .scene_representation);
            result.point_cloud = latestKind(rt, .point_cloud);
            result.refinement_count = countRefinements(rt);
        },
        .view_planner => {
            result.scene = latestKind(rt, .scene_representation);
            result.point_cloud = latestKind(rt, .point_cloud);
            result.view_request = latestKind(rt, .view_request);
            result.rendered_view = latestKind(rt, .rendered_view);
            result.evaluation = latestKind(rt, .evaluation_report);
            result.view_request_count = countKind(rt, .view_request);
            result.refinement_count = countRefinements(rt);
        },
        .novel_view => {
            result.scene = latestKind(rt, .scene_representation);
            result.view_request = latestKind(rt, .view_request);
            result.rendered_view = latestKind(rt, .rendered_view);
        },
        .fusion => {
            result.scene = latestKind(rt, .scene_representation);
            result.rendered_view = latestKind(rt, .rendered_view);
            result.point_cloud = latestKind(rt, .point_cloud);
        },
        .critic => {
            result.scene = latestKind(rt, .scene_representation);
            result.evaluation = latestKind(rt, .evaluation_report);
            result.view_request_count = countKind(rt, .view_request);
            result.refinement_count = countRefinements(rt);
        },
    }

    return result;
}

pub fn isNewer(
    candidate: artifacts.Artifact,
    reference: ?artifacts.Artifact,
) bool {
    return reference == null or
        candidate.created_step > reference.?.created_step;
}

pub fn latestKind(
    rt: *const runtime_mod.DemoRuntime,
    kind: artifacts.ArtifactKind,
) ?artifacts.Artifact {
    var latest: ?artifacts.Artifact = null;
    var i: usize = 0;
    while (i < rt.artifacts_store.len) : (i += 1) {
        const item = rt.artifacts_store.items[i];
        if (item.kind != kind) continue;
        if (latest == null or
            item.created_step > latest.?.created_step)
        {
            latest = item;
        }
    }
    return latest;
}

pub fn countKind(
    rt: *const runtime_mod.DemoRuntime,
    kind: artifacts.ArtifactKind,
) u8 {
    var count: u8 = 0;
    var i: usize = 0;
    while (i < rt.artifacts_store.len) : (i += 1) {
        if (rt.artifacts_store.items[i].kind == kind) {
            count +|= 1;
        }
    }
    return count;
}

pub fn countRefinements(
    rt: *const runtime_mod.DemoRuntime,
) u8 {
    var count: u8 = 0;
    var i: usize = 0;
    while (i < rt.artifacts_store.len) : (i += 1) {
        const item = rt.artifacts_store.items[i];
        if (item.kind != .scene_representation) continue;
        if (item.producer != .geometry) continue;
        if (item.parent_count != 2) continue;

        const second = rt.artifacts_store.get(item.parents[1]) orelse
            continue;
        if (second.kind == .point_cloud) {
            count +|= 1;
        }
    }
    return count;
}

pub fn isRefinedScene(
    rt: *const runtime_mod.DemoRuntime,
    scene: artifacts.Artifact,
) bool {
    if (scene.kind != .scene_representation or
        scene.producer != .geometry or
        scene.parent_count != 2)
    {
        return false;
    }

    const second = rt.artifacts_store.get(scene.parents[1]) orelse
        return false;
    return second.kind == .point_cloud;
}

fn remaining(limit: u64, used: u64) u64 {
    return if (used >= limit) 0 else limit - used;
}

test "D3 observation freshness is step ordered" {
    const older = artifacts.Artifact{
        .id = artifacts.zero_id,
        .kind = .scene_representation,
        .producer = .geometry,
        .created_step = 3,
        .wall_time_ms = 0,
        .value = 0,
        .payload_hash = artifacts.zero_id,
    };
    const newer = artifacts.Artifact{
        .id = artifacts.zero_id,
        .kind = .point_cloud,
        .producer = .fusion,
        .created_step = 4,
        .wall_time_ms = 0,
        .value = 0,
        .payload_hash = artifacts.zero_id,
    };
    try std.testing.expect(isNewer(newer, older));
    try std.testing.expect(!isNewer(older, newer));
}
