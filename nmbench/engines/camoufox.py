"""Camoufox: a patched Firefox driven through Playwright.

The patches live in the browser binary, so the values a detector reads are the
values the browser really holds - there is no JavaScript shim to catch by
inspecting a property descriptor, a prototype chain, a fresh iframe or a Web
Worker. What the patches do not reach is the TLS handshake, which NSS negotiates
below them, so the ClientHello is a stock Firefox one.
"""
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


class CamoufoxSession:
    def __init__(self, pages, *, preset, direct, params, headless, humanize,
                 ready_timeout_ms, version, store=None, provider=None):
        # A context, not the browser. `browser.new_page()` opens a fresh context
        # every time, so this engine alone was throwing its cookie jar away
        # between queries while the other three carried one across the batch:
        # measured 2026-08-11, two pages of one session, a cookie set on the
        # first was absent from the second. A batch is supposed to be one
        # identity doing N searches, and an identity that has never held a
        # cookie after nine searches is not the thing we set out to measure.
        self.pages = pages
        self.store = store
        self.preset = preset
        self.direct = direct
        self.params = params
        # The provider whose dialect built this session's username, None on the
        # direct arm. On the session rather than filled in by the runner so the
        # column reports what was used and not what was asked for.
        self.provider = provider
        self.headless = headless
        self.humanize = humanize
        self.ready_timeout_ms = ready_timeout_ms
        self.version = version
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
            f"camoufox-direct/{self.preset}" if self.direct
            else f"camoufox/{self.preset}",
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


class CamoufoxEngine:
    name = "camoufox"
    supports_blocking = True
    supports_headful = True
    supports_geo_align = True
    # The only engine here with humanized input. Declared so the runner can
    # refuse a mixed matrix rather than humanize this column alone.
    supports_humanize = True
    runs_script = True
    # `CamoufoxSession.search` exists, so this engine can be entered through the
    # target's own front page. See `ChromiumEngine` for why this is declared
    # rather than discovered at the call site.
    supports_typing = True
    # Playwright takes proxy credentials directly, so a relay would add a
    # loopback hop and buy nothing. See `nmbench.relay` for what that hop costs.
    needs_relay = False

    @classmethod
    def check(cls) -> str:
        try:
            import camoufox  # noqa: F401
        except ImportError:
            return ("camoufox is not installed, so this engine cannot run: "
                    "pip install camoufox[geoip] && camoufox fetch")
        return None

    @classmethod
    def version(cls) -> str:
        from importlib.metadata import PackageNotFoundError, version
        try:
            return version("camoufox")
        except PackageNotFoundError:
            return "unknown"

    @contextmanager
    def open(self, *, direct: bool = False, params: dict = None,
             preset: str = "light", headless: bool = True,
             humanize: bool = False, geoip: bool = False,
             ready_timeout_ms: int = 8000, store=None, provider=None,
             **ignored):
        unavailable = self.check()
        if unavailable:
            raise EngineUnavailable(unavailable)

        from camoufox.sync_api import Camoufox

        params = params or {}
        provider = None if direct else (provider or providers.load())
        proxy_cfg = None if direct else proxy.proxy_dict(provider=provider,
                                                        **params)
        # `geoip` defaults off, matching `CloakEngine` and every row on disk.
        # It defaulted True here until 2026-08-14, which put the two aligning
        # engines on opposite defaults and made "aligned" the state a caller got
        # by not mentioning geo - on the engine whose Amazon column is the
        # widest gap in the repository. The runners always passed the flag
        # explicitly, so no benchmark row is affected, but a probe calling
        # `fetch_camoufox` without it was aligned and its row does not say so.
        with Camoufox(headless=headless, humanize=humanize, geoip=geoip,
                      block_webrtc=True, proxy=proxy_cfg) as browser:
            # One context for the whole batch, the same shape the other engines
            # run in. The patched values live in the binary and the proxy is set
            # at launch, so a context made here presents what a page made from
            # the browser did: checked marker by marker on 2026-08-11.
            context = browser.new_context()
            yield CamoufoxSession(context, preset=preset, direct=direct,
                                  params=params, headless=headless,
                                  humanize=humanize,
                                  ready_timeout_ms=ready_timeout_ms,
                                  version=self.version(), store=store,
                                  provider=provider)
