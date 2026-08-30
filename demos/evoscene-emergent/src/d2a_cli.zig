const std = @import("std");
const artifacts = @import("artifacts.zig");
const contract = @import("d2a_contract.zig");

pub fn main(init: std.process.Init) !void {
    const io = init.io;
    const args = try init.minimal.args.toSlice(init.arena.allocator());

    if (args.len != 2 or
        !std.mem.eql(u8, args[1], "validate"))
    {
        try std.Io.File.stderr().writeStreamingAll(
            io,
            "usage: run-demo-evoscene-d2a validate\n",
        );
        std.process.exit(2);
    }

    const digest = contract.contractDigest();
    var digest_hex: [64]u8 = undefined;
    hexDigest(digest, &digest_hex);

    const sample = contract.PriorFiles{
        .width = 320,
        .height = 240,
        .valid_pixels = 1,
        .depth_bytes = 320 * 240 * 4,
        .mask_bytes = 320 * 240,
    };

    const out = std.Io.File.stdout();
    try writeLine(io, out, "EvoScene-emergent D2a spatial-prior contract\n", .{});
    try writeLine(io, out, "schema_version: {d}\n", .{contract.schema_version});
    try writeLine(io, out, "adapter_version: {d}\n", .{contract.adapter_version});
    try writeLine(io, out, "moge_commit: {s}\n", .{contract.moge_git_commit});
    try writeLine(
        io,
        out,
        "moge_v2_blob_sha1: {s}\n",
        .{contract.moge_v2_blob_sha1},
    );
    try writeLine(io, out, "model_repo: {s}\n", .{contract.model_repo});
    try writeLine(io, out, "model_sha256: {s}\n", .{contract.model_sha256});
    try writeLine(io, out, "canonical_device: {s}\n", .{contract.canonical_device});
    try writeLine(
        io,
        out,
        "canonical_num_tokens: {d}\n",
        .{contract.canonical_num_tokens},
    );
    try writeLine(
        io,
        out,
        "canonical_fp16: {s}\n",
        .{if (contract.canonical_use_fp16) "yes" else "no"},
    );
    try writeLine(io, out, "depth_encoding: {s}\n", .{contract.depth_encoding});
    try writeLine(io, out, "mask_encoding: {s}\n", .{contract.mask_encoding});
    try writeLine(
        io,
        out,
        "camera_convention: {s}\n",
        .{contract.camera_convention},
    );
    try writeLine(io, out, "contract_blake3: {s}\n", .{&digest_hex});
    try writeLine(
        io,
        out,
        "sample_file_contract: {s}\n",
        .{if (sample.valid()) "PASS" else "FAIL"},
    );

    if (!sample.valid()) {
        try writeLine(io, out, "D2a FAIL: spatial-prior contract invalid\n", .{});
        std.process.exit(2);
    }

    try writeLine(
        io,
        out,
        "D2a CONTRACT PASS: real spatial-prior boundary frozen\n",
        .{},
    );
}

fn hexDigest(
    digest: artifacts.ArtifactId,
    out: *[64]u8,
) void {
    const alphabet = "0123456789abcdef";
    var i: usize = 0;
    while (i < digest.len) : (i += 1) {
        out[i * 2] = alphabet[digest[i] >> 4];
        out[i * 2 + 1] = alphabet[digest[i] & 0x0f];
    }
}

fn writeLine(
    io: std.Io,
    out: std.Io.File,
    comptime format: []const u8,
    args: anytype,
) !void {
    var buffer: [4096]u8 = undefined;
    const line = try std.fmt.bufPrint(&buffer, format, args);
    try out.writeStreamingAll(io, line);
}
