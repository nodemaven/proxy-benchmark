"""What a warm-up page is asked to do once it has finished loading.

Until 2026-09-04 a warm-up visit was `page.goto` and then a sleep of 3 to 8
seconds, on every rung of the ladder. That is a client which navigates and then
dies, six times in a row, and it is not the session the ladder claims to model.

The measurement that makes this worth building is `probehold_20260904T000605Z`,
read inside the window all four of its cells share - the run was interrupted and
the L0/trueman cell stops at 02:18 while the others run to 06:27 - and on probe
rows only, position 0, because positions 1 to 3 reuse the identity that position
0 established and are not independent trials:

    warm-up, hand off        3/12 -> 8/11    25% -> 73%   Fisher p=0.039
    warm-up, hand trueman    0/11 -> 8/11     0% -> 73%   Fisher p=0.001
    hand, no warm-up         3/12 -> 0/11    25% ->  0%   p=0.217
    hand, full warm-up       8/11 -> 8/11    73% -> 73%   p=1.000

Two things follow and the second is the warning. Warm-up is the only axis this
repository has measured with an effect on the probe. And it produces that effect
while doing nothing whatsoever on the page, so the arm this module has to beat
is one that already works, and "interaction helps" is a claim that needs the
same evidence the ladder itself needed.

**This crosses the ladder, it does not redefine a rung.** Every L3 row on disk
was taken with the warm-up inert. Folding interaction into L3 would make depth
and interaction one treatment, and every comparison already computed would
silently change what it is a comparison of.

Nothing here invents a behavioural constant, and that is a constraint rather
than an accident. The cursor walk is `nmbench.pointer.trueman`, the keystroke
delay is `engines.base.typing_delay_ms` and the wheel is
`nmbench.pointer.scroll_plan`, all three unchanged by this module. A fitted
reading pause between actions was refused on 2026-09-04 when the idle
distribution turned out to be unfittable from the two traces on hand: the
lognormal reproduces the mouse (mean 195.8 measured against 190.8 fitted) and
fails on the touchpad (640.0 against 314.9), and the share of rests followed by
a real movement is 6/54 on one device against 18/41 on the other. Two humans
four-fold apart is not a population to fit to.

**The scroll was checked against that same refusal and came out the other side
of it, which is why it exists and the dwell curve does not.** Measured
2026-09-04 by `lab/probes/scroll_shape.py` on the same two traces: the flick and
the pause after it are 1.1x and 1.3x apart between the two people, against 27.8x
on the wheel notch and 3.6x on the cadence, and both read the same article in
exactly 11 flicks. So the wheel is the device and the page is the person, and
pooling the two traces is pooling two readings of one quantity. The refusal was
about a four-fold split and not about a policy of never fitting to two people.

The three are not equally strong and should not be quoted as though they were.
The walk and the keystroke delay are **scored** - `lab/probes/trace_compare.py`
runs them against a human trace and the arm reads 0 tells. The flick and rest
are **measured and unscored**: the detector reads wheel magnitude and wheel
interval per event and excludes both as `device`, so it has no metric that would
notice whether a driven scroll has this structure at all.

`settle` is the one number here and it is deliberately not a claim about people.
It is how long to wait for a page to answer, the same kind of quantity as a
timeout, and it is drawn from a range rather than fixed because a fleet that
waits an identical number of milliseconds is a tell whatever the number is.
"""
import time

from . import pointer
from .engines.base import clear_box, typing_delay_ms

# How long to wait for a declared element to appear. Shorter than the 30 s a
# navigation gets, because this runs on a page that has already loaded, so eight
# seconds is a long time for a box to stay invisible and waiting longer turns a
# wrong declaration into a slow run rather than into a visible one. It does not
# follow that a timeout here *proves* the selector wrong - see `run` below.
BOX_TIMEOUT_MS = 8000

# What a row carries when the arm is off, or when the page has no declared
# actions. Zeros and not None: no action was planned, which is a count, and the
# distinction from "an action was planned and did nothing" is the whole point of
# the column.
NOTHING = {"interact_planned": 0, "interact_done": 0,
           "interact_detail": None, "interact_ms": 0}


class SelectorMissed(RuntimeError):
    """A declared element never appeared. The declaration is wrong, or the page
    changed under it - either way the arm did not run and must not read as
    though it did."""


def _act_type(page, action, *, rng, hand, timeout_ms):
    """Click a box and type into it, by the same route the probe types.

    `hand.click` rather than `handle.click` when a hand exists, so a warm-up
    click is the same client as a probe click. Without that, `--humanize
    trueman --warm-interact on` would walk the cursor to the search box and
    teleport it to everything before, which is a worse shape than either arm.
    """
    _, selector, phrases = action
    try:
        handle = page.wait_for_selector(selector, timeout=timeout_ms,
                                        state="visible")
    except Exception as exc:
        raise SelectorMissed(selector) from exc
    if handle is None:
        raise SelectorMissed(selector)
    if hand is None:
        handle.click()
    else:
        hand.click(page, handle)
    # The same select-all-and-type-over as the probe uses. A second phrase typed
    # into a translator has to replace the first, and `fill("")` would produce
    # no key events at all - see `clear_box` for what that cost once.
    clear_box(handle)
    handle.type(rng.choice(phrases), delay=typing_delay_ms(rng))


def _act_settle(page, action, *, rng, hand, timeout_ms):
    """Wait for the page to react to what was just typed."""
    _, low_ms, high_ms = action
    time.sleep(rng.uniform(low_ms, high_ms) / 1000.0)


def _act_scroll(page, action, *, rng, hand, timeout_ms):
    """Read down the page, by the same wheel model the pointer traces scored.

    **The only action here with no selector to get wrong.** `type` is declared
    against a live surface this host may not touch, so its selector is a
    guess until a run reports back; a scroll names nothing on the page and
    cannot miss. On a host behind a VPN gateway that makes it the cheaper half
    of the axis to trust, and it is the reason it can be selected on its own.

    The distance is drawn from a range rather than fixed, for the reason
    `settle` gives: a fleet that scrolls an identical number of pixels is a tell
    whatever the number is. The range is the caller's, because how far down a
    page there is to read is a property of the page and not of the reader.

    With no hand the wheel is `pointer.wheel_plan`'s baseline - 120 px every
    30 ms - reached through `scroll_plan` rather than written out here, so the
    constant the `--humanize off` arm emits lives in one place and cannot drift
    from the one the detector scores that arm against.
    """
    _, low_px, high_px = action
    distance = rng.uniform(low_px, high_px)
    if hand is not None:
        hand.scroll(page, distance)
        return
    for delta, wait, _resting in pointer.scroll_plan("off", distance, rng):
        page.mouse.wheel(0, delta)
        time.sleep(wait / 1000.0)


KINDS = {"type": _act_type, "settle": _act_settle, "scroll": _act_scroll}

# Which kinds `--warm-interact` can select, and what each name selects. `on` is
# everything, so it stays what it was before the scroll existed.
#
# The axis is decomposable from the first run rather than after it, and that is
# the whole reason this exists. Declaring a scroll on two pages and a type on
# two others would otherwise make `on` a single treatment built out of three
# behaviours on four pages, and a difference in it would say nothing about which
# part moved. That is exactly what the warm ladder did - L1 through L3 varied
# depth and composition together - and it cost two extra rungs, N1 and N3, to
# untangle after the rows were already on disk.
#
# `settle` is in every set and is not selectable on its own: it is a wait for a
# page to answer, so it belongs to whatever action it follows. A set that
# reduced to nothing but settles would be the inert arm under another name, and
# `actions_for` drops the page rather than running it.
SETS = {"type": ("type", "settle"),
        "scroll": ("scroll", "settle"),
        "on": tuple(KINDS)}


def _select(actions, kinds) -> tuple:
    """The declared actions an arm of `kinds` would run, or nothing.

    Nothing rather than the settles alone when no real action survives the
    filter, so `--warm-interact scroll` on a page that only declares typing
    reports the page as untouched instead of visiting it to wait twice. A page
    whose actions are all settles is the inert arm with a label on it, which is
    the failure this module exists to make visible.
    """
    kept = tuple(a for a in actions if a and a[0] in kinds)
    if not any(a[0] != "settle" for a in kept):
        return ()
    return kept


def actions_for(target, url, kinds=None) -> tuple:
    """What this target declares for one of its own warm-up pages.

    Declared on the target and not here, for the same reason `warm_ladder` is:
    a probe that knew a domain would be a probe that could warm one target
    better than another, and a CSS selector is a stronger form of that knowledge
    than a URL is.

    `kinds` is the `--warm-interact` set. `None` means all of them, which is
    what `on` selects and what preflight asks when it wants to know whether the
    target declares anything at all.
    """
    declared = tuple(dict(getattr(target, "warm_actions", ())).get(url, ()))
    if kinds is None:
        return declared
    return _select(declared, kinds)


def pages_with_actions(target, kinds=None) -> set:
    return {url for url, actions in getattr(target, "warm_actions", ())
            if (actions if kinds is None else _select(actions, kinds))}


def _is_range(action, *, above_zero) -> bool:
    """Is this a (kind, low, high) whose bounds a draw could actually use?

    The type check is not decoration. `problems` promises preflight a list of
    faults **in words**, and until 2026-09-04 a bound that was not a number
    raised `TypeError` out of the comparison instead - preflight crashing on
    exactly the input it exists to describe. The likeliest way to write one is
    the cheapest kind of typo: copy a `type` row, leave its selector in place,
    and `("scroll", "body", 3)` compares a string against an int.

    `bool` is excluded because it is an `int` in Python, so `("scroll", True,
    3)` would otherwise read as a scroll of one pixel to somewhere between one
    and three.
    """
    if len(action) != 3:
        return False
    low, high = action[1], action[2]
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
               for v in (low, high)):
        return False
    return low <= high and (low > 0 or not above_zero)


def problems(target) -> list:
    """Everything wrong with a target's declared actions, in words.

    Called from preflight rather than checked at runtime, because every fault
    listed here produces the same failure: an arm labelled `on` that behaves
    exactly like the arm labelled `off`, for a whole night, with nothing in the
    output saying so. That is the gateway's unknown-parameter failure again -
    the baseline running twice under two names - and it is the reason this
    repository checks arms before it spends exits on them.
    """
    found = []
    for url, actions in getattr(target, "warm_actions", ()):
        if not actions:
            found.append(f"{url} declares an empty action list, so the arm "
                         f"would be the inert one under another name")
        for action in actions:
            kind = action[0] if action else None
            if kind not in KINDS:
                found.append(f"{url} declares action {kind!r}, known: "
                             f"{sorted(KINDS)}")
            elif kind == "type" and (len(action) != 3 or not action[2]):
                found.append(f"{url} declares a type action that is not "
                             f"(kind, selector, phrases) with phrases in it: "
                             f"{action!r}")
            elif kind == "settle" and not _is_range(action, above_zero=False):
                found.append(f"{url} declares a settle that is not "
                             f"(kind, low_ms, high_ms) with low under high: "
                             f"{action!r}")
            elif kind == "scroll" and not _is_range(action, above_zero=True):
                found.append(f"{url} declares a scroll that is not "
                             f"(kind, low_px, high_px) with low under high and "
                             f"above zero: {action!r}")
    return found


def run(page, target, url, *, rng, hand=None, kinds=None,
        timeout_ms=BOX_TIMEOUT_MS) -> dict:
    """Drive one warm-up page. Never raises; the outcome is in the return.

    A failed interaction does not fail the visit. The page loaded and set
    whatever it sets, so the visit still counts toward `warm_delivered`; what
    did not happen is the interaction, and the columns below are what say so.
    Stopping at the first failure rather than pressing on, because every action
    after a missed box is aimed at a page in an unknown state, and a `settle`
    that "succeeded" after a `type` that missed would inflate the count with
    something that did nothing.

    `interact_detail` is the diagnostic that matters when the arm reads as
    inert, and what it separates is *where* the action stopped rather than why.
    `type:miss` means the box was never found, so the declared selector is the
    first suspect. Anything else - `type:TimeoutError`, `type:Error` - means the
    box *was* found and the step after it failed, so the selector is right and
    the page is the suspect. The two want opposite fixes.

    This docstring said until 2026-09-04 that `type:miss` says the selector is
    wrong and `type:TimeoutError` says the page was slow. That is not what the
    code does and the distinction is not that clean: `wait_for_selector` timing
    out is recorded as `miss`, and a box that took over eight seconds to become
    visible on an already-loaded page produces exactly the same string as a
    selector that matches nothing. The wording was written from what the two
    outcomes are usually *caused by* rather than from what the code can
    actually tell apart, which is the same move that has cost this repository a
    day four times over. `miss` is one bit - the box was not there - and a
    second run against a selector list with a generic fallback is what
    distinguishes the two, not this string.
    """
    actions = actions_for(target, url, kinds)
    if not actions:
        return dict(NOTHING)
    started = time.perf_counter()
    detail, done = [], 0
    for action in actions:
        kind = action[0]
        run_action = KINDS.get(kind)
        if run_action is None:
            # Not caught below: an undeclared kind is a mistake in the target,
            # not a page behaving badly, and `problems` should have refused the
            # run before an exit was spent. Loud is correct here.
            raise ValueError(f"{url} declares unknown action {kind!r}, known: "
                             f"{sorted(KINDS)}")
        try:
            run_action(page, action, rng=rng, hand=hand, timeout_ms=timeout_ms)
        except SelectorMissed:
            detail.append(f"{kind}:miss")
            break
        except Exception as exc:
            detail.append(f"{kind}:{type(exc).__name__}")
            break
        detail.append(f"{kind}:ok")
        done += 1
    return {
        "interact_planned": len(actions),
        "interact_done": done,
        "interact_detail": ",".join(detail) or None,
        "interact_ms": round((time.perf_counter() - started) * 1000),
    }
