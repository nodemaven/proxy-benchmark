"""Botasaurus: the host's Chrome over raw CDP, driven by a scraping framework.

This engine exists for one axis and it is the axis this repository measured a
result on and could not attribute. `--geo align` was run three times on
2026-08-14: patchright came back flat, 34% against 35%, and zendriver lost six
sevenths of its yield, 57% against 9%, p = 0.0008. `geo_align_check.py` reads the
same zone and the same offset back from the page and from a Web Worker on either
engine, so what a detector can see is not what differs between them. The one
thing left that does is **when the override is installed**: Playwright takes
`timezone_id` as a context option, so it is in place before any target exists,
and zendriver sends `Emulation.setTimezoneOverride` to a tab already open on
about:blank.

One engine on each side of that is an anecdote. `set_locale_and_timezone` here is
documented per-tab in the vendor's own source and is sent to an open tab, so this
is a second post-tab installer, and `rebrowser` is a second pre-context one. Four
engines in a 2x2 is what can separate "post-tab installation costs yield" from
"zendriver specifically costs yield", and neither of those two sentences is
currently the one the data supports.

**Screened on 2026-08-18 before its first run, and the screening is what makes
the pairing above an argument rather than a hope.** Against zendriver,
`probes/engine_fingerprint.py` returns **the same value on 20 of its 21
markers**: `webdriver` false, 5 plugins, 2 mime types, `window.chrome` an object,
a real Intel ANGLE renderer, `pdf_viewer` true, no binding leak, no stack leak,
`screen` 800x600. The one row that differs is `inner`, 784x497 against 764x485,
which is window chrome and not a signal. Both report the same
`Chrome/149.0.0.0`, byte for byte.

That is the property the 2x2 needs and the reason this engine is worth its
module. The two post-tab installers are indistinguishable to anything reading
this layer, so if alignment costs botasaurus what it costs zendriver, the cause
is when the override is installed; and if botasaurus stays flat, the cause is
something in zendriver that a page cannot see. Either way the answer is
attributable, which it currently is not. Had the two fingerprints differed, a
divergence between them would have had a second explanation and the pair would
have bought nothing.

It is also the host's ordinary Chrome rather than a bundled build, so it carries
no Headless Shell and no `HeadlessChrome` token while headless - the single
property that sorts the DuckDuckGo table, 95 of 95 against 0 of 50. That was
reasoned from the binary until the screening; it is now read off the wire, a
clean `Chrome/149.0.0.0` on the same run where the control answered
`HeadlessChrome/148.0.7778.96`. That puts it with zendriver and seleniumbase
rather than with the Playwright family, and makes its headless column readable.

`screen` is 800x600 on a 1920x1080 machine, headless, which is Chrome's own
default window and is shared with zendriver rather than introduced here. It is
recorded and not fixed: `probes/screen_override.py` measured the metrics
override at 10 of 10 both ways against Google on 2026-08-12, so correcting it
would be an unmeasured change to one engine's launch path buying a marker no
target here has been shown to read.

The costs, and all of them are on the row rather than in a footnote.

**No `page.route`, so no byte counter and no blocking preset.** Those are
Playwright APIs and this is not Playwright. `nmbench.relay` is where bytes come
from for this engine, because it counts sockets and therefore counts the same way
for every engine that has one; on the direct arm there is no relay and the byte
column is empty. `supports_blocking` is False rather than accepting a preset and
dropping it.

**`needs_relay` is True, and the reason is the vendor's own proxy path rather
than a missing feature.** Read 2026-08-18 in
`botasaurus_proxy_authentication/__init__.py`: `is_auth_proxy` returns True as
soon as a username or a password is present in the URL, and `create_proxy` then
answers with `getchain().anonymizeProxy(...)`, where `getchain()` is
`require("proxy-chain")` over a Node.js runtime. Handing this engine a
credentialed gateway URL therefore starts a second, undeclared local hop under an
interpreter this repository does not otherwise use, and the bytes would be
counted by nobody. A credential-less address is returned unchanged, so
`nmbench.relay` in front buys four things at once: no Node, one hop instead of
two, bytes counted the way every other relayed engine counts them, and a
`relayed` column that is true. A pool arm without a relay is refused rather than
launched unproxied, because a browser that quietly ignored the proxy would write
this machine's own address into rows labelled as exits.

**`supports_typing` is False, and the mechanism is named rather than the
symptom.** `Element.send_keys` sends exactly one CDP call per character -
`Input.dispatchKeyEvent` with type `char` (`core/element.py:763`) - and
`Element.type` routes into the same method. There is no `keyDown` and no `keyUp`
anywhere in the package and no special-keys table, so there is no Enter to submit
a form with: a `char` event carrying a carriage return inserts a newline into a
textarea, and Google's search box is a textarea. Recorded this way because
Obscura's flag sat on a reason that had stopped being true for two days, and a
capability that is only argued about is one nobody re-checks.

**The status is bought with `Network.enable`**, the same purchase zendriver
documents and for the same reason: the marker the undetected-driver family exists
to remove is `Runtime.enable`, the Network domain does not create those
artefacts, and the Playwright-driven engines have it on throughout, so enabling
it here makes this column comparable rather than favoured. It is still a choice,
so `record_status=False` gives a session that touches nothing at the cost of the
same blindness Obscura had.

**`driver.get` does not raise on a failed navigation**, exactly as raw CDP does
not: the tab is left on `chrome-error://chromewebdata/` holding Chromium's own
error scaffold, and `page_html` hands it back like any other page. The guard in
`_judge_tab` is what keeps our own dead sockets out of the block rate, and it is
the one thing here that must not be simplified away.

No ceiling wrapper of zendriver's kind is needed, which is worth stating so
nobody adds one. Every CDP send in this driver is already bounded by the vendor
at `MAX_WAIT = 200` seconds (`core/connection.py:306`), which clears
`ENTRY_TIMEOUT_MS` at 60, so a browser that dies mid-series ends the identity
rather than the run.
"""
import inspect
import re
import time
from contextlib import contextmanager

from .. import providers
from .base import (
    EngineUnavailable,
    blank_row,
    keep_body,
    record_error,
    record_judgement,
)
from .chromium import _package_version

_VENDOR_DISABLE_FEATURES = re.compile(r'"--disable-features=([^"]*)"')


def merged_disable_features(*ours):
    """One `--disable-features` value carrying the vendor's names and ours.

    Chrome keeps command-line switches in a map, so only the **last**
    `--disable-features` survives and every earlier one is discarded whole -
    measured 2026-08-25 by ordering a flag with a visible network effect first
    and then last, and again 2026-08-31 with no network in it at all, on a
    feature gating a JavaScript property.

    botasaurus-driver ships its own `--disable-features` in
    `Config.default_arguments` and appends caller arguments after it, so a
    caller who passes one silently throws the vendor's away. This returns the
    union instead, so whichever switch Chrome honours, nothing anybody asked for
    is lost.

    The vendor's value is read out of its own source rather than copied here, so
    it cannot go stale behind us. It refuses rather than guessing: a value we
    cannot find is a value we would discard without knowing, and that would
    change the browser under measurement while every row still claimed it was
    the ordinary one.
    """
    from botasaurus_driver.core.config import Config

    found = _VENDOR_DISABLE_FEATURES.findall(inspect.getsource(Config.__init__))
    if len(found) != 1:
        raise EngineUnavailable(
            f"botasaurus-driver {_package_version('botasaurus-driver')} has "
            f"{len(found)} --disable-features in Config.__init__ and this "
            "engine knows how to merge exactly one. Merging the wrong set "
            "would launch a browser with features enabled that the vendor "
            "disables, or drop ours, and either way the rows would not say so. "
            "Re-read core/config.py and update merged_disable_features.")

    names = found[0].split(",") + list(ours)
    return "--disable-features=" + ",".join(dict.fromkeys(names))


class _Page:
    """The three calls the probes make, over a botasaurus driver.

    Deliberately not a general Playwright imitation, for the reason the zendriver
    shim gives: a shim that answered every Playwright method approximately would
    let a probe return numbers meaning something slightly different for this
    engine than for the others, which is worse than failing outright.
    """

    def __init__(self, driver):
        self.driver = driver

    def goto(self, url: str, **ignored):
        self.driver.get(url)
        return None

    def evaluate(self, script: str):
        # `run_js` takes a statement body and wraps it in an IIFE, where
        # Playwright's `page.evaluate` takes either a function expression or a
        # bare expression. So the arrow is called rather than unwrapped: a
        # block-bodied `() => { ... }` cannot be unwrapped by text at all,
        # because after a `return` its braces read as an object literal, and
        # every probe in this repository passes one of those.
        #
        # Promises resolve without anything being asked for here - the vendor's
        # `run_js` already passes `await_promise=True` - and that has to hold,
        # because `geo_align_check.py` reads a Web Worker and its answer is
        # asynchronous. An unresolved object here against data everywhere else
        # would read as the engine rather than as the adapter.
        body = script.strip()
        call = (f"return ({body})()" if body.startswith("()")
                else f"return ({body})")
        return self.driver.run_js(call)

    def close(self) -> None:
        pass


class BotasaurusSession:
    def __init__(self, driver, *, label, direct, params, headless, version,
                 ready_timeout_ms, seen_status, store=None, provider=None):
        self.driver = driver
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
        # A one-key dict written by the CDP handler. Same rule as the zendriver
        # and Obscura adapters: the main-frame document, last one wins, so the
        # status belongs to the page the verdict is then read from and a redirect
        # chain reports where it landed rather than where it started.
        self.seen_status = seen_status
        self.index = 0

    def new_page(self, counter: dict = None):
        """Part of the engine contract: a probe takes a page without knowing
        which engine it holds. One tab here, so this is a view onto it.

        `counter` is accepted and unused because this engine declares
        `supports_blocking` False - there is no `page.route` to count bytes
        through, and the relay counts them instead.
        """
        return _Page(self.driver)

    def _await_ready(self, target):
        selector = getattr(target, "ready_selector", None)
        if not selector:
            return None
        try:
            # `select` answers None rather than raising, so the bool is the
            # answer and the except is for a dead browser.
            return bool(self.driver.select(
                selector, wait=max(1, round(self.ready_timeout_ms / 1000))))
        except Exception:
            return False

    def _row(self, target, query: str, url: str) -> dict:
        """The columns that describe this session, whatever the entry shape.

        Factored out for the reason the other sessions factor it out: the label
        expression is what identifies an engine in every table, and two entry
        paths writing it two ways would split one engine into two columns nobody
        could pair up again.
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
        """Read the tab and judge it, or record why it cannot be judged."""
        html = self.driver.page_html
        row["html_len"] = len(html)
        row["status"] = self.seen_status.get("status")
        final_url = self.driver.current_url or url
        title = self.driver.title or ""
        # Chrome's own network error page, and it must never reach the
        # classifier. The Playwright engines get this for free because a failed
        # navigation raises; this driver does not raise, it leaves the tab on
        # `chrome-error://chromewebdata/` and hands back Chromium's error
        # scaffold. Judged, that is our own failed connection recorded as a
        # refusal by the target - the one direction of error this repository
        # cannot absorb, because it inflates a block rate with rows that cannot
        # be attributed afterwards.
        if final_url.startswith("chrome-error://"):
            record_error(row, ConnectionError(
                f"navigation to {url} ended on Chrome's error page, so "
                f"nothing from the target arrived and no verdict is "
                f"possible: the connection failed rather than the page"))
        else:
            record_judgement(row, target, final_url, title, html)
            keep_body(self.store, row, html)

    def fetch(self, target, query: str) -> dict:
        url = target.url(query)
        row = self._row(target, query, url)

        self.seen_status["status"] = None
        started = time.perf_counter()
        try:
            self.driver.get(url)
            row["ready"] = self._await_ready(target)
            self._judge_tab(row, target, url)
        except Exception as exc:
            record_error(row, exc)
        finally:
            row["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        return row


class BotasaurusEngine:
    name = "botasaurus"
    # page.route is a Playwright API and there is none here, so a preset asked
    # for would be silently dropped. Declared instead.
    supports_blocking = False
    supports_headful = True
    # Takes a timezone, and installs it on a tab that is already open. That is
    # the property this engine was added for, not an incidental one: it is the
    # second post-tab installer, against two pre-context installers, and the
    # `--geo align` result is currently unattributable without the pair.
    supports_geo_align = True
    supports_geoip = False
    supports_humanize = False
    runs_script = True
    # One `Input.dispatchKeyEvent` of type `char` per character and nothing
    # else, so there is no Enter to submit with. See the module docstring.
    supports_typing = False
    # The vendor's own auth path spawns a Node `proxy-chain` hop for any
    # credentialed proxy URL, so the relay is what keeps this to one hop and one
    # byte counter. See the module docstring.
    needs_relay = True

    @classmethod
    def check(cls) -> str:
        try:
            import botasaurus_driver  # noqa: F401
        except ImportError:
            return ("botasaurus-driver is not installed, so this engine cannot "
                    "run: pip install botasaurus-driver")
        # It drives the host's Chrome and downloads nothing, so an importable
        # package is not a runnable engine. The vendor raises a FileNotFoundError
        # from inside its own config at launch, which arrives in the elapsed
        # time of whichever cell happened to be first; asked here instead, for
        # the reason the other Chromium engines check their binary.
        try:
            from botasaurus_driver.core.config import find_chrome_executable
            find_chrome_executable()
        except Exception as exc:
            return (f"botasaurus-driver found no Chrome to drive, so this "
                    f"engine cannot run: {exc}")
        return None

    @classmethod
    def version(cls) -> str:
        return f"botasaurus-driver {_package_version('botasaurus-driver')}"

    @contextmanager
    def open(self, *, direct: bool = False, params: dict = None,
             headless: bool = True, record_status: bool = True,
             relay_address: str = None, ready_timeout_ms: int = 8000,
             store=None, timezone_id: str = None, provider=None, **ignored):
        unavailable = self.check()
        if unavailable:
            raise EngineUnavailable(unavailable)

        from botasaurus_driver import Driver, cdp

        params = params or {}
        # Resolved the same way `nmbench.relay` resolves it, so the row names the
        # provider the relay authenticated with even when the runner passed none.
        provider = None if direct else (provider or providers.load())
        proxy_url = None
        if not direct:
            if not relay_address:
                raise EngineUnavailable(
                    "this engine reaches a credentialed gateway only by "
                    "spawning a Node proxy-chain hop of its own, which would "
                    "put a second uncounted hop inside the measurement, so a "
                    "pool arm needs an nmbench.relay address. Launching "
                    "without one would write this machine's own address into "
                    "rows labelled as pool exits. Pass relay_address, or run "
                    "the direct arm.")
            # Deliberately credential-free: the vendor returns a proxy URL
            # unchanged when it carries no username or password, which is what
            # keeps Node out of the run.
            proxy_url = f"http://{relay_address}"

        # Chrome's own background traffic, which the pool pays for and this
        # repository does not measure. Measured 2026-08-19 on the server, with a
        # relay counting sockets and the browser left on `about:blank` for 60 s
        # with nothing navigated: **botasaurus 43.6 MB, seleniumbase 43.4 MB,
        # zendriver 72 KB, patchright 43 KB**. It is the safe-browsing lists,
        # the component updater and the optimization-hint models, and at
        # `--batch 1` a fresh profile pays it again on every attempt - about
        # 43 GB per thousand attempts, against a 100 GB shared quota, to measure
        # nothing.
        #
        # zendriver is the control that makes this a launch flag rather than a
        # property of driving the host's Chrome: it is the same Chrome 151 on
        # the same box and it costs 72 KB, because its own config sets these two
        # flags. Playwright sets them too, which is why the five Playwright
        # engines never showed this. So the uncontrolled variable in the matrix
        # was that some engines chatted to the vendor and some did not, and
        # setting them here removes it rather than adding one.
        #
        # **The zendriver control does not hold and the two flags do not work.**
        # Re-measured 2026-08-25, Windows, Chrome 149.0.7827.201, loopback
        # CONNECT proxy counting per authority, 60 s on `about:blank`:
        #
        #   zendriver                             39.46 MB, not 72 KB
        #   botasaurus, no flags                  45.17 MB
        #   botasaurus, both flags, run 1          0.10 MB
        #   botasaurus, both flags, run 2         39.32 MB
        #
        # zendriver sets both flags at `zendriver/core/config.py:129,132` and
        # pays 39.37 MB to the same host anyway, so the engine this note cites as
        # proof that the flags work is the one that refutes it.
        #
        # What the mistake looked like from the inside: the flags were credited
        # on a single quiet run, and a quiet run is cheap to get because the
        # fetch does not fire every time - 7 of 8 unflagged runs fetched, not 8.
        # Run 1 above reproduces the original error exactly; run 2 is the repeat
        # that should have been done in the first place.
        #
        # The flag that does work is `--disable-features=OptimizationHints`, the
        # master switch rather than the three `OptimizationGuide*` sub-features
        # SeleniumBase ships. With it the host is absent from the count in 4 of 4
        # runs across two engines. It is kept alongside the other two, which
        # still earn their place on the component updater: dropping them puts
        # 5.8 MB of CRX download back.
        #
        # The value is merged rather than passed on its own, because the vendor
        # ships a `--disable-features` of its own and Chrome honours only the
        # last one. See `merged_disable_features` above for the mechanism.
        #
        # **This comment said something else until 2026-09-01, and the part that
        # was wrong is the part that had never been run.** It read: ours wins
        # because `Config.browser_args` returns
        # `sorted(default_arguments + arguments)` and `I` sorts before `O`, so
        # give the value a name sorting before `IsolateOrigins` and this line
        # goes silently dead. Measured with
        # `lab/probes/probe_botasaurus_flag_order.py`, against
        # botasaurus-driver 4.0.101:
        #
        #   .browser_args, ours named AAAOptimizationHints   ours FIRST
        #   the launch path, same value                      ours LAST, wins
        #
        # `browser_args` is a property nothing on the launch path reads.
        # `Browser.start` calls `Config.__call__` (`core/browser.py:326`), which
        # copies `default_arguments` and appends the caller's after them, so
        # ours wins by position and the alphabet has nothing to do with it. The
        # warned-about hazard does not exist; a real one was sitting next to it
        # unnoticed.
        #
        # What the mistake looked like from the inside: `browser_args` is the
        # obviously-named public property on the config object, the `sorted()`
        # in it is real, and `I` really does sort before `O` - the explanation
        # fitted every character of the evidence and predicted the right
        # outcome. It was never checked which function the browser is launched
        # from, which is one `inspect` call. A cause that fits is not a cause
        # that was checked, and here it was load-bearing for a warning that
        # would have sent the next person to the wrong line.
        #
        # **The cost of getting it wrong was not the warning, it was what the
        # warning distracted from.** Ours winning meant the vendor's
        # `IsolateOrigins,site-per-process` was discarded whole on every
        # botasaurus row this repository has ever written, so site isolation was
        # left ON in an engine whose vendor disables it, and nothing in any row
        # said so. Same probe, arm 2: names lost `['IsolateOrigins',
        # 'site-per-process']`. The merge is what fixes that, and it is the same
        # fix proposed upstream as botasaurus-driver #29.
        #
        # Rows written before 2026-09-01 carry the unmerged flag. Nothing here
        # measured what site isolation is worth to a verdict, so this is a
        # difference between old rows and new ones and not a correction to any
        # published number - do not quote it as one until it has been measured.
        #
        # Not a hardening of the browser under test: no flag here is visible to
        # a page, and `ChromiumEngine` - the control - is deliberately left
        # alone whatever it costs.
        driver = Driver(headless=headless, proxy=proxy_url, arguments=[
            merged_disable_features("OptimizationHints"),
            "--disable-background-networking", "--disable-component-update"])
        try:
            driver.get("about:blank")

            if timezone_id:
                # Before anything is navigated, and it must not be swallowed: an
                # override that failed silently would leave the browser on the
                # host timezone in a cell whose rows say it was aligned, which is
                # the one outcome worse than not aligning at all. CDP rejects an
                # unknown zone rather than ignoring it, so a typo ends the
                # session here instead of at analysis time.
                #
                # The locale is deliberately left on the host's. See
                # `base.ROW_FIELDS`: every marker a verdict rests on is an
                # English string, so aligning the language invites the target to
                # answer in another one and be judged by a rule that cannot
                # match it.
                driver.set_locale_and_timezone(timezone_id=timezone_id)

            seen = {"status": None}
            if record_status:
                driver.run_cdp_command(cdp.network.enable())

                def on_response(request_id, response, event):
                    # The vendor's wrapper re-raises whatever this throws, into
                    # the CDP event loop, so it swallows its own failures: a
                    # missing status costs the column and must not cost the run.
                    try:
                        kind = getattr(event, "type_", None)
                        if getattr(kind, "value", kind) != "Document":
                            return
                        status = getattr(response, "status", None)
                        if status:
                            seen["status"] = int(status)
                    except Exception:
                        pass

                driver.after_response_received(on_response)

            try:
                agent = driver.run_js("return navigator.userAgent") or ""
            except Exception:
                agent = ""

            yield BotasaurusSession(
                driver, label=self.name, direct=direct, params=params,
                headless=headless,
                version=f"{agent[:40]} / {self.version()}",
                ready_timeout_ms=ready_timeout_ms, seen_status=seen,
                store=store, provider=provider)
        finally:
            try:
                driver.close()
            except Exception:
                pass
