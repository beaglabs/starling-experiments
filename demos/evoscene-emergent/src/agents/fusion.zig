const artifacts = @import("../artifacts.zig");
const messages = @import("../messages.zig");
const observation = @import("../d3_observation.zig");

pub fn propose(
    obs: observation.LocalObservation,
    seed: u64,
) ?messages.Proposal {
    _ = seed;
    if (obs.terminated or obs.scene == null or obs.rendered_view == null) {
        return null;
    }
    if (obs.remaining_actions == 0 or obs.remaining_tools == 0) return null;

    const rendered = obs.rendered_view.?;
    if (obs.point_cloud) |cloud| {
        if (cloud.created_step > rendered.created_step) return null;
    }

    return .{
        .operator = .fusion,
        .action = .fuse_view,
        .inputs = .{ obs.scene.?.id, rendered.id },
        .input_count = 2,
        .payload = rendered.value,
    };
}
