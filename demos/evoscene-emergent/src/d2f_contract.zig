const std = @import("std");
const artifacts = @import("artifacts.zig");

pub const schema_version: u8 = 1;
pub const adapter_version: u8 = 1;

pub const backend = "metaview";
pub const metaview_git_commit =
    "a8a3d46c198d7cc0627e8e8a55d93d362fc1ca55";
pub const metaview_inference_blob_sha1 =
    "e7aa670dcfbbf4d061f75ec8af01edf1b5ae8805";
pub const metaview_model_repo = "Kwai-Kolors/MetaView";
pub const metaview_model_file = "model-2500-best.safetensors";
pub const metaview_model_sha256 =
    "a67ae628ea665c0f9ef00be3db38eaebca02734f63a4e04118573ac9e30a74ef";

pub const qwen_image_edit_revision = "ac7f931";
pub const da3_giant_revision = "72ee9f8";
pub const da3_depth_revision = "b2359bd";

pub const canonical_seed: u32 = 0;
pub const canonical_steps: u16 = 40;
pub const canonical_width: u16 = 960;
pub const canonical_height: u16 = 528;
pub const canonical_radius_quantization_mm: u16 = 1;

pub const prompt_id = "official-metaview-camera-trigger-v1";
pub const pose_convention = "metaview-yaw-pitch-radius";
pub const output_rule = "right-half-of-official-stitched-output";
pub const cache_key_rule =
    "sha256-contract-input-pose-radius-model-dependencies";
pub const generated_filename = "novel.png";
pub const generation_manifest_filename = "generation.json";
pub const telemetry_filename = "telemetry.json";

pub fn posePayload(
    yaw_mdeg: i32,
    pitch_mdeg: i32,
) u64 {
    const yaw_bits: u32 = @bitCast(yaw_mdeg);
    const pitch_bits: u32 = @bitCast(pitch_mdeg);
    return (@as(u64, yaw_bits) << 32) | @as(u64, pitch_bits);
}

pub fn contractDigest() artifacts.ArtifactId {
    var hasher = std.crypto.hash.Blake3.init(.{});
    hasher.update("EVO-D2F-LEARNED-NOVEL-VIEW");
    hasher.update(&[_]u8{ schema_version, adapter_version });

    hashField(&hasher, backend);
    hashField(&hasher, metaview_git_commit);
    hashField(&hasher, metaview_inference_blob_sha1);
    hashField(&hasher, metaview_model_repo);
    hashField(&hasher, metaview_model_file);
    hashField(&hasher, metaview_model_sha256);
    hashField(&hasher, qwen_image_edit_revision);
    hashField(&hasher, da3_giant_revision);
    hashField(&hasher, da3_depth_revision);
    hashField(&hasher, prompt_id);
    hashField(&hasher, pose_convention);
    hashField(&hasher, output_rule);
    hashField(&hasher, cache_key_rule);
    hashField(&hasher, generated_filename);
    hashField(&hasher, generation_manifest_filename);

    var scalar: [4]u8 = undefined;
    encodeU32Le(canonical_seed, &scalar);
    hasher.update(&scalar);
    encodeU32Le(canonical_steps, &scalar);
    hasher.update(&scalar);
    encodeU32Le(canonical_width, &scalar);
    hasher.update(&scalar);
    encodeU32Le(canonical_height, &scalar);
    hasher.update(&scalar);
    encodeU32Le(canonical_radius_quantization_mm, &scalar);
    hasher.update(&scalar);

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

fn encodeU32Le(value: anytype, out: *[4]u8) void {
    const widened: u32 = @intCast(value);
    var i: usize = 0;
    while (i < 4) : (i += 1) {
        const shift: u5 = @intCast(i * 8);
        out[i] = @truncate(widened >> shift);
    }
}

test "D2f signed pose payload is stable" {
    const a = posePayload(35_000, 10_000);
    const b = posePayload(-35_000, 10_000);
    try std.testing.expect(a != b);
    try std.testing.expectEqual(a, posePayload(35_000, 10_000));
    try std.testing.expectEqual(b, posePayload(-35_000, 10_000));
}

test "D2f contract fingerprint is stable" {
    const first = contractDigest();
    const second = contractDigest();
    try std.testing.expect(artifacts.eqlId(first, second));
    try std.testing.expect(!artifacts.isZeroId(first));
}
