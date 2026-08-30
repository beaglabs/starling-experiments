const std = @import("std");
const schema = @import("schema.zig");
const protocol = @import("protocol.zig");
const context_mod = @import("context.zig");
const operators = @import("operators.zig");

pub const Rejection = enum(u8) {
    none,
    terminated,
    permission,
    duplicate_action,
    unmet_prerequisite,
};

pub const TraceEvent = struct {
    step: u32,
    role: protocol.Role,
    action: protocol.Action,
    accepted: bool,
    rejection: Rejection,
    fields_written: u8,
};

pub const Accounting = struct {
    proposed_actions: u64 = 0,
    accepted_actions: u64 = 0,
    rejected_actions: u64 = 0,
    tool_invocations: u64 = 0,
    fields_written: u64 = 0,
    communication_units: u64 = 0,
};

pub const Runtime = struct {
    context: context_mod.AcquisitionContext,
    facts: schema.FactStore = .{},
    completed: [protocol.action_count]bool = [_]bool{false} ** protocol.action_count,
    accounting: Accounting = .{},
    trace: [64]TraceEvent = undefined,
    trace_len: usize = 0,
    terminated: bool = false,
    step: u32 = 0,

    pub fn init(context: context_mod.AcquisitionContext) Runtime {
        return .{ .context = context };
    }

    pub fn actionDone(self: *const Runtime, action: protocol.Action) bool {
        return self.completed[@intFromEnum(action)];
    }

    pub fn allPrimaryInspectionsDone(self: *const Runtime) bool {
        const actions = [_]protocol.Action{
            .inspect_geometry,
            .inspect_terrain,
            .inspect_water,
            .inspect_illumination,
            .inspect_atmosphere,
            .inspect_vegetation,
            .inspect_built,
            .inspect_motion,
            .inspect_temporal,
            .inspect_material,
        };
        for (actions) |action| {
            if (!self.actionDone(action)) return false;
        }
        return true;
    }

    pub fn shadowfinderReady(self: *const Runtime) bool {
        if (!self.context.has_datetime) return false;
        if (!self.actionDone(.inspect_illumination)) return false;

        const solar = self.facts.status(.solar_angle);
        return solar == .derived or solar == .estimated or
            self.context.has_shadow_ratio;
    }

    pub fn submit(self: *Runtime, proposal: protocol.Proposal) !bool {
        self.accounting.proposed_actions += 1;
        self.accounting.communication_units += 1;

        var rejection: Rejection = .none;
        if (self.terminated) {
            rejection = .terminated;
        } else if (!protocol.permitted(proposal.role, proposal.action)) {
            rejection = .permission;
        } else if (self.actionDone(proposal.action)) {
            rejection = .duplicate_action;
        } else if (!self.prerequisitesMet(proposal.action)) {
            rejection = .unmet_prerequisite;
        }

        if (rejection != .none) {
            self.accounting.rejected_actions += 1;
            self.accounting.communication_units += 1;
            try self.appendTrace(proposal, false, rejection, 0);
            return false;
        }

        self.step +%= 1;
        const written = try operators.invoke(&self.facts, self.context, self.step, proposal.action);
        self.completed[@intFromEnum(proposal.action)] = true;
        self.accounting.accepted_actions += 1;
        self.accounting.communication_units += 1;
        self.accounting.fields_written += written;
        self.accounting.communication_units += written;

        if (proposal.action != .stop) {
            self.accounting.tool_invocations += 1;
        } else {
            self.terminated = true;
        }

        try self.appendTrace(proposal, true, .none, written);
        return true;
    }

    pub fn invariantsHold(self: *const Runtime) bool {
        if (self.accounting.proposed_actions !=
            self.accounting.accepted_actions + self.accounting.rejected_actions)
        {
            return false;
        }

        if (self.accounting.communication_units !=
            self.accounting.proposed_actions +
            self.accounting.accepted_actions +
            self.accounting.rejected_actions +
            self.accounting.fields_written)
        {
            return false;
        }

        if (self.terminated and !self.actionDone(.stop)) return false;
        if (self.terminated and !self.facts.allResolved()) return false;
        if (self.actionDone(.stop) and !self.actionDone(.assess_uncertainty)) {
            return false;
        }

        return true;
    }

    fn prerequisitesMet(self: *const Runtime, action: protocol.Action) bool {
        return switch (action) {
            .inspect_geometry,
            .inspect_terrain,
            .inspect_water,
            .inspect_illumination,
            .inspect_atmosphere,
            .inspect_vegetation,
            .inspect_built,
            .inspect_motion,
            .inspect_temporal,
            .inspect_material,
            => true,

            .run_shadowfinder =>
                self.allPrimaryInspectionsDone() and self.shadowfinderReady(),

            .fuse_geolocation =>
                self.allPrimaryInspectionsDone() and
                (!self.shadowfinderReady() or self.actionDone(.run_shadowfinder)),

            .assess_uncertainty =>
                self.actionDone(.fuse_geolocation),

            .stop =>
                self.actionDone(.assess_uncertainty) and
                self.facts.allResolved(),
        };
    }

    fn appendTrace(
        self: *Runtime,
        proposal: protocol.Proposal,
        accepted: bool,
        rejection: Rejection,
        fields_written: u8,
    ) !void {
        if (self.trace_len >= self.trace.len) return error.TraceCapacityExceeded;
        self.trace[self.trace_len] = .{
            .step = self.step,
            .role = proposal.role,
            .action = proposal.action,
            .accepted = accepted,
            .rejection = rejection,
            .fields_written = fields_written,
        };
        self.trace_len += 1;
    }
};

test "GEOINT runtime rejects cross-role action" {
    var rt = Runtime.init(context_mod.AcquisitionContext.photoNoDatetime());
    const accepted = try rt.submit(.{
        .role = .water,
        .action = .inspect_geometry,
    });
    try std.testing.expect(!accepted);
    try std.testing.expectEqual(@as(u64, 1), rt.accounting.rejected_actions);
}
