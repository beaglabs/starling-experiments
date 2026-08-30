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
        contract.depthEdgeAccepted(2.0, 2.04) and
        !contract.depthEdgeAccepted(2.0, 2.08) and
        contract.depthEdgeAccepted(6.0, 6.10) and
        !contract.depthEdgeAccepted(6.0, 6.25);

    const out = std.Io.File.stdout();
    try writeLine(io, out, "EvoScene-emergent D2e mesh-finalization contract\n", .{});
    try writeLine(io, out, "schema_version: {d}\n", .{contract.schema_version});
    try writeLine(io, out, "adapter_version: {d}\n", .{contract.adapter_version});
    try writeLine(
        io,
        out,
        "canonical_max_pixel_edge: {d}\n",
        .{contract.canonical_max_pixel_edge},
    );
    try writeLine(
        io,
        out,
        "canonical_max_depth_jump_mm: {d}\n",
        .{contract.canonical_max_depth_jump_mm},
    );
    try writeLine(
        io,
        out,
        "canonical_relative_depth_jump_ppm: {d}\n",
        .{contract.canonical_relative_depth_jump_ppm},
    );
    try writeLine(io, out, "scipy_version: {s}\n", .{contract.scipy_version});
    try writeLine(io, out, "surface_rule: {s}\n", .{contract.surface_rule});
    try writeLine(io, out, "raster_rule: {s}\n", .{contract.raster_rule});
    try writeLine(io, out, "triangle_rule: {s}\n", .{contract.triangle_rule});
    try writeLine(io, out, "normal_rule: {s}\n", .{contract.normal_rule});
    try writeLine(
        io,
        out,
        "coordinate_convention: {s}\n",
        .{contract.coordinate_convention},
    );
    try writeLine(io, out, "vertex_rule: {s}\n", .{contract.vertex_rule});
    try writeLine(
        io,
        out,
        "canonical_ordering: {s}\n",
        .{contract.canonical_ordering},
    );
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
        "D2e CONTRACT PASS: deterministic projected-surface boundary frozen\n",
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
