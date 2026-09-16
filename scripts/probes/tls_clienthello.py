"""Every engine's JA4, read from a listener on this machine.

`tls_echo.py` answers the same question by asking `tls.peet.ws`. That has two
costs this probe does not pay. It needs a live host, so it cannot be run from a
machine behind a VPN gateway and it stops working whenever the echo does - the
notebook recorded the JA4 table as unavailable from 18.08 for exactly that
reason. And it reports on a connection nobody benchmarked, out of band, which
means an engine can be upgraded between the echo and the run.

This one opens a socket to itself. It accepts, reads the first TLS record, and
answers nothing at all: no certificate, no key, no second implementation of TLS
that could be wrong. The handshake fails a moment later and the engine reports
an error, which is expected and is not a result. The ClientHello is the entire
subject and it is the first thing on the wire.

**Nothing leaves this machine.** That is what makes it the right thing to run
when an engine is added or upgraded, and it is why `SENDS_REQUESTS` is False.

**The Chrome installed on this machine is measured in the same sweep and every
engine is reported against it.** Without that the output answers "how do these
engines differ from each other", which is a question nobody arrives with; with
it, it answers "does this engine look like a browser", which is. The reference
is measured rather than shipped as a constant because it is not one - it moved
between Chrome 149 and 151 inside one week of this repository's data, on the
browser build alone, so a literal in the source would go wrong within a release
and would keep printing while it was wrong. `--no-reference` skips it.

What a match does **not** mean is printed next to it, and the warning is the
load-bearing half. This compares one record. Two engines that share a JA4 are
still told apart by the JavaScript surface, the HTTP/2 SETTINGS and the header
order, and this repository's own matrix separates engines that share one. The
sound direction to read it is the negative: an engine that does not match is
distinguishable before it has sent a request, and nothing done in JavaScript
afterwards reaches that.

**The target is `localhost` and not `127.0.0.1`, and the difference is not
cosmetic.** A client sends no `server_name` extension to a bare IP, which flips
the JA4 SNI character to `i` and drops the extension count by one, so a
fingerprint taken that way cannot be compared against one taken against a real
host. Measured 2026-09-02 on this repository's `chromium` engine:

    localhost    t13d1516h2_8daaf6152771_806a8c22fdea
    127.0.0.1    t13i1515h2_8daaf6152771_806a8c22fdea

The first of those is character for character the value `tls.peet.ws` computed
for `patchright` over the real network on 2026-09-01, stored in
`data/runs/tls_echo_20260901T112110Z.jsonl`. That is the check that makes this
probe a replacement for the echo rather than a different measurement: a loopback
listener and a remote host read the same handshake, provided the engine is given
a name to resolve.

**One engine cannot be measured this way at all, and it is not a bug here.**
`obscura` refuses the navigation outright: `Access to localhost domain
'localhost' is not allowed`, measured 2026-09-02. That is its own SSRF guard and
it applies to the name as well as to the address, so there is no loopback URL
that reaches it. For that engine `tls_echo.py` against a real host is the only
route, and it needs a machine that is not behind the gateway.

What this cannot see, and `tls_echo.py` still can: everything the echo derives
that is not in the ClientHello - the HTTP/2 SETTINGS fingerprint, the Akamai
hash, the header order. Those need a server that completes a handshake and
answers. If those are wanted, the echo is still the tool, on a host that may
reach it.

Headless does not move the fingerprint. Measured 2026-09-02 on `chromium` and
`camoufox`, `--headful` and the default give the same JA4 character for
character. That is worth having as a measurement rather than an assumption,
because every other row in this repository is taken headless.

QUIC is also out of scope. This listener is TCP, so every fingerprint it records
begins `t`. An engine that reached a real target over HTTP/3 would present a
different one, and no row here should be read as evidence about that path.

Usage:
    python scripts/probes/tls_clienthello.py
    python scripts/probes/tls_clienthello.py --engines chromium,patchright
    python scripts/probes/tls_clienthello.py --headful
    python scripts/probes/tls_clienthello.py --channel chrome
    python scripts/probes/tls_clienthello.py --no-reference
    python scripts/probes/tls_clienthello.py --chrome-binary "C:/.../chrome.exe"
"""
import argparse
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nmbench import engines, tlsfp
from nmbench.sink import JsonlSink

# Read by `python -m nmbench` when it lists what a command costs. The listener is
# in this process and the engines are pointed at it, so no gateway is touched, no
# target sees anything, and no pool reputation is spent.
SENDS_REQUESTS = False

# A name that resolves to loopback on every platform without touching the hosts
# file. See the module docstring for why a bare address will not do.
TARGET_HOST = "localhost"

# Long enough for a cold browser start on a loaded machine, short enough that a
# broken engine does not hold the run. The engine is expected to fail; this is
# how long we wait for it to fail with its handshake already sent.
ATTEMPT_TIMEOUT = 30.0


def _is_our_own_socket(peer, listen_port: int):
    """Did this process open the connection that just arrived?

    True, False, or None when it could not be established.

    **A browser engine is a child process, so a handshake sent from this
    process is not the engine's, and one of them sends one.** Measured
    2026-09-02: `seleniumbase` produced 19 hellos and two fingerprints, and the
    earliest was `t13d1812h1_85036bcba153_b26ce05bbdd6` - character for
    character the `http` engine's, which is `requests` on OpenSSL. It is not a
    coincidence and it is not this probe's own traffic leaking in. SeleniumBase
    in UC mode replaces `driver.get` with `uc_special_open_if_cf`
    (`seleniumbase/core/browser_launcher.py:5897`), which calls `requests_get`
    at `:496` before the browser navigates, to decide whether the page is
    Cloudflare-protected. That request runs inside this process. Ordering alone
    cannot exclude it, because it goes first; the fingerprint cannot exclude it
    either, because it is exactly what the `http` engine is supposed to report.
    Only ownership of the socket separates them.

    Consequently this is applied to browser engines only. For `http` and
    `curl_cffi` the client *is* this process and its handshake is the answer.

    `psutil` is imported lazily and its absence is not an error: it is a
    transitive dependency here rather than a declared one, and a probe that
    stopped working when it went away would be a worse trade than one that
    reports None and refuses to guess.
    """
    try:
        import psutil
    except ImportError:
        return None
    try:
        # Our own process only. That needs no privilege on any platform, unlike
        # the system-wide table, and it is the whole question.
        for conn in psutil.Process().net_connections(kind="tcp"):
            if (conn.raddr and conn.laddr
                    and conn.raddr.port == listen_port
                    and conn.laddr.port == peer[1]):
                return True
        return False
    except Exception:
        return None


class HelloListener:
    """Accepts, reads one TLS record, answers nothing.

    Every connection is served on its own thread and closed as soon as a record
    is complete or the peer gives up. Deliberately not a `socketserver`: this has
    to be startable and stoppable around each engine in turn, and the whole of it
    is twenty lines.

    **One listener per engine, on its own port, and that is not tidiness.** The
    first version of this probe used a single listener for the whole run and
    cleared its list between engines. A browser that cannot complete a handshake
    retries, so an engine's connections can arrive after the call that started
    them has returned and after its session has been closed - `camoufox` sent 10
    and `seleniumbase` 19 on 2026-09-02, against 1 for a plain client. Anything
    arriving late landed in whichever engine's window was open at the time and
    was recorded under that engine's name. A fresh port per engine ends that: a
    straggler from the previous engine reaches a closed port and is refused by
    the kernel, so it cannot be attributed to anybody.
    """

    def __init__(self):
        self._socket = socket.socket()
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(16)
        self.port = self._socket.getsockname()[1]
        self._lock = threading.Lock()
        self.hellos = []
        self._running = True
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self):
        while self._running:
            try:
                conn, peer = self._socket.accept()
            except OSError:
                return
            # Asked here, while the connection is certainly established, and not
            # in the reader thread, which runs after it may have closed.
            own = _is_our_own_socket(peer, self.port)
            threading.Thread(target=self._read, args=(conn, own),
                             daemon=True).start()

    def _read(self, conn, own=None):
        conn.settimeout(ATTEMPT_TIMEOUT)
        buffer = b""
        try:
            while (not tlsfp.hello_is_complete(buffer)
                   and len(buffer) < tlsfp.MAX_HELLO_BYTES):
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buffer += chunk
                # A peer that opens a connection and sends something that is not
                # a handshake has nothing here to wait for.
                if buffer[0] != 0x16:
                    break
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass
        if tlsfp.hello_is_complete(buffer):
            with self._lock:
                self.hellos.append((time.time(), own, buffer))

    def collected(self) -> list:
        """`(arrival_time, own, record)` triples, oldest first.

        `own` is True when this process opened the connection, False when
        another process did, and None when that could not be established. The
        time is kept because arrival order is what picks the engine's real
        handshake out of the fallbacks that follow it.
        """
        with self._lock:
            return list(self.hellos)

    def close(self) -> None:
        self._running = False
        try:
            self._socket.close()
        except OSError:
            pass

    @property
    def url(self) -> str:
        return f"https://{TARGET_HOST}:{self.port}/"


class LoopbackTarget:
    """A `Target` with the shape the non-browser engines call, pointing here.

    Their fingerprint is taken through `session.fetch` and not by reaching into
    the client, so the handshake recorded is the one their real code path emits.
    `curl_cffi` in particular exists to imitate a browser at this layer, and a
    fingerprint taken from a client this probe constructed itself would be a
    statement about the probe.
    """

    name = "tls_clienthello"

    def __init__(self, url: str):
        self._url = url

    def url_for(self, _query: str) -> str:
        return self._url

    def url(self, _query: str) -> str:
        return self._url


def capture(engine_name: str, *, headful: bool, channel, chrome_binary=None):
    """Drive one engine at a listener of its own.

    Returns the `(arrival_time, record)` pairs it sent, the engine's version
    string, and whatever the navigation raised. The last of those is reported
    rather than discarded: a handshake failure is expected here and looks
    exactly like a name that would not resolve or a port the engine refused to
    open, and only the message separates them.
    """
    engine = engines.REGISTRY[engine_name]
    listener = HelloListener()
    failure = None
    try:
        opened = engines.get(engine_name).open(
            direct=True, preset="none", headless=not headful, channel=channel,
            chrome_binary=chrome_binary)
        with opened as session:
            if getattr(engine, "runs_script", False):
                page = session.new_page()
                try:
                    page.goto(listener.url, timeout=int(ATTEMPT_TIMEOUT * 1000))
                except Exception as exc:
                    # Expected: nothing answers, so the handshake fails. The
                    # ClientHello was sent before it could.
                    failure = f"{type(exc).__name__}: {exc}"
                finally:
                    try:
                        page.close()
                    except Exception:
                        pass
            else:
                # `fetch` swallows its own exceptions and reports them on the
                # row, so there is nothing to catch here.
                session.fetch(LoopbackTarget(listener.url), "tls")
            # The engine's socket work can outlive the call that started it.
            deadline = time.time() + 5
            while not listener.collected() and time.time() < deadline:
                time.sleep(0.05)
            return listener.collected(), getattr(session, "version",
                                                 None), failure
    finally:
        # Before the next engine starts, so a browser still retrying this
        # handshake is refused by the kernel instead of being recorded under
        # somebody else's name.
        listener.close()


def choose(hellos, *, browser: bool) -> dict:
    """Pick the engine's own handshake out of everything that arrived.

    Two rules, each measured rather than assumed, and both on 2026-09-02.

    **Drop what this process sent, for browser engines only.** A browser runs
    in a child process, so a hello from this one is somebody else's - and one
    engine reliably produces one. `seleniumbase` sent 19 hellos under two
    fingerprints and the earliest was `requests` on OpenSSL, because
    SeleniumBase's UC mode calls `requests_get` before navigating. See
    `_is_our_own_socket` for the code path. For `http` and `curl_cffi` the
    client *is* this process, so nothing is dropped and their fingerprint is
    the answer; the `http` engine is the control that this scoping is right.

    **Take the first of what is left, not the most common.** Nothing answers
    here, so a client that sends a second hello is reacting to this probe, and
    what it sends then is a fallback rather than what a working host would have
    seen. `camoufox` sent 10: the first carrying extension `0x001b`
    (`compress_certificate`) and nine more that dropped exactly that extension
    and nothing else. A majority rule would have recorded the fallback. The
    first is confirmed from outside - it is character for character what
    `tls.peet.ws` computed for camoufox over the real network, where the
    handshake completed and no fallback was ever triggered.

    `groups` is every distinct fingerprint with its count and first arrival, in
    arrival order, and it is returned whatever the verdict. A rule that picks
    one value has to leave the ones it discarded visible, or the next engine
    whose second client is not `requests` will be silently mis-recorded exactly
    as `seleniumbase` was.
    """
    kept = [h for h in hellos if not (browser and h[1] is True)]
    groups = {}
    for arrived, _own, record in kept:
        value, readable = tlsfp.ja4(record)
        if not value:
            continue
        groups.setdefault(value, {"tls_ja4": value, "ja4_r": readable,
                                  "hellos": 0, "first": arrived})["hellos"] += 1
    ordered = sorted(groups.values(), key=lambda g: g["first"])

    value, readable = None, None
    if ordered:
        value, readable = tlsfp.ja4(kept[0][2])
    if len(ordered) > 1 and browser and any(h[1] is None for h in kept):
        # `psutil` was unavailable, so a hello sent by this process cannot be
        # told from one sent by the engine. With a single fingerprint that does
        # not matter; with more than one the first could be either, and a wrong
        # value in this column is worse than a missing one because nothing
        # downstream can tell that it is wrong.
        value, readable = None, None
    return {
        "tls_ja4": value, "ja4_r": readable,
        "hellos": len(kept),
        "hellos_from_this_process": len(hellos) - len(kept),
        "distinct_ja4": len(ordered),
        "candidates": [{"tls_ja4": g["tls_ja4"], "hellos": g["hellos"]}
                       for g in ordered] if len(ordered) > 1 else None,
        "groups": ordered,
    }


# The engine and channel that stand in for "the browser a person has". The
# `chromium` engine with `--channel chrome` launches the Chrome installed on this
# machine rather than Playwright's bundled Chromium, and at this layer the
# launcher does not matter: the ClientHello comes out of the BoringSSL compiled
# into that binary. That is a claim with evidence rather than an assumption -
# five engines here drive Chrome through five different automation stacks with
# wildly different flag sets, and measured 2026-09-02 every one of them lands on
# the value its Chrome major version predicts. It is not a control, though. No
# run has launched the same binary with and without an automation stack, so what
# is measured is that these stacks agree, not that a hand-started Chrome agrees
# with them.
REFERENCE = ("chromium", "chrome")


def capture_reference(sink, availability: dict, *, headful: bool,
                      chrome_binary=None):
    """Measure the stock browser on this machine, in this sweep, at this listener.

    Returns `(ja4, version)`, or None when there is nothing to compare against -
    which is a normal outcome, not a failure of the sweep. A machine may have no
    Chrome installed, and a probe that refused to report the engines because of
    that would be throwing away the measurement it was asked for.

    Measured here and never shipped as a literal, because the value is not a
    constant. It moved between Chrome 149 and 151 inside one week of this
    repository's own data, on nothing but the browser build, so a reference
    baked into the source would go wrong within a browser release and go wrong
    silently - it would still print, and it would still look like a comparison.

    `chrome_binary` replaces the channel rather than joining it, and it has to:
    under a pin the engines are all running that binary, so a reference on some
    other Chrome would put every one of them in the `different` bucket for a
    reason that has nothing to do with automation. The reference is whatever the
    engines were asked to run.
    """
    name, channel = REFERENCE
    if chrome_binary:
        channel = None
    if availability.get(name):
        print(f"  {'reference':<16}SKIPPED, {availability[name]}")
        return None
    try:
        hellos, version, failure = capture(name, headful=headful,
                                           channel=channel,
                                           chrome_binary=chrome_binary)
    except Exception as exc:
        print(f"  {'reference':<16}FAILED, {type(exc).__name__}: {exc}")
        return None
    if not hellos:
        print(f"  {'reference':<16}no ClientHello arrived"
              + (f", navigation said {failure}" if failure else ""))
        return None
    chosen = choose(hellos, browser=True)
    row = {k: v for k, v in chosen.items() if k != "groups"}
    sink.write({"engine": name, "engine_version": version, "headful": headful,
                "channel": channel, "chrome_binary": chrome_binary,
                "reference": True, **row})
    source = ("the pinned binary" if chrome_binary
              else "Chrome as installed here")
    print(f"  {'reference':<16}{row['tls_ja4'] or 'NO VALUE':<38}"
          f"  ({source}, {version})")
    if not row["tls_ja4"]:
        return None
    return (row["tls_ja4"], version)


def compare_to_reference(results: dict, reference) -> dict:
    """Split the measured engines by whether they match the stock browser.

    The grouping this probe prints answers "how do these engines differ from
    each other". A developer arrives with a different question - "do I look like
    a browser" - and it is the same numbers turned ninety degrees: one of the
    groups is the stock browser's, and which one is the whole answer.

    `results` maps an engine name to its `(ja4, version)`; `reference` is the
    same pair for the browser, or None when there is none to compare against.
    """
    if not reference or not reference[0]:
        return None
    value = reference[0]
    same, different = [], []
    for name, (found, version) in sorted(results.items()):
        (same if found == value else different).append((name, found, version))
    return {"reference": reference, "same": same, "different": different}


def main() -> None:
    known = list(engines.REGISTRY)
    parser = argparse.ArgumentParser()
    parser.add_argument("--engines", default=",".join(known),
                        help=f"comma separated, from {known}")
    parser.add_argument("--headful", action="store_true",
                        help="open real windows. Measured 2026-09-02 on "
                             "chromium and camoufox, the JA4 is identical "
                             "either way; the flag exists so that stays "
                             "checkable rather than assumed")
    parser.add_argument("--channel", default=None,
                        help="browser build for the Chromium engines, for "
                             "example chrome or msedge. A different build is a "
                             "different BoringSSL and is a different JA4: "
                             "measured 2026-09-02, chromium moves from "
                             "..._806a8c22fdea on the bundled Chromium 151 to "
                             "..._d8a2da3f94cd on the installed Chrome 149")
    parser.add_argument("--chrome-binary", default=None, metavar="PATH",
                        help="run every engine that can take one on this "
                             "browser. This is the probe that says whether the "
                             "pin took: if it did, the engines that accepted it "
                             "collapse onto one JA4, and any that did not stay "
                             "where their bundled build put them. Engines that "
                             "cannot be pinned are skipped rather than measured "
                             "unpinned beside the others")
    parser.add_argument("--no-reference", action="store_true",
                        help="skip the stock-Chrome measurement the engines are "
                             "compared against. It costs one browser start, and "
                             "without it this reports how the engines differ "
                             "from each other but not whether any of them looks "
                             "like a browser")
    args = parser.parse_args()

    names = [n.strip() for n in args.engines.split(",") if n.strip()]
    unknown = [n for n in names if n not in engines.REGISTRY]
    if unknown:
        parser.error(f"unknown engines {unknown}, known: {engines.names()}")
    if args.chrome_binary and args.channel:
        parser.error("--channel and --chrome-binary both name the browser to "
                     "launch and the path wins silently, so a sweep passing "
                     "both would record a channel that decided nothing. See "
                     "scripts/benchmark.py for where that was read")
    if args.chrome_binary and not Path(args.chrome_binary).is_file():
        parser.error(f"--chrome-binary {args.chrome_binary!r} is not a file")

    availability = engines.report_availability()
    print(f"reading ClientHellos on a fresh loopback port per engine, "
          f"as https://{TARGET_HOST}:<port>/")
    print("nothing leaves this machine\n")

    sink = JsonlSink("tls_clienthello")
    # First, so that a machine with no Chrome says so before spending the sweep,
    # and so the value every later line is read against is already on screen.
    reference = None
    if not args.no_reference:
        reference = capture_reference(sink, availability, headful=args.headful,
                                      chrome_binary=args.chrome_binary)

    results = {}
    for name in names:
        if availability.get(name):
            print(f"  {name}: SKIPPED, {availability[name]}")
            continue
        engine = engines.REGISTRY[name]
        if args.headful and not getattr(engine, "supports_headful", False):
            print(f"  {name}: SKIPPED, this engine is headless only")
            continue
        if args.chrome_binary and not engine.supports_chrome_binary:
            # Skipped rather than run unpinned, because the whole point of the
            # sweep is that the JA4 follows the browser: a row measured on this
            # engine's own bundled build would sit in the same table as the
            # pinned ones and its distance from them would read as a property
            # of the library.
            print(f"  {name}: SKIPPED, cannot be pointed at a browser binary")
            continue
        try:
            hellos, version, failure = capture(
                name, headful=args.headful, channel=args.channel,
                chrome_binary=args.chrome_binary)
        except Exception as exc:
            print(f"  {name}: FAILED, {type(exc).__name__}: {exc}")
            continue
        if not hellos:
            # The navigation error is the whole diagnosis here. Every engine is
            # expected to fail; an engine that failed *without* sending failed
            # somewhere else, and the message says where.
            print(f"  {name}: no ClientHello arrived"
                  + (f", navigation said {failure}" if failure else ""))
            continue

        chosen = choose(hellos,
                        browser=bool(getattr(engine, "runs_script", False)))
        if chosen["hellos"] == 0:
            print(f"  {name}: {chosen['hellos_from_this_process']} hellos, "
                  f"all sent by this process, none by the engine")
            continue
        if not chosen["groups"]:
            print(f"  {name}: {chosen['hellos']} records arrived, none parsed")
            continue

        row = {k: v for k, v in chosen.items() if k != "groups"}
        if row["tls_ja4"]:
            results[name] = (row["tls_ja4"], version)
        sink.write({
            "engine": name, "engine_version": version,
            "headful": args.headful, "channel": args.channel,
            "chrome_binary": args.chrome_binary,
            "reference": False, **row,
        })
        note = ""
        if chosen["hellos_from_this_process"]:
            note += (f"  ({chosen['hellos_from_this_process']} sent by this "
                     f"process, dropped)")
        if chosen["distinct_ja4"] > 1:
            note += (f"  ({chosen['distinct_ja4']} distinct, first taken)"
                     if row["tls_ja4"]
                     else f"  ({chosen['distinct_ja4']} distinct, "
                          f"unattributable)")
        print(f"  {name:<16}{row['tls_ja4'] or 'NO VALUE':<38}{note}")
        if chosen["distinct_ja4"] > 1:
            start = chosen["groups"][0]["first"]
            for group in chosen["groups"]:
                print(f"    {group['tls_ja4']}  x{group['hellos']}  first at "
                      f"+{group['first'] - start:.3f}s")

    if not results:
        print("\nnothing captured")
        return

    against = compare_to_reference(results, reference)
    if against:
        value, browser_version = against["reference"]
        print("\n" + "=" * 72)
        print(f"the Chrome installed here ({browser_version}) sends")
        print(f"  {value}\n")
        print("same handshake as that Chrome, byte for byte:")
        for name, _found, engine_version in against["same"]:
            print(f"  {name:<16}{engine_version or ''}")
        if not against["same"]:
            print("  nothing")
        print("\na different handshake, so distinguishable before the request:")
        for name, found, engine_version in against["different"]:
            print(f"  {name:<16}{found:<38}{engine_version or ''}")
        if not against["different"]:
            print("  nothing")
        # The line that stops this being an indicator that lies. Everything
        # above is one record on the wire, and every engine here is told apart
        # by things that are not in it - this repository's own matrix separates
        # engines that share a JA4 exactly.
        print("\nmatching is not the same as being undetectable. This compares "
              "one\nrecord, the ClientHello. It says nothing about the "
              "JavaScript surface,\nthe HTTP/2 SETTINGS, the header order, or "
              "the timing, and those are\nwhat the engines above are actually "
              "separated by. Read it the other\nway instead: an engine that "
              "does *not* match is distinguishable before\nit has sent a "
              "request, and no amount of JavaScript patching reaches\nthat.")

    groups = {}
    for name, (value, _version) in results.items():
        groups.setdefault(value, []).append(name)
    print("\n" + "=" * 72)
    print("engines sharing a JA4 cannot be told apart at this layer, whatever")
    print("their pass rates say. A pass-rate gap between two of them has to be")
    print("explained somewhere else.\n")
    for value, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        print(f"  {value}  {', '.join(sorted(members))}")
    # The converse is the one that misleads, and it is not hypothetical here.
    # Measured 2026-09-02 with the `chromium` engine driving two builds and
    # nothing else varied: Playwright's bundled Chromium 151.0.7922.34 gives
    # `..._806a8c22fdea` and the installed Chrome 149.0.7827.201, reached with
    # `--channel chrome`, gives `..._d8a2da3f94cd`. Identical extension lists,
    # identical cipher hash; the whole difference is three signature algorithms
    # `0904,0905,0906` that the newer build offers and the older one does not.
    # So two engines in different groups above may be running identical code
    # and differ only in which browser they launched.
    print("\ntwo engines in different groups may still be the same engine on a")
    print("different browser build. Compare `engine_version` before reading a")
    print("split as a property of the engine: measured 2026-09-02, one engine")
    print("moved between two of these groups on a `--channel chrome` alone.")
    print(f"\nraw rows: {sink.path}")


if __name__ == "__main__":
    main()
