"""Pointer and wheel models. A mover yields `(x, y, wait_ms_after)`.

Pure functions over a `random.Random`. Nothing here imports a browser, opens a
socket or reads a clock, so the same code can be scored offline against a
recorded human trace and driven live by `nmbench.humanize` - which is the point
of it being one module rather than two: a model that is measured in one place
and run from a copy somewhere else drifts, and the drift is invisible until the
numbers disagree weeks later.

**Read every number in this file with its denominator.** The constants below
were measured against **two traces of one person**, captured 2026-09-02 on one
Windows host: the same man, the same page, twenty minutes apart, once on a
laptop touchpad and once on a mouse. That is the whole reference population. A
detector built on it can say "this reproduces that person"; it cannot say "this
looks human", and the difference is not rhetorical - an earlier version of this
model scored a clean sheet against the trace it was fitted to and 8 tells
against the same man's other hand. Where a comment says a metric "matches", it
means it matches him.

**And nothing here shows that any target reads any of it.** The metrics this
model is scored on come from a detector written alongside it; whether a real
site looks at pointer timing is a separate question with a separate denominator,
and this repository answers it the only way it can - by running the axis on and
off against the same target and counting outcomes.

Timing is part of the model, not a constant the driver picks: the detector's
`interval spread (cv)` and `intervals on 15.6ms tick` are both about *when*
events are sent, and a mover that only decides where the pointer goes cannot fix
either of them.

Three movers, and the first two exist to be beaten:

    linear    straight line, fixed step. The crudest thing that works.
    bezier    one cubic with random control points, fixed step. What almost
              every anti-detect library ships.
    trueman   ballistic burst, a deliberate miss, one to three corrections,
              correlated tremor, integer coordinates, and a device clock.

Measured 2026-09-02 against one human trace: `bezier` closes 12% of the gap on
straightness, 6% on tremor and 3% on path wobble, and moves none of the other
fourteen metrics. The curve is not what the difference is made of - a cubic has
at most one inflection by construction, and the human turned 39 times per
movement.
"""

import math

# --------------------------------------------------------------------------
# device profiles
#
# **There is no such thing as a human pointer profile, and assuming there was
# cost this experiment a wrong result.** Until 2026-09-02 every constant here
# was a single set fitted to one capture, and `trueman` scored 0 tells against
# it. A second capture - the same person, the same page, twenty minutes later,
# an actual mouse instead of the laptop touchpad - scored the same mover 8. The
# two humans differ from each other on 5 of the detector's 18 metrics:
#
#     coalesced events, mean     1.000 touchpad   4.190 mouse
#     wheel |deltaY|             6.000 touchpad   166.667 mouse
#     wheel interval ms          7.000 touchpad   29.700 mouse
#     interval ms, median        7.100 touchpad   7.000 mouse
#     movement duration ms    1037.100 touchpad   644.000 mouse
#
# So a device is drawn per session and everything device-shaped hangs off it.
# That is also better operationally for a reason that has nothing to do with
# this experiment: a fleet whose pointer statistics are all identical is itself
# a tell, whatever value they are identical at.
#
# Both profiles are one person on one host - Windows 10, Chrome 149, dpr 1, 12
# movements each, measured 2026-09-02 - so they are two devices, not two people.
# Nothing here separates "what a human does" from "what THIS human does".
#
# **That gap is load-bearing and it is not going to be closed by tuning.** The
# detector's own rule is that a metric counts as a tell only when it separates
# human from synthetic *and fails to separate one real human from another*. The
# second half of that rule has never been evaluated, because there is no second
# person's trace. Every "defect" reported by `mover_offline.py` is therefore a
# distance from one person, and a metric this mover is beaten on might be one
# that two humans are equally far apart on. Stated as-is on the user's
# instruction, 2026-09-03; a second capture is wanted and not blocking.
#
# What that costs is now concrete rather than abstract, and `moves away from
# target` is the case. Measured 2026-09-03, n=240 mover movements over the
# human's own 12 trips, touchpad:
#
#     reversals per movement   human   mover
#     mean                      9.33    9.18
#     max                      28      26
#     median                    4.50    8.00
#
# The rate is right to 1.6% and the metric reads 1.78 of the human, because his
# 12 values are `0 0 0 0 2 2 7 10 17 20 26 28` - bimodal, with four movements at
# exactly zero, and a median sitting in the empty gap between the two humps. The
# mover's minimum over 240 movements is 1.
#
# The bimodality is not the trips: over those same 12 trips the mover's per-trip
# median is flat at 6.0 to 10.5 while the human's runs 0 to 28. It is not tremor
# amplitude either - `spearman(reversals, tremor rms)` is **-0.09**, and his
# clean movements carry slightly *more* wobble than his messy ones, median rms
# 2.39 against 2.26. What it tracks is the sample count, spearman **+0.83**: his
# four zero-reversal movements are four of his five shortest and his four worst
# are his four longest.
#
# So what is left on this metric is between-movement dispersion in one person,
# characterised from 12 movements. Whether that is a stable property of a hand or
# one session's mood is exactly the question the missing second trace answers,
# and fitting a dispersion to it now would be fitting to n=12 from n=1.


class Device:
    """One pointing device, as the page sees it.

    The interval model has two shapes and the difference between them is not
    cosmetic - it is what the two devices actually do.

    The **touchpad** is a metronome: 62% of its intervals land in a single
    0.5 ms bin at 7.1 ms, with clean humps at 2x and 3x where the OS did not
    deliver a report, and a long pause tail. `drop_two` and `drop_three` are
    those humps.

    The **mouse** has no humps at all - 1 interval of 818 at 2x - and instead a
    single broadened peak, sd 1.46 ms against the touchpad's 0.24. It reports
    far faster than it is delivered (`coalesced` 4.02 per event, so roughly
    600 Hz) and the browser bundles rather than drops, which smears delivery
    instead of quantising it. Same median, opposite mechanism, and a summary
    statistic cannot tell them apart.
    """

    def __init__(self, name, period_ms, period_sd, drop_two, drop_three,
                 burst_drop_two, burst_pause_rate, miss_frac,
                 pause_rate, rest_pause_rate,
                 pause_mu, pause_sigma, pause_min, pause_max,
                 wheel_notch, wheel_mu, wheel_sigma, wheel_max,
                 wheel_int_mu, wheel_int_sigma, wheel_int_min, wheel_int_max,
                 motion_scale, peak_k, overshoot_rate,
                 tremor_scale, tremor_memory,
                 press_rate, press_max, edge_overrun,
                 rest_tremor_scale=1.0, rest_tremor_memory=None,
                 press_ms=(90.0, 220.0)):
        self.__dict__.update(locals())
        del self.__dict__["self"]

    def step(self, rng, pause_rate=None, drop_two=None):
        """One interval in ms, and how much of it the hand spent moving.

        Three things put a gap between two reports and only two of them are
        the same thing:

            a report on time        the hand moved for one period
            a report not delivered  the hand moved for two or three periods
            the hand stopped        the hand moved for none of it

        Nothing in the interval alone tells them apart, which is how the
        distinction came to be lost: this returned one number, and every
        caller advanced the path by one step whatever the number was. A
        report the device never sent then bought no extra distance, so the
        path could not jump where the clock did.

        That is measurable and it was measured. `tremor_source.py`,
        2026-09-02, splits each point's deviation from the midpoint of its
        neighbours into the part where the velocity changed and the part
        where only the interval did, as projections onto the deviation so
        the two shares add up to it. On the touchpad human the large
        deviations are **65% the interval term**, median 11.73 px against
        the kinematic 1.72 over the 20 points above 8 px; the mover reached
        3.23 and 30%. The whole missing tail of `tremor px (rms)` on that
        arm is this one line.

        `pause_rate` is the caller's, because **stopping is a property of the
        phase and not of the device alone.** Both humans say so: the share of
        intervals arriving late, by speed quartile of the preceding interval,
        is 33.3 / 2.6 / 1.5 / 3.0 on the touchpad and 20.1 / 4.2 / 0.5 / 0.5
        on the mouse. It is a threshold at rest, not a rate throughout, and
        `trueman` passes the rest figure during a dwell and the device figure
        during a correction.

        **`drop_two` is the caller's for the same reason, and the burst's zero
        was costing the touchpad its whole tremor tail.** The two are not the
        same event and the difference is the one this method is organised
        around: a pause returns `moving = 0.0`, so the hand stopped and the
        position does not advance, while a drop returns `dt, dt`, so the report
        went missing and the hand kept going. Only the second produces the
        timing term, and the touchpad human's deviations above 8 px are 80% of
        it.

        The previous zero was reasoned - the rounding dedup at the bottom of
        `trueman` drops a point that lands on the previous pixel and carries
        its time forward, so a device-wide drop rate emits the same thing
        twice. The hole in that reasoning is that **the dedup can only fire
        where the pointer moves under a pixel between reports, which is the
        slow phase**, so it cannot produce a late report at speed and the
        burst was left with none.

        Measured 2026-09-03 on the fast speed quartile of each trace, where the
        dedup cannot reach: the touchpad has **5 of 230 intervals at 2x the
        7.10 ms median, 2.2%**, and nothing at 3x or above; the mouse has
        **0 of 205 at 2x** and one at 4x. So the `drop_two=0.0` on the mouse is
        its own measurement and stays, and the touchpad's was inherited from
        it. In the slower three quartiles the same count is 23.6% and 12.1%,
        which is the dedup's territory and is why the rate is passed per phase
        rather than raised device-wide.

        These are 5 intervals and 0 intervals. The direction is corroborated
        independently by the tail decomposition above rather than resting on
        the count alone, but a per-device rate fitted to n=5 is the reason the
        long capture is still worth taking.
        """
        if pause_rate is None:
            pause_rate = self.pause_rate
        if drop_two is None:
            drop_two = self.drop_two
        r = rng.random()
        if r < pause_rate:
            return self.pause(rng), 0.0
        r -= pause_rate
        if r < drop_two:
            dt = 2 * self.period_ms * rng.uniform(0.96, 1.04)
            return dt, dt
        if r < drop_two + self.drop_three:
            dt = 3 * self.period_ms * rng.uniform(0.97, 1.03)
            return dt, dt
        # An ordinary report carries the *nominal* period of motion and not its
        # own jittered length, because the jitter is in the timestamp and not
        # in the hand. Both humans say so directly: over every interior point
        # of both traces the median timing term is 0.000 px on the touchpad and
        # 0.074 on the mouse, which is what "the interval moved and the
        # distance did not" looks like.
        #
        # The mouse is where this is expensive, and it is the device whose
        # spread is almost all delivery: sd 1.46 ms against the touchpad's
        # 0.24, from a device reporting near 600 Hz that the browser bundles
        # rather than drops. Charging that spread as motion put 7 px of
        # deviation on a typical point at peak speed and took the arm's tremor
        # to 1.84 of the human, worse than the 0.82 it started the day at.
        dt = max(1.0, rng.gauss(self.period_ms, self.period_sd))
        return dt, self.period_ms

    def interval(self, rng):
        """One pointer interval in ms, for a caller not traversing a path.

        The dwell and the press are the hand sitting still, so how much of
        the interval it spent moving is not a question about them.
        """
        return self.step(rng)[0]

    def pause(self, rng):
        return min(self.pause_max,
                   max(self.pause_min,
                       rng.lognormvariate(self.pause_mu, self.pause_sigma)))

    def wheel_step(self, rng, direction):
        """One `(delta_y, wait_ms_after)`.

        A notched wheel emits one constant magnitude on a coarse timer and a
        touchpad emits a continuous distribution on the pointer clock. Both are
        real; treating either as "the human wheel" is what went wrong before.
        """
        if self.wheel_notch:
            delta = self.wheel_notch
            wait = min(self.wheel_int_max,
                       max(self.wheel_int_min,
                           rng.lognormvariate(self.wheel_int_mu,
                                              self.wheel_int_sigma)))
        else:
            delta = min(self.wheel_max,
                        max(1, round(rng.lognormvariate(self.wheel_mu,
                                                        self.wheel_sigma))))
            wait = self.interval(rng)
        return delta * direction, wait


# Touchpad. `human_touchpad_20260902T100329Z`, 917 within-movement intervals.
# Pause tail: 6.8% of intervals, median 39 ms, mean 89, p90 233, max 503, which
# lognormal(3.66, 1.29) reproduces on both moments.
#
# The pause figures were first taken over the whole trace, which pools the
# pauses *inside* a movement with the gaps *between* them - one of the latter is
# 16 seconds. That gave a mean of 110 against the true 89, and the 30% error
# cost 26 samples per movement: v2 scored 55.5 against the human's 81.5 while
# its interval histogram matched perfectly. Segment on `hit` first, exactly the
# way the detector does, then estimate.
TOUCHPAD = Device(
    name="touchpad",
    period_ms=7.05, period_sd=0.24,
    # 0.084 and 0.031 in the trace, and 0 here, for the reason already written
    # one field down about `pause_rate`: the mover produces this for free and
    # drawing the measured rate on top emits the same phenomenon twice. That
    # argument was made for the pause tail on 2026-09-02 and not carried to the
    # humps beside it, which is the whole of the error.
    #
    # A report is missing when the finger has not moved far enough to report,
    # so a dropped point is a *consequence of being slow* and not an event with
    # a rate of its own. Measured the same day with `tremor_source.py`, the
    # share of intervals arriving late by speed quartile:
    #
    #     touchpad human     33.3%   2.6%   1.5%   3.0%
    #     mouse human        20.1%   4.2%   0.5%   0.5%
    #     trueman, humps on  33.6%  29.3%  21.2%  17.0%
    #
    # The human's is a threshold and the mover's was nearly flat. The rounding
    # dedup at the bottom of `trueman` already reproduces the slow quartile on
    # its own - it drops a point that lands on the previous pixel and carries
    # its time forward - so the humps were a second copy, and unlike the dedup
    # they fired at peak speed too, where a missing report is worth 19 px of
    # deviation instead of 1.
    #
    # This only became visible after the profile started advancing on the clock
    # rather than on the sample index. Before that a dropped point cost time
    # and bought no distance, so the free copy produced no deviation at all and
    # the humps looked like the only source of one.
    drop_two=0.0, drop_three=0.0,
    # Measured 2026-09-03 on the fast speed quartile, where the dedup cannot
    # fire: 5 of 230 intervals at 2x the 7.10 ms median, and none at 3x. The
    # slower three quartiles are 23.6% at 2x or more and are the dedup's, which
    # is why this is a burst rate and not a device rate. See `Device.step`.
    burst_drop_two=0.022, burst_pause_rate=0.000,
    miss_frac=(0.10, 0.28),              # mean 0.19, measured 0.192
    # 0.050, not the 0.068 measured in the trace, and the gap is not a fudge.
    # A long interval in a human trace has two causes that cannot be told apart
    # from the outside: the hand stopped, or the hand moved less than a pixel
    # and the device sent nothing. The measured 6.8% is both. The mover now
    # produces the second for free - dropped points carry their time forward,
    # since 2026-09-02 - so drawing the measured rate on top emits the same
    # phenomenon twice. It showed up as `movement duration ms` at 1.23 of the
    # human with `samples per movement` at 1.02: the right number of events
    # spread over too much time.
    # Stopping is a property of the phase. Measured 2026-09-02 by binning each
    # movement into deciles of sample index and taking the share of intervals
    # over 20 ms in each - the four bins are the mover's own four phases:
    #
    #     phase          touchpad          mouse
    #     dwell d0       21.6%  86 ms      9.3%  53 ms
    #     burst d1-3      1.8% 234 ms      2.0%  77 ms
    #     settle d4-8     6.8%  52 ms      3.9%  48 ms
    #     hover d9       37.9%  47 ms     13.0% 139 ms
    #
    # `rest_pause_rate` is the two resting bins pooled, 54 late of 184 intervals
    # here and 18 of 163 on the mouse. They are pooled rather than split because
    # their *dead time* is what a metric sees and that is nearly equal at the
    # two ends - 150 ms against 130 on the touchpad - while the rate differs
    # only in how it is cut into pauses. Splitting them would be two more
    # constants fitted to 12 movements to move nothing measurable.
    #
    # `pause_rate` was a single fitted 0.050 until 2026-09-02, discounted by
    # hand from a trace-wide 0.068 because the rounding dedup produces some of
    # the tail for free. The discount was guesswork standing in for a phase, and
    # it could not be right at both ends of a movement: a flat rate that gives
    # the correct total dead time puts it in the middle deciles, where the human
    # has almost none. Measured that day, pause ms per movement by decile -
    # human 150/0/38/60/34/19/54/21/6/130 against a flat-rate mover's
    # 49/6/22/55/64/58/76/58/53/49. The totals agreed to 5%.
    pause_rate=0.020, rest_pause_rate=0.190,
    pause_mu=3.66, pause_sigma=1.29,
    pause_min=26.0, pause_max=520.0,
    wheel_notch=0,                       # continuous surface, 62 magnitudes
    wheel_mu=1.79, wheel_sigma=1.12, wheel_max=60,
    # Zero means "use the pointer clock", and that is a measurement rather than
    # a gap: split at 150 ms the 1251 cadences inside a flick are min 0.1, p10
    # 6.6, median 7.0, p90 13.5, which `step`'s gauss(7.05, 0.24) reproduces on
    # the median and the p10. A lognormal fitted to the same median and mean
    # would put 46% of gaps under that p10 against a true 10%, because a surface
    # reporting at a fixed rate has a floor and a lognormal does not.
    # `scroll_plan` supplies the rests between flicks for both devices, so what
    # is missing from this line is not missing from the scroll.
    wheel_int_mu=0, wheel_int_sigma=0, wheel_int_min=0, wheel_int_max=0,
    motion_scale=1.00, peak_k=0.251,     # peak speed / sqrt(distance)
    overshoot_rate=0.33,                 # past the target on 4 of 12
    tremor_scale=1.00,
    tremor_memory=0.86,                  # 0.505 sign changes per sample
    press_rate=0.46, press_max=3,        # 6 of 13 clicks carry a move
    # Wheel events spent turning after the page has already stopped, drawn
    # uniform between these. Of the 772 wheel events in this trace that moved
    # the page by nothing, 734 are at an edge: he went on scrolling after the
    # bottom, and roughly 370 events per edge is what that cost him.
    #
    # **The unit changed on 2026-09-04 and the quantity did not.** It read
    # `(26, 46)` and was counted in *batches* of the 8-16 wheel events that
    # `trace_capture_synthetic.py` used to emit between two `scrollY` reads.
    # That batch was invented in the capture and never measured, and it has
    # been replaced by the measured flick of `scroll_plan`, which is about six
    # times longer - so leaving the number alone would have multiplied the
    # overrun by six without one line of the file changing. Restated at the old
    # batch's own mean of 12 events: 26x12 and 46x12, mean 432 against the 370
    # this was fitted to by eye. Nothing was re-fitted; the events are the unit
    # the 734 was counted in, so this is the number moving to the unit it was
    # measured in rather than a new value.
    edge_overrun=(312, 552),
    # How long the button is held down, in ms, drawn uniform between these.
    # Measured 2026-09-03 off this trace's own `pointerdown`/`pointerup` pairs:
    # **13 presses**, median 131.6, range 90.3-217.6, p10 98.8, p90 142.3. The
    # bounds here are the p10 and the max rather than the full range, because
    # the 90.3 minimum is one press and drawing uniformly over the whole span
    # would spend a tenth of its mass below anything he did more than once.
    #
    # This is the one number in the file that no metric in `trace_compare.py`
    # reads, so it was never fitted and never scored - it was found by asking
    # what `handle.click()` sends and finding a 0 ms hold, which is not a thing
    # a finger can do. Unmeasured against a detector, in other words: it removes
    # an impossibility rather than matching a distribution.
    press_ms=(98.8, 217.6),
)

# Mouse. `human_mouse_20260902T104143Z`, 818 within-movement intervals, peak
# mean 6.86 sd 1.46, pause tail 7.2% with median 29 and mean 56.
#
# Wheel: 52 events for the same page travel against the touchpad's 1262, one
# distinct magnitude of 166.667. **This is what `bezier` already emitted** - a
# constant on a coarse timer - and it was scored as a tell and a fix was built
# for it, because the only human trace on hand came from a touchpad.
#
# **The interval was fitted over the whole gap distribution and that pooled two
# different behaviours, corrected 2026-09-04 by `lab/probes/scroll_shape.py`.**
# It read median 29.7 with a heavy tail (mean 95.7, p90 256) and lognormal(3.39,
# 1.53) reproduced both moments, which is why it stood. Split at 150 ms - the
# threshold at which both human traces fall into the same 11 bursts - those 51
# gaps are 41 cadences inside a flick, median 25.1 and mean 29.0, and 10 rests
# between flicks, median 309.1. One lognormal spanning both describes neither:
# it puts a 300 ms hole inside a flick and a 25 ms flick inside a rest.
#
# The values below are the cadence alone, lognormal(3.22, 0.54), which
# reproduces the median and the mean and lands 11% under the measured p10
# against a true 10%. The rests moved to `scroll_plan`, because the two humans
# are 27.8x apart on the notch and 3.6x on the cadence but only 1.3x apart on
# the rest - so the first two are the device and the rest is the person.
#
# This is the same error the touchpad's `pause_*` block records two screens up,
# where pauses inside a movement were pooled with the gaps between movements. It
# was made twice because a two-moment fit reproduces both moments of a mixture
# perfectly well, and agreeing with the data it was fitted to is not evidence
# that it is one distribution.
#
# **No detector score moves.** Both wheel metrics are excluded as `device` in
# `trace_compare.py`, so nothing scored reads this number; what changes is what
# a driven scroll puts on the wire.
MOUSE = Device(
    name="mouse",
    period_ms=7.00, period_sd=1.46,
    drop_two=0.0, drop_three=0.0,        # 1 interval of 818 at 2x: no hump
    # Still zero, and now on its own measurement rather than the trace-wide one
    # above. On the fast quartile, 0 of 205 intervals at 2x against the
    # touchpad's 5 of 230 - the one outlier is a 4x. The two devices differ
    # here, which is why the touchpad's zero could not be inherited from this.
    burst_drop_two=0.0, burst_pause_rate=0.005,
    miss_frac=(0.05, 0.23),              # mean 0.14, measured 0.141
    # 3.9% while correcting and 11.0% at rest, off this device's own phase
    # table - see the touchpad, where the table is written out. Was a single
    # fitted 0.038.
    pause_rate=0.028, rest_pause_rate=0.110,
    pause_mu=3.37, pause_sigma=1.14,
    pause_min=13.0, pause_max=480.0,
    wheel_notch=166.667,
    wheel_mu=0, wheel_sigma=0, wheel_max=0,
    wheel_int_mu=3.22, wheel_int_sigma=0.54,
    wheel_int_min=10.0, wheel_int_max=90.0,
    # 0.84 is the ratio of *motion* time, 467.5 ms against the touchpad's 556.5
    # (median over 12 movements each, 2026-09-02, intervals under 20 ms summed).
    # It was 0.62 for a day, which is the ratio of total *duration*, 644/1037.
    # That double-counts: duration is motion plus the pause tail, and the pause
    # tail is already drawn by `pause_rate` and `pause_mu` a few lines up, so
    # scaling the motion by it removes the same time twice. The visible cost was
    # `samples per movement` at 0.79 of the human on the mouse arm while the
    # touchpad arm - the one whose scale is 1.00 and cannot be wrong this way -
    # sat at 1.06. Any per-device scale multiplying a quantity the profile
    # already models has this failure mode.
    # `motion_scale` governs the dwell and the settle only; the burst gets its
    # time from `peak_k`. 0.50, not the 0.84 measured as a motion-time ratio,
    # because that ratio covered the burst too and the burst no longer takes its
    # time from here.
    motion_scale=0.50,
    # 0.21 against a measured 0.288. The measurement is of the *observed* peak,
    # which carries this device's own inflation: `period_sd` is 1.46 ms against
    # the touchpad's 0.24, and a short interval next to a normal step reports a
    # speed spike. Feeding the observed value into a model that then re-adds the
    # spread counts it twice - it read 1.32 of the human before this.
    peak_k=0.21,
    # Past the target on 9 of 12 movements. 0.55 rather than 0.75 because the
    # settle overshoots a little on its own, and at 0.75 `moves away from
    # target` went to 1.43 of the human.
    overshoot_rate=0.55,
    tremor_scale=0.89,
    tremor_memory=0.91,                  # 0.409 sign changes per sample
    press_rate=0.07, press_max=1,        # 1 of 15 clicks
    # Same habit, two orders of magnitude cheaper: this device crossed the whole
    # page in 52 wheel events where the touchpad took 1262, so an equal amount
    # of turning past the edge is a far smaller count. Restated in wheel events
    # on 2026-09-04 with the touchpad's - it was `(2, 5)` batches of 8-16, mean
    # 42 events, and these bounds keep that mean.
    edge_overrun=(24, 60),
    # 15 presses, median 47.0, range 13.3-71.4, p10 14.7, p90 61.7, measured
    # 2026-09-03 the same way as the touchpad's. **2.8x shorter at the median**,
    # which is the largest per-device gap of any constant in this file and rests
    # on 28 presses by one person - a click-pad has to be pushed through a hinge
    # and a mouse button does not, so the direction is at least mechanical, but
    # one hand is not evidence that every hand splits this way.
    #
    # Lower bound is the p10 again, upper is the max.
    press_ms=(14.7, 71.4),
)

DEVICES = {"touchpad": TOUCHPAD, "mouse": MOUSE}


def pick_device(rng, name=None):
    """The session's device. Drawn unless named, because a fleet that is all
    one device is a population of one wearing many IP addresses."""
    if name:
        return DEVICES[name]
    return rng.choice([TOUCHPAD, MOUSE])


# --------------------------------------------------------------------------
# What the profiles cannot reach on this host, stated rather than papered over.
#
# `coalesced events` is 4.02 per event on the mouse and 0.99 on the touchpad,
# and only the touchpad is reachable. One `mouse.move` costs 6.97 ms at the
# median with no sleeping at all (`dispatch_cost.py`, 2026-09-02, n=300, min
# 2.58, p90 8.36), and raw CDP is identical, so we deliver about one report per
# 7 ms and the browser has nothing to bundle. Reproducing 4 reports inside one
# delivery needs a dispatch path four times cheaper than any this host has.
#
# The same floor truncates the left half of the mouse's interval distribution:
# its p10 is 5.1 ms, which cannot be emitted, so `Pacer` will report overruns
# and the median will walk right. The touchpad's 7.1 ms spike sits just above
# the floor - which is luck, not design. The honest statement is that this host
# can imitate a 125-140 Hz device and cannot imitate a 500 or 1000 Hz one.
#
# The detector currently excludes both metrics as `device`, so neither costs a
# tell today. That is a property of having exactly two reference traces and it
# should not be read as the problem being solved.
#
# **Both paragraphs above are wrong about the floor, corrected 2026-09-03.**
# There is no 6.97 ms floor. That number is the cost of a *synchronous round
# trip*, and `dispatch_cost.py`'s own correction block of 2026-09-02 measured
# the same transport with the reply not awaited at a 0.50-0.60 ms median against
# its own awaited control in the same run, landing on a paced 3/4/5/6/7 ms
# target exactly, with zero order inversions in every arm of every run. So "this
# host can imitate a 125-140 Hz device and cannot imitate a 500 or 1000 Hz one"
# does not follow, and neither does "the browser has nothing to bundle" - the
# price of pipelining is 77-100% event survival, which is merging, which is
# exactly the `coalesced events` this comment says is unreachable.
#
# What the mistake looked like from the inside: the 6.97 ms was measured
# correctly, and then reused under a name it had not earned. "Cost of awaiting a
# reply" was written down as "floor", and three files went on to reason from the
# word rather than from the measurement - the awaited arms could not have
# detected a faster path, because every one of them waited. A negative result is
# only evidence against a mechanism the arm could have shown.
#
# Nothing here is changed on the strength of it. The pipelined path is not what
# `trace_capture_synthetic.py` uses today, event survival was noisy run to run
# with no clean threshold, and the two metrics remain excluded as `device`.
# The claim that is retracted is the impossibility, not the current design.

STEP_MS = 8
STEPS = 40


# --------------------------------------------------------------------------
# the two baselines

def linear(start, end, rng):
    x0, y0 = start
    x1, y1 = end
    for i in range(1, STEPS + 1):
        yield (x0 + (x1 - x0) * i / STEPS, y0 + (y1 - y0) * i / STEPS, STEP_MS)


def bezier(start, end, rng):
    x0, y0 = start
    x3, y3 = end
    dx, dy = x3 - x0, y3 - y0
    spread = 0.28
    x1 = x0 + dx * 0.3 + rng.uniform(-1, 1) * abs(dy) * spread
    y1 = y0 + dy * 0.3 + rng.uniform(-1, 1) * abs(dx) * spread
    x2 = x0 + dx * 0.7 + rng.uniform(-1, 1) * abs(dy) * spread
    y2 = y0 + dy * 0.7 + rng.uniform(-1, 1) * abs(dx) * spread
    for i in range(1, STEPS + 1):
        t = i / STEPS
        u = 1 - t
        yield (u**3 * x0 + 3 * u**2 * t * x1 + 3 * u * t**2 * x2 + t**3 * x3,
               u**3 * y0 + 3 * u**2 * t * y1 + 3 * u * t**2 * y2 + t**3 * y3,
               STEP_MS)


# --------------------------------------------------------------------------
# trueman

# Tremor amplitude, and the memory that decides its *shape*.
#
# Raised from 1.15 / 0.55 on 2026-09-02 because they were set to look right and
# the metric that measures them said otherwise. Raised again the same day, for a
# different reason: under the corrected detector both humans beat us on tremor
# amplitude (2.322 and 2.066 against 1.560) **and** on how rarely it changes
# direction (39 and 25 sign changes against our 44.5). Those pull in opposite
# directions through one knob and cannot both be fixed by it.
#
# They are separable through the AR(1) memory. Amplitude is
# `sigma / sqrt(1 - phi^2)`; the sign-change rate falls as `phi` rises, because
# a walk that keeps most of its last step reverses less often. So raising `phi`
# from 0.68 to 0.86 and holding sigma flat buys a bigger wobble that turns less
# - which is what a hand does and what independent noise per point cannot do.
#
# The cost is not only those two metrics. Coordinates are rounded to integers
# before dispatch and a point that rounds onto the previous one is dropped, so a
# hand that wobbles half a pixel emits fewer events than one that wobbles two -
# which is where v2 also lost `samples per movement`.
#
# `phi` is a per-device field and not a constant here, measured 2026-09-02: the
# curvature count divided by the sample count is 0.505 per sample on the
# touchpad and 0.409 on the mouse, 12 movements each. Both were served by one
# number set to the touchpad's, and the error was invisible while the mouse arm
# was also short on samples - the *count* landed near the human because too few
# samples at too high a rate multiply out to about the right product. Fixing
# `motion_scale` exposed it: samples went 0.79 -> 1.04 and the curvature ratio
# went 1.12 -> 1.48 in the same step, the two moving together by 32% each.
# Any metric that is a rate times a count can read correct with both factors
# wrong, and only stays correct while the errors stay matched.
TREMOR_BALLISTIC = 1.05
TREMOR_CORRECT = 0.62


def _walk(device, tremor, rest):
    """Innovation sd and AR(1) memory for one phase's tremor walk.

    The rest phase gets its own memory because the humans' resting wobble is
    not the same process as their moving one, and one pair of constants was
    being asked to be both. Measured 2026-09-03 over the last decile of each
    trace, radial RMS about the local mean, converted to a per-axis sd:

        touchpad   n=99 pts   2.445 px radial -> 1.729 per axis   step 1.00 px
        mouse      n=89 pts   6.004 px radial -> 4.245 per axis   step 1.00 px

    Against a mover whose stationary per-axis sd is 1.215 and 1.331 px. So the
    humans wobble **more** at rest, not less, which is the opposite of what the
    `moves away from target` defect looks like it is asking for - and the way
    out is that those two numbers do not describe the same walk. A walk that
    spreads 4 px in 1 px steps is a drift; a walk that spreads 1.2 px in 1 px
    steps is mean-reverting noise. `sd_step ~= sd_stat * sqrt(2(1-m))` solves
    the measured pair for an implied memory of **0.916 and 0.986** against the
    moving walk's 0.86 and 0.91.

    Kept separate from `tremor_scale` and `tremor_memory` rather than replacing
    them, because those were measured over a whole movement and this was
    measured over the last decile. Applying a last-decile statistic to the burst
    is the same defect corrected twice already today - see `burst_pause_rate` in
    `trueman` and `MISS_FRAC` below it.

    **It is off on both devices, because it moves nothing.** Swept the same day
    over memory 0.916 to 0.986 and amplitude 1.0 to 1.8, including the exactly
    measured corner: touchpad `tremor px (rms)` stayed 0.67-0.69 of the human and
    `moves away from target` 2.22-2.44, mouse 0.73-0.74 and 1.29-1.43. At a
    stationary sd of 4.2 px the 0-5 px reversal rate moved from 2.5% to 1.8%,
    against the human's 12.2%.

    The reason is the fade in `_submove`: the resting walk is multiplied by
    `(1-t)**2` over the last leg, so however wide it is made it is damped to zero
    exactly where it was supposed to show. The rest walk and the fade are the
    same knob pulling opposite ways, and the measurement that decided against
    removing the fade is written beside it.

    Left in place rather than deleted, at `None`, because the rest-phase
    measurement above is real and this is where it will be tried again when
    there is a second person's trace to say whether it generalises.
    """
    sd = tremor * device.tremor_scale
    memory = device.tremor_memory
    if rest and device.rest_tremor_memory is not None:
        memory = device.rest_tremor_memory
        sd *= device.rest_tremor_scale
    return sd, memory


def _submove(a, b, motion_ms, rng, tremor, device, settling=False, skew=1.0,
             pause_rate=None, drop_two=None, state=None):
    """One sub-movement on a minimum-jerk profile, with correlated tremor.

    Minimum jerk - 10t^3 - 15t^4 + 6t^5 - is the standard model of a reaching
    movement: it starts and ends at zero velocity and peaks in the middle. It is
    used here per sub-movement rather than over the whole trip, which is what
    puts the overall speed peak early: the ballistic burst covers most of the
    distance and every correction after it is slower.

    The tremor is an AR(1) walk, not independent noise per point. Independent
    noise would give a path that crosses itself constantly and a curvature count
    far above a human's; correlated noise wanders, which is what a hand does.

    `motion_ms` is time spent moving, and the sub-movement will take longer than
    that on the wall clock: the device drops reports and the hand stops. In the
    reference trace 7.8 s of the 12 movements is motion and 27.7 s is pauses, so
    a duration passed here is not a duration observed by the page. That is why
    `movement duration ms` and `samples per movement` cannot both be tuned by
    the same number, and treating them as if they could is what made the first
    version's intervals a smear.
    """
    # The profile is advanced by the clock and not by the sample index, which
    # is the whole of the 2026-09-02 fix. The two are the same only while every
    # interval is the same length; the moment the device drops a report, an
    # index-driven path emits the same small step with a long interval attached
    # and a hand emits a step twice as long. See `Device.step`.
    motion_ms = max(motion_ms, 3 * device.period_ms)
    tremor, memory = _walk(device, tremor, settling)
    # The walk is the caller's, so it survives a leg boundary. It restarted at
    # zero in every sub-movement until 2026-09-03, and a movement is six or
    # seven of them, so the offset jumped from wherever the walk had reached
    # back to (0, 0) five or six times per trip. That jump is the walk's own
    # stationary sd, `tremor*scale/sqrt(1-memory**2)` = 1.22 px on the touchpad
    # correction, against the 0.5 px `moves away from target` counts at - so
    # most boundaries bought a reversal, and a discontinuity is also a turn.
    #
    # It is the same defect the fade comment below describes and it was in the
    # line above that comment the whole time: the fix there stopped *damping*
    # the walk per leg, and left it *restarting* per leg.
    if state is None:
        state = [0.0, 0.0]
    elapsed = 0.0
    while True:
        t = (elapsed / motion_ms) ** skew
        s = 10 * t**3 - 15 * t**4 + 6 * t**5
        state[0] = state[0] * memory + rng.gauss(0, tremor)
        state[1] = state[1] * memory + rng.gauss(0, tremor)
        ox, oy = state
        # Tremor fades as the hand arrives, on the last leg only. Fading it at
        # the end of *every* leg was wrong once the approach became a chain of
        # them: it damped the wobble six times per movement instead of once,
        # and the walk restarted from zero each time, which is the independent
        # noise this model exists to avoid.
        #
        # **Fading all the way to zero was challenged on 2026-09-03 and it
        # survives, but not for the reason the challenge was answered with.**
        # The challenge is sound: the human's 0-5 px band is his second most
        # reversal-prone of five - 12.2% against 3.5% in the band just outside -
        # over 6.8 reports advancing 0.60 px each, which is not a hand that has
        # stopped wobbling. The mover has 3.1 reports there at 1.11 px and
        # reverses 1.9%, because with no lateral wobble a sub-pixel advance
        # rounds onto its own previous pixel and the dedup at the bottom of
        # `trueman` deletes it.
        #
        # The floor was going to be *derived* rather than fitted, from how far
        # short of the target the hand actually stops, since the last point is
        # `target + offset*fade`. **That quantity is 0.00 px on all 24 human
        # movements and it is zero by construction.** `movements()` cuts a trip
        # at the `hit` event and takes the target from the event's own
        # coordinates, and a click lands where the pointer is - so the trace
        # cannot say anything else, whatever the hand did. The "2 px to go" in
        # the decile table is decile 9's median over its samples, not its last
        # one, and reading it as an endpoint is what made the derivation look
        # available.
        #
        # This is the third time in this file a quantity fixed by the
        # measurement's own construction was mistaken for an observation, after
        # the overshoot projection ten lines into `OVERSHOOT_FRAC` and the pooled
        # human table in `mover_offline.py`. The tell is the same each time: the
        # value is identical across every movement.
        #
        # So the floor is unset, the fade is to zero, and the 0-5 px band stays
        # short. Swept anyway at 0.15/0.25/0.35/0.50/0.70/1.00: a floor of 1.00
        # buys the touchpad's sample count in that band exactly (6.8 against
        # 6.8) and takes its px-per-report to 0.28 against the human's 0.60,
        # which is a hand wobbling in place rather than arriving, and `moves
        # away from target` goes 1.78 -> 2.22.
        fade = 1.0 if not settling else max(0.0, (1 - t) ** 2)
        # `dt` is the gap *after* this point, so the distance covered in that
        # gap has to come from the same draw. Advancing `elapsed` before the
        # yield instead pairs each jump with the previous point's interval,
        # which is an off-by-one and not a cosmetic one: the big step then sits
        # next to an ordinary interval, so it reads as the hand accelerating
        # rather than as a report going missing. Measured 2026-09-02, that
        # version put the touchpad's tail at 19.07 px of kinematic against 4.07
        # of timing, the exact inverse of the human it was built from.
        dt, moving = device.step(rng, pause_rate, drop_two)
        yield (a[0] + (b[0] - a[0]) * s + ox * fade,
               a[1] + (b[1] - a[1]) * s + oy * fade,
               dt)
        if elapsed >= motion_ms:
            return
        elapsed = min(motion_ms, elapsed + moving)


def _toward(cur, end, frac, rng, angle_sd):
    """A point `frac` of the way from `cur` to `end`, with an angular error.

    The error is on the *direction of travel*, not on the landing point, which
    is the difference between a hand that aims slightly off and a mover that
    teleports to a random point near the target. It also means the error scales
    with how far there is left to go, so the last leg is nearly straight.
    """
    vx, vy = (end[0] - cur[0]) * frac, (end[1] - cur[1]) * frac
    a = rng.gauss(0, angle_sd)
    ca, sa = math.cos(a), math.sin(a)
    return (cur[0] + vx * ca - vy * sa, cur[1] + vx * sa + vy * ca)


def _dwell(at, motion_ms, rng, device, state=None):
    """The hand resting on a point, at the start of a trip and at the end of it.

    Not decoration. Both humans spend the first tenth of a movement's samples
    almost stationary - median speed 0.31 and 0.49 px/ms while the next decile
    is 2.03 and 4.15 - and that dwell is what puts `peak speed at (frac)` at
    0.245 instead of near zero. A mover that starts moving on the first sample
    has its peak in the first fifth by construction and cannot reach 0.245 no
    matter how the burst is shaped.

    **The humans rest at both ends and this modelled only the near one until
    2026-09-02.** Pause milliseconds per movement by decile of sample index,
    measured that day over the same 12 movements each:

        decile       0    1    2    3    4    5    6    7    8    9
        touchpad   150    0   38   60   34   19   54   21    6  130
        mouse       35   18    2   11   10   16    8   12   19  115

    Both are U-shaped and the last decile is the taller half on the mouse. That
    is the pointer arrived and the hand not having clicked yet: it reports
    rarely because it has stopped, and it wanders sub-pixel while it waits. The
    mover had no such phase, which is also why it produced 0.10 reversals per
    movement within 5 px of the target against the touchpad human's 0.83 - one
    absent behaviour showing up in two unrelated-looking metrics.

    The pause rate here is the device's *rest* rate rather than its moving one,
    for the reason given in `Device.step`.
    """
    n = max(1, round(motion_ms / device.period_ms))
    if state is None:
        state = [0.0, 0.0]
    sd, memory = _walk(device, TREMOR_CORRECT, True)
    for _ in range(n):
        state[0] = state[0] * memory + rng.gauss(0, sd)
        state[1] = state[1] * memory + rng.gauss(0, sd)
        yield (at[0] + state[0], at[1] + state[1],
               device.step(rng, device.rest_pause_rate)[0])


SETTLE_RATIO = (0.40, 0.62)   # share of the remaining gap each leg closes
SETTLE_FLOOR = 20.0           # px: below this, one last leg onto the target
# A correction leg is a fixed number of *reports*, not a fixed duration. It was
# `(95, 165)` ms scaled by `motion_scale` until 2026-09-03, which is 95-165 ms on
# the touchpad and 47-82 on the mouse.
#
# What is wrong with a duration is measurable, and the measurement is the reason
# this is in reports. Reversals were decomposed by distance-to-target that day -
# samples spent in each band, px closed per sample, and the share of samples that
# move away - over the 12 human movements and 240 mover movements per device:
#
#     band px      human smpl / px per smpl / away%    mover
#     touchpad 20-80   18.3    2.81    7.7%           30.2    1.77   11.3%
#     mouse    20-80   20.0    2.30   15.4%           21.5    2.30   15.6%
#
# The touchpad was 65% over on sample count in that band and the mouse was
# already right - and the only thing separating them is `motion_scale`, 1.00
# against 0.50. So the touchpad's band asks for roughly half its leg, which lands
# it on the leg the mouse already had: **7-12 reports on both devices**.
#
# That is a prediction rather than a fit, and it was written down before it was
# run: one number has to work for both, where two fitted constants would not have
# to. Measured after: touchpad 19.3 samples closing 2.70 px, mouse 22.0 closing
# 2.22, against the humans' 18.3/2.81 and 20.0/2.30.
#
# The mechanism is a threshold and it is why the metric was so far out. A
# reversal is `dist to target` rising by more than 0.5 px, so a band's reversal
# rate is decided by whether the advance per report clears the tremor's *step* -
# `sd_stat * sqrt(2(1-memory))`, 0.81 px radial on the touchpad. The old leg
# advanced 0.82 px per report in the 5-20 px band, which is the same number, so
# nearly every report there could reverse: 14.5% against the human's 3.5%. This
# is not a tuning parameter with a smooth cost, it is a crossing.
SETTLE_LEG_SAMPLES = (7, 12)  # reports in one settle leg, both devices
FINAL_MS = (65, 110)
DWELL_MS = (55, 110)
# Spread of peak speed around `peak_k * sqrt(distance)`. The measured cv is
# 0.255 and 0.252, and this is below both because part of that spread is tremor
# and rounding inflating the peak, which the mover gets for free.
PEAK_CV = 0.20

# The burst's velocity profile is asymmetric: minimum jerk is run over a warped
# time `t**BURST_SKEW`, so below 1 it accelerates hard and decelerates slowly.
#
# Symmetric minimum jerk was wrong and the metric that caught it is `tremor px
# (rms)`, which is not a tremor measurement. It is the RMS distance of each
# point from the midpoint of its neighbours, and RMS is not robust: measured
# 2026-09-02 over 905 and 806 interior points, the humans sit at a median of
# 0.71 and 0.85 with a p99 of 14.30 and 9.18 and a maximum of 29.8 and 46.1.
# The mover had a *higher* median - 1.00 and 1.12 - an identical p90, and no
# tail at all, p99 5.02 against 14.30.
#
# So the human's number is a handful of events and the mover's is constant
# jitter, and the two arrive at the same statistic from opposite directions.
# Raising `tremor_scale` to chase it made the typical sample worse to buy a tail
# it could not produce, which is why tremor and `moves away from target` moved
# together and neither could be satisfied: at scale 1.8 tremor reached only 0.86
# of the human while moves-away went to 1.86.
#
# Where the tail sits says what it is. By decile of sample index, 15 of the
# touchpad's 20 deviations above 8 px and 14 of the mouse's 17 are in the first
# three deciles, and the median falls monotonically to 0.50 by the last. It is
# acceleration out of the dwell, not tremor - a large second difference is what
# going from stationary to fast in two samples looks like - and a symmetric
# profile spreads that acceleration over the whole burst by construction.
#
# The lesson is the one already recorded twice in this file for `norotate` and
# `OptimizationHints`, in a new costume: a summary statistic agreed with in the
# aggregate was allowed to stand for the distribution, and the distribution said
# something else. Check a percentile before tuning a mean or an RMS.
#
# **The skew is 1.0, which is to say it is off, and the analysis above still
# stands.** Front-loading the acceleration does raise tremor exactly as the
# decile table predicts, and it costs more elsewhere than it buys. Measured the
# same day on the touchpad arm, dwell 55-110 ms, everything else held:
#
#     skew   tremor   peak speed at (frac)
#     1.00     0.66         0.85
#     0.75     0.69         0.68
#     0.62     0.72         0.61
#     0.50     0.83         0.39
#
# `peak speed at (frac)` is the metric a dwell exists to move, and the skew
# destroys it about three times faster than it repairs tremor: an earlier peak
# inside the burst is an earlier peak in the movement.
#
# The mechanism is real and the remedy was wrong, which is worth separating.
# What actually moved tremor and peak-at-frac together was the dropped-interval
# bug at the bottom of `trueman`, found while instrumenting this: the skew was
# being asked to compensate for a dwell that was generated and then deleted.
# Diagnose before prescribing, and re-check the diagnosis after fixing anything
# else - the second measurement here disagreed with the first because the ground
# had moved under it, not because either was misread.
#
# **Re-checked 2026-09-03 as that sentence asks, and the rejection holds.** The
# ground had moved twice - `peak speed at (frac)` was 0.85 when the sweep above
# was read and is 1.01 now - so there was headroom below it that had not existed.
# Swept 1.00 to 0.50 on the touchpad: `peak speed at (frac)` still falls three
# times faster than anything rises, 1.01 -> 0.73, and the deficit this was aimed
# at does not move at all - 17.1 reports in the 80-320 px band at skew 1.00
# against 17.0, 17.2, 17.0 at 0.90, 0.80, 0.70, where the human has 25.2. On the
# mouse skew 0.90 improves four metrics *and* moves the band measurement away
# from the human, 0.450 -> 0.486 of peak in the 320+ band against his 0.436.
# That is the definition of a fit, so it is not taken.
#
# **What the sweep did establish is that the burst's remaining defect is its
# duration, not its shape**, and the arithmetic is short enough to state. On the
# touchpad's median 396 px trip the burst covers about 321 px to the aim point.
# The human spends 41.9 reports getting there, roughly 295 ms; the mover solves
# `BURST_COEFF * D / peak` and gets 120 ms, 17 reports, at a peak speed already
# matched to 0.96 of his. Same distance, same peak, 2.5x the time.
#
# That is not something a warp of the time axis can reach, because a warp
# redistributes reports inside a fixed duration and leaves `D`, `peak` and
# therefore `T` alone. It says the human's velocity curve is more sharply peaked
# than minimum jerk - it reaches the same maximum and spends far more of the trip
# well below it - and minimum jerk's mean-to-peak of 1/1.875 = 0.533 is the thing
# that is wrong. Measured per movement as mean px per report in a band over that
# movement's own largest step: the human is 0.394 in the 320+ band and 0.282 in
# 80-320, the mover 0.277 and 0.323.
#
# This is the open defect. It is where `samples per movement` at 0.82 on the
# touchpad comes from, and it is the last candidate for `curvature sign changes`
# at 1.62 on the mouse, which was already shown to survive tremor being switched
# off entirely and so has to be geometry. Fixing it means replacing minimum jerk
# for the burst, not adding a constant in front of it.
BURST_SKEW = 1.0


def _peak_coeff(skew, steps=2000):
    """Peak of d/dt of minimum jerk run over `t**skew`, in units of D/T.

    1.875 at skew=1, the textbook value; the burst's time is solved from the
    peak speed it is asked for, so this factor has to follow the skew or
    `peak_k` silently absorbs it and stops meaning what it is named after.
    """
    best, prev = 0.0, 0.0
    for i in range(1, steps + 1):
        t = (i / steps) ** skew
        s = 10 * t**3 - 15 * t**4 + 6 * t**5
        best = max(best, (s - prev) * steps)
        prev = s
    return best


BURST_COEFF = _peak_coeff(BURST_SKEW)
# How far short of the target the burst stops. **Per device since 2026-09-03**,
# and it was a shared (0.18, 0.34) before that.
#
# Measured that day on each trace: take the peak speed of a movement, walk
# forward to the first sample under 20% of it, and read the distance still to
# go as a fraction of the trip. That is the ballistic phase ending. The
# touchpad human leaves **0.192** of the trip (p10 0.075, p90 0.517) and the
# mouse human **0.141** (p10 0.034, p90 0.264).
#
# The proxy is calibrated rather than assumed: run on `trueman` itself it reads
# 0.259 against a `MISS_FRAC` whose mean is 0.26, so the statistic recovers the
# constant it is being used to set. Both devices were short-changed by the old
# value and they are 36% apart from each other, which a shared constant cannot
# hold - the same shape as `drop_two` above.
#
# The cost of the old value was not on this metric at all, which is why it took
# a band decomposition to find. Stopping 26% short leaves a gap the geometric
# chain has to crawl, and the crawl is where the mover put its reversals:
# measured 2026-09-03 in the 20-80 px band, the touchpad human spends 18.3
# samples closing 2.05 px each while the mover spent 32.5 closing 1.35. The
# walk's stationary sd is 1.22 px, so the human's step clears its own tremor
# and the mover's did not, and a step that does not clear the tremor reverses.
MISS_FRAC = (0.18, 0.34)      # fallback for a Device that does not set one
# ...and how far past it, when it goes past. The burst only ever stopped short
# until 2026-09-02, which is why the mover scored 0.71 on `moves away from
# target` and 0.83 on `straightness`: an approach that never crosses the target
# has no reason to travel further than the direct line.
#
# Both are measured off the maximum projection onto the start-to-target axis.
# The pointer ends on the target, so that projection is 1.0 by construction and
# only the excess above it is an overshoot - reading the raw median of 1.007 and
# 1.077 as "overshoots by 1%" would be reading the constraint, not the hand.
OVERSHOOT_FRAC = (0.03, 0.35)

# `SETTLE_FLOOR` is 20 px and that is the detector's own threshold, which needs
# saying out loud because it looks like fitting to the test. It is not chosen to
# beat `samples within 20px`; it is chosen because both humans' decile table
# shows the geometric chain running down to about 30 px and then one quick leg
# onto the target, and 20 is where that transition sits. The metric happens to
# use the same number because 20 px is roughly where a click target starts being
# hit. If the detector moved its threshold to 30 this constant would not move.


def trueman(start, end, rng, device=TOUCHPAD):
    """Dwell, one fast burst, then a geometric settle.

    **This replaced "ballistic burst plus one to three min-jerk corrections" on
    2026-09-02, and the old shape was not a tuning problem - it was the wrong
    model.** Under the corrected detector it lost four metrics at once: `peak
    speed` 3.161 against 5.478 and 7.266, `samples within 20px` 0.324 against
    0.217 and 0.161, `tremor` 1.560 against 2.322 and 2.066, `curvature sign
    changes` 44.5 against 39 and 25. Shortening the burst fixed peak speed and
    broke sample count; lengthening the corrections fixed sample count and put
    them all inside 20 px. Nothing in that parameter space satisfies both,
    which is the signal that the structure is wrong rather than the constants.

    What the humans actually do, measured by binning each movement into deciles
    and taking the median speed and distance-to-target in each:

        decile      0     1     2     3     4     5     6     7     8     9
        touchpad  0.31  2.03  1.87  1.65  0.76  0.70  0.32  0.29  0.20  0.07
        px to go   386   357   248   171   144    95    51    34     9     2
        mouse     0.49  4.15  5.51  2.50  1.02  0.66  0.43  0.52  0.42  0.20
        px to go   752   581   282   132   121    77    53    31    16     2

    Three phases, not two:

    1. **A dwell.** Decile 0 barely moves - 29 px of 396 on the touchpad, 171
       of 752 on the mouse - and this is a tenth of every movement's samples.
       It is the hand still sitting where the last click left it. Without it
       `peak speed at (frac)` cannot reach the humans' 0.245.
    2. **A burst**, deciles 1-3, which covers most of the distance and holds
       the speed peak. It has to be *short* to peak that fast.
    3. **A geometric settle**, deciles 4-9, which is 60% of the samples and
       closes 144 px to 2. Distance falls by roughly half per decile, so the
       time per halving is constant - the signature of a chain of corrections
       each closing a fixed fraction of what is left, not of one decelerating
       curve. This is where the sample count comes from, and because it is
       geometric most of it happens *outside* 20 px, which is the part the old
       model could not do.

    The same radial structure shows up as a flat histogram in log distance:
    every octave band from >320 px down to 10 px holds 10-21% of the samples,
    both devices. A single min-jerk curve piles them at the ends.

    Unchanged from the previous version and still doing their job:

    - Integer coordinates. A real mouse at dpr 1 cannot produce a fractional
      `clientX`; ours did in 97% of events. Rounded here, and consecutive
      duplicates dropped rather than dispatched, because a mouse that has not
      moved does not send an event.
    - Every interval comes from the device's own clock, not from a draw over a
      range. See `Device`.
    - Tremor is an AR(1) walk. Independent noise per point gives a path that
      crosses itself constantly and a curvature count far above a human's.
    """
    dist = math.dist(start, end)
    if dist < 1:
        return
    scale = device.motion_scale
    # One tremor walk for the whole trip, handed to every phase. See `_submove`.
    tw = [0.0, 0.0]

    plan = list(_dwell(start, rng.uniform(*DWELL_MS) * scale, rng, device, tw))

    # The burst aims along the path rather than at a random point around the
    # target: a uniform angle makes every correction a reversal, and the humans
    # reverse 7 times per movement in total, not once per leg. Whether it stops
    # short or goes past is drawn per device - the mouse goes past on 9 of 12
    # movements and the touchpad on 4 of 12.
    if rng.random() < device.overshoot_rate:
        aim_frac = 1 + rng.uniform(*OVERSHOOT_FRAC)
    else:
        aim_frac = 1 - rng.uniform(*getattr(device, "miss_frac", MISS_FRAC))
    aim = _toward(start, end, aim_frac, rng, 0.055)

    # Peak speed is drawn, and the burst's motion time follows from it, rather
    # than the other way round. Minimum jerk peaks at 1.875 * D / T, so the two
    # are the same statement; choosing which one to draw is choosing which one
    # the measurement constrains, and it is the peak.
    #
    # `peak speed / sqrt(distance)` is what holds still inside a human trace.
    # Measured 2026-09-02 across the 12 movements of each trace, coefficient of
    # variation of three candidate normalisations:
    #
    #     normaliser        touchpad   mouse
    #     peak / sqrt(d)       0.255    0.252     <- flattest on both
    #     peak / d             0.421    0.258
    #     peak alone           0.393    0.447     <- worst on both
    #
    # This replaced `uniform(50, 80) + 16 * log2(1 + dist / 40)`, which was
    # fitted to the medians of the two traces while those medians were being
    # read off different trip geometry - see the note in `mover_offline.py`.
    #
    # The device constants are 0.251 and 0.288, a ratio of 1.15. The raw median
    # peaks are 5.478 and 7.266, a ratio of 1.33, and the difference between the
    # two ratios is entirely the confound: the mouse trace's median trip is
    # 693.7 px and the touchpad's is 396.2. Read as a device difference, the
    # mouse looked 33% faster; it is 15% faster and was measured further away.
    #
    # Two cautions on the law itself. It is 12 movements per device from one
    # person, so it is a within-trace regularity and not a fact about hands.
    # And it is the *measured* peak, which tremor and integer rounding inflate
    # above the geometric one, so `peak_k` is not expected to come out at the
    # measured value - `mover_offline.py` is what settles it.
    want_peak = device.peak_k * math.sqrt(dist) * rng.lognormvariate(0, PEAK_CV)
    burst_ms = BURST_COEFF * math.dist(start, aim) / max(0.5, want_peak)
    # `pause_rate=0` here and nowhere else. A hand does not freeze at peak
    # speed, and the humans say so - see the quartile table in `Device.step`.
    # Gating on the phase reproduces that threshold without a speed-dependent
    # rate, which would be a second free parameter fitted to the same table.
    #
    # Pauses were suppressed in *every* sub-movement for part of 2026-09-02 and
    # that overshot: it cut the touchpad's dead time to 148 ms of a movement
    # against the human's 519, so `movement duration ms` fell to 0.77 of the
    # human while `samples per movement` stayed at 1.01 - the right number of
    # events in too little time. The burst is what had to be protected; the
    # correction phase is where a hand actually hesitates.
    #
    # The burst stops the hand at `burst_pause_rate` and loses a report at
    # `burst_drop_two`, and those are different events with opposite effects on
    # the score - see `Device.step`. Measured on the fast speed quartile,
    # 2026-09-03, the two devices are mirrors: the touchpad has 0 pauses above
    # 20 ms in 230 intervals and 5 drops, the mouse has 1 pause in 205 and no
    # drops. So the touchpad stops the hand at 0.0 and the mouse at 0.005.
    #
    # This was 0.018 and 0.020 for part of 2026-09-03, from the share of
    # intervals above 20 ms in deciles **1-3 by index**, 5 of 274 and 5 of 245.
    # That window is not this phase: it starts at the dwell exit, so it is
    # mostly the acceleration ramp where the hand is still slow, and it carries
    # that phase's stopping rate into the one place the humans never stop. It
    # cost the mouse arm `movement duration ms` 1.12 -> 1.26 and `moves away`
    # 1.14 -> 1.29 to buy tremor 0.81 -> 0.87, and it took that arm's late
    # reports at top speed to 2.6% against the human's 0.5%.
    #
    # What the mistake looked like from the inside: it was a measurement, taken
    # that day, from the right trace, and it replaced a zero that had no number
    # behind it - so it looked strictly like an improvement. The defect is in
    # the denominator. `trueman`'s phases are defined by speed and the decile
    # table is cut by index, and the two only coincide if every movement
    # accelerates on the same schedule. Cut the window the way the phase is
    # defined, or the number is about a different phase than the one it is
    # assigned to.
    plan += _submove(start, aim, burst_ms, rng, TREMOR_BALLISTIC, device,
                     skew=BURST_SKEW, pause_rate=device.burst_pause_rate,
                     drop_two=device.burst_drop_two, state=tw)

    cur, guard = aim, 0
    while math.dist(cur, end) > SETTLE_FLOOR and guard < 12:
        guard += 1
        landing = _toward(cur, end, rng.uniform(*SETTLE_RATIO), rng, 0.12)
        # `period_ms` and not `scale`: see `SETTLE_LEG_SAMPLES`.
        leg = list(_submove(cur, landing,
                            rng.uniform(*SETTLE_LEG_SAMPLES) * device.period_ms,
                            rng, TREMOR_CORRECT, device, state=tw))
        # The pause before a correction is charged to the previous point, so it
        # appears as one long interval rather than as a gap with no event. It is
        # drawn from the same distribution as any other pause: deciding to
        # correct is not a different kind of stopping from the ones already in
        # the tail, and giving it its own range would be a second, invented
        # population inside a metric that already matches.
        if plan and rng.random() < 0.45:
            x, y, _ = plan[-1]
            plan[-1] = (x, y, device.pause(rng))
        plan += leg
        cur = landing

    # The last leg is a slow creep and not a landing, and it carries the resting
    # pause rate rather than the moving one. Both humans close the final 20 px
    # one pixel per report: over the last decile the step is median 1.00 px with
    # a p90 of 1.41 - which is 1 px diagonally, so essentially every step is the
    # smallest a device can send - while 37.9% and 13.0% of the intervals there
    # are over 20 ms. A hand creeping under visual control moves too little to
    # report, which is the same threshold as everywhere else in this file.
    #
    # **A stationary hover was tried here first on 2026-09-02 and it was the
    # wrong shape.** The decile table says the pointer is still closing during
    # the last tenth - 9 px to go at decile 8 and 2 at decile 9 - so the phase
    # is monotone approach, not wobble around the target. Modelled as a wobble
    # it produced 2.97 reversals per movement inside 5 px against the human's
    # 0.83, and `moves away from target` went to 1.86 of the human. A random
    # walk and a slow approach have the same speed and opposite reversal counts.
    plan += _submove(cur, end, rng.uniform(*FINAL_MS) * scale, rng,
                     TREMOR_CORRECT, device, settling=True,
                     pause_rate=device.rest_pause_rate, state=tw)

    # A point that rounds onto the previous one sends nothing - a mouse that has
    # not moved does not report. **Its time still passes**, and it was being
    # thrown away with the point until 2026-09-02: the interval was dropped
    # along with the coordinate, so a hand that sat still for 200 ms produced no
    # events *and* no elapsed time, and the movement simply got shorter.
    #
    # Charging it to the next event is what a real device does, and it is the
    # difference between a dwell and nothing at all. The visible symptom was
    # `peak speed at (frac)`, stuck at 0.53-0.80 against the human's 0.245 no
    # matter what the burst did: the dwell is the only thing that can put the
    # peak late in a movement, and it was being deleted here after being
    # generated correctly 40 lines up.
    #
    # It also means the mover was quietly failing to emit the long intervals it
    # asked for, which is a timing claim, so anything measured before this date
    # about interval distribution is suspect and not just the shape metrics.
    last, carry = None, 0.0
    for x, y, dt in plan:
        point = (round(x), round(y))
        if point == last:
            carry += dt
            continue
        last = point
        yield (point[0], point[1], dt + carry)
        carry = 0.0


def press_jitter(rng, device=TOUCHPAD):
    """Tiny moves between mouse-down and mouse-up. Usually none.

    **This was fired on every click and it should not have been.** The rule was
    written off a count - "11 pointermove events with pressure 0.5 across 13
    clicks" - and a count says nothing about how they are distributed. Per
    click, measured 2026-09-02:

        touchpad   [1,0,0,1,0,3,1,3,0,0,0,2,0]      median 0, mean 0.85, 6/13
        mouse      [0,0,0,0,0,0,0,0,0,0,1,0,0,0,0]  median 0, mean 0.07, 1/15
        v4         [2,2,2,1,2,2,2,2,1,2,1,2]        median 2, mean 1.75, 12/12

    Both humans have a **median of 0** and ours had 2. Emitting one every time
    is as detectable as emitting none, and against the mouse it was the single
    largest tell at |d| 0.78. A finger does slide under a click on a touchpad;
    a mouse under a fingertip mostly does not move at all.

    Deleting it outright was the other option and would be wrong - it is a real
    touchpad behaviour and would then be missing from the profile that has it.
    It is a rate, and the rate belongs to the device.
    """
    if rng.random() >= device.press_rate:
        return
    for _ in range(rng.randint(1, device.press_max)):
        yield (rng.choice([-1, 0, 1]), rng.choice([-1, 0, 1]),
               device.interval(rng))


# --------------------------------------------------------------------------
# wheel

def wheel_plan(mover_name, direction, rng, device=TOUCHPAD):
    """Yields `(delta_y, wait_ms_after)` for one wheel step.

    The baselines send a constant 120 every 30 ms, one distinct value across the
    whole session. **That was scored as a tell and it is not one.** The caveat
    was already written in this docstring - "a notched wheel really does emit
    120, so 120 alone is not a tell" - and it was disregarded, because the only
    human trace available came from a touchpad, whose continuous surface emits
    62 distinct magnitudes at a median of 6 every 7 ms. Measured 2026-09-02 on a
    real mouse from the same person: one distinct magnitude, 166.667, at a
    median interval of 29.7 ms. Against that trace `bezier`'s wheel is correct
    to p=0.94, and both wheel metrics are now excluded as `device`.

    What the mistake looked like from the inside: the caveat named the exact
    condition under which the conclusion fails and there was no way to check it,
    so it was written down and then reasoned past. A caveat that cannot be
    tested yet is a blocker on the claim, not a footnote to it.

    So the magnitude and the interval both come from the device, and neither is
    a property of being human.

    **`scroll per wheel event` is not a wheel property at all, and the note that
    stood here was wrong.** It said the ratio of 1.0 against the human's 0.388
    was the compositor coalescing wheels that arrive faster than a frame, and
    that bringing the interval down to 7 ms would fix it. The interval came down
    to 7.2 ms in v2 and the ratio stayed at 0.999. Measured instead of guessed:
    of the human's 772 wheel events that moved nothing, **734 are at a page
    edge** and 38 are mid-page. He kept turning the wheel after the page had
    stopped; only 3% of his events are the effect the note claimed was all of
    it. The fix is in the scroll loop of `trace_capture_synthetic.py`, not here.

    What the mistake looked like from the inside: the frame-rate arithmetic came
    out at 0.42 against a measured 0.388, and a prediction that lands within 8%
    of the observation is very hard to keep treating as a guess. It was still a
    guess - the agreement was a coincidence between two numbers near 0.4 - and
    the thing that caught it was writing "if the ratio does not fall, the cause
    is something else" into the comment before running. Predict in writing, then
    read the result, or a coincidence will be promoted to a cause.
    """
    if mover_name != "trueman":
        yield (120 * direction, 30)
        return

    # The touchpad's magnitude is fitted to both moments rather than to the
    # median alone: it is median 6 and mean 11.2, and the first fit reproduced
    # the median while its mean came out at 8.3. The mean is what decides how
    # many events it takes to cross the page, so getting it wrong inflates the
    # event count without touching the metric that checks it.
    yield device.wheel_step(rng, direction)


# How far one flick carries the page, in px, and how long the reader spends
# before the next one. Both measured 2026-09-04 by `lab/probes/scroll_shape.py`
# over the two human traces, grouping wheel events into flicks at a 150 ms gap.
#
# **These are the only constants in this file that are not per device, and that
# is the finding rather than a shortcut.** The two humans are 27.8x apart on the
# notch and 3.6x apart on the cadence, and 1.1x apart on how far a flick carries
# the page, 1.3x on the rest after it, and 1.0x on the number of flicks it took
# to read the same article - 11 and 11. So a flick is the device and a page is
# the person, and pooling the two traces here is pooling two measurements of the
# same quantity rather than two different ones.
#
# That is the opposite of the call `lab/probes/idle_fit.py` made on idle gaps,
# where the same two people came out four-fold apart and the fit was refused.
# The refusal was about a split, not about a policy of never fitting to two
# people, and this is what the other side of it looks like.
#
# n=20 flicks over 50 px and n=20 rests, from two people on one page. Two-moment
# lognormals, the same method as everything else here: the flick reproduces
# median 775 and mean 985 px, the rest median 250 and mean 421 ms. Clamped just
# outside the measured range, as the wheel interval is.
#
# **Not scored.** `trace_compare.py` has no burst metric - it reads wheel
# magnitude and wheel interval per event, and both are excluded as `device` - so
# nothing has ever checked a driven scroll for this structure. It is measured
# and unscored, which is a weaker claim than the walk's and is why it is written
# down here rather than left in the action that uses it.
SCROLL_FLICK_MU, SCROLL_FLICK_SIGMA = 6.65, 0.69
SCROLL_FLICK_MIN, SCROLL_FLICK_MAX = 200.0, 3500.0
SCROLL_REST_MU, SCROLL_REST_SIGMA = 5.52, 1.02
SCROLL_REST_MIN, SCROLL_REST_MAX = 170.0, 2000.0


def scroll_flick_px(rng) -> float:
    return min(SCROLL_FLICK_MAX,
               max(SCROLL_FLICK_MIN,
                   rng.lognormvariate(SCROLL_FLICK_MU, SCROLL_FLICK_SIGMA)))


def scroll_rest_ms(rng) -> float:
    return min(SCROLL_REST_MAX,
               max(SCROLL_REST_MIN,
                   rng.lognormvariate(SCROLL_REST_MU, SCROLL_REST_SIGMA)))


def scroll_plan(mover_name, distance_px, rng, device=TOUCHPAD, direction=1):
    """Yields `(delta_y, wait_ms_after, resting)` to carry the page that far.

    `resting` separates the two kinds of wait, because the caller has to treat
    them differently and cannot tell them apart from the number: a cadence is an
    interval between dispatches and belongs on `Pacer`'s timeline, a rest is the
    pointer sitting still and is exactly the case `Pacer.restart` exists for.
    Putting a 300 ms rest on the timeline would also count it as a paced point,
    which would dilute the `pointer_overruns` ratio with waits no transport can
    overrun.

    The distance is a target and not a promise. A flick is drawn whole and then
    delivered in whole wheel notches, so a mouse asked for 900 px delivers 5
    notches of 166.667 and overshoots by 33. Rounding the last notch down to
    land exactly on the asked pixel is what a driver does and what no wheel can
    do, and `scroll per wheel event` is the metric that catches it - see
    `wheel_plan`, where the same instinct produced a ratio of 1.0 against the
    human's 0.388.

    The baselines keep the constant they had, and the flick structure is not
    applied to them. `wheel_plan` yields 120 px every 30 ms for any mover that
    is not `trueman`, one distinct magnitude for the whole session, and that is
    the arm the humanized one is measured against - giving it human reading
    pauses would move the control and leave nothing to compare to. It is the
    same rule as `press_jitter` and the cursor walk, both of which `trueman`
    gates in the same way.
    """
    if distance_px <= 0:
        return
    if mover_name != "trueman":
        moved = 0.0
        while moved < distance_px:
            for delta, wait in wheel_plan(mover_name, direction, rng, device):
                moved += abs(delta)
                yield (delta, wait, False)
        return
    carried = 0.0
    first = True
    while carried < distance_px:
        if not first:
            yield (0, scroll_rest_ms(rng), True)
        first = False
        flick = min(scroll_flick_px(rng), distance_px - carried)
        moved = 0.0
        while moved < flick:
            for delta, wait in wheel_plan(mover_name, direction, rng, device):
                moved += abs(delta)
                yield (delta, wait, False)
        carried += moved
