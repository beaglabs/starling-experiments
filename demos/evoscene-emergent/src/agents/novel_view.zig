const artifacts = @import("../artifacts.zig");
const messages = @import("../messages.zig");
const observation = @import("../d3_observation.zig");

pub fn propose(
    obs: observation.LocalObservation,
    seed: u64,
) ?messages.Proposal {
    _ = seed;
    if (obs.terminated or obs.scene == null or obs.view_request == null) {
        return null;
    }
    if (obs.remaining_actions == 0 or obs.remaining_tools == 0) return null;

    const request = obs.view_request.?;
    if (obs.rendered_view) |rendered| {
        if (rendered.created_step > request.created_step) return null;
    }

    return .{
        .operator = .novel_view,
        .action = .render_view,
        .inputs = .{ obs.scene.?.id, request.id },
        .input_count = 2,
        .payload = request.value,
    };
}
