"""The wheel half of the pointer model.

Nothing here touches a browser: `scroll_plan` is a generator of numbers, and
what a test can check about it is the structure of those numbers.

**This is the weakest-scored thing in the module and the tests are what stand in
for the scoring.** The cursor walk and the keystroke delay are checked against a
human trace by `lab/probes/trace_compare.py` and the arm reads 0 tells. The flick
and the rest are **measured and unscored**: the detector reads wheel magnitude
and wheel interval per event and excludes both as `device`, so it has no metric
that would notice whether a driven scroll has this structure at all. A constant
that no metric checks and no test pins is a constant that will drift, and the
drift will not show up anywhere.

So the tests below are deliberately about the two properties a driver gets wrong
rather than about coverage: that the plan does not land exactly on the pixel it
was asked for, and that a rest is separable from a cadence.
"""
import itertools
import random
import statistics

import pytest

from nmbench import pointer

MOVERS = ["off", "bezier", "trueman"]


def rng(seed=7):
    return random.Random(seed)


def plan(mover, distance, seed=7, device=pointer.TOUCHPAD, direction=1):
    return list(pointer.scroll_plan(mover, distance, rng(seed), device,
                                    direction))


class TestWhatEveryArmDoes:
    @pytest.mark.parametrize("mover", MOVERS)
    @pytest.mark.parametrize("distance", [0, -1])
    def test_nothing_is_asked_of_a_page_there_is_nothing_to_scroll(
            self, mover, distance):
        """A zero-length plan and not one wheel event. `warm._act_scroll` draws
        its distance from a declared range so this should not happen, but a
        generator that emits one event for a distance of zero would put a wheel
        on a page the caller asked to leave alone."""
        assert plan(mover, distance) == []

    @pytest.mark.parametrize("mover", MOVERS)
    def test_it_carries_the_page_at_least_as_far_as_asked(self, mover):
        moved = sum(abs(d) for d, _w, resting in plan(mover, 4000) if not resting)
        assert moved >= 4000

    @pytest.mark.parametrize("mover", MOVERS)
    def test_direction_is_the_sign_and_nothing_else_moves_with_it(self, mover):
        """Scrolling back up is the same model with the sign flipped. It matters
        because the lab capture reads to the bottom of a page and then back to
        the top, and a plan that terminated on a signed sum rather than on an
        absolute one would never finish the second half."""
        down = plan(mover, 4000, direction=1)
        up = plan(mover, 4000, direction=-1)
        assert all(d >= 0 for d, _w, _r in down)
        assert all(d <= 0 for d, _w, _r in up)
        assert [(-d, w, r) for d, w, r in down] == up


class TestTheBaselineIsLeftAlone:
    """`--humanize off` is the arm the humanized one is measured against, so
    giving it human reading pauses would move the control and leave nothing to
    compare to. Same rule as `press_jitter` and the cursor walk, both of which
    `trueman` gates the same way."""

    @pytest.mark.parametrize("mover", ["off", "bezier"])
    def test_it_is_the_constant_it_always_was(self, mover):
        assert {(d, w) for d, w, _r in plan(mover, 1000)} == {(120, 30)}

    @pytest.mark.parametrize("mover", ["off", "bezier"])
    def test_it_never_rests(self, mover):
        """The flick structure is not applied to the baselines at all, so no
        step of theirs is ever flagged as one the caller should sleep through
        and restart the pacer after."""
        assert not any(resting for _d, _w, resting in plan(mover, 8000))


class TestTheFlickAndTheRest:
    """Measured 2026-09-04 by `lab/probes/scroll_shape.py` over the two human
    traces, grouping wheel events into flicks at a 150 ms gap.

    These are the only constants in `pointer.py` that are not per device, and
    that is the finding rather than a shortcut: the two people are 27.8x apart
    on the wheel notch and 3.6x on the cadence, but 1.1x apart on how far a
    flick carries the page, 1.3x on the rest after it, and 1.0x on the number of
    flicks it took to read the same article - 11 and 11.
    """

    def test_a_plan_never_opens_with_a_rest(self):
        """A rest is the pause *after* a flick. Opening with one would put a
        quarter of a second of nothing between arriving on a page and touching
        it, which is a different behaviour that nobody measured."""
        for seed in range(20):
            steps = plan("trueman", 6000, seed=seed)
            assert steps and not steps[0][2]

    def test_two_rests_never_come_back_to_back(self):
        rests = [resting for _d, _w, resting in plan("trueman", 20000)]
        assert not any(a and b for a, b in itertools.pairwise(rests))

    def test_a_rest_moves_the_page_by_nothing(self):
        """It is the pointer sitting still, so the delta is zero and not a small
        number. A caller that dispatched it would emit a wheel event of 0 px,
        which is the shape a page edge produces and would corrupt the one metric
        that reads those."""
        assert all(d == 0 for d, _w, resting in plan("trueman", 20000) if resting)

    def test_there_is_one_fewer_rest_than_there_are_flicks(self):
        steps = plan("trueman", 20000)
        rests = sum(1 for _d, _w, resting in steps if resting)
        flicks = 1 + rests
        assert flicks == len(_flick_sizes(steps))

    def test_a_rest_is_drawn_from_the_rest_distribution(self):
        steps = plan("trueman", 20000)
        rests = [w for _d, w, resting in steps if resting]
        assert rests, "no rest in a 20000 px read"
        assert all(pointer.SCROLL_REST_MIN <= w <= pointer.SCROLL_REST_MAX
                   for w in rests)

    def test_no_threshold_could_tell_a_rest_from_a_cadence(self):
        """The reason the flag exists rather than being inferred from the wait.

        A cadence is an interval between dispatches and belongs on `Pacer`'s
        timeline; a rest is the pointer sitting still and is what
        `Pacer.restart` is for. Putting a 300 ms rest on the timeline would also
        count it as a paced point, which dilutes `pointer_overruns` with waits
        no transport can overrun. So the caller has to treat them differently
        and this asserts it cannot work out which is which on its own.

        **Measured here rather than assumed, and the first version of this test
        asserted the opposite.** It read `all(w < SCROLL_REST_MIN for w in
        cadences)` - on the reasoning that a flick is a fast burst and 170 ms is
        far outside it - and that is true of the *median* cadence, 7.1 ms, and
        false of the distribution. The touchpad's cadence carries the device's
        whole pause tail, which runs to 520 ms: over 8 seeds and 15269 cadences,
        2026-09-04, **42 of them (0.28%) sit above `SCROLL_REST_MIN`**. Rare
        enough that a hand-checked sample would miss it and common enough that a
        night's run would not.

        What the mistake looked like from the inside: the docstring being tested
        already said the two are not separable from the number alone, and the
        test was written from what a flick is *usually* like instead of from the
        sentence it was supposed to be checking."""
        cadences = [w for seed in range(8)
                    for _d, w, resting in plan("trueman", 20000, seed=seed)
                    if not resting]
        assert any(w >= pointer.SCROLL_REST_MIN for w in cadences)

    def test_the_flick_is_the_person_and_the_notch_is_the_device(self):
        """The same reading over two devices takes a wildly different number of
        wheel events and about the same number of flicks. That is the finding
        stated as a test: if somebody makes the flick per-device, this is what
        says so.

        Over seeds and not on one, because the flick count of a single plan is a
        draw. The two devices consume the generator at very different rates -
        1922 wheel events against 120 for the same 20000 px - so the same seed
        reaches the flick draws at different points in the stream and single
        plans differ by 5. Measured over 40 seeds, 2026-09-04: 21.60 flicks
        against 19.32, against 16x on the events."""
        counts = {}
        for name, device in (("pad", pointer.TOUCHPAD),
                             ("mouse", pointer.MOUSE)):
            plans = [plan("trueman", 20000, seed=s, device=device)
                     for s in range(40)]
            counts[name] = (
                statistics.fmean(sum(1 for _d, _w, r in p if not r)
                                 for p in plans),
                statistics.fmean(1 + sum(1 for _d, _w, r in p if r)
                                 for p in plans))
        assert counts["pad"][0] > 8 * counts["mouse"][0]
        assert counts["pad"][1] == pytest.approx(counts["mouse"][1], rel=0.25)


class TestTheDistanceIsATargetAndNotAPromise:
    """A flick is drawn whole and then delivered in whole wheel notches, so a
    mouse asked for 900 px overshoots. Rounding the last notch down to land
    exactly on the asked pixel is what a driver does and what no wheel can do,
    and `scroll per wheel event` is the metric that catches it - see
    `wheel_plan`, where the same instinct produced a ratio of 1.0 against the
    human's 0.388."""

    def test_a_notched_wheel_delivers_whole_notches_and_overshoots(self):
        notch = pointer.MOUSE.wheel_notch
        for seed in range(10):
            steps = plan("trueman", 900, seed=seed, device=pointer.MOUSE)
            moved = sum(d for d, _w, r in steps if not r)
            assert moved > 900, "the last notch was rounded down to fit"
            assert abs(moved / notch - round(moved / notch)) < 1e-6

    def test_the_overshoot_is_under_one_notch(self):
        """It overshoots because a notch is indivisible, not because the plan
        drifts: a flick's own draw is clamped to what is left of the distance."""
        notch = pointer.MOUSE.wheel_notch
        for seed in range(10):
            moved = sum(d for d, _w, r in plan("trueman", 900, seed=seed,
                                               device=pointer.MOUSE) if not r)
            assert moved - 900 < notch


class TestTheConstantsStillReproduceTheTrace:
    """The fit is two-moment lognormal, the same method as everything else in
    the module, and it is **clamped** - which is what makes this worth asserting
    rather than true by construction. An unclamped lognormal reproduces its own
    moments exactly; these are cut at 200/3500 px and 170/2000 ms, and the cut
    at the bottom of the flick is inside the body of the distribution.

    n=20 flicks over 50 px and n=20 rests, from two people on one page,
    2026-09-04. Measured: the flick is median 775 and mean 985 px, the rest
    median 250 and mean 421 ms.
    """

    @staticmethod
    def _draw(fn, n=20000):
        r = random.Random(11)
        return [fn(r) for _ in range(n)]

    @pytest.mark.parametrize("fn,median,mean", [
        (pointer.scroll_flick_px, 775, 985),
        (pointer.scroll_rest_ms, 250, 421),
    ])
    def test_the_sampler_lands_on_what_was_measured(self, fn, median, mean):
        drawn = self._draw(fn)
        assert statistics.median(drawn) == pytest.approx(median, rel=0.10)
        assert statistics.fmean(drawn) == pytest.approx(mean, rel=0.10)

    @pytest.mark.parametrize("fn,low,high", [
        (pointer.scroll_flick_px, pointer.SCROLL_FLICK_MIN,
         pointer.SCROLL_FLICK_MAX),
        (pointer.scroll_rest_ms, pointer.SCROLL_REST_MIN,
         pointer.SCROLL_REST_MAX),
    ])
    def test_nothing_escapes_the_clamp(self, fn, low, high):
        assert all(low <= v <= high for v in self._draw(fn))


def _flick_sizes(steps) -> list:
    """Wheel events per flick, splitting on the rests."""
    sizes, run = [], 0
    for _d, _w, resting in steps:
        if resting:
            sizes.append(run)
            run = 0
        else:
            run += 1
    sizes.append(run)
    return sizes
