const std = @import("std");
const artifacts = @import("artifacts.zig");
const contract = @import("d2b_contract.zig");

pub fn main(init: std.process.Init) !void {
    const io = init.io;
    const args = try init.minimal.args.toSlice(init.arena.allocator());

    if (args.len != 2 or !std.mem.eql(u8, args[1], "validate")) {
        try std.Io.File.stderr().writeStreamingAll(
            io,
            "usage: run-demo-evoscene-d2b validate\n",
        );
        std.process.exit(2);
    }

    const digest = contract.contractDigest();
    var digest_hex: [64]u8 = undefined;
    hexDigest(digest, &digest_hex);

    const sample = contract.backprojectNormalized(
        0.75,
        0.25,
        2.0,
        0.5,
        0.5,
        0.5,
        0.5,
    );
    const sample_ok =
        @abs(sample.x - 1.0) < 1e-12 and
        @abs(sample.y + 1.0) < 1e-12 and
        @abs(sample.z - 2.0) < 1e-12;

    const out = std.Io.File.stdout();
    try writeLine(io, out, "EvoScene-emergent D2b backprojection contract\n", .{});
    try writeLine(io, out, "schema_version: {d}\n", .{contract.schema_version});
    try writeLine(io, out, "adapter_version: {d}\n", .{contract.adapter_version});
    try writeLine(io, out, "points_encoding: {s}\n", .{contract.points_encoding});
    try writeLine(io, out, "pixel_convention: {s}\n", .{contract.pixel_convention});
    try writeLine(io, out, "camera_convention: {s}\n", .{contract.camera_convention});
    try writeLine(io, out, "ply_format: {s}\n", .{contract.ply_format});
    try writeLine(io, out, "contract_blake3: {s}\n", .{&digest_hex});
    try writeLine(
        io,
        out,
        "sample_backprojection: {s}\n",
        .{if (sample_ok) "PASS" else "FAIL"},
    );

    if (!sample_ok) {
        try writeLine(io, out, "D2b FAIL: backprojection contract invalid\n", .{});
        std.process.exit(2);
    }

    try writeLine(
        io,
        out,
        "D2b CONTRACT PASS: deterministic point-cloud boundary frozen\n",
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
