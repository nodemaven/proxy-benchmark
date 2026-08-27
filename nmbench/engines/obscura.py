"""Obscura: a from-scratch browser in Rust, driven over CDP.

Not a fork. Obscura embeds V8 and renders on its own, so it is neither the
Chromium nor the Firefox family, and its anti-detection works by randomising
fingerprints in the running page rather than by patching a binary. That makes it
a third class of engine rather than a second implementation of the same idea,
and a table putting it beside Camoufox has to say so or it will be read as a
like-for-like comparison it is not.

Two properties of the process shape this adapter.

The proxy is process-global and fixed when `obscura serve` starts: there is no
per-context proxy. One session therefore means one process, started and stopped
around a batch of queries, which happens to be exactly the unit the benchmark
measures anyway.

A proxy URL the binary cannot parse is ignored and the browser connects
directly, with no error anywhere (`if let Ok(p) = Proxy::all(..)`). That failure
writes the operator's own address into rows labelled as pool exits and would
never be noticed. Credentials are passed through the environment rather than the
command line - the argv of a running process is readable by any local user - and
the caller is expected to verify the address the browser actually leaves from
before trusting a batch. See `exit_ip`.

Disclosure for the report: NodeMaven, the provider this harness was first
pointed at, is a paid sponsor listed in Obscura's README. That does not make the
measurements wrong, but a benchmark that scores a sponsor's product using a
sponsored tool has to declare the relationship.

Verified 2026-08-11 against a running binary, obscura 0.2.0 on Windows x86_64:
`serve --port N --stealth` is accepted, the CDP endpoint answers on
`/json/version` and reports Chrome/145.0.0.0 with V8 14.5.

Two things that verification turned up and the source alone did not.

The stealth transport ships only in the `-stealth` release archives. The plain
archive accepts `--stealth` and runs without TLS impersonation, so the flag is
not evidence that the feature is present - the build is. Install from
`obscura-<arch>-<os>-stealth`, and note that the `no-render` archives have no
renderer and cannot run this benchmark at all.

The CDP banner on `/json/version` reports `X11; Linux x86_64` even on Windows,
while the page itself reports the host correctly (`Windows NT 10.0; Win64; x64`,
Chrome/145). The banner is therefore build metadata, not the identity a target
sees, and it must not be quoted in the report as a fingerprint defect - only a
local CDP client can read it.

That conclusion was drawn from where the string was found, which was not enough:
the banner and `navigator.userAgent` are both read through CDP, so neither can
see what went out on the socket, and upstream reports this build leaking the same
`X11; Linux x86_64` in its request header. `probes/obscura_defects.py` closes it
by reading the header off a server on this machine and comparing it against the
page. Measured 2026-08-18 under `--stealth`: byte identical, both Windows. So no
`--user-agent` override is passed below, and the flag exists if a future build
reintroduces the split - a browser whose header and script disagree about the
operating system is refused by a detector that runs before any script does.

What a target can see, measured on about:blank: this browser exposes no WebGL
context at all. `webgl_renderer` is null where every other engine returns a
string. Missing an API that all real browsers implement is itself a signal, and
it is a different kind of exposure from reporting a wrong value.
"""
import json
import os
import shutil
import socket
import subprocess
import time
from contextlib import contextmanager

from .. import blocking, providers, proxy
from .base import (
    ENTRY_TIMEOUT_MS,
    EngineUnavailable,
    await_ready,
    blank_row,
    entry_row_url,
    keep_body,
    record_error,
    record_judgement,
    run_search,
    validate_preset,
)

BINARY = os.environ.get("OBSCURA_BINARY", "obscura")
READY_TIMEOUT_S = 30


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_cdp(port: int, process, timeout: int = READY_TIMEOUT_S) -> None:
    """Block until the CDP endpoint answers, or explain why it never will."""
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise EngineUnavailable(
                f"obscura exited with code {process.returncode} before opening "
                f"a CDP port, so nothing was measured"
            )
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/version", timeout=1) as resp:
                json.load(resp)
                return
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(0.2)
    raise EngineUnavailable(
        f"obscura did not answer on port {port} within {timeout}s"
    )


def _terminate_tree(process) -> None:
    """Stop the server and the worker processes it spawned.

    `obscura serve` runs its renderers in separate `obscura-worker` processes.
    Terminating only the parent leaves those workers alive, and a matrix run
    opens one session per batch: a few hundred orphans at roughly 70 MB of
    image each, on the same machine that is supposed to be producing timings.
    Slow measurements caused by our own leaked processes would be recorded as
    slow engines.
    """
    if process.poll() is not None:
        return
    if os.name == "nt":
        # No POSIX process groups here, and the workers are not our direct
        # children, so ask the OS to walk the tree.
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                       capture_output=True)
    else:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


class ObscuraSession:
    def __init__(self, browser, *, preset, direct, params, version,
                 ready_timeout_ms: int = 8000, store=None, provider=None):
        self.browser = browser
        self.store = store
        self.preset = preset
        self.direct = direct
        self.params = params
        # The provider whose dialect built this session's username, None on the
        # direct arm. On the session rather than filled in by the runner so the
        # column reports what was used and not what was asked for.
        self.provider = provider
        self.version = version
        self.ready_timeout_ms = ready_timeout_ms
        self.index = 0
        self.session_exit_prefix = None

    def new_page(self, counter: dict = None):
        """A page with this session's blocking and byte counter installed.

        Part of the engine contract rather than a private helper: probes need a
        page without knowing which engine they hold, and the alternative is code
        that branches on an engine name.
        """
        counter = {} if counter is None else counter
        context = (self.browser.contexts[0] if self.browser.contexts
                   else self.browser.new_context())
        page = context.new_page()
        blocking.install(page, self.preset, counter)
        blocking.install_counter(page, counter)
        return page

    def exit_ip(self) -> str:
        """The address the browser itself leaves from.

        This is the check that a silently ignored proxy cannot survive. It costs
        one small request and it is the only thing standing between a
        misconfigured run and a data file that quietly claims the operator's home
        address was a residential exit.
        """
        page = self.new_page()
        try:
            page.goto("https://ipinfo.io/json", wait_until="domcontentloaded",
                      timeout=30000)
            body = page.evaluate("() => document.body.innerText")
            return json.loads(body).get("ip")
        except Exception:
            return None
        finally:
            page.close()

    def _row(self, target, query: str, url: str) -> dict:
        """The columns that describe this session, whatever the entry shape.

        Factored out so `fetch` and `search` cannot drift: the label expression
        is what identifies an engine in every table, and two entry paths writing
        it two ways would split one engine into two columns nobody could pair up
        again.
        """
        row = blank_row(
            f"obscura-direct/{self.preset}" if self.direct
            else f"obscura/{self.preset}",
            self.version, query, url,
            target=getattr(target, "name", None),
            direct=self.direct, preset=self.preset,
            params={} if self.direct else dict(self.params),
            provider=getattr(self.provider, "id", None),
            headless=True, humanize=False,
            session_index=self.index,
            session_exit_prefix=self.session_exit_prefix,
        )
        self.index += 1
        return row

    def search(self, page, target, query: str, *, rng,
               counter: dict = None) -> dict:
        """One query typed into the target's own box, on a page held open.

        Part of the engine contract beside `fetch`, so a probe can choose the
        entry shape without knowing which engine it holds. Everything after the
        keystroke is `run_search`, shared with the other Playwright engines -
        this browser is driven over CDP through Playwright, so it gets the
        identical client rather than an imitation of one, which is what makes
        its column in an entry comparison readable beside the others.

        The status this path records comes from `submit_query`'s navigation
        response and not from `_watch_navigation_status`, so it carries the same
        redirect caveat: see that function. `probes/obscura_defects.py` covers
        the `fetch` path only, and a typed row landing on `/sorry/` is the case
        to re-check by hand if this engine is ever run through the entry axis in
        anger.
        """
        validate_preset(self.preset, target)
        row = self._row(target, query, entry_row_url(target, query))
        return run_search(page, target, query, row, rng=rng,
                          ready_timeout_ms=self.ready_timeout_ms,
                          store=self.store, counter=counter)

    def fetch(self, target, query: str) -> dict:
        validate_preset(self.preset, target)
        url = target.url(query)
        row = self._row(target, query, url)

        counter = {}
        page = self.new_page(counter)
        navigation = _watch_navigation_status(page)
        started = time.perf_counter()
        try:
            response = page.goto(url, wait_until="domcontentloaded",
                                 timeout=ENTRY_TIMEOUT_MS)
            row["status"] = response.status if response else navigation["status"]
            row["ready"] = await_ready(page, target, self.ready_timeout_ms)
            html = page.content()
            row["html_len"] = len(html)
            record_judgement(row, target, page.url, page.title(), html)
            keep_body(self.store, row, html)
        except Exception as exc:
            record_error(row, exc)
        finally:
            row["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
            row["bytes"] = counter.get("bytes", 0)
            row["blocked"] = counter.get("blocked", 0)
            row["allowed"] = counter.get("allowed", 0)
            page.close()
        return row


def _watch_navigation_status(page) -> dict:
    """Record the status of the main-frame document response.

    `page.goto` hands back None on this browser, so `status` was null on every
    Obscura row while the other engines carried the real code: measured
    2026-08-12 on obscura 0.2.0 against a local server answering 200 and 503,
    both reported as None by goto and correctly by the response event. A column
    that is empty for one engine and full for the rest is worse than an absent
    column, because anything grouping by status drops that engine without
    saying so.

    Re-measured 2026-08-14 by `probes/obscura_defects.py`, and the failure has
    narrowed rather than gone: on the same 0.2.0 binary goto now returns 200 and
    503 for plain responses and **still returns None for a redirect**, where the
    event reports the post-redirect 503. So this watcher is no longer the only
    source of the column, and it is still the only source of the case that
    matters - Google refuses an exit by redirecting it to `/sorry/`, so on the
    target this repository cares most about every refusal arrives by the one
    path goto cannot see. Deleting this as redundant would leave the successes
    and drop the refusals, which is the shape of error that flatters.

    The match is on the document of the main frame, and deliberately not on
    `is_navigation_request`, which looks like the tighter test and silently
    drops every redirect. This browser follows a redirect chain inside its own
    network layer and reports one event for it: the status of the final
    response, carried against the URL the chain started from, with
    `is_navigation_request` false. Measured 2026-08-12 - `/redirect -> /fail`
    reports 503 - so the value recorded here is the post-redirect status, which
    is what `goto` returns on the other engines. A target that refuses by
    redirecting, which is how Google serves `/sorry/`, is exactly the case that
    filter would have left null.

    The last document response wins, so the status belongs to the page the
    verdict is then read from.
    """
    seen = {"status": None}

    def on_response(response):
        if (response.request.resource_type == "document"
                and response.frame == page.main_frame):
            seen["status"] = response.status

    page.on("response", on_response)
    return seen


class ObscuraEngine:
    name = "obscura"
    supports_blocking = True
    # Headless only: there is no display, so the headful comparison that removes
    # the compositor and GPU tells cannot be run for this engine at all.
    supports_headful = False
    # These two are statements about this adapter, not about the engine.
    #
    # Upstream ships `OBSCURA_TIMEZONE` and `OBSCURA_GEOLOCATION` (documented in
    # `docs/Environment-variables.md`), and the timezone one drives the process
    # zone so `Date` and `Intl.DateTimeFormat` agree. This adapter sets neither,
    # so the browser reports the host's own zone against an exit that is usually
    # somewhere else: measured 2026-08-26 on this Windows workstation,
    # `Intl.DateTimeFormat().resolvedOptions().timeZone` reads `Europe/Moscow`
    # and `getTimezoneOffset()` reads -180 while the exit was drawn from
    # `country=any`. That mismatch is a signal in its own right, and it rides
    # under every obscura row this harness has produced.
    #
    # Flipping these to True means wiring the two variables into `_spawn` and
    # deriving their values from the exit, which is the same lookup the geo-align
    # axis already does for the other engines. Until then the flags stay False so
    # no run claims an alignment it did not perform.
    supports_geo_align = False
    supports_geoip = False
    supports_humanize = False
    runs_script = True
    # `ObscuraSession.search` exists and this still answers False, which is the
    # one combination worth explaining.
    #
    # The old reason is spent. It was that this engine recorded no HTTP status,
    # so `was_served` was structurally false for it and an entry comparison it
    # took part in could not be split into served and refused. The status has
    # been recorded since 2026-08-12 and `probes/obscura_defects.py` now checks
    # it, including through the redirect that carries every Google refusal.
    #
    # The reason it is still False was measured 2026-08-14, and it is a defect
    # in this browser's layout rather than in the harness: **it lays out a
    # `<textarea>` with height 0**. On one local page every other element
    # reports a correct box and the textarea reports 1264x0, against 168x36 on
    # Chromium for the identical markup, so Playwright resolves it to `hidden`
    # and `wait_for_selector(state="visible")` waits the full 60 s and records
    # an `error`. Every typed attempt, on every target.
    #
    # It lands exactly where it costs the most: Google's search box is a
    # textarea, which this repository already knew from the zendriver clearing
    # bug. Waiting on `attached` instead would type into an element this
    # renderer has not laid out, which is a different client from the one every
    # other engine presents - and the entry axis is only readable if the entry
    # is the one thing that changed. So this is refused rather than worked
    # around, on the same grounds `--humanize` was.
    #
    # `supports_headful` stays False and is a different kind of limit. It does
    # not disqualify this engine the way it disqualifies a Chromium one: the
    # marker headful buys is a User-Agent without `HeadlessChrome` in it, and
    # this browser reports `Chrome/145` while headless because it is not a
    # Chromium headless shell. Measured 2026-08-11 by `engine_fingerprint.py`,
    # and DuckDuckGo agrees - 44 of 44 served, against 0 of 50 for the two
    # engines that announce the mode. So the `fetch` path is a fair column for
    # this engine and only the typed path is unavailable.
    supports_typing = False
    # Takes credentials in its own proxy argument, so a relay would add a
    # loopback hop and buy nothing. See `nmbench.relay` for what that hop costs.
    needs_relay = False

    @classmethod
    def check(cls) -> str:
        if shutil.which(BINARY) is None:
            return (f"the {BINARY!r} binary is not on PATH, so this engine "
                    f"cannot run. Download the release for this platform and "
                    f"put it on PATH, or set OBSCURA_BINARY to its full path.")
        try:
            import playwright  # noqa: F401
        except ImportError:
            return "playwright is not installed: pip install playwright"
        return None

    @classmethod
    def version(cls) -> str:
        try:
            out = subprocess.run([BINARY, "--version"], capture_output=True,
                                 text=True, timeout=10)
            return (out.stdout or out.stderr).strip().splitlines()[0][:60]
        except Exception:
            return "unknown"

    @contextmanager
    def open(self, *, direct: bool = False, params: dict = None,
             preset: str = "light", stealth: bool = True, store=None,
             allow_private_network: bool = False, provider=None,
             ready_timeout_ms: int = 8000, **ignored):
        unavailable = self.check()
        if unavailable:
            raise EngineUnavailable(unavailable)

        from playwright.sync_api import sync_playwright

        params = params or {}
        provider = None if direct else (provider or providers.load())
        port = _free_port()
        command = [BINARY, "serve", "--port", str(port)]
        if stealth:
            command.append("--stealth")
        if allow_private_network:
            # Off by default and it must stay that way: this browser blocks
            # loopback and RFC1918 by default as an SSRF fix, and the CDP port
            # accepts navigations from anything that can reach it. The flag
            # exists for `probes/obscura_defects.py`, which checks the status
            # column against a server on this machine answering a known 200,
            # 503 and redirect, and reads the layout of a form off the same
            # page. The alternative is asking a real target for a refusal,
            # which costs traffic and cannot be made to answer 503 on demand.
            # No matrix run passes it.
            command.append("--allow-private-network")

        # The proxy goes in the environment, never in argv: a command line is
        # world-readable on this machine while the process runs, and these are
        # live credentials on a shared company account.
        env = dict(os.environ)
        env.pop("OBSCURA_PROXY", None)
        if not direct:
            env["OBSCURA_PROXY"] = proxy.proxy_url(provider=provider, **params)

        process = subprocess.Popen(command, env=env,
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        try:
            _wait_for_cdp(port, process)
            with sync_playwright() as pw:
                browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                try:
                    yield ObscuraSession(browser, preset=preset, direct=direct,
                                         params=params, version=self.version(),
                                         ready_timeout_ms=ready_timeout_ms,
                                         store=store, provider=provider)
                finally:
                    browser.close()
        finally:
            _terminate_tree(process)
