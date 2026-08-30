const protocol = @import("protocol.zig");
const observation = @import("observation.zig");

pub fn propose(obs: observation.Observation) ?protocol.Proposal {
    if (protocol.primaryInspection(obs.role)) |action| {
        if (obs.own_action_done) return null;
        return .{ .role = obs.role, .action = action };
    }

    return switch (obs.role) {
        .geolocation => proposeGeolocation(obs),
        .uncertainty => proposeUncertainty(obs),
        else => null,
    };
}

fn proposeGeolocation(
    obs: observation.Observation,
) ?protocol.Proposal {
    if (!obs.primary_complete) return null;

    if (obs.shadowfinder_ready and !obs.shadowfinder_done) {
        return .{
            .role = .geolocation,
            .action = .run_shadowfinder,
        };
    }

    if (!obs.geolocation_done) {
        return .{
            .role = .geolocation,
            .action = .fuse_geolocation,
        };
    }

    return null;
}

fn proposeUncertainty(
    obs: observation.Observation,
) ?protocol.Proposal {
    if (!obs.geolocation_done) return null;

    if (!obs.uncertainty_done) {
        return .{
            .role = .uncertainty,
            .action = .assess_uncertainty,
        };
    }

    if (!obs.stop_done) {
        return .{
            .role = .uncertainty,
            .action = .stop,
        };
    }

    return null;
}
