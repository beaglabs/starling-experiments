const cli = @import("finalization/f3b_cli.zig");

pub fn main(init: @import("std").process.Init) !void {
    return cli.main(init);
}
