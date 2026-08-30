const artifacts = @import("../artifacts.zig");
const messages = @import("../messages.zig");
const observation = @import("../d3_observation.zig");

pub const max_views: u8 = 4;
pub const stop_quality_target: u64 = 900;

const poses = [_]u64{
    (@as(u64, 35_000) << 32) | 10_000,
    (@as(u64, 325_000) << 32) | 10_000,
    (@as(u64, 55_000) << 32) | 15_000,
    (@as(u64, 305_000) << 32) | 15_000,
};

pub fn propose(
    obs: observation.LocalObservation,
    seed: u64,
) ?messages.Proposal {
    if (obs.terminated or obs.scene == null) return null;
    if (obs.remaining_actions == 0) return null;
    if (obs.view_request_count >= max_views) return null;

    // Never issue another request while the last one has not been rendered.
    if (obs.view_request) |request| {
        if (obs.rendered_view == null or
            request.created_step > obs.rendered_view.?.created_step)
        {
            return null;
        }
    }

    // A rendered observation must be fused before another view can be
    // requested. This is local causal backpressure, not a global phase rule.
    if (obs.rendered_view) |rendered| {
        if (obs.point_cloud == null or
            rendered.created_step > obs.point_cloud.?.created_step)
        {
            return null;
        }
    }

    // Fusion evidence exists but Geometry has not consumed it yet.
    if (obs.point_cloud) |cloud| {
        if (observation.isNewer(cloud, obs.scene)) return null;
    }

    const scene = obs.scene.?;

    // A newly refined scene must be evaluated before another view is chosen.
    if (obs.refinement_count > 0) {
        if (obs.evaluation == null or
            obs.evaluation.?.created_step < scene.created_step)
        {
            return null;
        }

        // Give Critic exclusive opportunity to STOP at target quality.
        if (obs.evaluation.?.value >= stop_quality_target) {
            return null;
        }
    }

    const index = poseIndex(scene, seed, obs.view_request_count);
    return .{
        .operator = .view_planner,
        .action = .propose_view,
        .inputs = .{ scene.id, artifacts.zero_id },
        .input_count = 1,
        .payload = poses[index],
    };
}

fn poseIndex(
    scene: artifacts.Artifact,
    seed: u64,
    count: u8,
) usize {
    var mixed = seed ^ @as(u64, count);
    var i: usize = 0;
    while (i < 8) : (i += 1) {
        const shift: u6 = @intCast(i * 8);
        mixed ^= @as(u64, scene.id[i]) << shift;
    }
    return @intCast(mixed % poses.len);
}
