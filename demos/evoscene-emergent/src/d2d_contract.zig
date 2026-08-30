const std = @import("std");
const artifacts = @import("artifacts.zig");

pub const schema_version: u8 = 1;
pub const adapter_version: u8 = 1;

pub const canonical_pose_a_azimuth_mdeg: u32 = 35_000;
pub const canonical_pose_a_elevation_mdeg: u32 = 10_000;
pub const canonical_pose_b_azimuth_mdeg: u32 = 325_000;
pub const canonical_pose_b_elevation_mdeg: u32 = 10_000;

pub const depth_filename = "render_depth.f32le";
pub const mask_filename = "render_mask.u8";
pub const points_filename = "points.f32le";
pub const scene_filename = "scene.json";
pub const render_manifest_filename = "render.json";
pub const telemetry_filename = "telemetry.json";

pub const raster_rule = "nearest-z-source-index-tiebreak";
pub const pixel_convention = "normalized-pixel-centers";
pub const camera_convention = "opencv-x-right-y-down-z-forward";
pub const orbit_rule = "source-origin-orbit-about-aabb-center-look-at-center";
pub const evidence_rule = "zbuffer-depth-backproject-to-world";

pub fn posePayload(
    azimuth_mdeg: u32,
    elevation_mdeg: u32,
) u64 {
    return (@as(u64, azimuth_mdeg) << 32) |
        @as(u64, elevation_mdeg);
}

pub fn contractDigest() artifacts.ArtifactId {
    var hasher = std.crypto.hash.Blake3.init(.{});
    hasher.update("EVO-D2D-NOVEL-VIEW");
    hasher.update(&[_]u8{ schema_version, adapter_version });

    var value_bytes: [4]u8 = undefined;
    encodeU32Le(canonical_pose_a_azimuth_mdeg, &value_bytes);
    hasher.update(&value_bytes);
    encodeU32Le(canonical_pose_a_elevation_mdeg, &value_bytes);
    hasher.update(&value_bytes);
    encodeU32Le(canonical_pose_b_azimuth_mdeg, &value_bytes);
    hasher.update(&value_bytes);
    encodeU32Le(canonical_pose_b_elevation_mdeg, &value_bytes);
    hasher.update(&value_bytes);

    hashField(&hasher, depth_filename);
    hashField(&hasher, mask_filename);
    hashField(&hasher, points_filename);
    hashField(&hasher, scene_filename);
    hashField(&hasher, render_manifest_filename);
    hashField(&hasher, raster_rule);
    hashField(&hasher, pixel_convention);
    hashField(&hasher, camera_convention);
    hashField(&hasher, orbit_rule);
    hashField(&hasher, evidence_rule);

    var digest: artifacts.ArtifactId = undefined;
    hasher.final(&digest);
    return digest;
}

fn hashField(
    hasher: *std.crypto.hash.Blake3,
    field: []const u8,
) void {
    hasher.update(field);
    hasher.update(&[_]u8{0});
}

fn encodeU32Le(value: u32, out: *[4]u8) void {
    var i: usize = 0;
    while (i < 4) : (i += 1) {
        const shift: u5 = @intCast(i * 8);
        out[i] = @truncate(value >> shift);
    }
}

test "D2d fixed pose payload matches D1 encoding" {
    try std.testing.expectEqual(
        (@as(u64, 35_000) << 32) | 10_000,
        posePayload(
            canonical_pose_a_azimuth_mdeg,
            canonical_pose_a_elevation_mdeg,
        ),
    );
    try std.testing.expectEqual(
        (@as(u64, 325_000) << 32) | 10_000,
        posePayload(
            canonical_pose_b_azimuth_mdeg,
            canonical_pose_b_elevation_mdeg,
        ),
    );
}

test "D2d contract fingerprint is stable" {
    const first = contractDigest();
    const second = contractDigest();
    try std.testing.expect(artifacts.eqlId(first, second));
    try std.testing.expect(!artifacts.isZeroId(first));
}
