const schema = @import("schema.zig");

pub const protocol_version: u8 = 1;

pub const Role = schema.Domain;

pub const Action = enum(u8) {
    inspect_geometry,
    inspect_terrain,
    inspect_water,
    inspect_illumination,
    inspect_atmosphere,
    inspect_vegetation,
    inspect_built,
    inspect_motion,
    inspect_temporal,
    inspect_material,
    run_shadowfinder,
    fuse_geolocation,
    assess_uncertainty,
    stop,

    pub fn name(self: Action) []const u8 {
        return @tagName(self);
    }
};

pub const action_count: usize = 14;

pub const Proposal = struct {
    role: Role,
    action: Action,
};

pub fn permitted(role: Role, action: Action) bool {
    return switch (role) {
        .geometry => action == .inspect_geometry,
        .terrain => action == .inspect_terrain,
        .water => action == .inspect_water,
        .illumination => action == .inspect_illumination,
        .atmospheric => action == .inspect_atmosphere,
        .vegetation => action == .inspect_vegetation,
        .built_environment => action == .inspect_built,
        .motion => action == .inspect_motion,
        .temporal => action == .inspect_temporal,
        .material_spectral => action == .inspect_material,
        .geolocation => action == .run_shadowfinder or action == .fuse_geolocation,
        .uncertainty => action == .assess_uncertainty or action == .stop,
    };
}

pub fn primaryInspection(role: Role) ?Action {
    return switch (role) {
        .geometry => .inspect_geometry,
        .terrain => .inspect_terrain,
        .water => .inspect_water,
        .illumination => .inspect_illumination,
        .atmospheric => .inspect_atmosphere,
        .vegetation => .inspect_vegetation,
        .built_environment => .inspect_built,
        .motion => .inspect_motion,
        .temporal => .inspect_temporal,
        .material_spectral => .inspect_material,
        .geolocation, .uncertainty => null,
    };
}
