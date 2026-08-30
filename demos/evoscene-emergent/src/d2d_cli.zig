const std = @import("std");
const artifacts = @import("artifacts.zig");
const contract = @import("d2d_contract.zig");

pub fn main(init: std.process.Init) !void {
    const io = init.io;
    const args = try init.minimal.args.toSlice(init.arena.allocator());

    if (args.len != 2 or !std.mem.eql(u8, args[1], "validate")) {
        try std.Io.File.stderr().writeStreamingAll(
            io,
            "usage: run-demo-evoscene-d2d validate\n",
        );
        std.process.exit(2);
    }

    const digest = contract.contractDigest();
    var digest_hex: [64]u8 = undefined;
    hexDigest(digest, &digest_hex);

    const payload_a = contract.posePayload(
        contract.canonical_pose_a_azimuth_mdeg,
        contract.canonical_pose_a_elevation_mdeg,
    );
    const payload_b = contract.posePayload(
        contract.canonical_pose_b_azimuth_mdeg,
        contract.canonical_pose_b_elevation_mdeg,
    );

    const out = std.Io.File.stdout();
    try writeLine(io, out, "EvoScene-emergent D2d novel-view contract\n", .{});
    try writeLine(io, out, "schema_version: {d}\n", .{contract.schema_version});
    try writeLine(io, out, "adapter_version: {d}\n", .{contract.adapter_version});
    try writeLine(
        io,
        out,
        "pose_a_azimuth_mdeg: {d}\n",
        .{contract.canonical_pose_a_azimuth_mdeg},
    );
    try writeLine(
        io,
        out,
        "pose_a_elevation_mdeg: {d}\n",
        .{contract.canonical_pose_a_elevation_mdeg},
    );
    try writeLine(
        io,
        out,
        "pose_b_azimuth_mdeg: {d}\n",
        .{contract.canonical_pose_b_azimuth_mdeg},
    );
    try writeLine(
        io,
        out,
        "pose_b_elevation_mdeg: {d}\n",
        .{contract.canonical_pose_b_elevation_mdeg},
    );
    try writeLine(io, out, "pose_a_payload_u64: {d}\n", .{payload_a});
    try writeLine(io, out, "pose_b_payload_u64: {d}\n", .{payload_b});
    try writeLine(io, out, "raster_rule: {s}\n", .{contract.raster_rule});
    try writeLine(io, out, "pixel_convention: {s}\n", .{contract.pixel_convention});
    try writeLine(io, out, "camera_convention: {s}\n", .{contract.camera_convention});
    try writeLine(io, out, "orbit_rule: {s}\n", .{contract.orbit_rule});
    try writeLine(io, out, "evidence_rule: {s}\n", .{contract.evidence_rule});
    try writeLine(io, out, "contract_blake3: {s}\n", .{&digest_hex});

    const encoding_ok =
        payload_a == ((@as(u64, 35_000) << 32) | 10_000) and
        payload_b == ((@as(u64, 325_000) << 32) | 10_000);
    try writeLine(
        io,
        out,
        "pose_encoding: {s}\n",
        .{if (encoding_ok) "PASS" else "FAIL"},
    );

    if (!encoding_ok) {
        try writeLine(io, out, "D2d FAIL: pose encoding invalid\n", .{});
        std.process.exit(2);
    }

    try writeLine(
        io,
        out,
        "D2d CONTRACT PASS: deterministic novel-view boundary frozen\n",
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
