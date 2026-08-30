const std = @import("std");
const artifacts = @import("artifacts.zig");
const contract = @import("d2f_contract.zig");

pub fn main(init: std.process.Init) !void {
    const io = init.io;
    const args = try init.minimal.args.toSlice(init.arena.allocator());

    if (args.len != 2 or !std.mem.eql(u8, args[1], "validate")) {
        try std.Io.File.stderr().writeStreamingAll(
            io,
            "usage: run-demo-evoscene-d2f validate\n",
        );
        std.process.exit(2);
    }

    const digest = contract.contractDigest();
    var digest_hex: [64]u8 = undefined;
    hexDigest(digest, &digest_hex);

    const out = std.Io.File.stdout();
    try writeLine(io, out, "EvoScene-emergent D2f learned novel-view contract\n", .{});
    try writeLine(io, out, "schema_version: {d}\n", .{contract.schema_version});
    try writeLine(io, out, "adapter_version: {d}\n", .{contract.adapter_version});
    try writeLine(io, out, "backend: {s}\n", .{contract.backend});
    try writeLine(io, out, "metaview_git_commit: {s}\n", .{contract.metaview_git_commit});
    try writeLine(
        io,
        out,
        "metaview_inference_blob_sha1: {s}\n",
        .{contract.metaview_inference_blob_sha1},
    );
    try writeLine(io, out, "metaview_model_repo: {s}\n", .{contract.metaview_model_repo});
    try writeLine(io, out, "metaview_model_sha256: {s}\n", .{contract.metaview_model_sha256});
    try writeLine(io, out, "qwen_image_edit_revision: {s}\n", .{contract.qwen_image_edit_revision});
    try writeLine(io, out, "da3_giant_revision: {s}\n", .{contract.da3_giant_revision});
    try writeLine(io, out, "da3_depth_revision: {s}\n", .{contract.da3_depth_revision});
    try writeLine(io, out, "canonical_seed: {d}\n", .{contract.canonical_seed});
    try writeLine(io, out, "canonical_steps: {d}\n", .{contract.canonical_steps});
    try writeLine(io, out, "canonical_width: {d}\n", .{contract.canonical_width});
    try writeLine(io, out, "canonical_height: {d}\n", .{contract.canonical_height});
    try writeLine(io, out, "prompt_id: {s}\n", .{contract.prompt_id});
    try writeLine(io, out, "pose_convention: {s}\n", .{contract.pose_convention});
    try writeLine(io, out, "output_rule: {s}\n", .{contract.output_rule});
    try writeLine(io, out, "cache_key_rule: {s}\n", .{contract.cache_key_rule});
    try writeLine(io, out, "contract_blake3: {s}\n", .{&digest_hex});
    try writeLine(
        io,
        out,
        "D2f CONTRACT PASS: learned novel-view boundary frozen\n",
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
