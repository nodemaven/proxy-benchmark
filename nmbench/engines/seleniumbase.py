"""SeleniumBase UC mode: the WebDriver family, which the matrix did not have.

Every other engine here speaks CDP - Playwright over Chromium, Playwright over
Firefox, raw CDP to Obscura. SeleniumBase drives Chrome through ChromeDriver and
the W3C WebDriver protocol instead, and that is a different surface with
different leavings: a driver process the page can be made to notice, and the
classic `cdc_` document properties that ChromeDriver injects and UC mode strips.
Without this column the repository can say a great deal about patched CDP
automation and nothing at all about the toolchain most scraping code is actually
written against.

It runs on the host's installed Chrome rather than a bundled build, so it does
not carry the Headless Shell and does not announce `HeadlessChrome` in
`headless2`. That is the same property `cloak` has, arrived at differently, and
having two of them is what stops the DuckDuckGo result from resting on one
browser.

Three things about this adapter are compromises and all three have to be read
off the row rather than assumed.

**The HTTP status is not available by default, and buying it changes the
browser.** WebDriver has no notion of a response status; the only way to get one
is Chrome's performance log, which means asking for `goog:loggingPrefs` and
enabling the Network domain. That is CDP instrumentation switched on inside the
engine whose selling point is not leaving CDP instrumentation switched on, so
turning it on by default would handicap this engine against the very engines it
is being compared with, and the handicap would look like a result.

So `record_status` defaults to False. `status` is then null and `was_served` is
structurally unavailable, exactly as it is for Obscura - the difference is that
here it is a declared cost of leaving the browser alone rather than a defect. It
matters less than it sounds: this repository judges by content, not by status,
so every verdict is unaffected. Only the served-versus-diverted split for Google
is lost, and that split has never applied to Amazon, Bing, DuckDuckGo or Walmart
anyway.

`record_status=True` is worth running as its own arm, because the difference
between the two settings is a measurement in its own right: it prices what CDP
instrumentation costs at the target. The setting reaches the engine label so the
two arms cannot be pooled by accident.

**Proxy credentials are delivered by an extension, not by the driver.** Chrome
takes no credentials in `--proxy-server`, so SeleniumBase writes a small
extension that answers the auth challenge. That extension is a modification of
the browser under test and a page can enumerate it. `nmbench.relay` exists to
avoid exactly this - an authenticating CONNECT forwarder on loopback, so the
browser sees an unauthenticated proxy and nothing is added to it - and this
engine prefers the relay whenever one is handed to it. The extension path stays
as the fallback, and the `relayed` column says which one a session took: a pool
row with `relayed` false is a row whose browser was carrying the extension.

**No `page.route`, so no byte counter and no blocking preset.** Those are
Playwright APIs. Bytes for this engine come from the relay, which counts sockets
and therefore counts the same way for every engine; on the direct arm there is
no relay and the byte column is empty. `supports_blocking` is False rather than
silently ignoring a preset that was asked for.
"""
import time
from contextlib import contextmanager

from .. import providers, proxy
from .base import (
    EngineUnavailable,
    blank_row,
    keep_body,
    record_error,
    record_judgement,
)


class _Page:
    """The three calls the probes make, over a WebDriver.

    Deliberately not a general Playwright imitation. A shim that answered every
    Playwright method approximately would let a probe run and return numbers
    that mean something slightly different for this engine than for the others,
    which is worse than failing. `engine_fingerprint.py` and
    `session_continuity.py` use `goto`, `evaluate` and `close`; anything else is
    absent and will raise.
    """

    def __init__(self, driver):
        self.driver = driver

    def goto(self, url: str, **ignored):
        self.driver.get(url)
        return None

    def evaluate(self, script: str):
        # Playwright takes `() => expr` as well as a bare expression; WebDriver
        # takes neither, it takes a function body. Normalising here keeps the
        # probes free of engine-specific script dialects.
        #
        # The arrow is called rather than unwrapped, and this is the third
        # adapter to need that fix. Unwrapping by text - dropping everything up
        # to the first `=>` - is correct for `() => expr` and cannot work for
        # `() => { ... }`: after `return` the block parses as an object literal.
        # Measured 2026-08-18 on the server, `engine_fingerprint.py` opens with
        # `const safe = (fn) => {...}` inside a block-bodied arrow, so the old
        # form cut at the inner arrow and ChromeDriver answered
        # `JavascriptException: Unexpected identifier 'safe'`. seleniumbase had
        # no fingerprint row at all on either machine because of it, exactly as
        # zendriver had none until the same day.
        body = script.strip()
        call = (f"return ({body})()" if body.startswith("()")
                else f"return ({body})")
        return self.driver.execute_script(call)

    def close(self) -> None:
        pass


class SeleniumBaseSession:
    def __init__(self, driver, *, label, direct, params, headless, version,
                 ready_timeout_ms, record_status, store=None, provider=None):
        self.driver = driver
        self.label = label
        self.store = store
        self.direct = direct
        self.params = params
        # The provider whose dialect built this session's username, None on the
        # direct arm. On the session rather than filled in by the runner so the
        # column reports what was used and not what was asked for.
        self.provider = provider
        self.headless = headless
        self.version = version
        self.ready_timeout_ms = ready_timeout_ms
        self.record_status = record_status
        self.index = 0

    def new_page(self, counter: dict = None):
        """Part of the engine contract: a probe takes a page without knowing
        which engine it holds. There is one window here, so this hands back a
        view onto it rather than opening a tab."""
        return _Page(self.driver)

    def _await_ready(self, target) -> bool:
        selector = getattr(target, "ready_selector", None)
        if not selector:
            return None
        from selenium.common.exceptions import WebDriverException
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as ec
        from selenium.webdriver.support.ui import WebDriverWait
        try:
            WebDriverWait(self.driver, self.ready_timeout_ms / 1000).until(
                ec.presence_of_element_located((By.CSS_SELECTOR, selector)))
            return True
        except WebDriverException:
            return False

    def _document_status(self):
        """The main-frame document status, from Chrome's performance log.

        Only reachable when the log was asked for at launch. Returns None
        otherwise, and None is the honest value: not 'no status was sent', but
        'this browser was not instrumented to tell us'.
        """
        if not self.record_status:
            return None
        import json
        status = None
        try:
            entries = self.driver.get_log("performance")
        except Exception:
            return None
        for entry in entries:
            try:
                message = json.loads(entry["message"])["message"]
            except Exception:
                continue
            if message.get("method") != "Network.responseReceived":
                continue
            params = message.get("params") or {}
            # Same rule as the Obscura adapter: the document of the main frame,
            # last one wins, so the status belongs to the page the verdict is
            # then read from and a redirect chain reports where it landed.
            if params.get("type") != "Document":
                continue
            response = params.get("response") or {}
            if response.get("status"):
                status = response["status"]
        return status

    def fetch(self, target, query: str) -> dict:
        url = target.url(query)
        row = blank_row(
            f"{self.label}-direct" if self.direct else self.label,
            self.version, query, url,
            target=getattr(target, "name", None),
            direct=self.direct, preset=None,
            params={} if self.direct else dict(self.params),
            provider=getattr(self.provider, "id", None),
            headless=bool(self.headless), humanize=False,
            session_index=self.index,
        )
        self.index += 1

        started = time.perf_counter()
        try:
            self.driver.get(url)
            row["ready"] = self._await_ready(target)
            html = self.driver.page_source
            row["html_len"] = len(html)
            row["status"] = self._document_status()
            record_judgement(row, target, self.driver.current_url,
                             self.driver.title, html)
            keep_body(self.store, row, html)
        except Exception as exc:
            record_error(row, exc)
        finally:
            row["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        return row


class SeleniumBaseEngine:
    name = "seleniumbase"
    # page.route is a Playwright API and there is none here, so a preset asked
    # for would be silently dropped. Declared instead.
    supports_blocking = False
    supports_headful = True
    # Reports the host timezone and language list whatever address it leaves
    # from, the same as the Chromium-family engines.
    supports_geo_align = False
    supports_geoip = False
    supports_humanize = False
    runs_script = True
    # This session has no `search`. The reason is `record_status`, which the
    # module docstring argues has to default False here: without a status
    # `was_served` is unavailable, and the entry question is only asked through
    # the served-versus-refused split. So this is a consequence of a deliberate
    # setting rather than a defect, and it is the one flag in the registry that
    # a caller can invalidate - a matrix running `record_status=True` has the
    # split back. Flipping the flag would then mean writing `search`, which
    # WebDriver supports and nothing here has needed yet.
    supports_typing = False
    # True even though this engine has a fallback, because the fallback is an
    # auth extension written into the browser under test and a page can
    # enumerate it. Given the choice between modifying the browser we are
    # fingerprinting and adding a loopback hop that only costs `elapsed_ms`, the
    # hop is the cheaper error. The `relayed` column still records which path a
    # session actually took, so a run made without a relay is readable rather
    # than merely wrong.
    needs_relay = True

    @classmethod
    def check(cls) -> str:
        try:
            import seleniumbase  # noqa: F401
        except ImportError:
            return ("seleniumbase is not installed, so this engine cannot run: "
                    "pip install seleniumbase")
        try:
            import selenium  # noqa: F401
        except ImportError:
            return "selenium is not installed: pip install selenium"
        return None

    @classmethod
    def version(cls) -> str:
        from importlib.metadata import PackageNotFoundError, version
        try:
            return f"seleniumbase {version('seleniumbase')}"
        except PackageNotFoundError:
            return "seleniumbase unknown"

    @contextmanager
    def open(self, *, direct: bool = False, params: dict = None,
             headless: bool = True, uc: bool = True,
             record_status: bool = False, relay_address: str = None,
             ready_timeout_ms: int = 8000, store=None, provider=None,
             **ignored):
        unavailable = self.check()
        if unavailable:
            raise EngineUnavailable(unavailable)

        from seleniumbase import Driver

        params = params or {}
        provider = None if direct else (provider or providers.load())
        proxy_string = None
        if not direct:
            if relay_address:
                # The relay authenticates upstream, so the browser is handed a
                # plain proxy and nothing is added to it.
                proxy_string = relay_address
            else:
                # No relay, so SeleniumBase writes the auth extension into the
                # browser. The runner records this as `relayed` false.
                url = proxy.proxy_url(provider=provider, **params)
                proxy_string = url.split("://", 1)[-1]

        options = {
            "uc": uc,
            # headless2 rather than headless: the old headless is a different
            # browser mode with its own tells, and headless2 runs the real
            # Chrome headlessly, which is what keeps the User-Agent clean.
            "headless2": bool(headless),
            "headless": False,
            # Chrome's own background traffic, which the pool pays for and this
            # repository does not measure. Measured 2026-08-19 on the server,
            # with a relay counting sockets and the browser left on
            # `about:blank` for 60 s with nothing navigated: **43.4 MB here,
            # 178 KB with these two flags, 72 KB on zendriver, 43 KB on
            # patchright**. At `--batch 1` a fresh profile pays it again on
            # every attempt, so it is about 43 GB per thousand attempts against
            # a 100 GB shared quota, spent on safe-browsing lists and component
            # updates rather than on a target.
            #
            # SeleniumBase adds `--disable-background-networking` itself in
            # `browser_launcher._set_chrome_options`, and the measurement says
            # UC mode does not deliver it - which is why this is handed over
            # explicitly and why the number above is the check on it. zendriver
            # is the control: same host Chrome 151, same box, 72 KB, because its
            # own config sets both flags. Playwright sets them too, so the
            # uncontrolled variable in the matrix was which engines chat to the
            # vendor, and this removes it rather than adding one.
            #
            # Not a hardening of the browser under test: neither flag is visible
            # to a page, and the `chromium` control is left alone whatever it
            # costs.
            "chromium_arg": ("--disable-background-networking,"
                             "--disable-component-update"),
        }
        if proxy_string:
            options["proxy"] = proxy_string
        if record_status:
            options["log_cdp_events"] = True

        label = self.name if not record_status else f"{self.name}-cdplog"
        if not uc:
            label = f"{label}-plain"

        driver = Driver(**options)
        try:
            try:
                browser_version = driver.capabilities.get("browserVersion", "")
            except Exception:
                browser_version = ""
            session = SeleniumBaseSession(
                driver, label=label, direct=direct, params=params,
                headless=headless,
                version=f"{browser_version} / {self.version()}",
                ready_timeout_ms=ready_timeout_ms,
                record_status=record_status, store=store, provider=provider)
            yield session
        finally:
            try:
                driver.quit()
            except Exception:
                pass
