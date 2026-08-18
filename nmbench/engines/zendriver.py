"""Zendriver: Chrome over raw CDP, with no WebDriver and no Playwright.

The matrix has had exactly one raw-CDP engine, and its column is broken.
Obscura carries no HTTP status on any row, so `was_served` is structurally false
for it and every served-versus-diverted figure it produced had to be struck.
That left "driven over raw CDP" measured by an engine that is also a
from-scratch browser with no WebGL context - two variables in one column, and
the interesting one unreadable.

Zendriver separates them. It is the host's ordinary Chrome, so the browser is
the same one `seleniumbase` drives and a near neighbour of the one Playwright
drives; the only thing it changes is how the browser is spoken to. It also
launches the installed Chrome rather than a bundled build, so it carries no
Headless Shell and reports a clean `Chrome/149` while headless.

**It is not driven through Playwright, and that is the whole point.** Obscura is
attached with `connect_over_cdp`, which is legitimate there because Obscura's
defences do not depend on which CDP domains are enabled. Here they do: this
class of driver earns its keep by not turning on the domains that leak, and
attaching Playwright would turn them on. The result would be a hybrid nobody
ships, scored as if it were the tool. So this adapter speaks zendriver's own
API, and pays for it - see below.

The costs, and both are on the row rather than in a footnote.

**No `page.route`, so no byte counter and no blocking preset.** Those are
Playwright APIs. `nmbench.relay` is where bytes come from for this engine,
because it counts sockets rather than framework events and therefore counts the
same way for every engine; on the direct arm there is no relay and the byte
column is empty. `supports_blocking` is False rather than accepting a preset and
dropping it.

**The status is bought with `Network.enable`, and that is a different purchase
from the one undetected drivers avoid.** The marker this family exists to remove
is `Runtime.enable`, which is what leaks through console and execution-context
artefacts; `engine_fingerprint.py` reads `binding_leaks` and `stack_leak` for
exactly that. The Network domain does not create those artefacts, and the
Playwright-driven engines have it on throughout, so enabling it here makes this
column comparable rather than favoured. It is still a choice, so it is a flag,
and `record_status=False` gives a session that touches nothing at the cost of
the same blindness Obscura has.

Proxy credentials: Chrome takes none in `--proxy-server`, and this driver offers
no auth hook, so the pool arm needs `nmbench.relay` in front. A session asked for
a pool arm without one is refused rather than being launched unproxied, because
a browser that quietly ignored the proxy would write this machine's own address
into rows labelled as exits.
"""
import asyncio
import time
from contextlib import contextmanager

from .. import providers
from .base import (
    ENTRY_TIMEOUT_MS,
    EngineUnavailable,
    blank_row,
    entry_row_url,
    keep_body,
    keep_error_body,
    record_error,
    record_judgement,
    typing_delay_ms,
)

# How long any single CDP round trip may take before the browser is presumed
# dead. Not a step timeout: every step here sets its own, and the longest of
# them is `ENTRY_TIMEOUT_MS` at 60 s. This is the outer bound that keeps a
# dead browser from costing the run instead of the identity.
CDP_CEILING_S = 180.0

# Shutting down a browser that may already be gone. Short, because nothing
# downstream needs the answer.
SHUTDOWN_CEILING_S = 20.0


class _Page:
    """The three calls the probes make, over a zendriver tab.

    Deliberately not a general Playwright imitation: a shim that answered every
    Playwright method approximately would let a probe return numbers that mean
    something slightly different for this engine than for the others, which is
    worse than failing outright.
    """

    def __init__(self, tab, run):
        self.tab = tab
        self._run = run

    def goto(self, url: str, **ignored):
        self._run(self.tab.get(url))
        return None

    def evaluate(self, script: str):
        # `await_promise` because Playwright's `page.evaluate` resolves a
        # returned promise and this shim has to mean the same thing. Without it
        # a script whose answer is asynchronous - anything reading a Web Worker,
        # for one - comes back as an unresolved object here and as data
        # everywhere else, so one engine would return None for a question the
        # others answered, and the difference would read as the engine rather
        # than as the adapter.
        #
        # The arrow is called rather than unwrapped, and that is a fix rather
        # than a preference. This shim used to strip everything up to the `=>`
        # and evaluate what was left, which works for `() => expr` and cannot
        # work for `() => { ... }`: a block handed to `Runtime.evaluate` is a
        # block statement, its `return` is illegal at the top level, and V8
        # answers `SyntaxError: Illegal return statement`. Measured 2026-08-18 -
        # `engine_fingerprint.py` and `detect_page.py` both pass a block-bodied
        # arrow, so **zendriver failed both outright** while the four
        # expression-bodied probes passed, and the engine carrying this
        # repository's largest unattributed result had no fingerprint row at
        # all. `(body)()` is valid for either shape.
        body = script.strip()
        expr = f"({body})()" if body.startswith("()") else body
        return self._run(self.tab.evaluate(expr, await_promise=True))

    def close(self) -> None:
        pass


class ZendriverSession:
    def __init__(self, browser, tab, loop, *, label, direct, params, headless,
                 version, ready_timeout_ms, seen_status, store=None,
                 provider=None):
        self.browser = browser
        self.tab = tab
        self.loop = loop
        self.label = label
        self.store = store
        self.direct = direct
        self.params = params
        # The provider whose dialect built the username the relay authenticates
        # with, None on the direct arm. This engine never builds one itself -
        # the relay does - so the value is carried for the row rather than used.
        self.provider = provider
        self.headless = headless
        self.version = version
        self.ready_timeout_ms = ready_timeout_ms
        # A one-key dict written by the CDP handler. Same rule as the Obscura
        # adapter: the main-frame document, last one wins, so the status belongs
        # to the page the verdict is then read from and a redirect chain reports
        # where it landed rather than where it started.
        self.seen_status = seen_status
        self.index = 0

    def _run(self, coro, ceiling: float = CDP_CEILING_S):
        # Every CDP interaction goes through here, and every one of them can
        # wait forever. Playwright bounds its own calls; raw CDP does not, so a
        # browser that dies mid-series leaves this process blocked on a reply
        # that will never arrive - not slow, stopped, with no browser left to
        # show for it and nothing written to the log.
        #
        # Measured 2026-08-14 twice on `probehold_geo`: the run stood for over
        # ten minutes with zero chrome processes alive and the python process
        # healthy, having written its last row mid-series. A run that cannot
        # outlive its browser spends the rest of the night proving nothing.
        #
        # The ceiling is deliberately far above every legitimate inner wait -
        # `ENTRY_TIMEOUT_MS` is 60 s and `select` is given up to that - because
        # this is not a step timeout and must never truncate one. It is the
        # difference between a lost identity and a lost run.
        return self.loop.run_until_complete(
            asyncio.wait_for(coro, timeout=ceiling))

    def new_page(self, counter: dict = None):
        """Part of the engine contract: a probe takes a page without knowing
        which engine it holds. One tab here, so this is a view onto it."""
        return _Page(self.tab, self._run)

    def _await_ready(self, target):
        selector = getattr(target, "ready_selector", None)
        if not selector:
            return None
        try:
            self._run(self.tab.select(selector,
                                      timeout=self.ready_timeout_ms / 1000))
            return True
        except Exception:
            return False

    def _row(self, target, query: str, url: str) -> dict:
        """The columns that describe this session, whatever the entry shape.

        Factored out so `fetch` and `search` cannot drift: the label expression
        is what identifies an engine in every table, and two entry paths writing
        it two ways would split one engine into two columns nobody could pair up
        again.
        """
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
        return row

    def _judge_tab(self, row: dict, target, url: str) -> None:
        """Read the tab and judge it, or record why it cannot be judged.

        Shared by both entry paths because the `chrome-error://` guard below is
        the difference between a failed connection and a refusal by the target,
        and an entry path that skipped it would put our own dead sockets into
        the block rate.
        """
        html = self._run(self.tab.get_content())
        row["html_len"] = len(html)
        row["status"] = self.seen_status.get("status")
        final_url = self._run(self.tab.evaluate("location.href")) or url
        title = self._run(self.tab.evaluate("document.title")) or ""
        # Chrome's own network error page, and it must never reach the
        # classifier. The Playwright engines get this for free because a
        # failed navigation raises; CDP does not raise, it leaves the tab
        # on `chrome-error://chromewebdata/` and `get_content` hands back
        # 188 KB of Chromium's error scaffold. Measured 2026-08-13 in
        # `benchmark_20260813T170711Z`: one such row was judged `block`,
        # "no result list and no known interstitial", against a target that
        # was never reached. That is our own failed connection recorded as
        # a refusal by the target, which is the one direction of error this
        # repository cannot absorb - it inflates a block rate with rows
        # that cannot be attributed afterwards.
        if final_url.startswith("chrome-error://"):
            record_error(row, ConnectionError(
                f"navigation to {url} ended on Chrome's error page, so "
                f"nothing from the target arrived and no verdict is "
                f"possible: the connection failed rather than the page"))
        else:
            record_judgement(row, target, final_url, title, html)
            keep_body(self.store, row, html)

    def _box(self, target, timeout_s: float):
        selector = getattr(target, "search_box", None)
        if not selector:
            raise ValueError(
                f"{getattr(target, 'name', target)} declares no search_box, so "
                f"it cannot be entered by typing. Add one to the target, or run "
                f"this target through the URL path instead - do not guess a "
                f"selector here, because a wrong one fails as an empty query "
                f"and is recorded as the target refusing.")
        return self._run(self.tab.select(selector, timeout=timeout_s))

    def _dismiss_consent(self, target):
        """`base.dismiss_consent` over CDP. Returns whether a wall was cleared.

        A second implementation of the same step, for the reason `search` gives:
        the other one is Playwright. What has to match is what the target sees -
        a pointer event on the button the target itself named - and the selector
        list still comes from the target, so the two paths cannot clear
        different things.

        The visibility test is `getBoundingClientRect`, because these panels
        stay in the document after they come down and a presence test would
        click a hidden control on every later query. Short timeout: no wall is
        the common case and is not an error, so this must not cost the length of
        a selector wait on every query in a held series.
        """
        for selector in getattr(target, "consent_dismiss", None) or ():
            try:
                handle = self._run(self.tab.select(selector, timeout=1))
            except Exception:
                continue
            try:
                visible = self._run(self.tab.evaluate(
                    f"(() => {{ const e = document.querySelector({selector!r});"
                    f" if (!e) return false; const r = e.getBoundingClientRect();"
                    f" return r.width > 0 && r.height > 0; }})()"))
            except Exception:
                visible = False
            if not visible:
                continue
            self._run(handle.click())
            time.sleep(1.0)
            return True
        return False

    def search(self, page, target, query: str, *, rng,
               counter: dict = None) -> dict:
        """One query typed into the target's own box, on the held tab.

        The same contract as the Playwright sessions and deliberately not the
        same implementation: `base.run_search` is Playwright, and imitating it
        approximately here would let this column mean something slightly
        different from the others. What has to match is the client the target
        sees - land on the front page, put a pointer event on the box, emit one
        key event per character - and that is what this does with zendriver's
        own API.

        `page` and `counter` are accepted and unused, for the same reason: a
        probe passes what it has without knowing which engine it holds. This
        engine keeps one tab, so there is nothing for `page` to select, and it
        declares `supports_blocking` False, so there is no `page.route` to count
        bytes through. `bytes` on these rows comes from the relay instead.
        """
        row = self._row(target, query, entry_row_url(target, query))
        row["entry"] = "home"

        self.seen_status["status"] = None
        started = time.perf_counter()
        try:
            home = getattr(target, "home_url", None)
            if not home:
                raise ValueError(
                    f"{getattr(target, 'name', target)} declares no home_url, "
                    f"so it cannot be entered through its own front page")
            # Land on the front page only when the box is not already here.
            # After a search both Google and Amazon keep their box on the
            # results page, so a held series types each query where the last one
            # landed, carrying the referrer and the history a person doing
            # several searches carries.
            try:
                self._box(target, 2)
            except Exception:
                self._run(self.tab.get(home))
            # Before the box is taken hold of, not after: clearing the wall can
            # re-render the page, and a handle taken in front of it would then
            # be stale. The Playwright path is in the same order for the same
            # reason.
            row["consent_dismissed"] = self._dismiss_consent(target)
            # `ENTRY_TIMEOUT_MS` and not `ready_timeout_ms`, which is what this
            # line used until 2026-08-13 and which is eight seconds. The
            # Playwright path gives the box sixty, so this engine was giving up
            # seven and a half times sooner than the engines it sits beside in
            # the same matrix, and the difference would have been read as
            # zendriver finding these targets harder.
            box = self._box(target, ENTRY_TIMEOUT_MS / 1000)

            self._run(box.click())
            # The box on a results page still holds the previous query, so a
            # held series appends to it: `base.clear_box` documents the three
            # rows where that was caught.
            #
            # Not zendriver's own `clear_input` or `clear_input_by_deleting`.
            # Both reach for the value setter on `HTMLInputElement.prototype`,
            # and Google's box is a textarea, so both raise `Illegal invocation`
            # on this target - measured 2026-08-13, and the append survived the
            # call. Written out here against whichever prototype the element
            # actually has, and applied to the element the keystrokes are about
            # to go to rather than to `document.activeElement`: the second
            # attempt at this cleared something else and produced
            # `mortgage rates vs savingskimchi mistakes`, which the guard below
            # caught and which is the whole reason the guard exists.
            self._run(box.apply(
                "(elem) => { if (typeof elem.value !== 'string') return false;"
                " const proto = (elem.tagName === 'TEXTAREA')"
                " ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;"
                " Object.getOwnPropertyDescriptor(proto, 'value')"
                ".set.call(elem, '');"
                " elem.dispatchEvent(new InputEvent('input', {bubbles: true}));"
                " return true; }"))
            # One `send_keys` per character rather than one for the whole
            # string. zendriver emits a real key event either way, but a box
            # that receives a 24-character query in a single burst with no gaps
            # is a stronger signal than the one this method is trying to avoid.
            # See `base.typing_delay_ms`: this is not humanization and is not
            # claimed to be.
            for char in query:
                self._run(box.send_keys(char))
                time.sleep(typing_delay_ms(rng) / 1000)

            # The same invariant `base.verify_box` documents, over CDP. Read off
            # the element itself rather than by re-selecting, so it is the box
            # the keystrokes actually went to that is checked.
            typed = self._run(box.apply("(elem) => elem.value || ''"))
            if typed != query:
                raise ValueError(
                    f"the {getattr(target, 'name', target)} search box holds "
                    f"{typed!r} after typing {query!r}, so this attempt would "
                    f"have asked a question the row does not record. Refusing "
                    f"to submit: a wrong query returns a real page and a "
                    f"plausible verdict, and nothing downstream can tell it "
                    f"from a correct one.")

            import zendriver as zd
            before = self._run(self.tab.evaluate("location.href")) or ""
            self._run(box.send_keys(zd.SpecialKeys.ENTER))
            # CDP does not hand back a navigation promise the way Playwright
            # does, so the settle is explicit: wait for the address to move,
            # then for the markup the verdict reads. Reading the tab without
            # this returns the page the query was typed into and judges the
            # entry page as the result.
            self._await_navigation(before)
            row["ready"] = self._await_ready(target)
            self._judge_tab(row, target, row["url"])
        except Exception as exc:
            record_error(row, exc)
            keep_error_body(
                self.store, row,
                lambda: self._run(self.tab.evaluate("location.href")),
                lambda: self._run(self.tab.get_content()))
        finally:
            row["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        return row

    def _await_navigation(self, before: str, timeout_s: float = 30.0) -> bool:
        """Wait until the tab leaves the address it was on. True if it did.

        False is not an error and is not recorded as one: a target that answers
        the form submission in place would look exactly like this, and the
        verdict is read off the body either way. It only stops the read from
        happening before the answer arrives.
        """
        deadline = time.perf_counter() + timeout_s
        while time.perf_counter() < deadline:
            try:
                now = self._run(self.tab.evaluate("location.href")) or ""
            except Exception:
                now = ""
            if now and now != before:
                return True
            time.sleep(0.25)
        return False

    def fetch(self, target, query: str) -> dict:
        url = target.url(query)
        row = self._row(target, query, url)

        self.seen_status["status"] = None
        started = time.perf_counter()
        try:
            self._run(self.tab.get(url))
            row["ready"] = self._await_ready(target)
            self._judge_tab(row, target, url)
        except Exception as exc:
            record_error(row, exc)
        finally:
            row["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        return row


class ZendriverEngine:
    name = "zendriver"
    # page.route is a Playwright API and there is none here, so a preset asked
    # for would be silently dropped. Declared instead.
    supports_blocking = False
    supports_headful = True
    # Takes a timezone, and gets it the cheapest way any engine here does: this
    # driver is raw CDP, so `Emulation.setTimezoneOverride` is one send rather
    # than a framework feature. The language list is left on the host's, for the
    # reason `base.ROW_FIELDS` gives.
    supports_geo_align = True
    supports_humanize = False
    runs_script = True
    # `ZendriverSession.search` exists, written against zendriver's own API
    # rather than through the shared Playwright helper. See `ChromiumEngine` for
    # why this is declared rather than discovered at the call site.
    supports_typing = True
    # This driver cannot send proxy credentials at all, so a pool arm without a
    # relay is not a degraded run, it is a run that would leave from this
    # machine's own address. `open` refuses it; this attribute is how the runner
    # knows to build one first, without knowing which engine asked.
    needs_relay = True

    @classmethod
    def check(cls) -> str:
        try:
            import zendriver  # noqa: F401
        except ImportError:
            return ("zendriver is not installed, so this engine cannot run: "
                    "pip install zendriver")
        return None

    @classmethod
    def version(cls) -> str:
        from importlib.metadata import PackageNotFoundError, version
        try:
            return f"zendriver {version('zendriver')}"
        except PackageNotFoundError:
            return "zendriver unknown"

    @contextmanager
    def open(self, *, direct: bool = False, params: dict = None,
             headless: bool = True, record_status: bool = True,
             relay_address: str = None, ready_timeout_ms: int = 8000,
             store=None, timezone_id: str = None, provider=None, **ignored):
        unavailable = self.check()
        if unavailable:
            raise EngineUnavailable(unavailable)

        import zendriver as zd

        params = params or {}
        # Resolved the same way `nmbench.relay` resolves it, so the row names the
        # provider the relay authenticated with even when the runner passed none.
        provider = None if direct else (provider or providers.load())
        browser_args = []
        if not direct:
            if not relay_address:
                raise EngineUnavailable(
                    "this driver has no way to send proxy credentials, and "
                    "Chrome takes none in --proxy-server, so a pool arm needs "
                    "an nmbench.relay address. Launching without one would "
                    "write this machine's own address into rows labelled as "
                    "pool exits. Pass relay_address, or run the direct arm.")
            browser_args.append(f"--proxy-server={relay_address}")

        loop = asyncio.new_event_loop()
        browser = None
        try:
            browser = loop.run_until_complete(
                zd.start(headless=headless, browser_args=browser_args or None))
            tab = loop.run_until_complete(browser.get("about:blank"))

            if timezone_id:
                # Before anything is navigated, and it must not be swallowed:
                # an override that failed silently would leave the browser on
                # the host timezone in a cell whose rows say it was aligned,
                # which is the one outcome worse than not aligning at all. CDP
                # rejects an unknown zone rather than ignoring it, so a typo
                # ends the session here instead of at analysis time.
                loop.run_until_complete(tab.send(
                    zd.cdp.emulation.set_timezone_override(timezone_id)))

            seen = {"status": None}
            if record_status:
                def on_response(event):
                    try:
                        if str(getattr(event, "type_", "")) not in (
                                "Document", "ResourceType.DOCUMENT"):
                            return
                        status = getattr(event.response, "status", None)
                        if status:
                            seen["status"] = int(status)
                    except Exception:
                        pass

                loop.run_until_complete(tab.send(zd.cdp.network.enable()))
                tab.add_handler(zd.cdp.network.ResponseReceived, on_response)

            try:
                chrome_version = loop.run_until_complete(
                    tab.evaluate("navigator.userAgent")) or ""
            except Exception:
                chrome_version = ""

            yield ZendriverSession(
                browser, tab, loop, label=self.name, direct=direct,
                params=params, headless=headless,
                version=f"{chrome_version[:40]} / {self.version()}",
                ready_timeout_ms=ready_timeout_ms, seen_status=seen,
                store=store, provider=provider)
        finally:
            try:
                if browser is not None:
                    # Bounded for the same reason as `_run`, and shorter: this
                    # is the shutdown of a browser that has quite possibly
                    # already died, which is exactly when it never answers.
                    # Hanging here would hold the run open on the one call whose
                    # result nobody needs.
                    loop.run_until_complete(
                        asyncio.wait_for(browser.stop(),
                                         timeout=SHUTDOWN_CEILING_S))
            except Exception:
                pass
            try:
                loop.close()
            except Exception:
                pass
