const artifacts = @import("../artifacts.zig");
const messages = @import("../messages.zig");
const observation = @import("../d3_observation.zig");

pub fn propose(
    obs: observation.LocalObservation,
    seed: u64,
) ?messages.Proposal {
    if (obs.terminated or obs.input == null) return null;
    if (obs.remaining_actions == 0 or obs.remaining_tools == 0) return null;

    const input = obs.input.?.id;
    const missing_depth = obs.depth == null;
    const missing_camera = obs.camera == null;

    if (!missing_depth and !missing_camera) return null;

    var action: messages.ActionKind = undefined;
    if (missing_depth and missing_camera) {
        action = if ((seed & 1) == 0)
            .estimate_depth
        else
            .estimate_camera;
    } else if (missing_depth) {
        action = .estimate_depth;
    } else {
        action = .estimate_camera;
    }

    return .{
        .operator = .spatial_prior,
        .action = action,
        .inputs = .{ input, artifacts.zero_id },
        .input_count = 1,
    };
}
