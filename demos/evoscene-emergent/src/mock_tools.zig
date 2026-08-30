const std = @import("std");
const artifacts = @import("artifacts.zig");
const messages = @import("messages.zig");
const accounting = @import("accounting.zig");

pub const mock_tool_version: u8 = 1;

pub const Output = struct {
    kind: artifacts.ArtifactKind,
    tool: accounting.ToolKind,
    wall_time_ms: u64,
    value: u64,
    payload_hash: artifacts.ArtifactId,
};

pub fn invoke(
    action: messages.ActionKind,
    inputs: [artifacts.max_parents]artifacts.Artifact,
    input_count: u8,
    seed: u64,
    payload: u64,
) !Output {
    const spec = switch (action) {
        .estimate_depth => .{
            .kind = artifacts.ArtifactKind.depth_map,
            .tool = accounting.ToolKind.depth,
            .wall_time_ms = @as(u64, 11),
        },
        .estimate_camera => .{
            .kind = artifacts.ArtifactKind.camera_estimate,
            .tool = accounting.ToolKind.camera,
            .wall_time_ms = @as(u64, 7),
        },
        .build_geometry => .{
            .kind = artifacts.ArtifactKind.scene_representation,
            .tool = accounting.ToolKind.geometry,
            .wall_time_ms = @as(u64, 31),
        },
        .refine_geometry => .{
            .kind = artifacts.ArtifactKind.scene_representation,
            .tool = accounting.ToolKind.geometry,
            .wall_time_ms = @as(u64, 29),
        },
        .render_view => .{
            .kind = artifacts.ArtifactKind.rendered_view,
            .tool = accounting.ToolKind.view,
            .wall_time_ms = @as(u64, 23),
        },
        .fuse_view => .{
            .kind = artifacts.ArtifactKind.point_cloud,
            .tool = accounting.ToolKind.fusion,
            .wall_time_ms = @as(u64, 13),
        },
        .evaluate => .{
            .kind = artifacts.ArtifactKind.evaluation_report,
            .tool = accounting.ToolKind.evaluator,
            .wall_time_ms = @as(u64, 5),
        },
        .propose_view,
        .propose_stop,
        => return error.NotToolAction,
    };

    const payload_hash = outputHash(
        action,
        inputs,
        input_count,
        seed,
        payload,
    );
    const raw_value = digestU64(payload_hash);
    const value = if (action == .evaluate)
        600 + (raw_value % 401)
    else
        raw_value;

    return .{
        .kind = spec.kind,
        .tool = spec.tool,
        .wall_time_ms = spec.wall_time_ms,
        .value = value,
        .payload_hash = payload_hash,
    };
}

pub fn viewRequestHash(
    input: artifacts.Artifact,
    seed: u64,
    payload: u64,
) artifacts.ArtifactId {
    var inputs = [_]artifacts.Artifact{input} ** artifacts.max_parents;
    return outputHash(
        .propose_view,
        inputs,
        1,
        seed,
        payload,
    );
}

fn outputHash(
    action: messages.ActionKind,
    inputs: [artifacts.max_parents]artifacts.Artifact,
    input_count: u8,
    seed: u64,
    payload: u64,
) artifacts.ArtifactId {
    var hasher = std.crypto.hash.Blake3.init(.{});
    const header = [_]u8{
        mock_tool_version,
        @intFromEnum(action),
        input_count,
    };
    hasher.update(&header);

    var i: usize = 0;
    while (i < input_count) : (i += 1) {
        hasher.update(&inputs[i].id);
    }

    var seed_bytes: [8]u8 = undefined;
    var payload_bytes: [8]u8 = undefined;
    encodeU64Le(seed, &seed_bytes);
    encodeU64Le(payload, &payload_bytes);
    hasher.update(&seed_bytes);
    hasher.update(&payload_bytes);

    var digest: artifacts.ArtifactId = undefined;
    hasher.final(&digest);
    return digest;
}

fn digestU64(digest: artifacts.ArtifactId) u64 {
    var value: u64 = 0;
    var i: usize = 0;
    while (i < 8) : (i += 1) {
        const shift: u6 = @intCast(i * 8);
        value |= @as(u64, digest[i]) << shift;
    }
    return value;
}

fn encodeU64Le(value: u64, out: *[8]u8) void {
    var i: usize = 0;
    while (i < 8) : (i += 1) {
        const shift: u6 = @intCast(i * 8);
        out[i] = @truncate(value >> shift);
    }
}

test "mock tool outputs are deterministic" {
    var store = artifacts.ArtifactStore(4){};
    const input_id = try store.addRoot(.input_image, "fixture");
    const input = store.get(input_id).?;

    var inputs = [_]artifacts.Artifact{input.*} ** artifacts.max_parents;
    const a = try invoke(.estimate_depth, inputs, 1, 7, 0);
    const b = try invoke(.estimate_depth, inputs, 1, 7, 0);

    try std.testing.expect(artifacts.eqlId(a.payload_hash, b.payload_hash));
    try std.testing.expectEqual(a.value, b.value);
    try std.testing.expectEqual(@as(u64, 11), a.wall_time_ms);
}

test "evaluation mock emits bounded quality score" {
    var store = artifacts.ArtifactStore(4){};
    const root = try store.addRoot(.input_image, "fixture");
    const root_artifact = store.get(root).?;

    var inputs = [_]artifacts.Artifact{root_artifact.*} ** artifacts.max_parents;
    const evaluation = try invoke(.evaluate, inputs, 1, 0, 0);

    try std.testing.expect(evaluation.value >= 600);
    try std.testing.expect(evaluation.value <= 1000);
}
