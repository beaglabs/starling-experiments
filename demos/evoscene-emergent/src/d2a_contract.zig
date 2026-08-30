const std = @import("std");
const artifacts = @import("artifacts.zig");

pub const schema_version: u8 = 1;
pub const adapter_version: u8 = 1;

pub const moge_git_commit =
    "925b8ed835a7a9cdb7578ba15c658a0afc969030";
pub const model_repo = "Ruicheng/moge-2-vits-normal";
pub const model_filename = "model.pt";
pub const model_sha256 =
    "79a16621928c2bf0ed04659218c55c01075e950507f40bb3332fb4c873d3e1dc";

pub const canonical_device = "cpu";
pub const canonical_num_tokens: u32 = 1200;
pub const canonical_use_fp16 = false;

pub const depth_filename = "depth.f32le";
pub const mask_filename = "mask.u8";
pub const camera_filename = "camera.json";
pub const manifest_filename = "prior.json";
pub const telemetry_filename = "telemetry.json";

pub const depth_encoding = "row-major-f32le-meters-invalid-zero";
pub const mask_encoding = "row-major-u8-valid-0-or-1";
pub const camera_convention = "opencv-normalized-image-coordinates";

pub const PriorFiles = struct {
    width: u32,
    height: u32,
    valid_pixels: u64,
    depth_bytes: u64,
    mask_bytes: u64,

    pub fn valid(self: PriorFiles) bool {
        if (self.width == 0 or self.height == 0) return false;

        const pixels =
            @as(u64, self.width) * @as(u64, self.height);

        return self.valid_pixels > 0 and
            self.valid_pixels <= pixels and
            self.depth_bytes == pixels * 4 and
            self.mask_bytes == pixels;
    }
};

pub fn contractDigest() artifacts.ArtifactId {
    var hasher = std.crypto.hash.Blake3.init(.{});
    hasher.update("EVO-D2A-SPATIAL-PRIOR");
    hasher.update(&[_]u8{ schema_version, adapter_version });

    hashField(&hasher, moge_git_commit);
    hashField(&hasher, model_repo);
    hashField(&hasher, model_filename);
    hashField(&hasher, model_sha256);
    hashField(&hasher, canonical_device);

    var token_bytes: [4]u8 = undefined;
    encodeU32Le(canonical_num_tokens, &token_bytes);
    hasher.update(&token_bytes);
    hasher.update(&[_]u8{
        if (canonical_use_fp16) 1 else 0,
    });

    hashField(&hasher, depth_filename);
    hashField(&hasher, mask_filename);
    hashField(&hasher, camera_filename);
    hashField(&hasher, manifest_filename);
    hashField(&hasher, depth_encoding);
    hashField(&hasher, mask_encoding);
    hashField(&hasher, camera_convention);

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

test "D2a spatial-prior contract fingerprint is stable" {
    const first = contractDigest();
    const second = contractDigest();
    try std.testing.expect(artifacts.eqlId(first, second));
    try std.testing.expect(!artifacts.isZeroId(first));
}

test "D2a prior file contract requires exact raw sizes" {
    const good = PriorFiles{
        .width = 320,
        .height = 240,
        .valid_pixels = 60_000,
        .depth_bytes = 320 * 240 * 4,
        .mask_bytes = 320 * 240,
    };
    try std.testing.expect(good.valid());

    var bad = good;
    bad.depth_bytes -= 4;
    try std.testing.expect(!bad.valid());

    bad = good;
    bad.valid_pixels = 0;
    try std.testing.expect(!bad.valid());
}
