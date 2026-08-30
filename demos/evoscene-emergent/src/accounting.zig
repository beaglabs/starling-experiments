const std = @import("std");

pub const ToolKind = enum(u8) {
    depth,
    camera,
    geometry,
    view,
    fusion,
    evaluator,

    pub fn name(self: ToolKind) []const u8 {
        return switch (self) {
            .depth => "depth",
            .camera => "camera",
            .geometry => "geometry",
            .view => "view",
            .fusion => "fusion",
            .evaluator => "evaluator",
        };
    }
};

pub const tool_count: usize = @typeInfo(ToolKind).@"enum".fields.len;

pub const Accounting = struct {
    proposed_actions: u64 = 0,
    accepted_actions: u64 = 0,
    rejected_actions: u64 = 0,

    tool_invocations: u64 = 0,
    accepted_control_actions: u64 = 0,
    produced_artifacts: u64 = 0,

    proposal_units: u64 = 0,
    decision_units: u64 = 0,
    evidence_units: u64 = 0,
    communication_units: u64 = 0,

    wall_time_ms: u64 = 0,
    tool_invocations_by_kind: [tool_count]u64 =
        [_]u64{0} ** tool_count,

    pub fn recordProposal(self: *Accounting) void {
        self.proposed_actions +%= 1;
        self.proposal_units +%= 1;
        self.communication_units +%= 1;
    }

    pub fn recordAcceptedDecision(
        self: *Accounting,
        has_output: bool,
    ) void {
        self.accepted_actions +%= 1;
        self.decision_units +%= 1;
        self.communication_units +%= 1;

        if (has_output) {
            self.evidence_units +%= 1;
            self.communication_units +%= 1;
            self.produced_artifacts +%= 1;
        }
    }

    pub fn recordRejectedDecision(self: *Accounting) void {
        self.rejected_actions +%= 1;
        self.decision_units +%= 1;
        self.communication_units +%= 1;
    }

    pub fn recordTool(
        self: *Accounting,
        tool: ToolKind,
        wall_time_ms: u64,
    ) void {
        self.tool_invocations +%= 1;
        self.wall_time_ms +%= wall_time_ms;
        self.tool_invocations_by_kind[@intFromEnum(tool)] +%= 1;
    }

    pub fn recordControlAction(self: *Accounting) void {
        self.accepted_control_actions +%= 1;
    }

    pub fn actionAccountingValid(self: Accounting) bool {
        return self.proposed_actions ==
            self.accepted_actions + self.rejected_actions and
            self.accepted_actions ==
                self.tool_invocations + self.accepted_control_actions;
    }

    pub fn communicationAccountingValid(self: Accounting) bool {
        return self.communication_units ==
            self.proposal_units +
                self.decision_units +
                self.evidence_units;
    }
};

test "accounting identities are exact" {
    var accounting = Accounting{};

    accounting.recordProposal();
    accounting.recordTool(.depth, 11);
    accounting.recordAcceptedDecision(true);

    accounting.recordProposal();
    accounting.recordControlAction();
    accounting.recordAcceptedDecision(false);

    accounting.recordProposal();
    accounting.recordRejectedDecision();

    try std.testing.expect(accounting.actionAccountingValid());
    try std.testing.expect(accounting.communicationAccountingValid());
    try std.testing.expectEqual(@as(u64, 3), accounting.proposed_actions);
    try std.testing.expectEqual(@as(u64, 2), accounting.accepted_actions);
    try std.testing.expectEqual(@as(u64, 1), accounting.rejected_actions);
    try std.testing.expectEqual(@as(u64, 1), accounting.tool_invocations);
    try std.testing.expectEqual(@as(u64, 11), accounting.wall_time_ms);
}
