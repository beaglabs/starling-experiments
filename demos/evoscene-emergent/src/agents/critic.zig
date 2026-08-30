const artifacts = @import("../artifacts.zig");
const messages = @import("../messages.zig");
const observation = @import("../d3_observation.zig");
const view_planner = @import("view_planner.zig");
const runtime_mod = @import("../runtime.zig");

pub fn propose(
    rt: *const runtime_mod.DemoRuntime,
    obs: observation.LocalObservation,
    seed: u64,
) ?messages.Proposal {
    _ = seed;
    if (obs.terminated or obs.scene == null) return null;
    if (obs.remaining_actions == 0) return null;

    const scene = obs.scene.?;
    if (!observation.isRefinedScene(rt, scene)) return null;

    if (obs.evaluation == null or
        obs.evaluation.?.created_step < scene.created_step)
    {
        if (obs.remaining_tools == 0) return null;
        return .{
            .operator = .critic,
            .action = .evaluate,
            .inputs = .{ scene.id, artifacts.zero_id },
            .input_count = 1,
        };
    }

    const evaluation = obs.evaluation.?;
    if (evaluation.value >= view_planner.stop_quality_target) {
        return .{
            .operator = .critic,
            .action = .propose_stop,
            .inputs = .{ evaluation.id, artifacts.zero_id },
            .input_count = 1,
            .payload = view_planner.stop_quality_target,
        };
    }

    if (obs.view_request_count >= view_planner.max_views) {
        return .{
            .operator = .critic,
            .action = .propose_stop,
            .inputs = .{ evaluation.id, artifacts.zero_id },
            .input_count = 1,
            .payload = runtime_mod.default_stop_quality_floor,
        };
    }

    return null;
}
