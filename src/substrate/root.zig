pub const stage5a = @import("stage5/stage5a_scaling.zig");
pub const stage7a = @import("stage7/stage7a_policy.zig");
pub const stage7c = @import("stage7/stage7c_async_transfer.zig");

test {
    _ = stage5a;
    _ = stage7a;
    _ = stage7c;
}
