const std = @import("std");
const artifacts = @import("artifacts.zig");
const contract = @import("d2e_contract.zig");

pub fn main(init: std.process.Init) !void {
    const io = init.io;
    const args = try init.minimal.args.toSlice(init.arena.allocator());

    if (args.len != 2 or !std.mem.eql(u8, args[1], "validate")) {
        try std.Io.File.stderr().writeStreamingAll(
            io,
            "usage: run-demo-evoscene-d2e validate\n",
        );
        std.process.exit(2);
    }

    const digest = contract.contractDigest();
    var digest_hex: [64]u8 = undefined;
    hexDigest(digest, &digest_hex);

    const primitive_ok =
        contract.exposedFaceCount(true, 0) == 6 and
        contract.exposedFaceCount(true, 3) == 3 and
        contract.exposedFaceCount(true, 6) == 0 and
        contract.exposedFaceCount(false, 0) == 0;

    const out = std.Io.File.stdout();
    try writeLine(io, out, "EvoScene-emergent D2e mesh-finalization contract\n", .{});
    try writeLine(io, out, "schema_version: {d}\n", .{contract.schema_version});
    try writeLine(io, out, "adapter_version: {d}\n", .{contract.adapter_version});
    try writeLine(
        io,
        out,
        "canonical_voxel_size_mm: {d}\n",
        .{contract.canonical_voxel_size_mm},
    );
    try writeLine(io, out, "surface_rule: {s}\n", .{contract.surface_rule});
    try writeLine(io, out, "face_order: {s}\n", .{contract.face_order});
    try writeLine(io, out, "triangle_rule: {s}\n", .{contract.triangle_rule});
    try writeLine(
        io,
        out,
        "coordinate_convention: {s}\n",
        .{contract.coordinate_convention},
    );
    try writeLine(io, out, "vertex_rule: {s}\n", .{contract.vertex_rule});
    try writeLine(io, out, "contract_blake3: {s}\n", .{&digest_hex});
    try writeLine(
        io,
        out,
        "surface_primitive: {s}\n",
        .{if (primitive_ok) "PASS" else "FAIL"},
    );

    if (!primitive_ok) {
        try writeLine(io, out, "D2e FAIL: surface primitive invalid\n", .{});
        std.process.exit(2);
    }

    try writeLine(
        io,
        out,
        "D2e CONTRACT PASS: deterministic mesh-finalization boundary frozen\n",
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
