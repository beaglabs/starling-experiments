const std = @import("std");
const artifacts = @import("artifacts.zig");

pub const schema_version: u8 = 1;
pub const adapter_version: u8 = 1;

pub const canonical_voxel_size_mm: u32 = 25;

pub const glb_filename = "scene.glb";
pub const obj_filename = "scene.obj";
pub const ply_filename = "scene_mesh.ply";
pub const mesh_manifest_filename = "mesh.json";
pub const telemetry_filename = "telemetry.json";

pub const surface_rule = "occupied-voxel-exposed-faces";
pub const face_order = "negx-posx-negy-posy-negz-posz";
pub const triangle_rule = "quad-v0-v1-v2-v0-v2-v3";
pub const coordinate_convention = "opencv-x-right-y-down-z-forward-meters";
pub const vertex_rule = "four-face-local-vertices-per-exposed-quad";

pub fn exposedFaceCount(
    center_occupied: bool,
    occupied_neighbors: u8,
) u8 {
    if (!center_occupied) return 0;
    if (occupied_neighbors > 6) return 0;
    return 6 - occupied_neighbors;
}

pub fn contractDigest() artifacts.ArtifactId {
    var hasher = std.crypto.hash.Blake3.init(.{});
    hasher.update("EVO-D2E-MESH-FINALIZATION");
    hasher.update(&[_]u8{ schema_version, adapter_version });

    var voxel_bytes: [4]u8 = undefined;
    encodeU32Le(canonical_voxel_size_mm, &voxel_bytes);
    hasher.update(&voxel_bytes);

    hashField(&hasher, glb_filename);
    hashField(&hasher, obj_filename);
    hashField(&hasher, ply_filename);
    hashField(&hasher, mesh_manifest_filename);
    hashField(&hasher, surface_rule);
    hashField(&hasher, face_order);
    hashField(&hasher, triangle_rule);
    hashField(&hasher, coordinate_convention);
    hashField(&hasher, vertex_rule);

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

test "D2e exposed face primitive is exact" {
    try std.testing.expectEqual(@as(u8, 6), exposedFaceCount(true, 0));
    try std.testing.expectEqual(@as(u8, 3), exposedFaceCount(true, 3));
    try std.testing.expectEqual(@as(u8, 0), exposedFaceCount(true, 6));
    try std.testing.expectEqual(@as(u8, 0), exposedFaceCount(false, 0));
}

test "D2e contract fingerprint is stable" {
    const first = contractDigest();
    const second = contractDigest();
    try std.testing.expect(artifacts.eqlId(first, second));
    try std.testing.expect(!artifacts.isZeroId(first));
}
