pub const Fixture = enum(u8) {
    photo_no_datetime,
    photo_shadow_ready,

    pub fn name(self: Fixture) []const u8 {
        return @tagName(self);
    }
};

pub const AcquisitionContext = struct {
    fixture: Fixture,
    has_datetime: bool,
    has_shadow_ratio: bool,
    object_height_mm: u32 = 0,
    shadow_length_mm: u32 = 0,

    pub fn photoNoDatetime() AcquisitionContext {
        return .{
            .fixture = .photo_no_datetime,
            .has_datetime = false,
            .has_shadow_ratio = false,
        };
    }

    pub fn photoShadowReady() AcquisitionContext {
        return .{
            .fixture = .photo_shadow_ready,
            .has_datetime = true,
            .has_shadow_ratio = true,
            // Synthetic context fixture only. These are not asserted to have
            // been measured from the user-supplied photograph.
            .object_height_mm = 1800,
            .shadow_length_mm = 1200,
        };
    }
};
