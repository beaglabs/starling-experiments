const std = @import("std");
const reference = @import("f3_stage7b_reference.zig");
const stage7a = @import("../substrate/stage7/stage7a_policy.zig");

pub const historical_search_blob =
    "e91f88b2ea2dafd6bd51113954ff03aee4330163";
pub const canonical_report_sha256 =
    "e3d27eec1f7bb78d5cabf869fc5172c3746a356f7f4cd9db4cc91f657e01ff2f";

const Anchor = struct {
    id: usize,
    theta: stage7a.Theta,
    rounds_sum: u64,
    communication_sum: u64,
    duplicate_sum: u64,
    computation_sum: u64,
};

const selected = [_]Anchor{
    .{
        .id = 37,
        .theta = .{
            .novelty_permille = 244,
            .exploration_permille = 94,
            .retry_permille = 15,
            .bandwidth_utilization_permille = 958,
        },
        .rounds_sum = 1046,
        .communication_sum = 258389,
        .duplicate_sum = 170485,
        .computation_sum = 55936,
    },
    .{
        .id = 51,
        .theta = .{
            .novelty_permille = 354,
            .exploration_permille = 141,
            .retry_permille = 0,
            .bandwidth_utilization_permille = 994,
        },
        .rounds_sum = 1054,
        .communication_sum = 255319,
        .duplicate_sum = 167211,
        .computation_sum = 56576,
    },
    .{
        .id = 93,
        .theta = .{
            .novelty_permille = 685,
            .exploration_permille = 283,
            .retry_permille = 960,
            .bandwidth_utilization_permille = 344,
        },
        .rounds_sum = 1435,
        .communication_sum = 250805,
        .duplicate_sum = 162945,
        .computation_sum = 76704,
    },
};

pub fn validateCanonicalSelectedFamily() !void {
    const candidates = reference.generateCandidates();
    try std.testing.expectEqual(
        @as(usize, 134),
        candidates.len,
    );

    for (selected) |anchor| {
        try std.testing.expect(
            candidates.items[anchor.id].theta.eql(anchor.theta),
        );

        const metrics = try reference.evaluateCandidate(
            candidates.items[anchor.id],
            .validation,
        );
        try std.testing.expectEqual(@as(usize, 24), metrics.runs);
        try std.testing.expectEqual(@as(usize, 0), metrics.failures);
        try std.testing.expectEqual(anchor.rounds_sum, metrics.rounds_sum);
        try std.testing.expectEqual(
            anchor.communication_sum,
            metrics.communication_sum,
        );
        try std.testing.expectEqual(
            anchor.duplicate_sum,
            metrics.duplicate_sum,
        );
        try std.testing.expectEqual(
            anchor.computation_sum,
            metrics.computation_sum,
        );
        try std.testing.expectEqual(
            anchor.communication_sum - anchor.duplicate_sum,
            metrics.useful_sum,
        );
        try std.testing.expectEqual(@as(u64, 0), metrics.violations);
    }
}

test "F3 Stage 7B selected-family anchor matches canonical report" {
    try validateCanonicalSelectedFamily();
}
