"""What a warm-up page does once it has loaded, and what it records afterwards.

Nothing here touches a browser. The point of the module under test is that a
failed interaction must not fail the visit and must not read as a successful
one, and that distinction lives entirely in four columns - so the tests are
about the columns.

The failure being guarded against is the one this repository has now recorded
five times: an arm labelled `on` that behaves exactly like the arm labelled
`off`, with nothing in the output saying so. Here it has a specific shape. The
selectors are declared against a live Google surface and cannot be verified from
this host - it is behind a VPN gateway and may not touch one - so the run's own
output is the only check there is, and a blank `interact_done` beside a
populated `interact_planned` is what makes the arm's failure visible at all.
"""
import random

import pytest

from nmbench import warm

PAGE = "https://example.com/a"


class Handle:
    """A text box. Records what was done to it, in order."""

    def __init__(self, value="", typeable=True):
        self.value = value
        self.typeable = typeable
        self.log = []

    def click(self):
        self.log.append("click")

    def input_value(self):
        if not self.typeable:
            raise RuntimeError("not an input")
        return self.value

    def press(self, keys):
        self.log.append(f"press:{keys}")

    def type(self, text, delay=None):
        self.log.append(f"type:{text}")
        self.value = text


class Mouse:
    """`wheel` and nothing else, which is all a scroll asks of a page."""

    def __init__(self):
        self.wheeled = []

    def wheel(self, dx, dy):
        self.wheeled.append(dy)


class Page:
    """`wait_for_selector` and a mouse, which is all `warm` asks of a page.

    `answers` maps a selector to a handle, to an exception to raise, or to None -
    Playwright returns None on a timeout in some versions and raises in others,
    and both have to end in `SelectorMissed` or the arm reports a click that
    never happened.
    """

    def __init__(self, answers):
        self.answers = answers
        self.asked = []
        self.mouse = Mouse()

    def wait_for_selector(self, selector, timeout=None, state=None):
        self.asked.append(selector)
        answer = self.answers.get(selector, KeyError(selector))
        if isinstance(answer, BaseException):
            raise answer
        return answer


class Hand:
    def __init__(self):
        self.clicked = []
        self.scrolled = []

    def click(self, page, handle):
        self.clicked.append(handle)
        handle.log.append("hand-click")

    def scroll(self, page, distance_px):
        self.scrolled.append(distance_px)


def target(actions, url=PAGE):
    return type("T", (), {"warm_actions": ((url, actions),)})


def rng():
    return random.Random(7)


class TestAVisitWithNothingDeclared:
    def test_a_page_with_no_actions_reports_zeros_and_not_none(self):
        """Zeros because no action was planned, which is a count. `None` would
        be indistinguishable from an action that was planned and did nothing,
        and that distinction is the whole point of the column."""
        got = warm.run(Page({}), target(()), "https://example.com/other",
                       rng=rng())
        assert got == {"interact_planned": 0, "interact_done": 0,
                       "interact_detail": None, "interact_ms": 0}

    def test_a_target_that_declares_nothing_at_all_is_fine(self):
        """Six of google_serp's seven L3 pages are this case, so it is the
        common path rather than an edge."""
        assert warm.run(Page({}), type("T", (), {}), PAGE,
                        rng=rng())["interact_planned"] == 0


class TestAVisitThatWorks:
    def test_every_action_lands_and_says_so(self):
        box = Handle()
        page = Page({"textarea": box})
        got = warm.run(page, target((("type", "textarea", ("hello",)),
                                     ("settle", 1, 2))), PAGE, rng=rng())
        assert got["interact_planned"] == 2
        assert got["interact_done"] == 2
        assert got["interact_detail"] == "type:ok,settle:ok"
        assert box.log == ["click", "type:hello"]

    def test_a_second_phrase_replaces_the_first_rather_than_appending(self):
        """`clear_box`'s reason, in the place it now runs a second time. On
        2026-08-13 a held series without it asked `q=kimchi+mistakesmortgage+
        rates` while every row recorded the query it meant to ask. A translator
        keeps the source text on the page exactly as a SERP keeps the query."""
        box = Handle()
        page = Page({"textarea": box})
        warm.run(page, target((("type", "textarea", ("first",)),
                               ("type", "textarea", ("second",)))), PAGE,
                 rng=rng())
        assert box.log == ["click", "type:first",
                           "click", "press:Control+a", "type:second"]
        assert box.value == "second"

    def test_the_hand_clicks_when_there_is_one(self):
        """`hand.click` and not `handle.click`, so a warm-up click is the same
        client as a probe click. Without it, `--humanize trueman
        --warm-interact on` would walk the cursor to the box and teleport it to
        everything before, which is a worse shape than either arm alone."""
        box, hand = Handle(), Hand()
        warm.run(Page({"textarea": box}),
                 target((("type", "textarea", ("hello",)),)), PAGE,
                 rng=rng(), hand=hand)
        assert hand.clicked == [box]
        assert box.log == ["hand-click", "type:hello"]

    def test_it_is_priced_in_the_same_units_as_the_navigation_beside_it(self):
        got = warm.run(Page({"textarea": Handle()}),
                       target((("settle", 40, 60),)), PAGE, rng=rng())
        assert got["interact_ms"] >= 40


class TestAVisitThatFails:
    def test_a_missed_selector_is_recorded_and_not_raised(self):
        """The page loaded and set whatever it sets, so the visit still counts
        toward `warm_delivered`. What did not happen is the interaction."""
        got = warm.run(Page({}), target((("type", "gone", ("hello",)),)), PAGE,
                       rng=rng())
        assert got["interact_planned"] == 1
        assert got["interact_done"] == 0
        assert got["interact_detail"] == "type:miss"

    def test_a_selector_that_returns_none_is_a_miss_too(self):
        got = warm.run(Page({"gone": None}),
                       target((("type", "gone", ("hello",)),)), PAGE, rng=rng())
        assert got["interact_detail"] == "type:miss"

    def test_the_detail_says_where_the_action_stopped(self):
        """The diagnostic that matters when the arm reads as inert, and it is
        about *where* rather than why. `miss` means the box was never found, so
        the selector is the first suspect; any other name means the box was
        found and the step after it failed, so the selector is right and the
        page is the suspect.

        The first arm below is why it is worded that way rather than as "wrong
        selector against slow page": a `wait_for_selector` timeout *is* a slow
        page and is still recorded as `miss`, because from here a box that took
        nine seconds and a selector matching nothing are one observation."""
        page = Page({"textarea": TimeoutError("slow")})
        got = warm.run(page, target((("type", "textarea", ("hello",)),)), PAGE,
                       rng=rng())
        assert got["interact_detail"] == "type:miss"
        box = Handle()
        box.type = lambda *a, **k: (_ for _ in ()).throw(TimeoutError("slow"))
        got = warm.run(Page({"textarea": box}),
                       target((("type", "textarea", ("hello",)),)), PAGE,
                       rng=rng())
        assert got["interact_detail"] == "type:TimeoutError"

    def test_it_stops_at_the_first_failure(self):
        """Every action after a missed box is aimed at a page in an unknown
        state, and a `settle` that 'succeeded' after a `type` that missed would
        inflate `interact_done` with something that did nothing."""
        got = warm.run(Page({}), target((("type", "gone", ("hello",)),
                                         ("settle", 1, 2),
                                         ("type", "gone", ("bye",)))), PAGE,
                       rng=rng())
        assert got["interact_done"] == 0
        assert got["interact_detail"] == "type:miss"
        assert got["interact_planned"] == 3

    def test_a_box_that_cannot_be_read_is_still_typed_into(self):
        """`clear_box` returns False on anything that is not an input, and the
        typing is what fails loudly afterwards if that was wrong."""
        box = Handle(typeable=False)
        got = warm.run(Page({"div": box}),
                       target((("type", "div", ("hello",)),)), PAGE, rng=rng())
        assert got["interact_done"] == 1
        assert box.log == ["click", "type:hello"]

    def test_an_undeclared_kind_raises_rather_than_being_recorded(self):
        """Loud on purpose: this is a mistake in the target, not a page
        behaving badly, and `problems` should have refused the run before an
        exit was spent on it.

        The kind here was `scroll` until 2026-09-04, when scrolling became a
        real one. A test that asserts an unknown name is refused has to hold a
        name that is actually unknown, or it silently stops testing anything the
        day the name is implemented - and this one did worse than that: it went
        on passing for one run and then failed, because `("scroll", "body", 3)`
        now reaches the scroll branch instead."""
        with pytest.raises(ValueError, match="unknown action"):
            warm.run(Page({}), target((("hover", "body", 3),)), PAGE,
                     rng=rng())


class TestTheScroll:
    """The half of the axis that names nothing on the page.

    `type` is declared against a live Google surface this host may not touch, so
    every selector in it is a guess until a run reports back. A scroll has no
    selector and cannot miss, which is why it is separately selectable and why
    it is the half that can be trusted from here.
    """

    def test_with_no_hand_it_wheels_the_page_itself(self):
        page = Page({})
        got = warm.run(page, target((("scroll", 100, 200),)), PAGE, rng=rng())
        assert got["interact_detail"] == "scroll:ok"
        assert page.mouse.wheeled, "the arm reported ok having wheeled nothing"

    def test_the_unhumanized_arm_emits_the_baseline_constant_and_not_a_new_one(self):
        """Through `scroll_plan` rather than written out here, so the constant
        the `--humanize off` arm emits lives in one place and cannot drift from
        the one the detector scores that arm against."""
        page = Page({})
        warm.run(page, target((("scroll", 100, 200),)), PAGE, rng=rng())
        assert set(page.mouse.wheeled) == {120}

    def test_it_carries_at_least_as_far_as_it_was_asked(self):
        page = Page({})
        warm.run(page, target((("scroll", 300, 301),)), PAGE, rng=rng())
        assert sum(page.mouse.wheeled) >= 300

    def test_the_hand_scrolls_when_there_is_one_and_the_page_is_not_touched(self):
        """Same rule as the click: a warm-up scroll is the same client as a
        probe scroll, or `--humanize trueman` would walk the cursor and then
        wheel the page by a constant."""
        page, hand = Page({}), Hand()
        warm.run(page, target((("scroll", 800, 2200),)), PAGE, rng=rng(),
                 hand=hand)
        assert len(hand.scrolled) == 1
        assert 800 <= hand.scrolled[0] <= 2200
        assert page.mouse.wheeled == []

    def test_the_distance_is_drawn_rather_than_fixed(self):
        """`settle`'s reason, in pixels: a fleet that scrolls an identical
        number of pixels is a tell whatever the number is."""
        seen = set()
        for seed in range(12):
            hand = Hand()
            warm.run(Page({}), target((("scroll", 800, 2200),)), PAGE,
                     rng=random.Random(seed), hand=hand)
            seen.add(round(hand.scrolled[0], 3))
        assert len(seen) > 1


class TestWhichKindsAnArmRuns:
    """`--warm-interact` selects a kind, and the axis is decomposable from the
    first run rather than after it.

    Declaring a scroll on two pages and a type on two others would otherwise
    make `on` one treatment built out of three behaviours on four pages, and a
    difference in it would say nothing about which part moved. That is exactly
    what the warm ladder did - L1 to L3 varied depth and composition together -
    and it cost two extra rungs to untangle after the rows were on disk.
    """

    MIXED = (("type", "textarea", ("hello",)), ("settle", 1, 2),
             ("scroll", 100, 200), ("settle", 1, 2))

    def test_on_selects_every_kind_there_is(self):
        """So a kind added later joins `on` by existing, rather than by somebody
        remembering to list it here."""
        assert set(warm.SETS["on"]) == set(warm.KINDS)

    def test_off_is_absent_rather_than_empty(self):
        """`SETS.get("off")` is `None`, and `None` means *every* kind to
        `actions_for` downstream. Nothing may gate on truthiness: an empty tuple
        and `None` are opposite instructions that both read as false."""
        assert warm.SETS.get("off") is None

    def test_a_kind_takes_its_settles_with_it(self):
        """A settle is a wait for a page to answer, so it belongs to whatever
        action it follows rather than being an arm of its own."""
        kept = warm.actions_for(target(self.MIXED), PAGE,
                                kinds=warm.SETS["scroll"])
        assert [a[0] for a in kept] == ["settle", "scroll", "settle"]

    def test_a_page_left_holding_nothing_but_settles_is_dropped(self):
        """The inert arm with a label on it, which is the failure this module
        exists to make visible. Reporting the page as untouched is right;
        visiting it to wait twice and calling that `--warm-interact scroll` is
        the thing that has cost this repository five nights."""
        typing_only = (("type", "textarea", ("hello",)), ("settle", 1, 2))
        assert warm.actions_for(target(typing_only), PAGE,
                                kinds=warm.SETS["scroll"]) == ()

    def test_a_dropped_page_is_not_visited_at_all(self):
        page = Page({"textarea": Handle()})
        got = warm.run(page, target((("type", "textarea", ("hello",)),)), PAGE,
                       rng=rng(), kinds=warm.SETS["scroll"])
        assert got == dict(warm.NOTHING)
        assert page.asked == []

    def test_the_page_list_shrinks_with_the_kind(self):
        t = type("T", (), {"warm_actions": (
            (PAGE, (("type", "textarea", ("a",)),)),
            ("https://example.com/b", (("scroll", 100, 200),)))})
        assert warm.pages_with_actions(t) == {PAGE, "https://example.com/b"}
        assert warm.pages_with_actions(t, warm.SETS["scroll"]) == {
            "https://example.com/b"}
        assert warm.pages_with_actions(t, warm.SETS["type"]) == {PAGE}

    def test_no_kinds_means_all_of_them_and_not_none_of_them(self):
        assert len(warm.actions_for(target(self.MIXED), PAGE)) == 4
        assert len(warm.actions_for(target(self.MIXED), PAGE, kinds=None)) == 4

    def test_the_selected_arm_runs_only_what_it_selected(self):
        page = Page({"textarea": Handle()})
        got = warm.run(page, target(self.MIXED), PAGE, rng=rng(),
                       kinds=warm.SETS["scroll"])
        assert got["interact_planned"] == 3
        assert got["interact_detail"] == "settle:ok,scroll:ok,settle:ok"
        assert page.asked == [], "the type arm ran under the scroll label"


class TestWhatPreflightReads:
    def test_actions_are_looked_up_by_exact_url(self):
        t = target((("settle", 1, 2),))
        assert len(warm.actions_for(t, PAGE)) == 1
        assert warm.actions_for(t, PAGE + "?x=1") == ()

    def test_pages_with_actions_omits_a_page_declaring_none(self):
        t = type("T", (), {"warm_actions": ((PAGE, ()),
                                            ("https://example.com/b",
                                             (("settle", 1, 2),)))})
        assert warm.pages_with_actions(t) == {"https://example.com/b"}

    @pytest.mark.parametrize("actions,fault", [
        ((), "empty action list"),
        ((("hover", "body", 3),), "known"),
        ((("type", "textarea"),), "phrases in it"),
        ((("type", "textarea", ()),), "phrases in it"),
        ((("settle", 2600, 1200),), "low under high"),
        ((("settle", 1200),), "low under high"),
        ((("scroll", 3500, 1500),), "low under high"),
        ((("scroll", 0, 1500),), "above zero"),
        ((("scroll", 1500),), "low under high"),
    ])
    def test_every_fault_is_reported_in_words(self, actions, fault):
        found = warm.problems(target(actions))
        assert found and fault in found[0]

    @pytest.mark.parametrize("action", [("scroll", "body", 3),
                                        ("settle", "body", 3),
                                        ("scroll", 200, None)])
    def test_a_bound_that_is_not_a_number_is_words_and_not_a_crash(self, action):
        """The whole contract of this function is faults **in words**, and until
        2026-09-04 a non-numeric bound raised `TypeError` out of the comparison
        instead - preflight crashing on exactly the input it exists to describe.

        Found by a test rather than by a run, and only because `("scroll",
        "body", 3)` was already sitting in the table above as an unknown kind: it
        became a known kind with a string where a pixel count goes, which is the
        cheapest typo there is - copy a `type` row and leave its selector in
        place."""
        found = warm.problems(target((action,)))
        assert found and "low under high" in found[0]

    def test_a_bound_that_is_a_bool_is_refused_rather_than_read_as_one_pixel(self):
        """`True` is an `int` in Python, so an unguarded comparison reads this as
        a scroll of one pixel to somewhere between one and three."""
        assert warm.problems(target((("scroll", True, 3),)))

    def test_a_sound_declaration_reports_nothing(self):
        assert warm.problems(target((("type", "textarea", ("a", "b")),
                                     ("settle", 1200, 2600),
                                     ("scroll", 800, 2200)))) == []

    def test_a_target_with_no_actions_reports_nothing(self):
        assert warm.problems(type("T", (), {})) == []
