const std = @import("std");
const zquic = @import("zquic");
const scaling = @import("../substrate/stage5/stage5a_scaling.zig");
const wire = @import("f1c_wire.zig");

const io_mod = zquic.transport.io;
const Address = @TypeOf(@as(io_mod.ConnState, undefined).peer);

pub const max_links: usize =
    scaling.max_operators * (scaling.max_operators - 1);
const base_port: u16 = 47200;
const alpn = "starlings/1";
const cert_pem = @embedFile("../../fixtures/f1c/cert.pem");
const key_pem = @embedFile("../../fixtures/f1c/key.pem");

pub const Counters = struct {
    poll_iterations: u64 = 0,
    udp_datagrams_received: u64 = 0,
    backpressure_events: u64 = 0,
    send_failures: u64 = 0,
    malformed_frames: u64 = 0,
};

pub const Link = struct {
    sender: u16,
    recipient: u16,
    client: *io_mod.Client,
    server_conn: ?*io_mod.ConnState = null,
    server_addr: Address,
    stream_id: u64 = 0,
    send_offset: u64 = 0,
    recv_cursor: usize = 0,
};

pub const Runtime = struct {
    allocator: std.mem.Allocator,
    node_count: usize,
    servers: [scaling.max_operators]?*io_mod.Server =
        [_]?*io_mod.Server{null} ** scaling.max_operators,
    links: std.ArrayList(Link) = .empty,
    counters: Counters = .{},

    pub fn init(
        allocator: std.mem.Allocator,
        node_count: usize,
        topology: scaling.TopologyKind,
    ) !Runtime {
        if (node_count < 2 or node_count > scaling.max_operators) {
            return error.InvalidPopulationSize;
        }

        var self = Runtime{
            .allocator = allocator,
            .node_count = node_count,
        };
        errdefer self.deinit();

        var node: usize = 0;
        while (node < node_count) : (node += 1) {
            const server = try io_mod.Server.init(allocator, .{
                .port = portFor(node),
                .cert_pem = cert_pem,
                .key_pem = key_pem,
                .alpn = alpn,
                .raw_application_streams = true,
            });
            self.servers[node] = server;
        }

        var sender: usize = 0;
        while (sender < node_count) : (sender += 1) {
            var recipient: usize = 0;
            while (recipient < node_count) : (recipient += 1) {
                if (sender == recipient or
                    !isTopologyEdge(topology, sender, recipient, node_count))
                {
                    continue;
                }

                const client = try allocator.create(io_mod.Client);
                errdefer allocator.destroy(client);
                try io_mod.Client.initInPlace(allocator, .{
                    .host = "127.0.0.1",
                    .port = portFor(recipient),
                    .alpn = alpn,
                    .raw_application_streams = true,
                }, client);

                const server_addr = try Address.parseIp4(
                    "127.0.0.1",
                    portFor(recipient),
                );
                try client.startHandshake(server_addr);
                try self.links.append(allocator, .{
                    .sender = @intCast(sender),
                    .recipient = @intCast(recipient),
                    .client = client,
                    .server_addr = server_addr,
                });
            }
        }

        try self.finishHandshakes();
        for (self.links.items) |*link| {
            const conn = findServerConn(
                self.servers[link.recipient].?,
                link.client,
            ) orelse return error.ServerConnectionMissing;
            if (conn.phase != .connected) return error.HandshakeIncomplete;
            link.server_conn = conn;
            link.stream_id = try link.client.tryOpenLocalUniStream();
        }

        return self;
    }

    pub fn deinit(self: *Runtime) void {
        for (self.links.items) |link| {
            link.client.deinit();
            self.allocator.destroy(link.client);
        }
        self.links.deinit(self.allocator);

        var node: usize = 0;
        while (node < self.node_count) : (node += 1) {
            if (self.servers[node]) |server| {
                server.deinit();
                self.servers[node] = null;
            }
        }
    }

    pub fn send(
        self: *Runtime,
        sender: u16,
        recipient: u16,
        envelope: wire.Envelope,
        fact_count: usize,
    ) !bool {
        const link = self.findLink(sender, recipient) orelse {
            return error.LinkNotFound;
        };

        var frame_buf: [wire.max_frame_bytes]u8 = undefined;
        const frame = try wire.encode(&frame_buf, envelope, fact_count);

        var consumed: usize = 0;
        var attempts: usize = 0;
        while (consumed < frame.len and attempts < 64) : (attempts += 1) {
            const accepted = link.client.sendRawStreamData(
                link.stream_id,
                link.send_offset,
                frame[consumed..],
                false,
            );
            if (accepted == 0) {
                self.counters.backpressure_events +%= 1;
                try self.pump(1);
                continue;
            }

            consumed += accepted;
            link.send_offset += accepted;
            if (consumed < frame.len) {
                try self.pump(0);
            }
        }

        if (consumed != frame.len) {
            self.counters.send_failures +%= 1;
            return false;
        }
        return true;
    }

    pub fn pump(self: *Runtime, timeout_ms: i32) !void {
        self.counters.poll_iterations +%= 1;

        for (self.servers[0..self.node_count]) |maybe_server| {
            if (maybe_server) |server| server.processPendingWork();
        }
        for (self.links.items) |link| {
            link.client.processPendingWork(link.server_addr);
        }

        var fds: [scaling.max_operators + max_links]std.posix.pollfd = undefined;
        var count: usize = 0;

        var node: usize = 0;
        while (node < self.node_count) : (node += 1) {
            fds[count] = .{
                .fd = self.servers[node].?.sock,
                .events = std.posix.POLL.IN,
                .revents = 0,
            };
            count += 1;
        }
        for (self.links.items) |link| {
            fds[count] = .{
                .fd = link.client.sock,
                .events = std.posix.POLL.IN,
                .revents = 0,
            };
            count += 1;
        }

        _ = try std.posix.poll(fds[0..count], timeout_ms);

        node = 0;
        while (node < self.node_count) : (node += 1) {
            if (fds[node].revents & std.posix.POLL.IN != 0) {
                try self.recvServer(self.servers[node].?);
            }
        }

        var link_index: usize = 0;
        while (link_index < self.links.items.len) : (link_index += 1) {
            const fd_index = self.node_count + link_index;
            if (fds[fd_index].revents & std.posix.POLL.IN != 0) {
                try self.recvClient(self.links.items[link_index].client);
            }
        }

        for (self.servers[0..self.node_count]) |maybe_server| {
            if (maybe_server) |server| server.processPendingWork();
        }
        for (self.links.items) |link| {
            link.client.processPendingWork(link.server_addr);
        }
    }

    pub fn drainFrames(
        self: *Runtime,
        fact_count: usize,
        context: anytype,
        comptime onEnvelope: fn (@TypeOf(context), wire.Envelope) anyerror!void,
    ) !void {
        for (self.links.items) |*link| {
            const conn = link.server_conn orelse return error.ServerConnectionMissing;
            const bytes = io_mod.rawAppRecvBuffer(conn, link.stream_id) orelse continue;

            while (link.recv_cursor < bytes.len) {
                const parsed = wire.parse(bytes[link.recv_cursor..], fact_count) catch {
                    self.counters.malformed_frames +%= 1;
                    return error.MalformedTransportFrame;
                } orelse break;

                if (parsed.envelope.sender != link.sender or
                    parsed.envelope.recipient != link.recipient)
                {
                    self.counters.malformed_frames +%= 1;
                    return error.TransportIdentityMismatch;
                }

                link.recv_cursor += parsed.total_len;
                try onEnvelope(context, parsed.envelope);
            }
        }
    }

    fn finishHandshakes(self: *Runtime) !void {
        var iteration: usize = 0;
        while (iteration < 5000) : (iteration += 1) {
            var connected = true;
            for (self.links.items) |link| {
                if (link.client.conn.phase != .connected) {
                    connected = false;
                    break;
                }
            }
            if (connected) return;
            try self.pump(1);
        }
        return error.HandshakeTimeout;
    }

    fn findLink(
        self: *Runtime,
        sender: u16,
        recipient: u16,
    ) ?*Link {
        for (self.links.items) |*link| {
            if (link.sender == sender and link.recipient == recipient) {
                return link;
            }
        }
        return null;
    }

    fn recvServer(self: *Runtime, server: *io_mod.Server) !void {
        var buf: [2048]u8 = undefined;
        var src: Address = undefined;
        var addr_len: std.posix.socklen_t = @sizeOf(std.posix.sockaddr.in);
        const rc = std.posix.system.recvfrom(
            server.sock,
            buf[0..].ptr,
            buf.len,
            0,
            &src.any,
            &addr_len,
        );
        const errno = std.posix.errno(rc);
        if (errno == .AGAIN or errno == .INTR) return;
        if (errno != .SUCCESS) return std.posix.unexpectedErrno(errno);
        const n: usize = @intCast(rc);
        self.counters.udp_datagrams_received +%= 1;
        server.feedPacket(buf[0..n], src);
    }

    fn recvClient(self: *Runtime, client: *io_mod.Client) !void {
        var buf: [2048]u8 = undefined;
        const rc = std.posix.system.recvfrom(
            client.sock,
            buf[0..].ptr,
            buf.len,
            0,
            null,
            null,
        );
        const errno = std.posix.errno(rc);
        if (errno == .AGAIN or errno == .INTR) return;
        if (errno != .SUCCESS) return std.posix.unexpectedErrno(errno);
        const n: usize = @intCast(rc);
        self.counters.udp_datagrams_received +%= 1;
        client.feedPacket(buf[0..n]);
    }
};

fn findServerConn(
    server: *io_mod.Server,
    client: *io_mod.Client,
) ?*io_mod.ConnState {
    for (&server.conns) |*slot| {
        if (slot.*) |conn| {
            if (zquic.types.ConnectionId.eql(
                conn.remote_cid,
                client.conn.local_cid,
            )) {
                return conn;
            }
        }
    }
    return null;
}

fn portFor(node: usize) u16 {
    return base_port + @as(u16, @intCast(node));
}

fn isTopologyEdge(
    topology: scaling.TopologyKind,
    sender: usize,
    recipient: usize,
    population: usize,
) bool {
    return switch (topology) {
        .complete => sender != recipient,
        .ring => blk: {
            const left = (sender + population - 1) % population;
            const right = (sender + 1) % population;
            break :blk recipient == left or recipient == right;
        },
        .grid => blk: {
            const width = scaling.gridWidth(population);
            const sr = sender / width;
            const sc = sender % width;
            const rr = recipient / width;
            const rc = recipient % width;
            const row_neighbor = sr == rr and
                (if (sc > rc) sc - rc == 1 else rc - sc == 1);
            const col_neighbor = sc == rc and
                (if (sr > rr) sr - rr == 1 else rr - sr == 1);
            break :blk row_neighbor or col_neighbor;
        },
    };
}

test "F1c directed topology edge counts are exact" {
    var ring: usize = 0;
    var grid: usize = 0;
    var sender: usize = 0;
    while (sender < 8) : (sender += 1) {
        var recipient: usize = 0;
        while (recipient < 8) : (recipient += 1) {
            if (isTopologyEdge(.ring, sender, recipient, 8)) ring += 1;
            if (isTopologyEdge(.grid, sender, recipient, 8)) grid += 1;
        }
    }
    try std.testing.expectEqual(@as(usize, 16), ring);
    try std.testing.expectEqual(@as(usize, 20), grid);
}
