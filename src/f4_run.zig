const replay = @import("finalization/f4_replay.zig");

pub fn main(init: @import("std").process.Init) !void {
    return replay.main(init);
}
