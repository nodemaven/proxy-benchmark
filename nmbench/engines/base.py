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
import re
import time

from .. import host

# The full row schema. Documented here because it is the published interface of
# data/runs/: anyone forking this repository reads these files, not our code.
ROW_FIELDS = (
    # what was run
    "engine", "engine_version", "target", "query", "url", "cell",
    "preset", "direct", "headless", "humanize", "params", "session_index",
    "session_exit_prefix",
    # Which humanization was applied, where `humanize` above says only whether
    # any was. Three values: "off", "engine" for a browser that synthesises its
    # own input - Camoufox and cloak - and "trueman" for the model in
    # `nmbench.pointer`, driven from `nmbench.humanize`.
    #
    # A second column rather than widening `humanize` into a string, and the
    # reason is a trap rather than a preference. `data/runs/` is a published
    # interface: anyone forking this reads the files, and the obvious way to
    # read this column is `if row["humanize"]`. Every string is truthy, so
    # "off" would have made every row read as humanized in every such reader,
    # including ours, and nothing would have raised. The bool keeps working and
    # this says which model.
    #
    # Absent on every row written before 2026-09-03. For those, `humanize=true`
    # necessarily meant the engine's own - it was the only kind there was - so
    # the old rows are readable without being rewritten.
    "humanize_mode",
    # Wall time inside `elapsed_ms` that was spent walking the cursor, in ms.
    # None when no pointer was driven, which is every row that is not
    # `humanize_mode=trueman`, and None is right there rather than 0: the walk
    # did not take no time, it did not happen.
    #
    # It exists for the same reason `relayed` does, one column up. A walk to the
    # search box is on the order of a second of deliberate waiting, all of it
    # between the timer starting and the verdict, so without this column a
    # humanized arm reads as a slower engine and the difference belongs to our
    # own plumbing. `elapsed_ms - pointer_ms` is the comparable number.
    "pointer_ms",
    # Which of the two device profiles the session drew. The model has no single
    # "human pointer": the two captured traces differ from each other on 5 of the
    # detector's 19 metrics, so a device is drawn per session and the interval
    # and wheel constants hang off it. Without this column two rows of the same
    # arm are not comparable on any timing metric and nothing says why.
    "pointer_device",
    # How many paced points overran their slot, against how many were paced.
    # This is the column that says whether the run measured the model's timing
    # or the driver's: `Pacer` never emits below the cost of an awaited round
    # trip, so when the cost exceeds the ask the page sees the cost instead.
    #
    # Measured 2026-09-03, `lab/probes/humanize_smoke.py`, 18 paced points an
    # arm: 1 overrun headful and 14-16 headless, because an awaited
    # `page.mouse.move` is frame-bound at one 60 Hz frame with no window while
    # the model asks for a ~7.1 ms median. So a headless `trueman` row carries
    # the model's geometry and the transport's rhythm, and this ratio is how a
    # reader tells that row from a headful one without knowing how it was
    # launched. Differenced per attempt like `pointer_ms`, because the pacer
    # belongs to the session and a held identity would otherwise charge its
    # tenth query with the whole session's overruns.
    #
    # **`pointer_points = 0` on a `trueman` row is legitimate and does not mean
    # the hand was absent.** A walk of zero length emits zero points, measured
    # 2026-09-03, and that is reachable in a held series: `ensure_entry` does not
    # re-navigate when the box is already on the results page, so a target whose
    # box sits at the same coordinates after a search leaves the cursor already
    # on it. Absent is None on these columns and zero is a walk that had nowhere
    # to go; do not collapse the two when aggregating.
    "pointer_overruns",
    "pointer_points",
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
    # The engine's JA4 TLS fingerprint, read off the ClientHello as it passed
    # through `nmbench.relay`. See `nmbench.tlsfp` for how it is computed and
    # what it was checked against.
    #
    # It is here because it is the layer `engine_fingerprint.py` states it
    # cannot reach: everything that probe reads is JavaScript, negotiated after
    # the connection exists, and the ClientHello is sent before a byte of script
    # runs. The standing explanation for Amazon serving Camoufox and throttling
    # the Playwright-driven engines is that the handshake sorts them, and that
    # explanation has only ever been checked out of band, by a separate probe
    # asking an echo service about a connection nobody benchmarked. On the row
    # it is checkable against the verdict in the same line.
    #
    # **It is None on more rows than it is filled on, and the reason is
    # structural rather than missing work.** The fingerprint is read at the
    # relay, and only the three engines that declare `needs_relay` have one:
    # `zendriver`, `seleniumbase` and `botasaurus`. The Playwright-driven
    # engines take proxy credentials directly, so no ClientHello of theirs
    # passes through this process. Measured 2026-09-02 over `data/runs/`: 3815
    # of 16579 attempt rows are `relayed`, so a column filled this way would
    # have covered 23% of the history - and not `patchright`, which is the
    # engine the largest open question in `RESULTS.md` rests on.
    #
    # That is a real limit and it is left visible rather than papered over: the
    # `relayed` column immediately above already says which rows can carry this
    # one, so an absence here reads as "this engine does not pass its handshake
    # through us" and never as "the handshake was not recorded".
    #
    # The other engines are covered off the row, by `tls_clienthello.py`, which
    # points each one at a local listener that answers nothing and reads the
    # first record. That is a per-engine value and not a per-attempt one, so it
    # does not fill this column and is not meant to: it says what the engine's
    # handshake is, while this column says what it was on the connection that
    # produced this verdict. Read the probe's run file beside the matrix.
    "tls_ja4",
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
    # which machine produced the row
    #
    # `host` is a label for the computer, `host_os` is its system, kernel release
    # and architecture, `host_cpus` is its core count. Collected in `nmbench.host`
    # with no browser and no network, so every engine fills them - including the
    # two that run no script and have no page to ask.
    #
    # These are here because the largest unexplained result in this repository is
    # a difference between two computers. Measured 2026-08-26, one target, one
    # engine, one entry shape and one set of gateway parameters, run from two
    # machines in overlapping hours: 39% (24/61) against 0% (0/84), separated at
    # p = 3.7e-11. `RESULTS.md` attributes those rows to a host by reading their
    # timestamp, which is only possible because the two machines happened to run
    # at different times - host and date are one variable under two names there,
    # and no cut of the files can undo it.
    #
    # Absent means the row was written before these columns existed, and for
    # those rows the timestamp remains the only handle there is. It does not mean
    # the machine was unknown at the time: it means nobody wrote it down.
    #
    # `host` alone is deliberately not enough to explain a split, and that is the
    # point of the other two. A label groups the rows; the system and the core
    # count are the first two properties that reach a target on their own path,
    # through the User-Agent's platform token and through
    # `navigator.hardwareConcurrency`. What the page sees is a larger set than
    # this and is not a property of the host - the WebGL renderer, the screen and
    # the handshake vary by engine and by headless mode on one machine, so they
    # belong to the session and are not these columns.
    "host", "host_os", "host_cpus",
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


def browser_build(user_agent: str) -> str:
    """The browser build out of a User-Agent, for engines that expose no other.

    The Playwright engines read `browser.version` and get `151.0.7922.34`.
    `zendriver` and `botasaurus` expose no such handle, so both took the User-
    Agent and sliced it to 40 characters - which keeps `Mozilla/5.0 (Windows NT
    10.0; Win64; x64` and throws away the one token in the string that anybody
    would want. Measured on `tls_clienthello_20260902T180555Z`: eight engines
    share a TLS fingerprint that is decided by the Chrome build, and these two
    are the only ones whose rows cannot say which build they ran. The variable
    the finding turns on was cut off by a slice.

    Falls back to the same 40-character slice when no build is there, so a
    Firefox UA or an empty string still produces something readable rather than
    an empty column.

    **This changes the format of `engine_version` on those two engines**, so
    rows before 2026-09-02 read `Mozilla/5.0 (Windows NT 10.0; Win64; x64` and
    rows after read `149.0.7827.201`. The discontinuity is where the column
    started carrying the answer; the old rows genuinely did not record it, and
    rewriting them to look as though they did would be worse.
    """
    if not user_agent:
        return ""
    match = re.search(r"(?:Chrome|Chromium|Firefox)/(\d+(?:\.\d+)+)",
                      user_agent)
    return match.group(1) if match else user_agent[:40]


HUMANIZE_MODES = ("off", "engine", "trueman")


def humanize_mode(value) -> str:
    """Normalise whatever the caller passed into one of `HUMANIZE_MODES`.

    The option was a boolean until 2026-09-03 and is a mode now, and this exists
    so the change cannot go halfway. Probes outside this package pass
    `humanize=False` positionally-by-keyword, `benchmark.py` used to pass
    `args.humanize` straight from a `store_true`, and a session that took a
    bool and stored it would write `humanize_mode=False` into a column the
    schema says holds a string.

    True maps to "engine" rather than to "trueman", which is the direction that
    cannot silently change what an existing caller does: "engine" is what
    `--humanize` meant for the whole time it was a boolean, and every row on
    disk carrying `humanize=true` was produced by Camoufox's own input.
    """
    if value is True:
        return "engine"
    if value is False or value is None:
        return "off"
    if value not in HUMANIZE_MODES:
        raise ValueError(
            f"humanize={value!r} is not one of {HUMANIZE_MODES}. This is a mode "
            f"and not a flag since 2026-09-03: 'engine' is the browser's own "
            f"input synthesis and 'trueman' is the model in `nmbench.pointer`, "
            f"and a run that could not tell them apart would put two different "
            f"clients in one column")
    return value


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


def submit_query(page, target, query: str, *, rng, hand=None,
                 timeout_ms: int = ENTRY_TIMEOUT_MS):
    """Type the query into the target's own box and press Enter. Playwright.

    Returns the navigation response, or None when the browser did not report one
    - the caller records `status` from it and must treat None as absent rather
    than as a failure, the same way `fetch` does.

    `hand` is a `nmbench.humanize.PointerHand` or None. When it is given the
    cursor walks to the box along the model in `nmbench.pointer` before the
    click; when it is None the click is Playwright's own, which teleports. The
    difference is the whole of what `--humanize trueman` does on this path, and
    it is one argument deep so that the two arms differ in nothing else.

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
    if hand is None:
        handle.click()
    else:
        hand.click(page, handle)
    clear_box(handle)
    handle.type(query, delay=typing_delay_ms(rng))
    verify_box(handle, query, target)
    # `no_wait_after=True` is what makes the Enter a keystroke and nothing else,
    # and it is the fix rather than a tuning knob. Playwright's `press` is not a
    # key press: read off the shipped driver 2026-09-05,
    # `patchright/driver/package/lib/coreBundle.js`, `_press` wraps the keystroke
    # in `waitForSignalsCreatedBy(progress, !options.noWaitAfter, ...)`, which
    # holds a `SignalBarrier` until any navigation the keystroke started has
    # **committed** - the barrier waits on `Frame.Events.InternalNavigation` on
    # the main frame, so it is the same event `expect_navigation` waits for
    # first, and not the load state it waits for second. That overlap is the
    # defect: without this flag the commit is waited for **twice** - once
    # implicitly inside `press`, once by the `expect_navigation` below - and the
    # two waits carried different budgets, because `press` was passed no timeout
    # and fell back to Playwright's default of 30 s against our 60 s. The inner
    # wait therefore always tripped first and the outer `timeout_ms` was
    # unreachable on exactly the case it was written for.
    #
    # Measured on `probehold_20260904T214642Z`: a row on an RU exit
    # (AS204272) died with `ElementHandle.press: Timeout 30000ms exceeded` and a
    # call log holding one line, `elementHandle.press("Enter")`. That log is
    # complete rather than truncated - `_press` has exactly one `progress.log`
    # and everything after it is silent waiting - which is why the failure reads
    # as "the key never went in" and was in fact "the key went in and the
    # navigation never finished". The walk, the click, the typing and
    # `verify_box` had all already succeeded on that row.
    #
    # It is rare and it is old, and the first reading of it here was wrong. It
    # was called new to that run on the strength of one comparison run that
    # happened to be the cleanest in the archive; over every `probehold_*` run
    # before it, 3120 probe rows, the same timeout is there **3 times, 0.10%**,
    # against an overall `error` share of 4.8% that swings from 0% to 15% run to
    # run. So the run's 2 errors in 10 rows are unremarkable - Fisher against the
    # pooled share gives p=0.082 - and picking the quiet run as the baseline was
    # what made them look like a new defect. The lesson is the sampling one this
    # repository already has written down twice: a rate needs a stated window,
    # not a chosen comparison.
    #
    # What the fix buys is therefore not a lower failure rate - the slow exit is
    # still slow - but a diagnosable one, and it costs wall time to get it: the
    # rare row that used to die at 30 s now dies at 60 s. At a 0.10% base rate
    # that is worth paying for an error message that names the navigation
    # instead of the keystroke.
    #
    # **The name is `no_wait_after`, and writing it `noWaitAfter` cost a whole
    # run.** That is what this line said on 2026-09-04 and it raised `TypeError:
    # ElementHandle.press() got an unexpected keyword argument 'noWaitAfter'` on
    # every single probe: `probehold_20260904T224114Z`, 25 attempts, 8.26 MB of
    # proxy traffic, 0 judged rows, all four cells stopped by the breaker on 6
    # consecutive failures each, every one of those failures a fresh exit spent.
    #
    # What the mistake looked like from the inside: two checks were made and both
    # were of the wrong layer. `patchright/_impl/_element_handle.py` was read and
    # it does take `noWaitAfter` - but that is the **async impl**, and the harness
    # calls `patchright.sync_api`. The driver's validator was read too,
    # `ElementHandlePressParams = tObject({key, delay?, noWaitAfter?})` - but that
    # is the **wire protocol**, on the far side of the connection. Both are
    # camelCase and both are real; neither governs the Python signature actually
    # being called. The generated sync wrapper is snake_case and translates at the
    # boundary: `no_wait_after` in, `noWaitAfter=no_wait_after` out to the impl.
    # Two confirmations of a camelCase name felt like corroboration and were the
    # same wrong reading twice.
    #
    # The check that would have caught it takes one line and cannot go stale:
    # `inspect.signature(patchright.sync_api.ElementHandle.press)`, today
    # `press(key, *, delay=None, timeout=None, no_wait_after=None)`. It is now
    # `test_the_fakes_are_no_looser_than_the_library`, which reads that signature
    # off the installed package and refuses to let the test doubles accept a
    # keyword the real one rejects - because the double written alongside the
    # broken line took `**kwargs` and swallowed the misspelling exactly as
    # happily as the correct spelling. **A fake with a looser signature than the
    # thing it stands for cannot fail where the run fails**, and four green tests
    # plus a green suite are what that bought.
    #
    # Neither check involves a browser, so neither would have caught a keyword
    # that is accepted and then ignored. `lab/probes/probe_press_no_wait_after_
    # local.py` is the one that does: it serves a form off 127.0.0.1, runs this
    # exact call three ways - `no_wait_after=True`, `=False` as the control, and
    # the camelCase spelling as the arm that must raise - and checks the browser
    # actually landed on the submitted URL. All three behaved 2026-09-05. There
    # is no live host in it, so it is runnable from the workstation with the
    # gateway up.
    #
    # `no_wait_after` is deprecated upstream and may be dropped. If it goes, the
    # explicit `timeout` still leaves one budget instead of two; what would come
    # back is the doubled wait, so re-read `_press` before assuming its removal
    # is cosmetic.
    #
    # **`click` carries the same barrier and is deliberately left alone.** Same
    # driver, same date: `async click` passes `waitAfter: !options.noWaitAfter`,
    # so it too waits for a commit by default, while `hover`, `dblclick`, `tap`
    # and `check` pass `waitAfter: "disabled"` and do not. Two of our clicks can
    # navigate - the consent button in `dismiss_consent` and the link in
    # `warm._act_click` - and neither is being changed, because the archive says
    # the case has never arrived: **zero `ElementHandle.click` timeouts in 6714
    # rows** across every `probehold_*` run to 2026-09-05, against 4 for `press`.
    # The mechanism being present is not a reason to touch a path; a measurement
    # is, and this one says no.
    with page.expect_navigation(wait_until="domcontentloaded",
                                timeout=timeout_ms) as navigation:
        handle.press("Enter", no_wait_after=True, timeout=timeout_ms)
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


def dismiss_consent(page, target, *, hand=None,
                    timeout_ms: int = 10000) -> bool:
    """Clear a consent wall the target serves over its own front page. Playwright.

    Returns whether one was cleared, which the caller records: a run where the
    wall stopped being dismissed would otherwise show up as the entry shape
    getting worse, and the entry shape is the thing being measured.

    The `hand` goes through here as well as through `submit_query`, and not
    doing so would have been the subtler bug: the wall is the *first* thing on
    the page and it is intermittent, so a run that walked to the search box and
    teleported to the consent button would have humanized a variable number of
    the clicks in a session, decided by the target. `consent_dismissed` records
    which rows met one.

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
        if hand is None:
            handle.click(timeout=timeout_ms)
        else:
            hand.click(page, handle, timeout_ms=timeout_ms)
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


def run_search(page, target, query: str, row: dict, *, rng, hand=None,
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
    # Differenced the same way the byte counter is, and for the same reason: the
    # hand belongs to the session and outlives the query, so reading its total
    # would charge the tenth query in a held identity with the whole session's
    # walking. `walk_ms` is None-safe because a run with no hand records None
    # rather than a zero that would read as "walked, instantly".
    walked = hand.walk_ms if hand is not None else None
    paced = hand.stats() if hand is not None else None
    started = time.perf_counter()
    try:
        ensure_entry(page, target, timeout_ms=timeout_ms)
        # After the entry page is up and before anything is typed. Recorded on
        # the row rather than done quietly, because clearing a wall is an
        # interaction the target sees and a query typed after one is not the
        # same client as a query typed without one.
        row["consent_dismissed"] = dismiss_consent(page, target, hand=hand)
        response = submit_query(page, target, query, rng=rng, hand=hand,
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
        # In the `finally` beside `elapsed_ms`, so a failed attempt still says
        # how much of its time was ours. An attempt that threw *during* the walk
        # is the one where the distinction matters most.
        if hand is not None:
            row["pointer_ms"] = round(hand.walk_ms - walked)
            now = hand.stats()
            # `device` is the session's and is copied, not differenced. The two
            # counters are differenced for the same reason `walk_ms` is.
            row["pointer_device"] = now["device"]
            row["pointer_overruns"] = now["overruns"] - paced["overruns"]
            row["pointer_points"] = now["paced_points"] - paced["paced_points"]
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
    # Here as well as in `JsonlSink.write`, and the duplication is deliberate.
    # The sink is what guarantees no run file lacks the host, because most
    # `sink.write` call sites build their dict by hand. This line is what keeps
    # the promise in this function's own docstring - every column present - so
    # anything reading a row between here and the sink sees the real value
    # rather than a None that gets filled in later.
    row.update(host.facts())
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
