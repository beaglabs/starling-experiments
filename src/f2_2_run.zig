const cli = @import("finalization/f2_2_cli.zig");

pub fn main(init: @import("std").process.Init) !void {
    return cli.main(init);
}
