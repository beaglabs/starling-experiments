const std = @import("std");
const historical = @import("../substrate/stage7/stage7a_cli.zig");

pub fn main(init: std.process.Init) !void {
    return historical.main(init);
}
