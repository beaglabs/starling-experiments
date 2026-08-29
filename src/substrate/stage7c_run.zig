const std = @import("std");
const historical = @import("stage7/stage7c_cli.zig");

pub fn main(init: std.process.Init) !void {
    return historical.main(init);
}
