const std = @import("std");
const artifacts = @import("artifacts.zig");
const contract = @import("d2c_contract.zig");

pub fn main(init: std.process.Init) !void {
    const io = init.io;
    const args = try init.minimal.args.toSlice(init.arena.allocator());

    if (args.len != 2 or !std.mem.eql(u8, args[1], "validate")) {
        try std.Io.File.stderr().writeStreamingAll(
            io,
            "usage: run-demo-evoscene-d2c validate\n",
        );
        std.process.exit(2);
    }

    const digest = contract.contractDigest();
    var digest_hex: [64]u8 = undefined;
    hexDigest(digest, &digest_hex);

    const c = contract.centroid2(
        .{ .x = 0.0, .y = 2.0, .z = 4.0 },
        .{ .x = 2.0, .y = 4.0, .z = 6.0 },
    );
    const sample_ok =
        @abs(c.x - 1.0) < 1e-12 and
        @abs(c.y - 3.0) < 1e-12 and
        @abs(c.z - 5.0) < 1e-12;

    const out = std.Io.File.stdout();
    try writeLine(io, out, "EvoScene-emergent D2c fusion/refinement contract\n", .{});
    try writeLine(io, out, "schema_version: {d}\n", .{contract.schema_version});
    try writeLine(io, out, "adapter_version: {d}\n", .{contract.adapter_version});
    try writeLine(
        io,
        out,
        "canonical_voxel_size_mm: {d}\n",
        .{contract.canonical_voxel_size_mm},
    );
    try writeLine(
        io,
        out,
        "canonical_min_neighbors: {d}\n",
        .{contract.canonical_min_neighbors},
    );
    try writeLine(io, out, "points_encoding: {s}\n", .{contract.points_encoding});
    try writeLine(io, out, "voxel_indexing: {s}\n", .{contract.voxel_indexing});
    try writeLine(io, out, "refinement_rule: {s}\n", .{contract.refinement_rule});
    try writeLine(io, out, "canonical_ordering: {s}\n", .{contract.canonical_ordering});
    try writeLine(io, out, "contract_blake3: {s}\n", .{&digest_hex});
    try writeLine(
        io,
        out,
        "sample_centroid: {s}\n",
        .{if (sample_ok) "PASS" else "FAIL"},
    );

    if (!sample_ok) {
        try writeLine(io, out, "D2c FAIL: fusion primitive invalid\n", .{});
        std.process.exit(2);
    }

    try writeLine(
        io,
        out,
        "D2c CONTRACT PASS: deterministic fusion/refinement boundary frozen\n",
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
