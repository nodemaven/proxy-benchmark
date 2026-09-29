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

    `echo_ms` is the whole round trip: this host to the gateway, the gateway to
    an exit, the exit to the echo service and back. It is deliberately NOT
    reported as `connect_ms`, which the other path produces, because the two
    measure different distances and a column that silently holds either one
    would make a provider look faster or slower according to which backend
    happened to answer its CONNECT. Kept under its own name so a reader can see
    which instrument produced the number.
    """
    result = {"exit_ip": None, "org": None, "country": None, "region": None,
              "city": None, "timezone": None, "source": "echo",
              "echo_ms": None, "error": None}
    started = time.perf_counter()
    try:
        url = proxy.proxy_url(provider=provider, **params)
        resp = requests.get(ECHO_URL, proxies={"http": url, "https": url},
                            timeout=timeout)
        result["echo_ms"] = round((time.perf_counter() - started) * 1000, 1)
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


# Same latching as `_header_misses` above and for the same reason: a lookup
# service that is unreachable is unreachable for the whole process, and paying
# its timeout once per identity would slow a run down without adding a column.
_locate_misses = 0
_LOCATE_GIVE_UP = 3
_locate_cache = {}


def locate(ip: str, timeout: int = 5) -> dict:
    """Country, ASN and zone for an address, asked from *this* machine.

    The point of the whole function is the direction of the request. `echo`
    answers the same questions by sending a request *through* the session,
    which is a page fetch the exit performs before the browser opens - and this
    harness is measuring what a warmed exit does, so a request through the exit
    is warming. Paying it per identity would put a navigation into the treatment
    of every arm; paying it in some arms only, which is what the aligned-cell
    branch in `probe_and_hold` has to do, puts it into the comparison.

    Asking about the address instead of from it costs the exit nothing, so it
    can be done for every identity in every arm unconditionally. That is what
    makes the answer usable: `exit_timezone`, sourced from `identify`, is
    present on 56 of the 117 probes of `probehold_20260827T201123Z`, and its
    presence is not random - those identities were served 14% of the time
    against 28% for the ones without it, because the fallback that produces the
    column also happens on the sessions that behave differently. A per-country
    table drawn from that column is drawn from a biased half of the run. This
    one has no such half.

    Anonymous ipinfo is rate limited - 1000 lookups a day, measured against the
    published limit and not against a run - so the answers are cached by
    address. Exits repeat inside a run.

    Never raises. A missing country is a missing column; it is not a reason to
    lose an identity that is otherwise fine.
    """
    global _locate_misses
    if not ip:
        return {"country": None, "asn": None, "timezone": None}
    if ip in _locate_cache:
        return dict(_locate_cache[ip])
    if _locate_misses >= _LOCATE_GIVE_UP:
        return {"country": None, "asn": None, "timezone": None}
    try:
        body = requests.get(f"https://ipinfo.io/{ip}/json", timeout=timeout).json()
        # `org` arrives as "AS7922 Comcast Cable Communications, LLC" - the
        # number and the name in one string. Kept whole: splitting it here would
        # make the column depend on a format this repository does not control,
        # and every analysis that has wanted it so far has wanted the name.
        found = {"country": body.get("country"), "asn": body.get("org"),
                 "timezone": body.get("timezone")}
        _locate_misses = 0
        _locate_cache[ip] = found
        return dict(found)
    except Exception:
        _locate_misses += 1
        return {"country": None, "asn": None, "timezone": None}


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
                # The two timings are carried out rather than dropped, and that
                # is the whole of the free half of the speed measurement: this
                # CONNECT is opened by every session anyway and `open_tunnel`
                # has already timed it. Until 2026-09-28 they were discarded on
                # this line, which is why they sit on 486 of 21891 rows on disk -
                # the few written by probes that call `open_tunnel` themselves.
                # A latency series existed in the code and nowhere in the corpus.
                return {"exit_ip": probe["exit_ip"], "org": None,
                        "country": None, "source": "connect-header",
                        "tcp_ms": probe.get("tcp_ms"),
                        "connect_ms": probe.get("connect_ms"),
                        "error": None}
            _header_misses[provider.id] = misses + 1

    return echo(provider=provider, **params)


# Why a re-check cannot be `identify`, which already answers this question.
#
# `identify` falls back to `echo`, and `echo` sends a real request *through the
# session*. `locate`'s docstring states the consequence for this harness: a
# request through the exit is warming, so an exit re-checked by echo has been
# given a page fetch the unchecked ones did not get. Calling it once at the top
# of a session is already part of the treatment and is the same in every arm.
# Calling it again in the middle would put an extra navigation into the middle of
# the batch being measured - the instrument would be changing the thing it is
# reading, and it would do so on exactly the sessions where the header is absent,
# which are not a random half. `identify` itself measured that: `exit_timezone`
# is present on 56 of 117 probes of `probehold_20260827T201123Z` and those
# identities were served 14% against 28%.
#
# So the re-check is header-only and returns "not measured" rather than paying
# for an answer. That is the trade and it is the right way round: a missing
# column is a gap a reader can see, and a warmed exit is a number that looks fine
# and is about a different experiment.
RECHECK_NO_HEADER = "provider defines no exit-ip header"
RECHECK_NOT_OFFERED = "backend answered without the header"
RECHECK_GAVE_UP = "header source latched off for this provider"


def recheck_exit(provider=None, **params) -> dict:
    """Read the exit address of a live session again, without warming it.

    Returns `{"exit_ip": str | None, "recheck_source": str,
    "recheck_reason": str | None}`. `exit_ip` is None whenever the address could
    not be read, and `recheck_reason` says which of the ways that happened.

    **None is not "rotated".** The caller has to keep the two apart: a direct
    cell, a provider with no header, and a backend that answered without one all
    produce None, and none of them is evidence that the session moved. A column
    that folded them together would report rotation on precisely the providers
    this harness can say the least about.

    It does not touch `_header_misses`. It reads the latch, so a provider whose
    header has been given up on is not charged a handshake per check, but it
    never increments it - a re-check must not be able to change how `identify`
    behaves on the next session, because `identify`'s fallback costs traffic and
    the cost model of a run would then depend on how many re-checks it ran.
    `identify` runs once per session and latches on its own, so nothing is lost.

    Limitation, stated because it cannot be fixed from here: `open_tunnel` opens
    its own TCP connection to the gateway. On a sticky session that connection is
    itself activity, so the check may refresh whatever TTL the gateway keeps. It
    reads the session without fetching a page; it does not read it without being
    seen.
    """
    provider = provider or providers.load()
    result = {"exit_ip": None, "recheck_source": "connect-header",
              "recheck_reason": None}

    if not provider.exit_ip_header:
        result["recheck_source"] = "none"
        result["recheck_reason"] = RECHECK_NO_HEADER
        return result
    if _header_misses.get(provider.id, 0) >= _HEADER_GIVE_UP:
        result["recheck_source"] = "none"
        result["recheck_reason"] = RECHECK_GAVE_UP
        return result

    probe = exit_ip(provider=provider, **params)
    if probe.get("exit_ip"):
        result["exit_ip"] = probe["exit_ip"]
        return result
    result["recheck_reason"] = probe.get("error") or RECHECK_NOT_OFFERED
    return result


def summarise_identity(points) -> dict:
    """Turn a session's identity readings into the columns a row carries.

    `points` is an ordered sequence of `(when, exit_ip_or_None)`. `when` is a
    label - "start", "middle", "end" - and the order is the order they were
    taken in.

    Returns `identity_points`, `identity_read`, `identity_stable` and
    `identity_changed_at`.

    **The rule that decides everything here: unread is not unchanged.** A
    provider with no header, a backend that answered without one and a refused
    handshake all produce None, and none of them is evidence the session held.
    So `identity_stable` is:

      - True  - two or more addresses were read and they all agree
      - False - two or more were read and at least one differs
      - None  - fewer than two were read, so the question was not answered

    A boolean would have to fold the third case into one of the first two, and
    both foldings are wrong in a way that matters. Folding into True claims
    stability for every provider whose gateway does not offer the header, which
    is the population this harness can say the least about and would then appear
    to say the most about. Folding into False charges an instrument gap to the
    provider. Three states, and the count of reads beside them so a reader can
    see how much the verdict rests on.

    `identity_changed_at` names the first label whose address differs from the
    first one read, or None. It is deliberately the *first* divergence and not a
    count of distinct addresses: with three points the distinction only shows up
    on a session that moved twice, and "when did it first stop being the same
    session" is the question a sticky-session claim is about.
    """
    read = [(when, ip) for when, ip in points if ip]
    summary = {"identity_points": len(points), "identity_read": len(read),
               "identity_stable": None, "identity_changed_at": None}
    if len(read) < 2:
        return summary
    first = read[0][1]
    summary["identity_stable"] = True
    for when, ip in read[1:]:
        if ip != first:
            summary["identity_stable"] = False
            summary["identity_changed_at"] = when
            break
    return summary
