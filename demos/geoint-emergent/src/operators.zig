const std = @import("std");
const schema = @import("schema.zig");
const protocol = @import("protocol.zig");
const context_mod = @import("context.zig");

pub fn invoke(
    facts: *schema.FactStore,
    context: context_mod.AcquisitionContext,
    step: u32,
    action: protocol.Action,
) !u8 {
    return switch (action) {
        .inspect_geometry => inspectGeometry(facts, step),
        .inspect_terrain => inspectTerrain(facts, step),
        .inspect_water => inspectWater(facts, step),
        .inspect_illumination => inspectIllumination(facts, context, step),
        .inspect_atmosphere => inspectAtmosphere(facts, step),
        .inspect_vegetation => inspectVegetation(facts, step),
        .inspect_built => inspectBuilt(facts, step),
        .inspect_motion => inspectMotion(facts, step),
        .inspect_temporal => inspectTemporal(facts, step),
        .inspect_material => inspectMaterial(facts, step),
        .run_shadowfinder => runShadowFinder(facts, context, step),
        .fuse_geolocation => fuseGeolocation(facts, step),
        .assess_uncertainty => assessUncertainty(facts, step),
        .stop => 0,
    };
}

fn inspectGeometry(facts: *schema.FactStore, step: u32) u8 {
    set(facts, .horizon, .not_visible, 900, 0, .geometry_operator, step);
    set(facts, .vanishing_points, .estimated, 620, 2, .geometry_operator, step);
    set(facts, .scale, .blocked, 950, 0, .geometry_operator, step);
    set(facts, .camera_pose, .estimated, 520, 0, .geometry_operator, step);
    set(facts, .object_height, .blocked, 950, 0, .geometry_operator, step);
    set(facts, .slope, .estimated, 680, 20, .geometry_operator, step);
    return 6;
}

fn inspectTerrain(facts: *schema.FactStore, step: u32) u8 {
    set(facts, .elevation, .blocked, 980, 0, .terrain_operator, step);
    set(facts, .grade, .estimated, 650, 20, .terrain_operator, step);
    set(facts, .ridgelines, .not_visible, 880, 0, .terrain_operator, step);
    set(facts, .depressions, .not_visible, 760, 0, .terrain_operator, step);
    set(facts, .drainage, .not_visible, 740, 0, .terrain_operator, step);
    return 5;
}

fn inspectWater(facts: *schema.FactStore, step: u32) u8 {
    set(facts, .shoreline, .not_visible, 980, 0, .water_operator, step);
    set(facts, .water_level, .unavailable, 980, 0, .water_operator, step);
    set(facts, .inundation, .not_visible, 930, 0, .water_operator, step);
    set(facts, .flow_direction, .unavailable, 980, 0, .water_operator, step);
    set(facts, .wave_state, .unavailable, 980, 0, .water_operator, step);
    return 5;
}

fn inspectIllumination(
    facts: *schema.FactStore,
    context: context_mod.AcquisitionContext,
    step: u32,
) u8 {
    if (context.has_shadow_ratio) {
        // The matched structural fixture uses 1800 / 1200 = 1.5, whose
        // solar altitude is approximately 56.310 degrees. The real adapter
        // computes this from supplied measurements at runtime.
        set(
            facts,
            .solar_angle,
            .derived,
            850,
            56_310,
            .illumination_operator,
            step,
        );
        set(
            facts,
            .shadow_object_height,
            .observed,
            900,
            @intCast(context.object_height_mm),
            .illumination_operator,
            step,
        );
        set(
            facts,
            .shadow_length,
            .observed,
            900,
            @intCast(context.shadow_length_mm),
            .illumination_operator,
            step,
        );
    } else {
        set(
            facts,
            .solar_angle,
            .blocked,
            970,
            0,
            .illumination_operator,
            step,
        );
        set(
            facts,
            .shadow_object_height,
            .blocked,
            970,
            0,
            .illumination_operator,
            step,
        );
        set(
            facts,
            .shadow_length,
            .blocked,
            970,
            0,
            .illumination_operator,
            step,
        );
    }

    set(
        facts,
        .time_of_day_consistency,
        if (context.has_datetime) .derived else .unavailable,
        if (context.has_datetime) 800 else 990,
        0,
        .illumination_operator,
        step,
    );
    return 4;
}

fn inspectAtmosphere(facts: *schema.FactStore, step: u32) u8 {
    set(facts, .haze, .estimated, 780, 100, .atmospheric_operator, step);
    set(facts, .visibility, .estimated, 760, 850, .atmospheric_operator, step);
    set(facts, .cloud_base, .unavailable, 940, 0, .atmospheric_operator, step);
    set(
        facts,
        .smoke_plume_direction,
        .not_visible,
        920,
        0,
        .atmospheric_operator,
        step,
    );
    return 4;
}

fn inspectVegetation(facts: *schema.FactStore, step: u32) u8 {
    set(
        facts,
        .canopy_height,
        .estimated,
        560,
        12_000,
        .vegetation_operator,
        step,
    );
    set(
        facts,
        .vegetation_health,
        .estimated,
        720,
        820,
        .vegetation_operator,
        step,
    );
    set(
        facts,
        .seasonality,
        .estimated,
        700,
        700,
        .vegetation_operator,
        step,
    );
    set(
        facts,
        .vegetation_disturbance,
        .not_visible,
        780,
        0,
        .vegetation_operator,
        step,
    );
    return 4;
}

fn inspectBuilt(facts: *schema.FactStore, step: u32) u8 {
    set(facts, .roads, .observed, 980, 1, .built_operator, step);
    set(facts, .roofs, .observed, 720, 1, .built_operator, step);
    set(facts, .towers, .not_visible, 900, 0, .built_operator, step);
    set(facts, .bridges, .not_visible, 900, 0, .built_operator, step);
    set(facts, .utilities, .not_visible, 700, 0, .built_operator, step);
    set(facts, .construction, .not_visible, 860, 0, .built_operator, step);
    return 6;
}

fn inspectMotion(facts: *schema.FactStore, step: u32) u8 {
    set(
        facts,
        .vehicle_tracks,
        .not_visible,
        760,
        0,
        .motion_operator,
        step,
    );
    set(
        facts,
        .vessel_wakes,
        .not_visible,
        990,
        0,
        .motion_operator,
        step,
    );
    set(
        facts,
        .movement_vectors,
        .unavailable,
        990,
        0,
        .motion_operator,
        step,
    );
    set(
        facts,
        .changed_objects,
        .unavailable,
        990,
        0,
        .motion_operator,
        step,
    );
    return 4;
}

fn inspectTemporal(facts: *schema.FactStore, step: u32) u8 {
    const fields = [_]schema.Field{
        .new_objects,
        .removed_objects,
        .expanded_features,
        .contracted_features,
        .seasonal_change,
    };
    for (fields) |field| {
        set(facts, field, .unavailable, 995, 0, .temporal_operator, step);
    }
    return 5;
}

fn inspectMaterial(facts: *schema.FactStore, step: u32) u8 {
    set(facts, .asphalt, .observed, 950, 1, .material_operator, step);
    set(facts, .soil, .estimated, 520, 1, .material_operator, step);
    set(
        facts,
        .vegetation_material,
        .observed,
        980,
        1,
        .material_operator,
        step,
    );
    set(
        facts,
        .water_material,
        .not_visible,
        990,
        0,
        .material_operator,
        step,
    );
    set(facts, .metal_like, .observed, 900, 1, .material_operator, step);
    return 5;
}

fn runShadowFinder(
    facts: *schema.FactStore,
    context: context_mod.AcquisitionContext,
    step: u32,
) u8 {
    std.debug.assert(context.has_datetime);
    std.debug.assert(context.has_shadow_ratio);

    // The deterministic Zig gate models the typed result boundary. The live
    // Python adapter executes Bellingcat ShadowFinder itself.
    set(
        facts,
        .candidate_region,
        .derived,
        820,
        1,
        .shadowfinder,
        step,
    );
    return 1;
}

fn fuseGeolocation(facts: *schema.FactStore, step: u32) u8 {
    var written: u8 = 0;
    if (facts.status(.candidate_region) == .unknown) {
        set(
            facts,
            .candidate_region,
            .blocked,
            990,
            0,
            .geolocation_fusion,
            step,
        );
        written += 1;
    }

    set(
        facts,
        .landmark_match,
        .blocked,
        920,
        0,
        .geolocation_fusion,
        step,
    );
    set(
        facts,
        .terrain_match,
        .blocked,
        920,
        0,
        .geolocation_fusion,
        step,
    );
    written += 2;
    return written;
}

fn assessUncertainty(facts: *schema.FactStore, step: u32) u8 {
    const has_candidate = facts.status(.candidate_region) == .derived;
    set(
        facts,
        .confidence,
        .derived,
        1000,
        if (has_candidate) 680 else 430,
        .uncertainty_agent,
        step,
    );
    set(
        facts,
        .ambiguity,
        .derived,
        1000,
        if (has_candidate) 520 else 880,
        .uncertainty_agent,
        step,
    );
    set(
        facts,
        .competing_hypotheses,
        .derived,
        1000,
        if (has_candidate) 3 else 8,
        .uncertainty_agent,
        step,
    );
    return 3;
}

fn set(
    facts: *schema.FactStore,
    field: schema.Field,
    status: schema.Status,
    confidence_permille: u16,
    value_milli: i64,
    source: schema.Source,
    step: u32,
) void {
    facts.set(
        field,
        status,
        confidence_permille,
        value_milli,
        source,
        step,
    );
}

test "illumination operator refuses to fabricate missing shadow ratio" {
    var facts = schema.FactStore{};
    _ = inspectIllumination(
        &facts,
        context_mod.AcquisitionContext.photoNoDatetime(),
        1,
    );
    try std.testing.expectEqual(schema.Status.blocked, facts.status(.solar_angle));
    try std.testing.expectEqual(
        schema.Status.unavailable,
        facts.status(.time_of_day_consistency),
    );
}

test "shadow-ready fixture derives solar altitude" {
    var facts = schema.FactStore{};
    _ = inspectIllumination(
        &facts,
        context_mod.AcquisitionContext.photoShadowReady(),
        1,
    );
    try std.testing.expectEqual(schema.Status.derived, facts.status(.solar_angle));
    const angle = facts.get(.solar_angle).value_milli;
    try std.testing.expect(angle > 56_000);
    try std.testing.expect(angle < 57_000);
}
