"""A local CONNECT relay that authenticates upstream and counts the wire.

It listens on loopback, takes unauthenticated CONNECT from a browser, and opens
the real tunnel to the gateway with the username DSL in a `Proxy-Authorization`
header. Everything after that is copied through and counted.

Three separate problems collapse into this one object, which is why it is here
rather than in a probe.

**Chrome takes no credentials in `--proxy-server`.** That is not a limitation of
one library, it is Chrome's, so every engine that drives Chrome directly rather
than through Playwright hits it: zendriver, seleniumbase, anything over raw CDP.
The obvious alternative was answering `Fetch.authRequired` over CDP, and it was
tried and measured on 2026-08-13: `Fetch.RequestPaused` fired six times and
`Fetch.authRequired` fired **zero** times, with `ERR_TUNNEL_CONNECTION_FAILED`.
The tunnel fails before the gateway ever issues a challenge, so a page-scoped
handler never sees one. Pointing the browser at a relay that authenticates for
it sidesteps the problem instead of fighting it.

**Traffic has to be measured somewhere that is not the provider dashboard.** The
dashboard rounds to 0.01 GB, so its resolution is about 10 MB and a 330-byte
request is invisible to it. The counters we have instead are installed through
`page.route`, which is a Playwright API: they exist for four engines and not for
Obscura, whose byte column is empty for that reason alone. Counting here counts
the same way for every engine, because it counts sockets rather than framework
events. It also counts what `Content-Length` summing cannot - request headers,
TLS record overhead, chunked bodies, and the handshake itself.

**The exit address sometimes arrives for free, and it is currently paid for
twice.** The gateway puts `X-Proxy-Exit-IP` on the CONNECT reply, and the relay
is holding that reply already. Today a run learns its exit by making a separate
probe through `gateway.py`, which is another CONNECT, another chance to draw a
different address, and one more entry in the 15-20% of CONNECTs that are never
answered. Read off the reply we are already parsing, the address belongs to the
tunnel the page actually used, which is the only one worth recording.

Sometimes, because the header is not reliable and the measurement is worse than
this file used to imply. Six CONNECTs on 2026-08-13, all answered 200, returned
three different reason phrases and the header on two of them:

    HTTP/1.1 200 Connection established   ->  X-Proxy-Exit-IP present
    HTTP/1.1 200 Connection Established   ->  absent
    HTTP/1.1 200 OK                       ->  absent

So there are at least three implementations behind one hostname and only one of
them names the exit. `exits` is therefore a partial record by construction: an
empty list means the gateway did not say, never that the tunnel had no exit, and
nothing may infer a reused address from two attempts that both failed to report
one. It is still worth reading, because when it is there it is the address the
page actually used, which no separate probe can promise.

What this deliberately does not do:

- **It does not retry.** A gateway that does not answer is recorded and the
  client connection is dropped, so the browser reports the same
  `ERR_TUNNEL_CONNECTION_FAILED` it reported without the relay. Retrying here
  would quietly repair the failure floor that the direct arm exists to measure,
  and every error rate in `data/runs/` would drop for a reason no row would
  name.
- **It does not modify the stream.** No header rewriting, no injected values,
  nothing that would make a browser's request differ from what that browser
  emits. A relay that touched the ClientHello would invalidate every handshake
  measurement in this repository.

The cost it does impose has to be stated on any row it produced: an extra
loopback hop, so latency is not comparable against a run made without it. Bytes
and verdicts are; elapsed time is not.

Never log the header this builds. It is base64, not encryption.
"""
import select
import socket
import threading

from . import config, providers, proxy

# Long enough that a slow residential exit is not cut off mid-page, short enough
# that a hung tunnel does not hold a worker for the length of a run. The gateway
# has a measured habit of accepting a connection and never replying.
CONNECT_TIMEOUT = 30.0
IDLE_TIMEOUT = 120.0
CHUNK = 65536


class Relay:
    """One authenticated upstream identity, listening on loopback.

    One relay per session rather than one per run: the username carries the
    gateway parameters, and the parameter set is the sticky session key, so two
    sessions sharing a relay would share an exit address without either row
    saying so.
    """

    def __init__(self, *, params: dict = None, strict: bool = True,
                 provider=None):
        self.params = dict(params or {})
        self.provider = provider or providers.load()
        creds = config.credentials(self.provider)
        self.username = proxy.build_username(creds.login, strict=strict,
                                             provider=self.provider,
                                             **self.params)
        self._password = creds.password
        self.upstream_host = creds.host
        self.upstream_port = creds.port

        self._lock = threading.Lock()
        self.up = 0
        self.down = 0
        self.tunnels = 0
        # One counter over four causes: the gateway was unreachable, it dropped
        # the CONNECT mid-exchange, it answered with a status other than 200, or
        # this relay itself threw. Deliberately not split, because nothing reads
        # the number: `snapshot` carries it for symmetry and no row on disk has
        # ever recorded it. Read it as "this session had trouble", never as
        # evidence about the gateway - the unanswered-CONNECT floor is an open
        # question with a support ticket behind it, and a counter that cannot
        # tell a dead gateway from a bug in this file must not be quoted at it.
        self.failures = 0
        # Every distinct exit the gateway named, in the order it named them. A
        # list and not a single value: a session that silently moved to another
        # address is a fact about the run, and storing the last one would hide
        # it.
        self.exits = []
        self._server = None
        self._thread = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> str:
        """Bind an ephemeral loopback port and serve. Returns `host:port`."""
        import socketserver

        relay = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                relay._handle(self.request)

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = True

        self._server = Server(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)
        self._thread.start()
        host, port = self._server.server_address
        return f"{host}:{port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def __enter__(self):
        self.address = self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
        return False

    # -- measurement -------------------------------------------------------

    def snapshot(self) -> dict:
        """The counters as they stand. Cheap, so a row can take one per attempt."""
        with self._lock:
            return {"bytes_up": self.up, "bytes_down": self.down,
                    "bytes": self.up + self.down, "tunnels": self.tunnels,
                    "tunnel_failures": self.failures, "exits": list(self.exits)}

    def since(self, before: dict) -> dict:
        """The difference against an earlier snapshot, for one attempt.

        Differencing rather than resetting, because a page can still have
        sockets open when the next attempt starts and a reset would post those
        bytes to whichever row happened to be current.
        """
        now = self.snapshot()
        seen = before.get("exits", [])
        return {"bytes_up": now["bytes_up"] - before["bytes_up"],
                "bytes_down": now["bytes_down"] - before["bytes_down"],
                "bytes": now["bytes"] - before["bytes"],
                "tunnels": now["tunnels"] - before["tunnels"],
                "tunnel_failures": now["tunnel_failures"]
                                   - before["tunnel_failures"],
                "exits": now["exits"][len(seen):]}

    # -- the relay itself --------------------------------------------------

    def _auth_header(self) -> str:
        """The upstream credential. Built here, never returned to a caller."""
        import base64
        raw = f"{self.username}:{self._password}".encode()
        return "Proxy-Authorization: Basic " \
               + base64.b64encode(raw).decode() + "\r\n"

    def _handle(self, client: socket.socket) -> None:
        try:
            head = _read_head(client)
            if not head:
                return
            line = head.split("\r\n", 1)[0]
            method = line.split(" ", 1)[0].upper()
            if method == "CONNECT":
                self._tunnel(client, line.split(" ")[1], head)
            else:
                self._forward(client, head)
        except Exception:
            # A relay that raised into the server would print a traceback
            # carrying the request line. Failures are counted, not narrated.
            with self._lock:
                self.failures += 1
        finally:
            _close(client)

    def _open_upstream(self) -> socket.socket:
        upstream = socket.create_connection(
            (self.upstream_host, self.upstream_port), timeout=CONNECT_TIMEOUT)
        upstream.settimeout(CONNECT_TIMEOUT)
        return upstream

    def _tunnel(self, client: socket.socket, authority: str, head: str) -> None:
        try:
            upstream = self._open_upstream()
        except OSError:
            with self._lock:
                self.failures += 1
            return

        request = (f"CONNECT {authority} HTTP/1.1\r\n"
                   f"Host: {authority}\r\n"
                   + self._auth_header()
                   + "Proxy-Connection: Keep-Alive\r\n\r\n")
        try:
            upstream.sendall(request.encode())
            reply = _read_head(upstream)
        except OSError:
            with self._lock:
                self.failures += 1
            _close(upstream)
            return

        status = 0
        if reply:
            parts = reply.split("\r\n", 1)[0].split(" ")
            status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0

        if status != 200:
            # Pass the gateway's own reply through rather than inventing one.
            # Its status codes are the measurement: 406 for a bad country, 407
            # for a bad filter, and a silent 200 for a parameter it does not
            # know. Substituting a generic failure here would erase the one
            # signal the gateway gives.
            with self._lock:
                self.failures += 1
            if reply:
                try:
                    client.sendall(reply.encode())
                except OSError:
                    pass
            _close(upstream)
            return

        exit_ip = _header_value(reply, "X-Proxy-Exit-IP")
        with self._lock:
            self.tunnels += 1
            if exit_ip and (not self.exits or self.exits[-1] != exit_ip):
                self.exits.append(exit_ip)

        try:
            client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        except OSError:
            _close(upstream)
            return
        self._pump(client, upstream)

    def _forward(self, client: socket.socket, head: str) -> None:
        """Plain HTTP through the upstream proxy, absolute-URI form.

        Browsers still send these - revocation checks, http:// redirects - and
        dropping them would make the relayed arm quietly differ from the
        Playwright arm in what it could reach.
        """
        try:
            upstream = self._open_upstream()
        except OSError:
            with self._lock:
                self.failures += 1
            return
        lines = [ln for ln in head.split("\r\n")
                 if not ln.lower().startswith("proxy-authorization:")]
        rebuilt = lines[0] + "\r\n" + self._auth_header() \
            + "\r\n".join(lines[1:])
        try:
            upstream.sendall(rebuilt.encode())
        except OSError:
            with self._lock:
                self.failures += 1
            _close(upstream)
            return
        self._pump(client, upstream)

    def _count(self, *, up: int = 0, down: int = 0) -> None:
        with self._lock:
            self.up += up
            self.down += down

    def _pump(self, client: socket.socket, upstream: socket.socket) -> None:
        """Copy both ways until either end closes, counting every byte.

        Counted per chunk rather than totalled and posted when the tunnel ends.
        The difference is not tidiness: a browser holds its tunnel open across
        several attempts on keep-alive, so `since()` read between two attempts
        would return zero for every one of them and the whole page's traffic
        would land on whichever row happened to be current when the socket
        finally closed - or on none, if the session outlived the run. Measured
        2026-08-13: a completed HTTPS exchange through this relay reported
        `bytes_up: 0, bytes_down: 0` for exactly that reason.

        One lock acquisition per 64 KB is not worth avoiding.
        """
        client.settimeout(None)
        upstream.settimeout(None)
        try:
            while True:
                readable, _, broken = select.select(
                    [client, upstream], [], [client, upstream], IDLE_TIMEOUT)
                if broken or not readable:
                    break
                for source in readable:
                    data = source.recv(CHUNK)
                    if not data:
                        return
                    if source is client:
                        upstream.sendall(data)
                        self._count(up=len(data))
                    else:
                        client.sendall(data)
                        self._count(down=len(data))
        except OSError:
            pass
        finally:
            _close(upstream)


def _read_head(sock: socket.socket) -> str:
    """Read up to the end of the header block. Returns '' if the peer hung up."""
    buffer = b""
    while b"\r\n\r\n" not in buffer:
        try:
            chunk = sock.recv(CHUNK)
        except OSError:
            return ""
        if not chunk:
            return ""
        buffer += chunk
        if len(buffer) > 65536:
            return ""
    return buffer.decode("latin-1")


def _header_value(head: str, name: str) -> str:
    wanted = name.lower() + ":"
    for line in head.split("\r\n")[1:]:
        if line.lower().startswith(wanted):
            return line.split(":", 1)[1].strip()
    return ""


def _close(sock: socket.socket) -> None:
    try:
        sock.close()
    except OSError:
        pass
