const artifacts = @import("../artifacts.zig");
const messages = @import("../messages.zig");
const observation = @import("../d3_observation.zig");

pub fn propose(
    obs: observation.LocalObservation,
    seed: u64,
) ?messages.Proposal {
    _ = seed;
    if (obs.terminated) return null;
    if (obs.remaining_actions == 0 or obs.remaining_tools == 0) return null;

    if (obs.scene == null) {
        if (obs.depth == null or obs.camera == null) return null;
        return .{
            .operator = .geometry,
            .action = .build_geometry,
            .inputs = .{ obs.depth.?.id, obs.camera.?.id },
            .input_count = 2,
        };
    }

    if (obs.point_cloud) |cloud| {
        if (observation.isNewer(cloud, obs.scene)) {
            return .{
                .operator = .geometry,
                .action = .refine_geometry,
                .inputs = .{ obs.scene.?.id, cloud.id },
                .input_count = 2,
            };
        }
    }

    return null;
}
