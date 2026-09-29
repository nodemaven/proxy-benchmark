"""How fast a session's exit is, and how to ask that on every run without
turning a large run into a bandwidth bill.

This exists because the study this harness is being asked to extend has a
throughput hypothesis and this corpus has nothing to answer it with. Every row
carries `elapsed_ms` for a page fetch, which is a browser rendering a target
under whatever that target was doing at the time - it is not a property of the
proxy, and averaging it over a run measures Amazon as much as it measures the
exit.

**Two measurements, and the reason they are separate is that one is free.**

The latency half is already being paid for. Every session opens a CONNECT to
resolve its exit, `gateway.open_tunnel` times the TCP handshake and the
gateway's own reply, and `identify` threw both away one line after receiving
them. Recording them adds no request and no byte. They are also two different
numbers: `tcp_ms` is the distance from this host to the gateway's front door and
says nothing about the exit, while `connect_ms` is what the gateway spent
getting from that door to an exit and opening a socket onward - which is where a
pool of live exits and a device dialled on demand differ, and nowhere in anyone's
marketing. Averaging them together would destroy the only interesting one.

The throughput half is new traffic and it does not scale the way the rest of a
run does. It is charged per session, not per request, and session counts in this
corpus run from 20 on a median run to 7627 on the largest - so a flat "one
megabyte per session" is 20 MB on a typical run and 7.6 GB on the big one,
measured 2026-09-28. A default that is harmless nineteen times out of twenty and
catastrophic the twentieth is not a default, it is a trap with a long fuse.

Hence the budget below. The download runs on the first N sessions of a run and
then stops, so the cost is bounded by N regardless of how large the run turns
out to be, and the rows that got a reading say so while the rest carry nothing
rather than a zero. N=20 at 1 MB is 20 MB against a run that already spends
251 MB on average - under a tenth - and it is the same 20 MB on a run a hundred
times larger.

What the bound costs, stated because it is a real limitation and not a
formality: the sampled sessions are the FIRST N of a run, not N drawn at random
from it. If throughput drifts over a long run - a pool degrading, a provider
throttling after some volume - this measures the good half and cannot see the
drift. It is the right trade for a default whose job is to be always-on and
cheap, and it is the wrong instrument for the question "does this provider slow
down after an hour". That question needs the cap lifted, which is what the flag
is for.
"""
import time

import requests

# Cloudflare's own speed-test endpoint, which returns exactly the number of
# bytes asked for. A fixed, exactly-sized body is the whole point: a real file
# on a real CDN changes size when someone republishes it, and then a throughput
# series has a step in it that looks like a provider event.
#
# It is not a neutral choice and that is worth saying next to the number. This
# measures the path from the exit to Cloudflare, so a provider whose peers sit
# close to Cloudflare will read faster than one whose peers do not, and that is
# a property of the route rather than of the proxy. It is consistent across
# providers within a run, which is what makes the comparison fair; it is not an
# absolute a customer would reproduce against their own target.
DOWNLOAD_URL = "https://speed.cloudflare.com/__down"
DEFAULT_BYTES = 1024 * 1024
DEFAULT_SESSIONS = 20


class Budget:
    """How many more sessions this run may spend a download on.

    A counter rather than a flag because the thing being bounded is cost, and
    cost is a count. `limit=0` turns the download off entirely and `limit=None`
    lifts the cap, which is the arm for anyone actually studying throughput
    rather than carrying it along.

    Not thread-safe on purpose: the runner opens one session at a time, and a
    lock here would be a claim about the runner that this module cannot keep.
    If that ever stops being true this is the line that has to change, so it is
    written down rather than left to be discovered by a budget that silently
    overspends.
    """

    def __init__(self, limit: int | None = DEFAULT_SESSIONS):
        self.limit = limit
        self.spent = 0

    def take(self) -> bool:
        """Claim one session's worth of budget, or refuse."""
        if self.limit is not None and self.spent >= self.limit:
            return False
        self.spent += 1
        return True

    def describe(self) -> str:
        if self.limit == 0:
            return "speed test off"
        if self.limit is None:
            return f"speed test on every session, {self.spent} so far"
        return (f"speed test on the first {self.limit} sessions, "
                f"{self.spent} used")


def measure(provider=None, size: int = DEFAULT_BYTES, timeout: int = 60,
            url: str = DOWNLOAD_URL, direct: bool = False,
            params: dict | None = None) -> dict:
    """Pull a fixed-size body through one session and time it.

    Returns a dict whose keys are all prefixed `speed_`, so the result can be
    merged straight onto an attempt row without colliding with anything the
    engines write.

    The body is streamed and counted rather than trusted: `speed_bytes` is what
    arrived, not what was asked for. A gateway that truncates, or a transfer cut
    short by a timeout, would otherwise be recorded as a fast download of the
    full size - the failure and the good case produce the same number unless the
    arrival is counted. `speed_short` marks that case rather than hiding it, and
    the rate is still reported, because a truncated transfer has a real rate and
    throwing it away would lose the evidence that the truncation happened at all.

    The clock starts before the request and not after the headers. That includes
    connection setup and the gateway's own dial in the figure, which is the
    conservative direction: it makes every provider look slower by the same kind
    of constant and it cannot make a slow provider look fast. `speed_ttfb_ms` is
    carried separately for anyone who wants the transfer rate without the setup.

    The gateway parameters arrive as an explicit `params` dict and NOT as
    `**params`, which breaks the convention every neighbour here follows -
    `gateway.echo`, `gateway.exit_ip`, `proxy.proxy_url` all splat. The reason is
    that this function has knobs of its own and those neighbours mostly do not.
    With `**params`, a provider defining a parameter named `size`, `url`,
    `timeout` or `direct` would have it bound to the argument by Python itself:
    the download would be silently retuned, the parameter would never reach the
    gateway, and *the function cannot detect it* - by the time the body runs, the
    name is sitting in the argument and `params` is empty, so there is nothing
    left to test. A guard was written for this and deleted for that reason; it
    could not fire. An explicit dict is the only shape that removes the trap
    rather than documenting it.

    Not hypothetical at the edges: the fourteen parameters defined across the
    providers here include `speed`, `type` and `filter`, so the namespace is
    already busy and is set by TOML files rather than by this module.
    """
    from nmbench import proxy  # local: avoids a cycle

    params = dict(params or {})
    result = {"speed_bytes": None, "speed_ms": None, "speed_kbps": None,
              "speed_ttfb_ms": None, "speed_asked": size, "speed_short": None,
              "speed_url": url, "speed_direct": direct, "speed_error": None}
    started = time.perf_counter()
    try:
        # The direct arm is measured too, and it is not a throwaway: it is the
        # only figure in a run that says what this host and this line can do
        # with no proxy in the way. Without it a slow provider and a slow
        # afternoon on the VPS produce the same column, and the run has no
        # control to tell them apart.
        proxies = None
        if not direct:
            proxy_url = proxy.proxy_url(provider=provider, **params)
            proxies = {"http": proxy_url, "https": proxy_url}
        response = requests.get(
            url, params={"bytes": size}, stream=True, timeout=timeout,
            proxies=proxies)
        result["speed_ttfb_ms"] = round(
            (time.perf_counter() - started) * 1000, 1)
        response.raise_for_status()
        got = 0
        for chunk in response.iter_content(65536):
            got += len(chunk)
        elapsed = time.perf_counter() - started
        result["speed_bytes"] = got
        result["speed_ms"] = round(elapsed * 1000, 1)
        result["speed_short"] = got < size
        if elapsed > 0:
            result["speed_kbps"] = round(got * 8 / 1000 / elapsed, 1)
    except Exception as exc:
        # The class and the message, never the request: `proxy_url` carries the
        # credentials and `requests` puts the URL it was given into several of
        # its own exception messages. This tree has leaked a credential twice,
        # once through a header and once through a response body, and an
        # exception string is the third surface nobody looks at.
        result["speed_error"] = f"{type(exc).__name__}: {_scrub(exc, provider)}"
    return result


def _scrub(exc: Exception, provider) -> str:
    """The message with any credential-bearing proxy URL taken out of it.

    Written as a whitelist of what to keep rather than a blacklist of what to
    remove, because a blacklist has to anticipate every shape `requests` might
    print the URL in, and the cost of missing one is a password in a run log
    that gets read aloud later.
    """
    text = str(exc)
    from nmbench import config, providers
    try:
        creds = config.credentials(provider or providers.load())
    except Exception:
        return text
    for secret in (creds.password, creds.login):
        if secret:
            text = text.replace(secret, "<redacted>")
    return text
