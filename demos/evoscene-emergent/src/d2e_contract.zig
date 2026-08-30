const std = @import("std");
const artifacts = @import("artifacts.zig");

pub const schema_version: u8 = 1;
pub const adapter_version: u8 = 2;

pub const canonical_max_pixel_edge: u16 = 16;
pub const canonical_max_depth_jump_mm: u32 = 50;
pub const canonical_relative_depth_jump_ppm: u32 = 30_000;

pub const scipy_version = "1.18.1";

pub const glb_filename = "scene.glb";
pub const obj_filename = "scene.obj";
pub const ply_filename = "scene_mesh.ply";
pub const mesh_manifest_filename = "mesh.json";
pub const telemetry_filename = "telemetry.json";

pub const surface_rule =
    "source-camera-zbuffer-delaunay-filtered";
pub const raster_rule =
    "nearest-z-source-index-tiebreak";
pub const triangle_rule =
    "delaunay-filter-pixel-edge-depth-jump";
pub const normal_rule =
    "area-weighted-vertex-normal-facing-source-camera";
pub const coordinate_convention =
    "opencv-x-right-y-down-z-forward-meters";
pub const vertex_rule =
    "one-visible-source-point-per-source-pixel";
pub const canonical_ordering =
    "vertices-source-pixel-index-triangles-lexicographic";

pub fn depthJumpLimitMeters(depth_m: f64) f64 {
    const absolute = @as(f64, canonical_max_depth_jump_mm) / 1000.0;
    const relative =
        depth_m *
        (@as(f64, canonical_relative_depth_jump_ppm) / 1_000_000.0);
    return @max(absolute, relative);
}

pub fn depthEdgeAccepted(a_m: f64, b_m: f64) bool {
    if (a_m <= 0.0 or b_m <= 0.0) return false;
    const near_depth = @min(a_m, b_m);
    return @abs(a_m - b_m) <= depthJumpLimitMeters(near_depth);
}

pub fn contractDigest() artifacts.ArtifactId {
    var hasher = std.crypto.hash.Blake3.init(.{});
    hasher.update("EVO-D2E-MESH-FINALIZATION");
    hasher.update(&[_]u8{ schema_version, adapter_version });

    var u16_bytes: [2]u8 = undefined;
    encodeU16Le(canonical_max_pixel_edge, &u16_bytes);
    hasher.update(&u16_bytes);

    var u32_bytes: [4]u8 = undefined;
    encodeU32Le(canonical_max_depth_jump_mm, &u32_bytes);
    hasher.update(&u32_bytes);
    encodeU32Le(canonical_relative_depth_jump_ppm, &u32_bytes);
    hasher.update(&u32_bytes);

    hashField(&hasher, scipy_version);
    hashField(&hasher, glb_filename);
    hashField(&hasher, obj_filename);
    hashField(&hasher, ply_filename);
    hashField(&hasher, mesh_manifest_filename);
    hashField(&hasher, surface_rule);
    hashField(&hasher, raster_rule);
    hashField(&hasher, triangle_rule);
    hashField(&hasher, normal_rule);
    hashField(&hasher, coordinate_convention);
    hashField(&hasher, vertex_rule);
    hashField(&hasher, canonical_ordering);

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

fn encodeU16Le(value: u16, out: *[2]u8) void {
    out[0] = @truncate(value);
    out[1] = @truncate(value >> 8);
}

fn encodeU32Le(value: u32, out: *[4]u8) void {
    var i: usize = 0;
    while (i < 4) : (i += 1) {
        const shift: u5 = @intCast(i * 8);
        out[i] = @truncate(value >> shift);
    }
}

test "D2e depth discontinuity primitive is exact" {
    try std.testing.expect(depthEdgeAccepted(2.0, 2.04));
    try std.testing.expect(!depthEdgeAccepted(2.0, 2.08));
    try std.testing.expect(depthEdgeAccepted(6.0, 6.10));
    try std.testing.expect(!depthEdgeAccepted(6.0, 6.25));
}

test "D2e contract fingerprint is stable" {
    const first = contractDigest();
    const second = contractDigest();
    try std.testing.expect(artifacts.eqlId(first, second));
    try std.testing.expect(!artifacts.isZeroId(first));
}
