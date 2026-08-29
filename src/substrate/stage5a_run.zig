const std = @import("std");
const historical = @import("stage5/stage5a_cli.zig");

pub fn main(init: std.process.Init) !void {
    return historical.main(init);
}
