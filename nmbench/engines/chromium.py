"""Chromium engines: the control, and Patchright.

Both drive Chromium through a Playwright API, so they share a session class and
differ only in which driver launches the browser. That is the point of putting
them in one file: the request path is identical, and any difference in the
numbers comes from the patches, not from two adapters written on different days.

**ChromiumEngine is the control and is deliberately not hardened.** Stock
Playwright Chromium announces itself: `navigator.webdriver` is true, the
`--enable-automation` switch is set, the renderer is SwiftShader rather than a
GPU. Every instinct says to paper over that, and doing so would destroy the only
thing this engine is for. Without an unmodified browser in the matrix there is
no baseline, and "Camoufox passed 60% of the time" is a number with nothing to
compare against - it cannot be told apart from the target simply letting 60% of
everything through. The control is what converts pass rates into evidence that
the anti-detect work is doing something.

It also settles a confound the earlier findings could not. Camoufox is Firefox,
and every refusal we had measured came from a Firefox. "The browser alone is
sufficient to be refused" was therefore only ever "*this* browser is
sufficient". Running the same address through a Chromium separates the engine
family from the automation markers.

The control is run both proxied and direct, because the two answer different
questions: direct isolates the browser, proxied isolates the address, and the
pair of them brackets what the gateway is contributing.

Patchright is the opposite case: a drop-in Playwright replacement that removes
the CDP artefacts (`Runtime.enable`, console leaks, the automation switches)
rather than repainting properties from JavaScript. It is included because the
comparison is more honest with it than without it - it occupies the same niche
as Camoufox for a different browser family.

Both engines take a `channel`, and it is left unset by default on purpose.

Measured 2026-08-11 on about:blank, no traffic. Three results that decide how
these engines have to be run.

On the bundled Chromium, Patchright clears `navigator.webdriver` but reports
zero plugins, zero MIME types, `pdfViewerEnabled: false` and a SwiftShader-class
renderer - the same values as the unmodified control. Its own documentation asks
for `channel="chrome"`, and on installed Chrome all four move to real values
(5 plugins, 2 MIME types, the host's Intel GPU).

The User-Agent is not fixed by the channel. Headless sends
`HeadlessChrome/149.0.0.0` on both the bundled build and installed Chrome, and
`--channel chromium` does not help either. Every headless cell driven from here
therefore announces itself in a header, where one substring test is enough to
refuse it, and no amount of driver patching reaches it. This is measured, not
inferred, and it applies to the control as well.

The reason is the binary, not the mode, and this docstring said otherwise until
2026-08-13: a Playwright install downloads a separate **Chromium Headless
Shell**, and the shell is what carries the substring. So the marker is a
property of these two engines rather than of headless Chromium in general -
cloakbrowser is headless Chromium and sends a clean `Chrome/146.0.0.0`. Setting
`headless=False` here avoids the shell; it does not fix a browser that would
otherwise be announcing itself.

On installed Chrome, `navigator.webdriver` is then the *only* marker in this
probe where Patchright and the raw control differ. That is a narrower result
than the tool's reputation suggests and it belongs in the report as measured.

The channel default stays on the bundled build, because that comparison has one
variable in it: Patchright and the control on the same Chromium isolate the
patches. `channel="chrome"` changes the browser as well as the driver and
answers the other question worth asking - how the tool does when used as
directed. Run both; the channel reaches the cell key, so the rows stay apart.
What is not acceptable is picking one silently, which is why nothing here falls
back to another build when the requested one is missing.
"""
import os
import shutil
import tempfile
import time
from contextlib import contextmanager

from .. import blocking, providers, proxy
from .base import (
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


class ChromiumSession:
    """One browser, several queries. Shared by both Chromium engines."""

    def __init__(self, pages, *, label, preset, direct, params, headless,
                 version, ready_timeout_ms, humanize=False, store=None,
                 provider=None):
        self.pages = pages           # anything with .new_page()
        self.label = label
        self.store = store
        self.preset = preset
        self.direct = direct
        self.params = params
        # The provider whose dialect actually built this session's username,
        # None on the direct arm. Carried on the session rather than filled in
        # by the runner so the column reports what was used and not what was
        # asked for: `params` is written in the harness's own vocabulary and
        # each vendor spells those settings differently, so a row without this
        # cannot say what left the machine.
        self.provider = provider
        self.headless = headless
        # Carried rather than hardcoded to False, because a Chromium-family
        # engine with humanized input exists now. A session that wrote False
        # while moving the cursor would put the flag out of reach of anyone
        # reading the rows back, which is the failure --humanize already had
        # once at the command line.
        self.humanize = humanize
        self.version = version
        self.ready_timeout_ms = ready_timeout_ms
        self.index = 0

    def new_page(self, counter: dict = None):
        """A page with this session's blocking and byte counter installed.

        Part of the engine contract rather than a private helper: probes need a
        page without knowing which engine they hold, and the alternative is code
        that branches on an engine name.
        """
        counter = {} if counter is None else counter
        page = self.pages.new_page()
        blocking.install(page, self.preset, counter)
        blocking.install_counter(page, counter)
        return page

    def _row(self, target, query: str, url: str) -> dict:
        """The columns that describe this session, whatever the entry shape.

        Factored out so `fetch` and `search` cannot drift: the label expression
        is what identifies an engine in every table, and two entry paths writing
        it two ways would split one engine into two columns nobody could pair up
        again.
        """
        row = blank_row(
            f"{self.label}-direct/{self.preset}" if self.direct
            else f"{self.label}/{self.preset}",
            self.version, query, url,
            target=getattr(target, "name", None),
            direct=self.direct, preset=self.preset,
            params={} if self.direct else dict(self.params),
            provider=getattr(self.provider, "id", None),
            headless=bool(self.headless), humanize=bool(self.humanize),
            session_index=self.index,
        )
        self.index += 1
        return row

    def search(self, page, target, query: str, *, rng,
               counter: dict = None) -> dict:
        """One query typed into the target's own box, on a page held open.

        Part of the engine contract beside `fetch`, so a probe can choose the
        entry shape without knowing which engine it holds. Everything after the
        keystroke is `run_search`, shared with the other Playwright engines.
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

        started = time.perf_counter()
        try:
            response = page.goto(url, wait_until="domcontentloaded",
                                 timeout=60000)
            if response:
                row["status"] = response.status
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


def label_for(name: str, channel: str = None) -> str:
    """The engine label, carrying the browser build when it is not the default.

    Two runs of the same engine on different browser builds are two different
    measurements, so the difference has to reach the cell key. Patchright on
    bundled Chromium and Patchright on installed Chrome do not score the same,
    and a row that called both `patchright` could not be told apart afterwards.
    """
    return name if not channel else f"{name}-{channel}"


def _package_version(package: str) -> str:
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version(package)
    except PackageNotFoundError:
        return "unknown"


class ChromiumEngine:
    """Stock Playwright Chromium. The control - do not harden it."""

    name = "chromium"
    driver = "playwright"
    supports_blocking = True
    supports_headful = True
    # No equivalent of Camoufox's geoip: this engine reports the host timezone
    # and language list whatever address it leaves from. Declared rather than
    # silently ignored, so asking for alignment fails loudly instead of
    # producing a cell that claims an alignment it never had.
    supports_geo_align = False
    # No humanized input. The row already records humanize=False for this
    # engine, but a flag accepted at the command line and honoured by only one
    # column is a difference nobody reads back out of the rows.
    supports_humanize = False
    runs_script = True
    # This session implements `search`, so it can be entered through the
    # target's own front page. Declared rather than discovered by calling it,
    # because a probe that fell back to `fetch` for the engines without it would
    # put two different clients in one entry column and read as an engine
    # difference. `probe_and_hold.py` refuses an engine that answers False.
    supports_typing = True
    # Playwright takes proxy credentials directly, so a relay would add a
    # loopback hop and buy nothing. See `nmbench.relay` for what that hop costs.
    needs_relay = False

    @classmethod
    def check(cls) -> str:
        try:
            __import__(f"{cls.driver}.sync_api")
        except ImportError:
            return (f"{cls.driver} is not installed, so this engine cannot "
                    f"run: pip install {cls.driver}")
        # An installed driver with no browser is the common failure and the
        # error it produces at launch names a path, not a fix.
        from importlib import import_module
        api = import_module(f"{cls.driver}.sync_api")
        try:
            with api.sync_playwright() as pw:
                if not os.path.exists(pw.chromium.executable_path):
                    return (f"{cls.driver} has no chromium binary: run "
                            f"python -m {cls.driver} install chromium")
        except Exception as exc:
            return f"{cls.driver} could not start: {exc}"
        return None

    @classmethod
    def version(cls) -> str:
        return f"{cls.driver} {_package_version(cls.driver)}"

    @contextmanager
    def open(self, *, direct: bool = False, params: dict = None,
             preset: str = "light", headless: bool = True,
             channel: str = None, ready_timeout_ms: int = 8000, store=None,
             provider=None, **ignored):
        unavailable = self.check()
        if unavailable:
            raise EngineUnavailable(unavailable)

        from importlib import import_module
        api = import_module(f"{self.driver}.sync_api")

        params = params or {}
        provider = None if direct else (provider or providers.load())
        proxy_cfg = None if direct else proxy.proxy_dict(provider=provider,
                                                        **params)

        with api.sync_playwright() as pw:
            # No args. Playwright's defaults include the automation switches,
            # and stripping them here would quietly turn the control into
            # another anti-detect engine with nothing left to control against.
            browser = pw.chromium.launch(headless=headless, proxy=proxy_cfg,
                                         channel=channel)
            try:
                context = browser.new_context()
                yield ChromiumSession(
                    context, label=label_for(self.name, channel), preset=preset,
                    direct=direct, params=params, headless=headless,
                    version=f"{browser.version} / {self.version()}"
                            f"{' / ' + channel if channel else ''}",
                    ready_timeout_ms=ready_timeout_ms, store=store,
                    provider=provider)
            finally:
                browser.close()


class PatchrightEngine(ChromiumEngine):
    """Patchright: Playwright with the CDP tells removed.

    Launched as a persistent context, which is what its documentation asks for.
    The undetected path depends on not enabling the domains a fresh context
    turns on, so driving it the way stock Playwright is driven would measure a
    configuration nobody ships.
    """

    name = "patchright"
    driver = "patchright"
    supports_headful = True
    # Unlike the control above, this engine takes a timezone. It has no
    # equivalent of Camoufox's geoip and never will: Camoufox looks the exit up
    # in a bundled database, and this one is handed the answer by the caller,
    # which is why the option carries a value rather than a boolean. What it
    # buys is the same thing - `timezone_id` reaches Chromium's own emulation
    # through CDP, so `Intl` and `Date` agree in the main frame, in a fresh
    # iframe and in a Web Worker. That is the distinction this repository draws
    # everywhere else: patched JavaScript properties do not survive those three
    # readings and browser-level emulation does.
    supports_geo_align = True

    @contextmanager
    def open(self, *, direct: bool = False, params: dict = None,
             preset: str = "light", headless: bool = True,
             channel: str = None, ready_timeout_ms: int = 8000, store=None,
             timezone_id: str = None, provider=None, **ignored):
        unavailable = self.check()
        if unavailable:
            raise EngineUnavailable(unavailable)

        from importlib import import_module
        api = import_module(f"{self.driver}.sync_api")

        params = params or {}
        provider = None if direct else (provider or providers.load())
        proxy_cfg = None if direct else proxy.proxy_dict(provider=provider,
                                                        **params)

        with api.sync_playwright() as pw:
            profile = tempfile.mkdtemp(prefix="patchright-")
            # `locale` is deliberately not set beside it. See `base.ROW_FIELDS`.
            context = pw.chromium.launch_persistent_context(
                user_data_dir=profile, headless=headless, proxy=proxy_cfg,
                channel=channel, no_viewport=True, timezone_id=timezone_id)
            try:
                browser = context.browser
                yield ChromiumSession(
                    context, label=label_for(self.name, channel), preset=preset,
                    direct=direct, params=params, headless=headless,
                    version=f"{browser.version if browser else 'unknown'} / "
                            f"{self.version()}"
                            f"{' / ' + channel if channel else ''}",
                    ready_timeout_ms=ready_timeout_ms, store=store,
                    provider=provider)
            finally:
                context.close()
                shutil.rmtree(profile, ignore_errors=True)
