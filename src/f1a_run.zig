const cli = @import("finalization/f1a_cli.zig");

pub fn main(init: @import("std").process.Init) !void {
    return cli.main(init);
}
