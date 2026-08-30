const std = @import("std");
const artifacts = @import("artifacts.zig");

pub const schema_version: u8 = 1;
pub const adapter_version: u8 = 1;

pub const canonical_voxel_size_mm: u32 = 25;
pub const canonical_min_neighbors: u8 = 2;

pub const fused_points_filename = "fused_points.f32le";
pub const refined_points_filename = "refined_points.f32le";
pub const refined_ply_filename = "refined.ply";
pub const fusion_manifest_filename = "fusion.json";
pub const telemetry_filename = "telemetry.json";

pub const points_encoding = "voxel-centroid-xyz-f32le-meters";
pub const voxel_indexing = "floor-coordinate-over-voxel-size";
pub const refinement_rule = "keep-if-occupied-26-neighbors-gte-threshold";
pub const canonical_ordering = "lexicographic-voxel-key";

pub const Point3 = struct {
    x: f64,
    y: f64,
    z: f64,
};

pub fn centroid2(a: Point3, b: Point3) Point3 {
    return .{
        .x = (a.x + b.x) / 2.0,
        .y = (a.y + b.y) / 2.0,
        .z = (a.z + b.z) / 2.0,
    };
}

pub fn contractDigest() artifacts.ArtifactId {
    var hasher = std.crypto.hash.Blake3.init(.{});
    hasher.update("EVO-D2C-FUSION-REFINEMENT");
    hasher.update(&[_]u8{
        schema_version,
        adapter_version,
        canonical_min_neighbors,
    });

    var voxel_bytes: [4]u8 = undefined;
    encodeU32Le(canonical_voxel_size_mm, &voxel_bytes);
    hasher.update(&voxel_bytes);

    hashField(&hasher, fused_points_filename);
    hashField(&hasher, refined_points_filename);
    hashField(&hasher, refined_ply_filename);
    hashField(&hasher, fusion_manifest_filename);
    hashField(&hasher, points_encoding);
    hashField(&hasher, voxel_indexing);
    hashField(&hasher, refinement_rule);
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

fn encodeU32Le(value: u32, out: *[4]u8) void {
    var i: usize = 0;
    while (i < 4) : (i += 1) {
        const shift: u5 = @intCast(i * 8);
        out[i] = @truncate(value >> shift);
    }
}

test "D2c centroid primitive is exact for simple points" {
    const c = centroid2(
        .{ .x = 0.0, .y = 2.0, .z = 4.0 },
        .{ .x = 2.0, .y = 4.0, .z = 6.0 },
    );
    try std.testing.expect(@abs(c.x - 1.0) < 1e-12);
    try std.testing.expect(@abs(c.y - 3.0) < 1e-12);
    try std.testing.expect(@abs(c.z - 5.0) < 1e-12);
}

test "D2c contract fingerprint is stable" {
    const first = contractDigest();
    const second = contractDigest();
    try std.testing.expect(artifacts.eqlId(first, second));
    try std.testing.expect(!artifacts.isZeroId(first));
}
