"""Drive a Playwright pointer along the model in `nmbench.pointer`.

`pointer.py` is the model and knows nothing about a browser; this is the only
place that turns it into `page.mouse.move` calls. The split is the point: the
model is scored offline against recorded human traces by `lab/probes/`, and a
second implementation of the same walk living next to the harness would drift
from the one being measured without either output saying so.

**What this is and is not.** It moves the cursor to an element the way a hand
moves it, holds the button down for as long as a finger does, and turns the
wheel in flicks with a reading pause between them. It does not move the cursor
at any moment other than immediately before a click or a scroll. A page watching
for cursor motion during the 30 seconds this harness spends waiting for results
sees a frozen pointer, which no human produces. That gap is known and unmeasured.

**The scroll is measured but not scored, and the walk is both.** Every constant
`walk_to` uses was checked against a human trace by `lab/probes/trace_compare.py`
and the arm scores 0 tells; the flick and rest constants `scroll` uses were
measured the same way from the same two traces, on 2026-09-04, but the detector
has no burst metric - it reads wheel magnitude and wheel interval per event and
excludes both as `device` - so nothing has ever scored a driven scroll for this
structure. The two claims are not the same strength and should not be quoted as
though they were.

**And nothing here shows any target reads any of it.** The model scores clean
against a detector we wrote, on traces of one person; whether Google or Amazon
looks at pointer telemetry at all is a separate question this repository has not
answered. `--humanize trueman` is an axis to measure, not a fix to apply.

**The geometry survives this wiring in both modes. The timing survives headful
and is destroyed headless, so `--headless` decides what a `trueman` arm
measures.** Measured 2026-09-03, `lab/probes/humanize_smoke.py`, four arms in
one process, before this module had ever driven a browser.

| arm | delivered median | overruns of 18 | on the 15.6 ms tick |
|---|---|---|---|
| headful, `fine_timer` | **7.00 ms** | **1** | 0% |
| headful, no timer | **7.15 ms** | **1** | 11% |
| headless, `fine_timer` | 16.65 ms | 14 | 44% |
| headless, no timer | 16.65 ms | 16 | 50% |

The model asks for a median of 7.11 ms (touchpad) and 7.22 ms (mouse), measured
offline over 40 walks a side. Headful the page receives that. Headless it
receives a 16.6 ms metronome, which is one frame at 60 Hz, and 14-16 of 18
points are the cost of the call rather than the interval the model asked for.

`page.mouse.move()` in the sync API awaits the CDP reply, and that reply is
frame-bound with no window. It agrees with `lab/probes/dispatch_cost.py`, which
measured the same split independently on 2026-09-02 over eight runs and stated
it in one line: headless is the worse case synchronously at a flat 16.6-16.7 ms,
headful is not.

**What the mistake looked like from the inside, and it is the reason the table
above has four rows instead of two.** The first version of this probe launched
headless only, in both its paced arms and its floor arm. All three agreed at
16.6 ms, which is an internally consistent result with an explanation that fits
perfectly - the transport is frame-bound, the model asks for less than a frame,
therefore the timing model is undeliverable and only the geometry is real. That
was written into this docstring as a finding. It is true of headless and it was
written as though it were true of the tool. The thing that should have caught it
was already on disk: `dispatch_cost.py`'s docstring names the headful/headless
split in a bullet, and the detector captures in `lab/` that scored `trueman` 0
tells were all headful and carried a delivered interval median of 7.275 ms - a
number that cannot exist if the floor is 16.6. Two records contradicted the
claim before it was made and neither was read, because the probe's own three
arms agreed with each other.

One arm still to distrust: the floor arm sends 60 back-to-back moves and reads
13.88 ms headful, which is nearly twice what the paced arm then achieves. So it
measures saturated throughput and not per-call latency, and the two are not the
same quantity. **`overruns` is the direct measurement** - it counts the points
where the call cost more than the model's ask - and it is the number to read.

Two consequences:

- **`fine_timer()` is not what decides this, in either mode.** Headful 7.00
  against its own no-timer control's 7.15, headless 16.65 against 16.65. Kept
  because it costs one system call and the bottleneck moves the moment the
  dispatch path does, but a fix that started from sleep resolution would be
  fixing the wrong thing.
- **A headless `trueman` arm measures the geometry and not the rhythm**, and has
  to be read that way: the positions, overshoots, settle chain and press
  duration are the model's, the intervals are the transport's. A headless arm
  that beat its control would not be evidence the timing model works.

The fix for the headless case is measured and not built: `Input.dispatchMouseEvent`
is a command whose result nobody reads, and unawaited the same transport
delivers a 0.50-0.60 ms median and hits a paced 3/4/5/6/7 ms exactly, with zero
order inversions over eight runs, headless indistinguishable from headful. It
costs 77-100% event survival, because the browser merges what arrives inside one
frame. It is not built because the harness drives Playwright's sync API, which
has no fire-and-forget, so it means a second dispatch path - and shipping that
untested on the evening of a run is how the `--humanize` failure happened the
first time.

The click goes through Playwright's own `handle.click()` rather than through
`page.mouse.down()/up()`, and that costs something specific: `press_jitter` in
the model cannot be emitted, because there is no hook between the two. The
median human click carries zero moves anyway - 0 on both traces - so the loss is
in the tail. It is paid deliberately: `handle.click()` keeps the actionability
checks, and `base.submit_query`'s docstring records an attempt lost to a hidden
element that those checks are what catch.
"""
import contextlib
import ctypes
import random
import time

from . import pointer


@contextlib.contextmanager
def fine_timer():
    """Raise the Windows timer resolution to 1 ms for the length of the block.

    Without it `time.sleep` rounds up to the 15.625 ms system tick, and that
    reaches the page: measured 2026-09-02 in `lab/`, 24% of a driven pointer's
    intervals landed on that tick against 4% of the human's. It is the host's
    scheduler leaking into the pointer and it cannot be fixed by asking for a
    shorter sleep.

    Process-wide and paired with `timeEndPeriod`, which is why it is a context
    manager and not a line at import time. A no-op off Windows, where the sleep
    granularity is already finer than the pointer's period and the spin at the
    end of `Pacer.hold` covers the rest.

    **It changes nothing measurable on the path this module drives, in either
    display mode, measured 2026-09-03 against its own no-timer control:
    headful 7.00 ms median against 7.15, headless 16.65 against 16.65.** On the
    tick it reads 0% against 11% headful and 44% against 50% headless, over 18
    paced points an arm - too few to separate from nothing, and pointing in
    opposite directions between the modes, which is what a null looks like.

    The reason the arms agree is that the sleep is not what is slow. Headless
    the awaited round trip is 16.6 ms, longer than 90% of the intervals the
    model asks for, so `Pacer` overruns and never reaches a sleep worth
    quantising. Headful the round trip is under the ask, so the sleep is short
    and the spin at the end of `Pacer.hold` already covers it.

    Kept rather than removed: it costs one system call a run, the bottleneck
    moves the moment the dispatch path does, and deleting it would take the
    measurement with it. But a reader reaching for it to explain a timing result
    should read the module docstring first. The 2026-09-02 number above is a
    measurement of the host's sleep and stands; it was never a measurement of
    this path.
    """
    raised = False
    try:
        raised = ctypes.windll.winmm.timeBeginPeriod(1) == 0
    except (AttributeError, OSError):
        pass                              # not Windows, or no winmm
    try:
        yield
    finally:
        if raised:
            ctypes.windll.winmm.timeEndPeriod(1)


class Pacer:
    """Dispatch on an absolute timeline, so the round trip is absorbed.

    Sleeping *after* each call adds the cost of the call to every interval, and
    that cost is not noise: one `mouse.move` measured 6.97 ms at the median on
    the Windows workstation with no sleeping at all, `lab/probes/dispatch_cost.py`,
    2026-09-02, n=300. Asking for 6.3 ms produced about 13 on the wire and the
    model was blamed for it.

    Here the deadline for point n+1 is set relative to the deadline for point n
    rather than to the moment the call returned, so the cost is spent inside the
    interval instead of after it. Two consequences:

    - **No catch-up.** If a dispatch overruns its slot the next deadline resets
      to now and the interval the page saw is `max(asked, cost)`. Letting it
      catch up would emit a burst of zero-interval events, and the human traces
      contain not one.
    - **Nothing below the cost of an awaited round trip**, which is what this
      path is. That is a property of awaiting a reply and not of the host: the
      same transport with the reply not awaited measured 0.50-0.60 ms in the
      same run. Anything wanting a 500 or 1000 Hz device needs the pipelined
      path and has to pay for it in merged events; this is not that path.

    **Whether the second bullet is an edge case or the normal case is decided by
    `--headless`, measured 2026-09-03 over four arms of 18 paced points:
    `overruns` is 1 headful and 14-16 headless.** Headless the round trip is
    16.6 ms against a median ask of 7.1, so the delivered interval is `cost`
    rather than `asked` almost everywhere; headful the round trip is under the
    ask and the model's distribution reaches the page intact.

    So `overruns` is not a warning counter with one expected value. It is the
    reading that says which of those two runs happened, and it is the reason
    this class counts rather than assumes. It is also the *direct* measurement:
    the floor arm in `humanize_smoke.py` sends back-to-back moves and reports
    13.88 ms headful, nearly twice what the paced arm achieves in the same
    process, because saturated throughput and per-call latency are different
    quantities. Trust the counter over the floor.

    `overruns` and `points` reach the row as `pointer_overruns` and
    `pointer_points`, so a run whose intervals were the driver's rather than the
    model's says so in a column instead of having to be inferred from the
    timings. **This docstring claimed that before it was true** - the counters
    existed and `stats()` returned them, nothing wrote them, and the sentence
    was written from the design rather than from `ROW_FIELDS`. Wired 2026-09-03,
    caught while checking a second claim that depended on it.
    """

    # Aim this many ms early, to deconvolve a known transport bias: the page
    # sees `max(asked, cost)`, so a model asking for exactly its own median
    # loses the left half of its distribution and the median walks right.
    #
    # **Zero by default, and that is deliberate rather than unfinished.** The
    # lab capture uses 0.15 because that is what 6.97 ms of awaited dispatch
    # needs on one Windows host, and a lead not measured on the machine it runs
    # on is a fudge factor rather than a correction - the harness's runs happen
    # on the VPS, where this has never been measured. Set it from
    # `lab/probes/dispatch_cost.py` on the host in question or leave it alone.
    LEAD_MS = 0.0

    def __init__(self):
        self.next_at = time.perf_counter()
        self.overruns = 0
        self.points = 0

    def restart(self) -> None:
        """Drop the timeline. Called when the pointer has been idle.

        Without this the first move after a 30-second wait for a page arrives
        with a deadline 30 seconds in the past, is counted as an overrun, and
        drags the whole walk into catch-up. The timeline is only meaningful
        across a continuous sequence of dispatches.
        """
        self.next_at = time.perf_counter()

    def hold(self, ms: float) -> None:
        self.points += 1
        self.next_at += max(0.0, ms - self.LEAD_MS) / 1000.0
        now = time.perf_counter()
        if now >= self.next_at:
            self.overruns += 1
            self.next_at = now
            return
        coarse = self.next_at - now - 0.002
        if coarse > 0:
            time.sleep(coarse)            # quantised even at 1 ms resolution,
        while time.perf_counter() < self.next_at:
            pass                          # so the last two ms are spun


class PointerHand:
    """One session's cursor: a device, a position, and a clock.

    Held by the session rather than made per click, because the position has to
    persist - a hand that teleports to the same starting point before every
    click is a stronger signal than no hand at all. The device is drawn once per
    session for the same reason `pick_device` exists: a fleet whose pointer
    statistics are identical is a tell whatever value they share.

    **`fine_timer()` is the runner's job, not this class's.** The timer
    resolution is a process-wide setting and has to be paired, so scoping it to
    a session would be fake precision - two sessions in one process share it
    whatever this object does. The runner wraps its whole run in it; without
    that the intervals below quantise to the 15.6 ms tick on Windows and the
    walk is measurably worse than the model it is driving.
    """

    # Where the cursor is taken to have been when the session opened. Playwright
    # starts its virtual mouse at (0, 0) and a real one is wherever the person
    # left it, which for a first navigation is unknowable - so it is drawn, in
    # the middle half of the viewport, rather than either left at the corner or
    # pretended to be known. Stated because it is a guess and the only one here.
    START_BOX = (0.25, 0.75)

    def __init__(self, rng=None, device=None, viewport=(1280, 800)):
        self.rng = rng or random.Random()
        self.device = pointer.pick_device(self.rng, device)
        self.pacer = Pacer()
        self.viewport = viewport
        lo, hi = self.START_BOX
        self.pos = (self.rng.uniform(lo, hi) * viewport[0],
                    self.rng.uniform(lo, hi) * viewport[1])
        # Wall time spent walking, in ms, accumulated over the session. The
        # caller differences it per attempt and writes `pointer_ms`, because a
        # walk sits inside `elapsed_ms` and without the column a humanized arm
        # reads as a slower engine.
        self.walk_ms = 0.0

    def walk_to(self, page, x: float, y: float) -> None:
        """Move the cursor to (x, y) along the model. Nothing is clicked."""
        started = time.perf_counter()
        self.pacer.restart()
        for px, py, dt in pointer.trueman(self.pos, (x, y), self.rng,
                                          self.device):
            page.mouse.move(px, py)
            self.pacer.hold(dt)
            self.pos = (px, py)
        self.walk_ms += (time.perf_counter() - started) * 1000.0

    def scroll(self, page, distance_px: float, direction: int = 1) -> None:
        """Carry the page about `distance_px`, in flicks with a pause between.

        Two things this deliberately does not do, both for reasons already
        recorded elsewhere in this repository:

        It does not add its wall time to `walk_ms`. `hand_delta`'s contract is
        that `pointer_ms` on a warm row and `pointer_ms` on a probe row are the
        same quantity and add up over a session, and a probe row's is walking
        only. Scroll time is not lost - it is inside `interact_ms`, which is
        what `run_identity` subtracts from the dwell.

        Its dispatches *do* go through the pacer and so are counted in
        `pointer_overruns` and `pointer_points`. That is the point of those
        columns: they say whether this host delivered the model's intervals, and
        a touchpad scroll asks for roughly 290 dispatches at 7 ms where a walk
        asks for 18, so it is the harder case and the one worth reading. A
        headless run will overrun nearly all of them - see the module docstring
        - and a scroll arm read from a headless run is the geometry without the
        rhythm, same as the walk.

        The rest between flicks is a `time.sleep` and not a `pacer.hold`,
        followed by `restart`. A rest is the pointer sitting still, which is
        exactly the case `Pacer.restart` exists for; putting it on the timeline
        would also count a 300 ms wait no transport can overrun as a paced point
        and dilute the ratio the column is for.
        """
        self.pacer.restart()
        for delta, wait, resting in pointer.scroll_plan(
                "trueman", distance_px, self.rng, self.device, direction):
            if resting:
                time.sleep(wait / 1000.0)
                self.pacer.restart()
                continue
            page.mouse.wheel(0, delta)
            self.pacer.hold(wait)

    def click(self, page, handle, *, timeout_ms: int = None) -> None:
        """Walk to an element and click it, keeping Playwright's own checks.

        Scrolled into view *before* the box is read, not after: `click` scrolls
        on its own, and a walk aimed at a box read beforehand would land where
        the element used to be and then be silently corrected by the click's own
        move. The correction would not fail, which is what makes it worth
        avoiding - the row would record a humanized click that ended in a jump.

        Aimed at the centre of the box. A hand lands anywhere inside a button
        and this does not model that, on purpose: every metric in the detector
        is computed against the target point, so the model was fitted to
        approaching a centre. Adding spread here would be a change to the thing
        that was scored, made after the scoring.
        """
        try:
            handle.scroll_into_view_if_needed()
            box = handle.bounding_box()
        except Exception:
            box = None
        if box:
            self.walk_to(page, box["x"] + box["width"] / 2,
                         box["y"] + box["height"] / 2)
        # A hold of 0 ms is what Playwright sends by default and is not a thing
        # a finger can do. See `Device.press_ms` for the measurement and for the
        # denominator it rests on.
        delay = self.rng.uniform(*self.device.press_ms)
        if timeout_ms is None:
            handle.click(delay=delay)
        else:
            handle.click(delay=delay, timeout=timeout_ms)

    def stats(self) -> dict:
        """What the run record keeps about this hand, for `--humanize trueman`.

        `overruns` against `paced_points` is the honest health check on the
        walk: a high ratio means the intervals the page saw were this host's
        driver rather than the model, and the pointer columns of that run should
        not be read as a statement about the model.
        """
        return {"device": self.device.name,
                "overruns": self.pacer.overruns,
                "paced_points": self.pacer.points}
