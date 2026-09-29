"""The consent wall on the URL-entry path.

Written 2026-09-29 against a defect the corpus found rather than a review:
`google_maps` from it, gb and de returned `consent` on 25 of 25 attempts for
decodo, nodemaven and oxylabs alike - 221 rows in
`benchmark_20260929T031705Z` - while the six non-EU codes in the same sweep
returned none. Three of nine countries were unmeasured, and identically so for
every gateway, which is the signature of our own client failing rather than of
a pool being refused.

Two separate faults produced it and each gets its own test below, because
fixing either alone leaves the run still broken:

- `dismiss_consent` was reachable only from `run_search`, the typed-query
  path, and every matrix cell here enters by URL. The mechanism existed and no
  run could reach it.
- the selector loop took the FIRST match of each selector, which is right for
  the ids `google_serp` uses and wrong for a page that ships its reject form
  four times.

Nothing launches a browser. The page is a fake with the two methods the code
calls, which is enough because what is being pinned is the control flow and
not Chromium.
"""
import pytest

from nmbench.engines.base import clear_entry_wall, dismiss_consent
from nmbench.targets import TARGETS


class Handle:
    def __init__(self, visible, name="btn"):
        self._visible = visible
        self.name = name
        self.clicked = False

    def is_visible(self):
        return self._visible

    def click(self, timeout=None):
        self.clicked = True


class Page:
    """The three methods `dismiss_consent` touches, and a match table."""

    def __init__(self, matches, url="https://consent.google.com/m?continue=x"):
        self.matches = matches
        self.url = url

    def query_selector_all(self, selector):
        return self.matches.get(selector, [])

    def wait_for_load_state(self, state, timeout=None):
        return None

    def wait_for_timeout(self, ms):
        return None


class Target:
    def __init__(self, selectors):
        self.consent_dismiss = selectors


class TestEveryMatchIsTried:
    def test_a_hidden_first_match_does_not_abandon_the_selector(self):
        """The fault that made the fix necessary rather than merely tidy.

        Four `consent.google.com/save` forms ship on the interstitial - two
        reject, two accept - so one honest selector matches twice. With only
        the first match tried, whether the wall clears depends on which copy
        the layout puts first, which is a coin this has no reason to toss.
        """
        hidden, shown = Handle(False, "first"), Handle(True, "second")
        page = Page({"form button": [hidden, shown]})

        assert dismiss_consent(page, Target(("form button",))) is True
        assert shown.clicked is True
        assert hidden.clicked is False

    def test_all_hidden_is_still_no_wall(self):
        """The control on the test above.

        Without it, "try every match" could be satisfied by clicking a hidden
        control, which is the behaviour the visibility check exists to stop -
        these panels stay in the document after they are cleared.
        """
        handles = [Handle(False), Handle(False)]
        page = Page({"form button": handles})

        assert dismiss_consent(page, Target(("form button",))) is False
        assert not any(h.clicked for h in handles)

    def test_selector_order_still_belongs_to_the_target(self):
        first = Handle(True, "first-selector")
        second = Handle(True, "second-selector")
        page = Page({"a": [first], "b": [second]})

        assert dismiss_consent(page, Target(("a", "b"))) is True
        assert first.clicked is True
        assert second.clicked is False


class TestTheUrlEntryPathClearsIt:
    def test_the_row_records_that_a_wall_was_cleared(self):
        shown = Handle(True)
        row = {}

        clear_entry_wall(Page({"form button": [shown]}),
                         Target(("form button",)), row)

        assert shown.clicked is True
        assert row["consent_dismissed"] is True

    def test_a_target_with_no_wall_writes_nothing(self):
        """`consent_dismissed` absent and False are different evidence.

        Absent is "this target has no wall to meet"; False is "it has one and
        this attempt did not meet it". Collapsing them would make the column
        unable to answer the question it was added for - whether clearing
        silently stopped working.
        """
        row = {}
        clear_entry_wall(Page({}), Target(None), row)
        assert "consent_dismissed" not in row

    def test_a_failed_click_is_not_an_error_row(self):
        """A wall that will not clear leaves the pre-fix outcome, not a crash.

        The attempt then reads `consent` off the page it is still sitting on,
        which is true. Recording `error` instead would price the attempt as
        the harness's fault using, as its only evidence, the thing that just
        failed.
        """
        class Angry(Handle):
            def click(self, timeout=None):
                raise RuntimeError("detached")

        row = {}
        clear_entry_wall(Page({"form button": [Angry(True)]}),
                         Target(("form button",)), row)

        assert "consent_dismissed" not in row


class TestTheTargetCarriesTheSelector:
    def test_google_maps_declares_one(self):
        assert TARGETS["google_maps"].consent_dismiss

    def test_it_selects_the_reject_form_and_not_the_accept_one(self):
        """Pinned because the difference is one hidden input and it is a
        choice, not a detail: `set_eom=true` alone rejects, `set_sc` plus
        `set_aps` plus `set_eom=false` accepts. An edit that reached for the
        other form would be invisible in a diff read quickly.
        """
        selector = " ".join(TARGETS["google_maps"].consent_dismiss)
        assert 'input[name="set_eom"][value="true"]' in selector
        assert "set_aps" not in selector

    def test_it_does_not_rest_on_an_obfuscated_class(self):
        """Google rotates these without notice and this repository has already
        dated one such rotation. A rotation here would turn every EU row back
        into `consent` silently, which is the failure this file exists about.
        """
        for selector in TARGETS["google_maps"].consent_dismiss:
            assert "AeBiU" not in selector and "UywwFc" not in selector

    @pytest.mark.parametrize("name", sorted(TARGETS))
    def test_no_target_promises_a_wall_it_cannot_name(self, name):
        selectors = getattr(TARGETS[name], "consent_dismiss", None)
        assert selectors is None or (isinstance(selectors, tuple)
                                     and all(selectors))


class TestGoogleSerpMeetsTheRedirectToo:
    def test_the_redirect_form_is_among_its_selectors(self):
        """`google_serp` has never been run from an EU exit. If `/search` is
        redirected the way Maps is, the two overlay ids match nothing on that
        page and every EU row would come back `consent` - the defect this file
        exists about, on the next target over."""
        selectors = TARGETS["google_serp"].consent_dismiss
        assert any('input[name="set_eom"][value="true"]' in s for s in selectors)

    def test_reject_still_comes_before_accept(self):
        selectors = TARGETS["google_serp"].consent_dismiss
        assert selectors.index("button#L2AGLb") == len(selectors) - 1
        assert selectors[0] == "button#W0wltc"
