const std = @import("std");
const zquic = @import("zquic");
const scaling = @import("../substrate/stage5/stage5a_scaling.zig");

pub const magic: u8 = 0x53;
pub const version: u8 = 1;
pub const max_frame_bytes: usize = 256;

pub const Envelope = struct {
    sender: u16,
    recipient: u16,
    sequence: u32,
    selected: u16,
    facts: scaling.BitSet,
};

pub const Parsed = struct {
    envelope: Envelope,
    total_len: usize,
};

pub const Error = error{
    BufferTooSmall,
    InvalidFrame,
    InvalidVersion,
    FactOutOfRange,
};

pub fn encode(
    out: []u8,
    envelope: Envelope,
    fact_count: usize,
) Error![]const u8 {
    if (envelope.selected == 0 or envelope.selected > fact_count) {
        return error.InvalidFrame;
    }
    if (envelope.facts.count(fact_count) != envelope.selected) {
        return error.InvalidFrame;
    }

    const body_len: usize = 12 + @as(usize, envelope.selected) * 2;
    var prefix_scratch: [8]u8 = undefined;
    const prefix = zquic.varint.encode(&prefix_scratch, body_len) catch {
        return error.InvalidFrame;
    };
    const total_len = prefix.len + body_len;
    if (out.len < total_len) return error.BufferTooSmall;

    @memcpy(out[0..prefix.len], prefix);
    var i = prefix.len;
    out[i] = magic;
    i += 1;
    out[i] = version;
    i += 1;
    putU16(out[i .. i + 2], envelope.sender);
    i += 2;
    putU16(out[i .. i + 2], envelope.recipient);
    i += 2;
    putU32(out[i .. i + 4], envelope.sequence);
    i += 4;
    putU16(out[i .. i + 2], envelope.selected);
    i += 2;

    var fact: usize = 0;
    var written: usize = 0;
    while (fact < fact_count) : (fact += 1) {
        if (!envelope.facts.has(fact)) continue;
        putU16(out[i .. i + 2], @intCast(fact));
        i += 2;
        written += 1;
    }
    if (written != envelope.selected) return error.InvalidFrame;
    return out[0..total_len];
}

pub fn parse(buf: []const u8, fact_count: usize) Error!?Parsed {
    const prefix = decodeQuicVarint(buf) orelse return null;
    if (prefix.value > max_frame_bytes) return error.InvalidFrame;
    const body_len: usize = @intCast(prefix.value);
    const total_len = prefix.len + body_len;
    if (buf.len < total_len) return null;
    if (body_len < 12) return error.InvalidFrame;

    const body = buf[prefix.len..total_len];
    if (body[0] != magic) return error.InvalidFrame;
    if (body[1] != version) return error.InvalidVersion;

    const sender = getU16(body[2..4]);
    const recipient = getU16(body[4..6]);
    const sequence = getU32(body[6..10]);
    const selected = getU16(body[10..12]);
    const expected_len = 12 + @as(usize, selected) * 2;
    if (selected == 0 or selected > fact_count or expected_len != body.len) {
        return error.InvalidFrame;
    }

    var facts = scaling.BitSet{};
    var i: usize = 12;
    while (i < body.len) : (i += 2) {
        const fact = getU16(body[i .. i + 2]);
        if (fact >= fact_count) return error.FactOutOfRange;
        facts.set(fact);
    }
    if (facts.count(fact_count) != selected) return error.InvalidFrame;

    return .{
        .envelope = .{
            .sender = sender,
            .recipient = recipient,
            .sequence = sequence,
            .selected = selected,
            .facts = facts,
        },
        .total_len = total_len,
    };
}

const DecodedVarint = struct {
    value: u64,
    len: usize,
};

fn decodeQuicVarint(buf: []const u8) ?DecodedVarint {
    if (buf.len == 0) return null;
    const tag = buf[0] >> 6;
    const len: usize = @as(usize, 1) << @intCast(tag);
    if (buf.len < len) return null;

    var value: u64 = buf[0] & 0x3f;
    var i: usize = 1;
    while (i < len) : (i += 1) {
        value = (value << 8) | buf[i];
    }
    return .{ .value = value, .len = len };
}

fn putU16(out: []u8, value: u16) void {
    out[0] = @truncate(value);
    out[1] = @truncate(value >> 8);
}

fn putU32(out: []u8, value: u32) void {
    out[0] = @truncate(value);
    out[1] = @truncate(value >> 8);
    out[2] = @truncate(value >> 16);
    out[3] = @truncate(value >> 24);
}

fn getU16(buf: []const u8) u16 {
    return @as(u16, buf[0]) | (@as(u16, buf[1]) << 8);
}

fn getU32(buf: []const u8) u32 {
    return @as(u32, buf[0]) |
        (@as(u32, buf[1]) << 8) |
        (@as(u32, buf[2]) << 16) |
        (@as(u32, buf[3]) << 24);
}

test "F1c wire round trip is exact" {
    var facts = scaling.BitSet{};
    facts.set(3);
    facts.set(17);
    const source = Envelope{
        .sender = 4,
        .recipient = 1,
        .sequence = 77,
        .selected = 2,
        .facts = facts,
    };
    var buf: [max_frame_bytes]u8 = undefined;
    const encoded = try encode(&buf, source, 32);
    const parsed = (try parse(encoded, 32)).?;
    try std.testing.expectEqual(encoded.len, parsed.total_len);
    try std.testing.expectEqual(source.sender, parsed.envelope.sender);
    try std.testing.expectEqual(source.recipient, parsed.envelope.recipient);
    try std.testing.expectEqual(source.sequence, parsed.envelope.sequence);
    try std.testing.expectEqual(source.selected, parsed.envelope.selected);
    try std.testing.expect(scaling.BitSet.eql(source.facts, parsed.envelope.facts));
}

test "F1c wire parser waits for complete frame" {
    var facts = scaling.BitSet{};
    facts.set(1);
    const source = Envelope{
        .sender = 1,
        .recipient = 0,
        .sequence = 1,
        .selected = 1,
        .facts = facts,
    };
    var buf: [max_frame_bytes]u8 = undefined;
    const encoded = try encode(&buf, source, 8);
    try std.testing.expect((try parse(encoded[0 .. encoded.len - 1], 8)) == null);
}
