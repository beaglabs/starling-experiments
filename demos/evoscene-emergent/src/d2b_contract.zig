const std = @import("std");
const artifacts = @import("artifacts.zig");

pub const schema_version: u8 = 1;
pub const adapter_version: u8 = 1;

pub const input_manifest_filename = "prior.json";
pub const input_depth_filename = "depth.f32le";
pub const input_mask_filename = "mask.u8";
pub const input_camera_filename = "camera.json";

pub const points_filename = "points.f32le";
pub const ply_filename = "scene.ply";
pub const scene_manifest_filename = "scene.json";
pub const telemetry_filename = "telemetry.json";

pub const points_encoding = "row-major-valid-pixels-xyz-f32le-meters";
pub const pixel_convention = "normalized-pixel-centers";
pub const camera_convention = "opencv-x-right-y-down-z-forward";
pub const ply_format = "ascii-ply-v1-xyz-only";

pub const Point3 = struct {
    x: f64,
    y: f64,
    z: f64,
};

pub fn backprojectNormalized(
    u: f64,
    v: f64,
    z: f64,
    fx: f64,
    fy: f64,
    cx: f64,
    cy: f64,
) Point3 {
    return .{
        .x = (u - cx) * z / fx,
        .y = (v - cy) * z / fy,
        .z = z,
    };
}

pub fn contractDigest() artifacts.ArtifactId {
    var hasher = std.crypto.hash.Blake3.init(.{});
    hasher.update("EVO-D2B-BACKPROJECTION");
    hasher.update(&[_]u8{ schema_version, adapter_version });

    hashField(&hasher, input_manifest_filename);
    hashField(&hasher, input_depth_filename);
    hashField(&hasher, input_mask_filename);
    hashField(&hasher, input_camera_filename);
    hashField(&hasher, points_filename);
    hashField(&hasher, ply_filename);
    hashField(&hasher, scene_manifest_filename);
    hashField(&hasher, points_encoding);
    hashField(&hasher, pixel_convention);
    hashField(&hasher, camera_convention);
    hashField(&hasher, ply_format);

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

test "D2b normalized center backprojection is exact for simple camera" {
    const p = backprojectNormalized(
        0.75,
        0.25,
        2.0,
        0.5,
        0.5,
        0.5,
        0.5,
    );
    try std.testing.expectApproxEqAbs(@as(f64, 1.0), p.x, 1e-12);
    try std.testing.expectApproxEqAbs(@as(f64, -1.0), p.y, 1e-12);
    try std.testing.expectApproxEqAbs(@as(f64, 2.0), p.z, 1e-12);
}

test "D2b contract fingerprint is stable" {
    const first = contractDigest();
    const second = contractDigest();
    try std.testing.expect(artifacts.eqlId(first, second));
    try std.testing.expect(!artifacts.isZeroId(first));
}
