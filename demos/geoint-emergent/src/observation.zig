const protocol = @import("protocol.zig");
const runtime_mod = @import("runtime.zig");

pub const Observation = struct {
    role: protocol.Role,
    own_action_done: bool = false,

    primary_complete: bool = false,
    shadowfinder_ready: bool = false,
    shadowfinder_done: bool = false,
    geolocation_done: bool = false,

    uncertainty_done: bool = false,
    stop_done: bool = false,
};

pub fn observe(
    rt: *const runtime_mod.Runtime,
    role: protocol.Role,
) Observation {
    var result = Observation{ .role = role };

    if (protocol.primaryInspection(role)) |action| {
        result.own_action_done = rt.actionDone(action);
        return result;
    }

    switch (role) {
        .geolocation => {
            result.primary_complete = rt.allPrimaryInspectionsDone();
            result.shadowfinder_ready = rt.shadowfinderReady();
            result.shadowfinder_done = rt.actionDone(.run_shadowfinder);
            result.geolocation_done = rt.actionDone(.fuse_geolocation);
        },
        .uncertainty => {
            result.geolocation_done = rt.actionDone(.fuse_geolocation);
            result.uncertainty_done = rt.actionDone(.assess_uncertainty);
            result.stop_done = rt.actionDone(.stop);
        },
        else => unreachable,
    }

    return result;
}
