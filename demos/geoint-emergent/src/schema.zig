const std = @import("std");

pub const schema_version: u8 = 1;

pub const Domain = enum(u8) {
    geometry,
    terrain,
    water,
    illumination,
    atmospheric,
    vegetation,
    built_environment,
    motion,
    temporal,
    material_spectral,
    geolocation,
    uncertainty,

    pub fn name(self: Domain) []const u8 {
        return switch (self) {
            .geometry => "geometry",
            .terrain => "terrain",
            .water => "water",
            .illumination => "illumination",
            .atmospheric => "atmospheric",
            .vegetation => "vegetation",
            .built_environment => "built_environment",
            .motion => "motion",
            .temporal => "temporal",
            .material_spectral => "material_spectral",
            .geolocation => "geolocation",
            .uncertainty => "uncertainty",
        };
    }
};

pub const Field = enum(u8) {
    horizon,
    vanishing_points,
    scale,
    camera_pose,
    object_height,
    slope,

    elevation,
    grade,
    ridgelines,
    depressions,
    drainage,

    shoreline,
    water_level,
    inundation,
    flow_direction,
    wave_state,

    solar_angle,
    shadow_object_height,
    shadow_length,
    time_of_day_consistency,

    haze,
    visibility,
    cloud_base,
    smoke_plume_direction,

    canopy_height,
    vegetation_health,
    seasonality,
    vegetation_disturbance,

    roads,
    roofs,
    towers,
    bridges,
    utilities,
    construction,

    vehicle_tracks,
    vessel_wakes,
    movement_vectors,
    changed_objects,

    new_objects,
    removed_objects,
    expanded_features,
    contracted_features,
    seasonal_change,

    asphalt,
    soil,
    vegetation_material,
    water_material,
    metal_like,

    candidate_region,
    landmark_match,
    terrain_match,

    confidence,
    ambiguity,
    competing_hypotheses,

    pub fn name(self: Field) []const u8 {
        return @tagName(self);
    }
};

pub const field_count: usize = 54;

pub const Status = enum(u8) {
    unknown,
    observed,
    estimated,
    derived,
    not_visible,
    unavailable,
    blocked,
    conflicting,

    pub fn resolved(self: Status) bool {
        return self != .unknown;
    }
};

pub const Source = enum(u8) {
    none,
    visual_operator,
    geometry_operator,
    terrain_operator,
    water_operator,
    illumination_operator,
    atmospheric_operator,
    vegetation_operator,
    built_operator,
    motion_operator,
    temporal_operator,
    material_operator,
    shadowfinder,
    geolocation_fusion,
    uncertainty_agent,
};

pub const Fact = struct {
    field: Field,
    status: Status = .unknown,
    confidence_permille: u16 = 0,
    value_milli: i64 = 0,
    source: Source = .none,
    created_step: u32 = 0,
};

pub const FactStore = struct {
    facts: [field_count]Fact = initFacts(),

    pub fn get(self: *const FactStore, field: Field) Fact {
        return self.facts[@intFromEnum(field)];
    }

    pub fn status(self: *const FactStore, field: Field) Status {
        return self.get(field).status;
    }

    pub fn set(
        self: *FactStore,
        field: Field,
        status_value: Status,
        confidence_permille: u16,
        value_milli: i64,
        source: Source,
        step: u32,
    ) void {
        std.debug.assert(confidence_permille <= 1000);
        self.facts[@intFromEnum(field)] = .{
            .field = field,
            .status = status_value,
            .confidence_permille = confidence_permille,
            .value_milli = value_milli,
            .source = source,
            .created_step = step,
        };
    }

    pub fn domainResolved(self: *const FactStore, domain_value: Domain) bool {
        var i: usize = 0;
        while (i < self.facts.len) : (i += 1) {
            const field: Field = @enumFromInt(i);
            if (domainOf(field) != domain_value) continue;
            if (!self.facts[i].status.resolved()) return false;
        }
        return true;
    }

    pub fn allResolved(self: *const FactStore) bool {
        for (self.facts) |fact| {
            if (!fact.status.resolved()) return false;
        }
        return true;
    }

    pub fn resolvedCount(self: *const FactStore) u8 {
        var count: u8 = 0;
        for (self.facts) |fact| {
            if (fact.status.resolved()) count +|= 1;
        }
        return count;
    }
};

pub fn domainOf(field: Field) Domain {
    return switch (field) {
        .horizon,
        .vanishing_points,
        .scale,
        .camera_pose,
        .object_height,
        .slope,
        => .geometry,

        .elevation,
        .grade,
        .ridgelines,
        .depressions,
        .drainage,
        => .terrain,

        .shoreline,
        .water_level,
        .inundation,
        .flow_direction,
        .wave_state,
        => .water,

        .solar_angle,
        .shadow_object_height,
        .shadow_length,
        .time_of_day_consistency,
        => .illumination,

        .haze,
        .visibility,
        .cloud_base,
        .smoke_plume_direction,
        => .atmospheric,

        .canopy_height,
        .vegetation_health,
        .seasonality,
        .vegetation_disturbance,
        => .vegetation,

        .roads,
        .roofs,
        .towers,
        .bridges,
        .utilities,
        .construction,
        => .built_environment,

        .vehicle_tracks,
        .vessel_wakes,
        .movement_vectors,
        .changed_objects,
        => .motion,

        .new_objects,
        .removed_objects,
        .expanded_features,
        .contracted_features,
        .seasonal_change,
        => .temporal,

        .asphalt,
        .soil,
        .vegetation_material,
        .water_material,
        .metal_like,
        => .material_spectral,

        .candidate_region,
        .landmark_match,
        .terrain_match,
        => .geolocation,

        .confidence,
        .ambiguity,
        .competing_hypotheses,
        => .uncertainty,
    };
}

fn initFacts() [field_count]Fact {
    var result: [field_count]Fact = undefined;
    var i: usize = 0;
    while (i < result.len) : (i += 1) {
        result[i] = .{
            .field = @enumFromInt(i),
        };
    }
    return result;
}

test "GEOINT field taxonomy covers every requested family" {
    var store = FactStore{};
    try std.testing.expectEqual(@as(usize, 54), store.facts.len);
    try std.testing.expectEqual(Domain.geometry, domainOf(.camera_pose));
    try std.testing.expectEqual(Domain.illumination, domainOf(.solar_angle));
    try std.testing.expectEqual(Domain.geolocation, domainOf(.candidate_region));
    try std.testing.expectEqual(Domain.uncertainty, domainOf(.ambiguity));
    try std.testing.expect(!store.allResolved());
}
