const std = @import("std");

pub const ArtifactId = [32]u8;
pub const zero_id: ArtifactId = [_]u8{0} ** 32;
pub const max_parents: usize = 2;
pub const canonical_version: u8 = 1;

pub const Operator = enum(u8) {
    spatial_prior,
    geometry,
    view_planner,
    novel_view,
    fusion,
    critic,

    pub fn name(self: Operator) []const u8 {
        return switch (self) {
            .spatial_prior => "spatial_prior",
            .geometry => "geometry",
            .view_planner => "view_planner",
            .novel_view => "novel_view",
            .fusion => "fusion",
            .critic => "critic",
        };
    }
};

pub const Producer = enum(u8) {
    external,
    spatial_prior,
    geometry,
    view_planner,
    novel_view,
    fusion,
    critic,

    pub fn fromOperator(operator: Operator) Producer {
        return switch (operator) {
            .spatial_prior => .spatial_prior,
            .geometry => .geometry,
            .view_planner => .view_planner,
            .novel_view => .novel_view,
            .fusion => .fusion,
            .critic => .critic,
        };
    }
};

pub const ArtifactKind = enum(u8) {
    input_image,
    depth_map,
    camera_estimate,
    point_cloud,
    scene_representation,
    mesh,
    view_request,
    rendered_view,
    synthesized_view,
    confidence_map,
    evaluation_report,
    cost_record,

    pub fn name(self: ArtifactKind) []const u8 {
        return switch (self) {
            .input_image => "input_image",
            .depth_map => "depth_map",
            .camera_estimate => "camera_estimate",
            .point_cloud => "point_cloud",
            .scene_representation => "scene_representation",
            .mesh => "mesh",
            .view_request => "view_request",
            .rendered_view => "rendered_view",
            .synthesized_view => "synthesized_view",
            .confidence_map => "confidence_map",
            .evaluation_report => "evaluation_report",
            .cost_record => "cost_record",
        };
    }
};

pub const Artifact = struct {
    id: ArtifactId,
    kind: ArtifactKind,
    producer: Producer,
    parents: [max_parents]ArtifactId = .{ zero_id, zero_id },
    parent_count: u8 = 0,
    created_step: u32,
    wall_time_ms: u64,
    value: u64,
    payload_hash: ArtifactId,
};

pub fn eqlId(a: ArtifactId, b: ArtifactId) bool {
    return std.mem.eql(u8, &a, &b);
}

pub fn isZeroId(id: ArtifactId) bool {
    return eqlId(id, zero_id);
}

pub fn hashPayload(payload: []const u8) ArtifactId {
    var hasher = std.crypto.hash.Blake3.init(.{});
    hasher.update(payload);
    var digest: ArtifactId = undefined;
    hasher.final(&digest);
    return digest;
}

pub fn artifactId(
    kind: ArtifactKind,
    producer: Producer,
    parents: [max_parents]ArtifactId,
    parent_count: u8,
    value: u64,
    payload_hash: ArtifactId,
) ArtifactId {
    std.debug.assert(parent_count <= max_parents);

    var hasher = std.crypto.hash.Blake3.init(.{});
    const header = [_]u8{
        canonical_version,
        @intFromEnum(kind),
        @intFromEnum(producer),
        parent_count,
    };
    hasher.update(&header);

    var i: usize = 0;
    while (i < parent_count) : (i += 1) {
        hasher.update(&parents[i]);
    }

    var value_bytes: [8]u8 = undefined;
    encodeU64Le(value, &value_bytes);
    hasher.update(&value_bytes);
    hasher.update(&payload_hash);

    var digest: ArtifactId = undefined;
    hasher.final(&digest);
    return digest;
}

pub fn ArtifactStore(comptime capacity: usize) type {
    return struct {
        const Self = @This();

        items: [capacity]Artifact = undefined,
        len: usize = 0,

        pub fn addRoot(
            self: *Self,
            kind: ArtifactKind,
            payload: []const u8,
        ) !ArtifactId {
            const payload_hash = hashPayload(payload);
            const parents = [max_parents]ArtifactId{ zero_id, zero_id };
            const id = artifactId(
                kind,
                .external,
                parents,
                0,
                0,
                payload_hash,
            );
            if (self.find(id)) |_| return id;
            if (self.len >= capacity) return error.ArtifactCapacityExceeded;

            self.items[self.len] = .{
                .id = id,
                .kind = kind,
                .producer = .external,
                .parents = parents,
                .parent_count = 0,
                .created_step = 0,
                .wall_time_ms = 0,
                .value = 0,
                .payload_hash = payload_hash,
            };
            self.len += 1;
            return id;
        }

        pub fn insertProduced(
            self: *Self,
            kind: ArtifactKind,
            producer: Operator,
            parents: [max_parents]ArtifactId,
            parent_count: u8,
            created_step: u32,
            wall_time_ms: u64,
            value: u64,
            payload_hash: ArtifactId,
        ) !ArtifactId {
            if (parent_count > max_parents) return error.TooManyParents;

            var i: usize = 0;
            while (i < parent_count) : (i += 1) {
                if (self.find(parents[i]) == null) {
                    return error.UnknownParent;
                }
            }

            const artifact_producer = Producer.fromOperator(producer);
            const id = artifactId(
                kind,
                artifact_producer,
                parents,
                parent_count,
                value,
                payload_hash,
            );

            if (self.find(id)) |_| return id;
            if (self.len >= capacity) return error.ArtifactCapacityExceeded;

            self.items[self.len] = .{
                .id = id,
                .kind = kind,
                .producer = artifact_producer,
                .parents = parents,
                .parent_count = parent_count,
                .created_step = created_step,
                .wall_time_ms = wall_time_ms,
                .value = value,
                .payload_hash = payload_hash,
            };
            self.len += 1;
            return id;
        }

        pub fn find(self: *const Self, id: ArtifactId) ?usize {
            var i: usize = 0;
            while (i < self.len) : (i += 1) {
                if (eqlId(self.items[i].id, id)) return i;
            }
            return null;
        }

        pub fn get(self: *const Self, id: ArtifactId) ?*const Artifact {
            const index = self.find(id) orelse return null;
            return &self.items[index];
        }

        pub fn allHaveValidProvenance(self: *const Self) bool {
            var i: usize = 0;
            while (i < self.len) : (i += 1) {
                const item = self.items[i];
                if (item.producer == .external) {
                    if (item.parent_count != 0) return false;
                    continue;
                }

                if (item.parent_count == 0) return false;
                var p: usize = 0;
                while (p < item.parent_count) : (p += 1) {
                    if (self.find(item.parents[p]) == null) return false;
                }
            }
            return true;
        }
    };
}

fn encodeU64Le(value: u64, out: *[8]u8) void {
    var i: usize = 0;
    while (i < 8) : (i += 1) {
        const shift: u6 = @intCast(i * 8);
        out[i] = @truncate(value >> shift);
    }
}

test "artifact identity is stable and parent-sensitive" {
    const root_hash = hashPayload("fixture");
    const parents = [max_parents]ArtifactId{ zero_id, zero_id };

    const a = artifactId(
        .input_image,
        .external,
        parents,
        0,
        0,
        root_hash,
    );
    const b = artifactId(
        .input_image,
        .external,
        parents,
        0,
        0,
        root_hash,
    );
    try std.testing.expect(eqlId(a, b));

    var parented = parents;
    parented[0] = a;
    const c = artifactId(
        .depth_map,
        .spatial_prior,
        parented,
        1,
        1,
        root_hash,
    );
    const d = artifactId(
        .depth_map,
        .spatial_prior,
        parents,
        0,
        1,
        root_hash,
    );
    try std.testing.expect(!eqlId(c, d));
}

test "artifact store requires causal parents" {
    var store = ArtifactStore(8){};
    const root = try store.addRoot(.input_image, "image");
    var parents = [max_parents]ArtifactId{ root, zero_id };

    const depth = try store.insertProduced(
        .depth_map,
        .spatial_prior,
        parents,
        1,
        1,
        11,
        42,
        hashPayload("depth"),
    );
    try std.testing.expect(store.get(depth) != null);
    try std.testing.expect(store.allHaveValidProvenance());

    parents[0] = hashPayload("missing");
    try std.testing.expectError(
        error.UnknownParent,
        store.insertProduced(
            .depth_map,
            .spatial_prior,
            parents,
            1,
            2,
            11,
            43,
            hashPayload("other-depth"),
        ),
    );
}
