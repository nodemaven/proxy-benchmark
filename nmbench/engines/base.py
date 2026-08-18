"""The contract every engine implements.

An engine is a way of making a request. A session is one identity making
several: the same browser, the same exit address, the same device as far as the
target can tell. Batching queries into a session is not an optimisation, it is
the unit the numbers are about - a pass rate over ten fresh browsers and a pass
rate over one browser doing ten searches are different measurements, and the row
records which one it was.

Adding a framework means adding a module here and one line in the registry. The
runner is never edited, because a runner that knows the name of a framework
cannot be trusted to have treated it the same as the others.

Every engine emits the same columns. A column an engine cannot fill stays None
rather than being dropped: absent evidence and zero are not the same, and a
missing key turns into a silent zero the moment anything sums a column.
"""
import time

# The full row schema. Documented here because it is the published interface of
# data/runs/: anyone forking this repository reads these files, not our code.
ROW_FIELDS = (
    # what was run
    "engine", "engine_version", "target", "query", "url", "cell",
    "preset", "direct", "headless", "humanize", "params", "session_index",
    "session_exit_prefix",
    # Which gateway carried the request. None on a direct row, which reached no
    # gateway at all, and that is a different statement from an unset column.
    #
    # It is on the row and not merely in the run's filename because `params` is
    # written in this harness's own vocabulary while every vendor spells those
    # settings differently: `country-us` does not say what left the machine
    # until the row also says whose dialect built the username. And the cell key
    # omits the provider whenever the axis is not varied, so the key is not a
    # substitute - a row is attributable here or nowhere.
    #
    # Every row recorded before this column existed came through nodemaven, so
    # an absent value reads as that rather than as unknown.
    "provider",
    # Whether the request went through `nmbench.relay` rather than straight to
    # the gateway. Recorded because it changes what one other column means: the
    # relay adds a loopback hop, so `elapsed_ms` on a relayed row is not
    # comparable against an unrelayed one. Bytes and verdicts are. Without this
    # column a matrix mixing relayed and unrelayed engines would show a latency
    # difference that belongs to our own plumbing and read as an engine result.
    "relayed",
    # Whether the browser's timezone was aligned with the exit address.
    # Recorded because only some engines can do it: a run that mixed aligned and
    # unaligned engines and did not say which is a comparison nobody can read
    # afterwards.
    #
    # Timezone and not the language list, and the asymmetry is deliberate rather
    # than partial work. Every marker a verdict rests on in `targets.py` is an
    # English string, and the URLs pin `hl=en` for that reason, so aligning the
    # locale to the exit country invites the target to answer in another
    # language and be judged by a rule that cannot match it. That would move
    # verdicts in the aligned arm and read as the target reacting to alignment
    # when it was us. The two halves are also not equally suspicious: English on
    # a German address is an ordinary person, a Moscow timezone on a Texan
    # address is not a person at all.
    "geo",
    # what came back
    "status", "final_url", "title", "verdict", "verdict_reason", "markers",
    "html_len", "bytes", "blocked", "allowed", "ready", "elapsed_ms", "error",
    # Where the body was kept, when it was kept. A verdict is a claim about a
    # body, so a row that cannot be traced back to one cannot be rechecked.
    "artifact",
    # How the request reached the results: "url" for a navigation straight to
    # the target's search URL, "home" for landing on its front page and typing
    # the query into its own box. Every row written before 2026-08-13 is "url",
    # and the two are not the same client - one arrives with a query string and
    # no keystroke behind it, the other with a session, a referrer and a form
    # submission. Recorded rather than assumed, because the whole probe-and-hold
    # method rests on the difference and a run that mixed the two without saying
    # so would be unreadable.
    "entry",
    # Whether a consent wall was cleared before the query was typed. Only the
    # typed path can fill it, so it is None on every `fetch` row, and None here
    # means "this entry shape never meets one" rather than "there was none".
    # Recorded because clearing a wall is an interaction the target sees, and
    # because the wall is intermittent: without the column, a window where it
    # stopped being dismissed would read as the typed entry shape getting worse.
    "consent_dismissed",
)


# How long any step of the typed entry shape is given: the front page to load,
# the box to appear, the results to arrive after Enter. One constant, exported,
# because it is the number the engines have to agree on. Measured 2026-08-13:
# the zendriver path waited `ready_timeout_ms` (8 s) for the search box while
# every Playwright path waited 60 s, and the two rows that timed out on it both
# landed in one arm of the axis the run was measuring. An engine that gives up
# sooner than its neighbours does not measure a harder target, it measures the
# harness, and it does so in a column nobody would think to check.
#
# Distinct from `ready_timeout_ms`, which is short on purpose: that one asks
# whether the results finished rendering and False is a real answer there. This
# one guards a step that has no answer, only a completed attempt or a lost one.
ENTRY_TIMEOUT_MS = 60000


def typing_delay_ms(rng, low: int = 45, high: int = 140) -> int:
    """Per-keystroke delay for a typed query.

    Not humanization and not claimed to be: there is no cursor path, no dwell
    curve and no correction. It exists because `fill()` sets the value in one
    assignment, which produces one input event and no key events at all, and a
    box that receives a 24-character query in zero milliseconds is a stronger
    signal than the one this method is trying to avoid. Camoufox's `humanize`
    is the real thing and it is a separate axis with its own flag.
    """
    return rng.randint(low, high)


def clear_box(handle) -> bool:
    """Empty a search box that is holding the previous query. Playwright.

    Returns whether there was anything to clear. Both of these targets keep
    their box on the results page **with the last query still in it**, which is
    the whole point of holding a session and is also a trap: measured 2026-08-13
    on a three-query held series, the second query landed on
    `q=kimchi+mistakesmortgage+rates` and the third on
    `q=kimchi+mistakesmortgagswimming`. Every row came back `ok` and every one
    of them was a different query from the one the row records. A held series
    without this does not measure what the queries file says it measures, and
    nothing in the output shows it.

    Select-all and type over it, not `fill("")`. `fill` assigns the value in one
    step and produces no key events, which is the shape `typing_delay_ms` exists
    to avoid; a selection replaced by typed characters is what a person
    produces. The selection is only made when there is something in the box, so
    the front page - the common case, and an empty box - still sees one click
    and nothing else.
    """
    try:
        existing = handle.input_value()
    except Exception:
        # Not an input or textarea. Nothing to clear and nothing to report: the
        # caller's typing is about to fail loudly on its own if that is wrong.
        return False
    if not existing:
        return False
    handle.press("Control+a")
    return True


def verify_box(handle, query: str, target) -> None:
    """Refuse to submit a box that does not hold the query the row claims.

    An `error` row is a bad outcome and a wrong row is a worse one. The
    append bug this guards against produced three consecutive `ok` rows for
    queries that were never asked, and nothing in the output distinguished them
    from a working held series - the pass rate was 100% and the queries were
    fiction. There is no analysis in this repository that could have caught it,
    because every column was internally consistent.

    So the invariant is checked at the one moment it is cheap: after the last
    keystroke and before Enter. A mismatch stops the attempt, and
    `record_error` files it as ours rather than as a verdict about the target,
    which is exactly what it is.
    """
    try:
        typed = handle.input_value()
    except Exception:
        # Not an input or a textarea, so there is no value to compare. Silence
        # here is safe in a way silence after a failed comparison is not.
        return
    if typed != query:
        raise ValueError(
            f"the {getattr(target, 'name', target)} search box holds "
            f"{typed!r} after typing {query!r}, so this attempt would have "
            f"asked a question the row does not record. Refusing to submit: a "
            f"wrong query returns a real page and a plausible verdict, and "
            f"nothing downstream can tell it from a correct one.")


def submit_query(page, target, query: str, *, rng,
                 timeout_ms: int = ENTRY_TIMEOUT_MS):
    """Type the query into the target's own box and press Enter. Playwright.

    Returns the navigation response, or None when the browser did not report one
    - the caller records `status` from it and must treat None as absent rather
    than as a failure, the same way `fetch` does.

    The click before the typing is deliberate. Google's box is focused by script
    on load, so typing into it usually works without one, and "usually" is what
    produces a run of empty queries nobody can explain afterwards. The click
    also puts a real pointer event on the element, which the keystrokes then
    follow, in the order a person produces them.

    Everything acts on the handle `wait_for_selector` returned, never on the
    selector again. Measured 2026-08-13 on Google's front page: the selector
    matches more than one element, `wait_for_selector` resolves to the first
    *visible* one and `page.click` resolves to the first one in document order,
    which is a hidden field. The result was a 30 s actionability timeout
    recorded as `error` - an attempt that never reached the target at all, from
    a selector that had already been found. One lookup, one element.
    """
    box = getattr(target, "search_box", None)
    if not box:
        raise ValueError(
            f"{getattr(target, 'name', target)} declares no search_box, so it "
            f"cannot be entered by typing. Add one to the target, or run this "
            f"target through the URL path instead - do not guess a selector "
            f"here, because a wrong one fails as an empty query and is recorded "
            f"as the target refusing.")
    handle = page.wait_for_selector(box, timeout=timeout_ms, state="visible")
    handle.click()
    clear_box(handle)
    handle.type(query, delay=typing_delay_ms(rng))
    verify_box(handle, query, target)
    with page.expect_navigation(wait_until="domcontentloaded",
                                timeout=timeout_ms) as navigation:
        handle.press("Enter")
    return navigation.value


def entry_row_url(target, query: str) -> str:
    """What the `url` column holds for a row that was typed rather than fetched.

    The results URL, even though nothing navigated to it. This looks like a lie
    and is the opposite: `url` is read by exactly one thing, `report.was_served`,
    which asks whether the response is still on the host and path that were
    asked for. Recording the homepage instead would compare `/` against
    `/search` and mark every typed row as diverted - the harness scoring its own
    entry shape as a refusal by the target, which is the one direction of error
    this repository cannot absorb.

    So the column means "the page this attempt was asking for", which is the
    same for both entry shapes, and `entry` is what says how it was asked for.
    Google's typed search lands on `/search` and Amazon's on `/s`, so the
    comparison holds for both and the query string is ignored either way.
    """
    return target.url(query)


def ensure_entry(page, target, *, timeout_ms: int = ENTRY_TIMEOUT_MS) -> None:
    """Put the page somewhere carrying the target's search box. Playwright.

    Navigates to the front page only when the box is not already present. That
    is not an optimisation either: after a search both Google and Amazon keep
    their box on the results page, so a held series types each query where the
    last one landed, carrying the referrer and the history a person doing
    several searches carries. Going back to the homepage between them would
    throw that away and measure a different client on every query.
    """
    home = getattr(target, "home_url", None)
    box = getattr(target, "search_box", None)
    if not home or not box:
        raise ValueError(
            f"{getattr(target, 'name', target)} declares no home_url and "
            f"search_box, so it cannot be entered through its own front page. "
            f"Add both to the target, or run it through the URL path instead - "
            f"a guess at either one fails as an empty query and is recorded as "
            f"the target refusing.")
    # Visible, not merely present. The same selector matches hidden fields on
    # both of these targets, and a present-but-hidden match would make this
    # decide the box is already here and hand `submit_query` a page with nothing
    # to type into.
    visible = any(handle.is_visible() for handle in page.query_selector_all(box))
    if not visible:
        page.goto(home, wait_until="domcontentloaded", timeout=timeout_ms)


def dismiss_consent(page, target, *, timeout_ms: int = 10000) -> bool:
    """Clear a consent wall the target serves over its own front page. Playwright.

    Returns whether one was cleared, which the caller records: a run where the
    wall stopped being dismissed would otherwise show up as the entry shape
    getting worse, and the entry shape is the thing being measured.

    The selectors come from the target, never from here, for the same reason
    `search_box` does - a probe or an engine that knew which button Google
    serves would be one that could clear one target's wall and not another's,
    and the difference would read as a target result.

    Nothing is raised when no wall is up, because on both of these targets the
    wall is intermittent: the same fresh profile met it on three navigations out
    of three in one window and on none in another, measured 2026-08-13. A method
    that insisted on finding one would turn the absence of an obstacle into an
    error row.

    Order matters and belongs to the target: the first selector that matches a
    *visible* element wins. Visibility is checked because these panels are
    overlays that stay in the document after they are cleared - the button is
    still there, and a presence test would click a hidden control on every
    subsequent query.
    """
    for selector in getattr(target, "consent_dismiss", None) or ():
        handle = page.query_selector(selector)
        if handle is None or not handle.is_visible():
            continue
        handle.click(timeout=timeout_ms)
        # The click may answer in place or navigate; both were observed on
        # Google in one window. Waiting for the load state covers the second and
        # returns immediately for the first, and the panel is then given a
        # moment to come down before anything tries to type underneath where it
        # was.
        try:
            page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        except Exception:
            pass
        page.wait_for_timeout(500)
        return True
    return False


def await_ready(page, target, timeout_ms: int = 8000):
    """Wait for the markup the verdict reads, and say whether it arrived.

    `domcontentloaded` fires before a client-rendered target has built anything.
    Snapshotting there catches Google's "enable JavaScript" scaffold and records
    it as a refusal, which is the harness failing rather than the target:
    measured 2026-08-10, 91,577 characters, nothing blocked, scripts allowed.

    A rejection page never grows the selector, so the timeout is short and
    expected. Three states, not two: True when the markup appeared, False when
    we waited and it never did, None when the target declares no selector.
    "Waited and never saw it" and "never looked" are different pieces of
    evidence, and a boolean would merge them.

    It lives here because `ready` is a column every Playwright engine writes,
    and four copies of the wait is four places for the timeout to drift. That
    had already happened once: Obscura's copy took no timeout at all, so a
    matrix raising `--ready-timeout` raised it for eight engines and not the
    ninth, which reads in the report as an engine difference.
    """
    selector = getattr(target, "ready_selector", None)
    if not selector:
        return None
    try:
        page.wait_for_selector(selector, timeout=timeout_ms, state="attached")
        return True
    except Exception:
        return False


def run_search(page, target, query: str, row: dict, *, rng,
               ready_timeout_ms: int = 8000,
               timeout_ms: int = ENTRY_TIMEOUT_MS,
               store=None, counter: dict = None) -> dict:
    """One typed query on a page the caller is holding. Playwright.

    Shared by every Playwright-driven session so the entry shape is a single
    implementation. An engine that found its way to the box its own way would be
    a different client from the others, and the entry axis is only readable if
    the entry is the one thing that changed.

    The page is passed in rather than made here, and that is the whole
    difference from `fetch`. `fetch` opens a page per query, which is the right
    shape for a matrix cell asking N independent questions. A held identity asks
    them in one tab, so the page has to outlive the query.
    """
    counter = {} if counter is None else counter
    # Differenced rather than read, because the counter belongs to the page and
    # the page outlives the query. `fetch` gets a fresh page and a fresh counter
    # per attempt, so reading it whole is correct there; here the tenth query in
    # a held identity would otherwise be charged the whole session's traffic and
    # cost per page would climb with position for no reason but the arithmetic.
    base = dict(counter)
    row["entry"] = "home"
    started = time.perf_counter()
    try:
        ensure_entry(page, target, timeout_ms=timeout_ms)
        # After the entry page is up and before anything is typed. Recorded on
        # the row rather than done quietly, because clearing a wall is an
        # interaction the target sees and a query typed after one is not the
        # same client as a query typed without one.
        row["consent_dismissed"] = dismiss_consent(page, target)
        response = submit_query(page, target, query, rng=rng,
                                timeout_ms=timeout_ms)
        # None means the browser reported no navigation response, not that the
        # navigation failed. Left absent, the same way `fetch` leaves it absent,
        # because a zero here would be counted as a status by anything reading
        # the column.
        if response is not None:
            row["status"] = response.status
        row["ready"] = await_ready(page, target, ready_timeout_ms)
        html = page.content()
        row["html_len"] = len(html)
        record_judgement(row, target, page.url, page.title(), html)
        keep_body(store, row, html)
    except Exception as exc:
        record_error(row, exc)
        keep_error_body(store, row, lambda: page.url, page.content)
    finally:
        row["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        for field in ("bytes", "blocked", "allowed"):
            row[field] = counter.get(field, 0) - base.get(field, 0)
    return row


class EngineUnavailable(RuntimeError):
    """The engine cannot run on this machine, with the reason and the fix."""


def blank_row(engine: str, engine_version: str, query: str, url: str,
              **extra) -> dict:
    """A row with every column present, so nothing is silently absent."""
    row = dict.fromkeys(ROW_FIELDS)
    row.update({
        "engine": engine, "engine_version": engine_version,
        "query": query, "url": url,
        "html_len": 0, "bytes": 0, "blocked": 0, "allowed": 0,
        # The default rather than something each engine sets, because a
        # navigation to the search URL is what every `fetch` does and an engine
        # that forgot the column would write None, which reads as "not recorded"
        # and is indistinguishable from the entry axis never having existed.
        # `run_search` overrides it for the typed path.
        "entry": "url",
    })
    row.update(extra)
    return row


def record_judgement(row: dict, target, url: str, title: str, html: str) -> dict:
    """Judge a response and store the reasoning next to the verdict.

    Centralised so no engine can record a verdict without the evidence behind
    it. A bare verdict is not reviewable: "block" meant both "the target refused
    us" and "our client cannot run JavaScript" until the two were separated, and
    no row written before that could be re-read to tell which.
    """
    from ..targets import fingerprint

    judgement = target.judge(url, title, html)
    row["verdict"] = judgement.verdict
    row["verdict_reason"] = judgement.reason
    row.update(fingerprint(url, html))
    return row


def keep_body(store, row: dict, html: str) -> None:
    """Archive the body this verdict was read from, if the run is keeping them.

    Called after the verdict so the store can decide by it: failures are kept in
    full, successes are sampled. Centralised so every engine archives on the
    same policy - an engine that kept more of its own bodies would look better
    than the others under any later re-judging.
    """
    if store is not None:
        row["artifact"] = store.save(row, html)


def keep_error_body(store, row: dict, read_url, read_html) -> None:
    """Archive whatever the browser was showing when the attempt threw.

    An `error` row is the only row with no body behind it, and the typed entry
    shape made that gap expensive. Measured 2026-08-13: a row read
    `Timeout waiting for element with selector 'input#twotabsearchtextbox'` and
    carried nothing else, while Amazon's own `ref=cs_503` throttle page has no
    such box on it. Our selector missing and the target refusing the front page
    produce the identical row without this, and they fall on opposite sides of
    every question the probe asks.

    The verdict stays `error` and no judge is called. The query was never
    submitted, so there is no answer to judge and nothing here may become a
    claim about the target - `AmazonSearch.judge` would file a perfectly good
    front page under the catch-all, which is the failure this would be
    introducing rather than fixing. What is fixed is that the distinction can be
    made offline afterwards, the way the Amazon archive already was.

    The readers are passed as callables because the two entry paths get at the
    body differently, and both can fail: a browser that lost its transport
    cannot be asked anything, and this must never turn one lost attempt into a
    lost session.
    """
    try:
        html = read_html() or ""
    except Exception:
        return
    if not html:
        return
    row["html_len"] = len(html)
    try:
        row["final_url"] = read_url()
    except Exception:
        pass
    keep_body(store, row, html)


def record_error(row: dict, exc: Exception) -> dict:
    """An engine that threw produced no evidence, so it produces no verdict.

    `error` is not a claim about the target. Counting it as a failure would let
    a flaky local network lower a provider's score.
    """
    row["error"] = f"{type(exc).__name__}: {exc}"
    row["verdict"] = "error"
    row["verdict_reason"] = "the attempt did not complete, so the target was "\
                            "never judged"
    return row


def validate_preset(preset: str, target) -> None:
    """Refuse a preset that would make the target fail for our reasons.

    Checked before a run rather than after, because the failure it prevents is
    indistinguishable from a real block in the output.
    """
    from ..blocking import PRESETS, SCRIPT_BLOCKING

    if preset not in PRESETS:
        raise ValueError(f"unknown preset {preset!r}, known: {sorted(PRESETS)}")
    if preset in SCRIPT_BLOCKING and getattr(target, "needs_script", False):
        raise ValueError(
            f"preset {preset!r} blocks script and {target.name} builds its "
            f"results in the browser: every attempt would return the no-JS stub "
            f"and be recorded as a failure caused by this harness, not by the "
            f"target. Use 'light', which still drops images, media and fonts."
        )
