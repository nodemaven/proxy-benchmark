"""Raw CONNECT probe against the proxy gateway.

Some gateways report the exit IP in a header on the CONNECT reply, so on those
the address costs one handshake and no target traffic at all. Which header, or
whether there is one, is a property of the provider and is read from its
definition. It is not guaranteed even where it exists - one reply arrived without
it and with a differently cased reason phrase - so callers must handle
`exit_ip is None`, and where the provider names no header the echo service is the
only source and costs a real request.

The probe opens its own TCP connection. It therefore reports the exit IP that
the sticky session resolved to at probe time, which is evidence about the
session, not a guarantee about the connection the next request will use.
"""
import base64
import socket
import time

import requests

from . import config, providers, proxy

PROBE_HOST = "ipinfo.io"
PROBE_PORT = 443
ECHO_URL = "https://ipinfo.io/json"


def open_tunnel(host: str = PROBE_HOST, port: int = PROBE_PORT,
                timeout: int = 20, strict: bool = True, provider=None, **params):
    """Establish a CONNECT tunnel and hand the open socket to the caller.

    Returns `(sock, info)`. The caller owns the socket and must close it; `sock`
    is None when the tunnel was refused or never came up.

    The two timings are reported separately because they answer different
    questions. `tcp_ms` is the handshake with the gateway itself, so it measures
    the distance to the front door. `connect_ms` is what the gateway took to
    answer, which includes whatever it did to reach an exit and open a socket
    onward. A provider that keeps a pool of live exits and one that dials a
    device on demand differ here, and nowhere in their marketing.

    Credentials are built here and never returned or logged.
    """
    provider = provider or providers.load()
    creds = config.credentials(provider)
    username = proxy.build_username(creds.login, strict=strict, provider=provider,
                                    **params)
    auth = base64.b64encode(f"{username}:{creds.password}".encode()).decode()

    request = (
        f"CONNECT {host}:{port} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Proxy-Authorization: Basic {auth}\r\n"
        f"Proxy-Connection: Keep-Alive\r\n"
        f"\r\n"
    )

    info = {"status": None, "reason": None, "exit_ip": None, "error": None,
            "tcp_ms": None, "connect_ms": None, "elapsed_ms": None}

    started = time.perf_counter()
    sock = None
    try:
        sock = socket.create_connection((creds.host, creds.port), timeout=timeout)
        sock.settimeout(timeout)
        info["tcp_ms"] = round((time.perf_counter() - started) * 1000, 1)

        asked = time.perf_counter()
        sock.sendall(request.encode())

        buffer = b""
        while b"\r\n\r\n" not in buffer:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buffer += chunk
            if len(buffer) > 65536:
                break
        info["connect_ms"] = round((time.perf_counter() - asked) * 1000, 1)

        head = buffer.split(b"\r\n\r\n", 1)[0].decode("utf-8", errors="replace")
        lines = head.split("\r\n")

        if lines and lines[0].startswith("HTTP/"):
            bits = lines[0].split(" ", 2)
            info["status"] = int(bits[1]) if len(bits) > 1 else None
            info["reason"] = bits[2] if len(bits) > 2 else ""

        wanted = provider.exit_ip_header.lower()
        for line in lines[1:] if wanted else ():
            if ":" in line:
                key, value = line.split(":", 1)
                if key.strip().lower() == wanted:
                    info["exit_ip"] = value.strip()

        if info["status"] != 200:
            sock.close()
            sock = None
    except Exception as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
        if sock is not None:
            sock.close()
            sock = None
    info["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
    return sock, info


def exit_ip(host: str = PROBE_HOST, port: int = PROBE_PORT,
            timeout: int = 20, strict: bool = True, provider=None,
            **params) -> dict:
    """Open a CONNECT to the gateway, read the reply headers, close.

    Returns the status line, the exit IP if the gateway offered one, and never
    the credentials used to get them.
    """
    sock, info = open_tunnel(host, port, timeout, strict, provider, **params)
    if sock is not None:
        sock.close()
    return info


def echo(timeout: int = 30, provider=None, **params) -> dict:
    """Ask an echo service which address the target sees.

    Costs a real request through the session - around 330 bytes - and returns
    the operator, the region and the timezone the address belongs to, none of
    which the CONNECT header carries. The timezone is what a geoip-configured
    browser has to agree with.
    """
    result = {"exit_ip": None, "org": None, "country": None, "region": None,
              "city": None, "timezone": None, "source": "echo", "error": None}
    try:
        url = proxy.proxy_url(provider=provider, **params)
        resp = requests.get(ECHO_URL, proxies={"http": url, "https": url},
                            timeout=timeout)
        body = resp.json()
        result.update({k: body.get(k) for k in
                       ("org", "country", "region", "city", "timezone")})
        result["exit_ip"] = body.get("ip")
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


# Consecutive CONNECT replies that carried no exit IP. Retrying a source that is
# genuinely unavailable costs a handshake on every attempt, so it is given up on -
# but not on the first miss, which is what this counter exists to prevent.
#
# Measured 2026-08-12 over 17 opened tunnels: there are two implementations
# behind the name, and they are told apart by the reason phrase. Every reply
# reading `Connection established` carried the header and every reply reading
# `OK` carried none, with no exception either way. So a miss is which backend
# answered, not whether the feature exists, and latching on the first one threw
# away a free source for the rest of the process and paid an echo request per
# session instead.
#
# Counted per provider. A matrix interleaves providers in one process, so a
# single counter would let one gateway that never sends the header spend the
# other's allowance and push every cell onto the paid echo request.
_header_misses = {}
_HEADER_GIVE_UP = 5


class ExitRegistry:
    """Turns exit addresses into something publishable.

    `data/runs/` is committed, and these are the home addresses of real people
    whose devices carry the pool - and, for the direct control, the operator's
    own address. A /24 prefix plus a label assigned in order of first appearance
    keeps what the analysis needs: how many distinct exits were seen, and which
    network each belonged to. The full address stays on the console.
    """

    def __init__(self):
        self.labels = {}

    def record(self, ip: str) -> dict:
        if not ip:
            return {"exit_prefix": None, "exit_label": None}
        if ip not in self.labels:
            self.labels[ip] = f"exit_{len(self.labels) + 1}"
        if ":" in ip:
            prefix = ":".join(ip.split(":")[:3]) + "::/48"
        else:
            prefix = ".".join(ip.split(".")[:3]) + ".0/24"
        return {"exit_prefix": prefix, "exit_label": self.labels[ip]}


def identify_direct(timeout: int = 30) -> dict:
    """Which address the target sees when no proxy is involved.

    The direct control only means something if the network it ran on is on the
    record: a clean residential line and a VPN exit are different experiments
    with the same command.
    """
    result = {"exit_ip": None, "org": None, "country": None,
              "source": "echo-direct", "error": None}
    try:
        resp = requests.get(ECHO_URL, timeout=timeout)
        body = resp.json()
        result["exit_ip"] = body.get("ip")
        result["org"] = body.get("org")
        result["country"] = body.get("country")
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def identify(provider=None, **params) -> dict:
    """Exit address for a session, cheapest source first.

    The CONNECT reply header is free but not guaranteed, and which backend
    answers decides it, so roughly half of the attempts get it. When it is
    absent the echo service is the fallback, and it is the only source that also
    names the operator, so step 2 asks for it explicitly. A provider whose
    definition names no header skips straight to the echo request, which is the
    honest thing for its cost estimate: on that provider an exit address is a
    request through the session rather than a free header.
    """
    provider = provider or providers.load()

    if provider.exit_ip_header:
        misses = _header_misses.get(provider.id, 0)
        if misses < _HEADER_GIVE_UP:
            probe = exit_ip(provider=provider, **params)
            if probe["exit_ip"]:
                _header_misses[provider.id] = 0
                return {"exit_ip": probe["exit_ip"], "org": None, "country": None,
                        "source": "connect-header", "error": None}
            _header_misses[provider.id] = misses + 1

    return echo(provider=provider, **params)
