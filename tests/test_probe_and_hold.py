"""The gateway parameter axis in `scripts/probes/probe_and_hold.py`.

Covered here rather than left to the run because of what the gateway does with a
mistake. An unknown parameter *name* is answered with 200 and the setting
silently dropped, so a mistyped arm is not an error, it is the baseline running
twice under a different label - two hours of exits spent producing a difference
of zero that means nothing. A bad *value* answers 407, which reads as a
credential problem and sends the operator to check the account.

Nothing here sends anything. The module is loaded from its path because it is a
script and stays one: `python -m nmbench` finds it on disk and every probe keeps
its own argparse.
"""
import argparse
import importlib.util
import inspect
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load():
    path = ROOT / "scripts" / "probes" / "probe_and_hold.py"
    spec = importlib.util.spec_from_file_location("probe_and_hold", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe_and_hold = load()


class Parser:
    """argparse's `error` contract: report and stop, never return."""

    def error(self, message):
        raise ValueError(message)


@pytest.fixture
def parser():
    return Parser()


class TestTheParameterAxis:
    def test_none_is_the_arm_that_passes_nothing(self, parser):
        assert probe_and_hold.parse_param_sets("none", {}, parser) == [
            ("params-none", {})]

    def test_three_arms_stay_in_order(self, parser):
        arms = probe_and_hold.parse_param_sets(
            "none,filter=medium,filter=high", {}, parser)
        assert [label for label, _ in arms] == [
            "params-none", "params-filter-medium", "params-filter-high"]
        assert [params for _, params in arms] == [
            {}, {"filter": "medium"}, {"filter": "high"}]

    def test_an_arm_can_hold_a_set(self, parser):
        arms = probe_and_hold.parse_param_sets("filter=high+speed=fast", {},
                                               parser)
        assert arms == [("params-filter-high+speed-fast",
                         {"filter": "high", "speed": "fast"})]

    def test_the_label_reaches_the_cell_key(self, parser):
        cell = probe_and_hold.Cell("patchright", "google_serp", "any",
                                   ("params-filter-high", {"filter": "high"}),
                                   "L1", "home")
        assert cell.key == ("patchright/google_serp/any/params-filter-high/"
                            "warm-on/entry-home/geo-off")
        assert cell.params == {"filter": "high"}


class TestWhatIsRefused:
    def test_an_unknown_parameter_name_is_refused_here(self, parser):
        """The gateway answers 200 and drops it, so this arm would run the
        baseline again under a label saying otherwise."""
        with pytest.raises(ValueError, match="unknown parameter"):
            probe_and_hold.parse_param_sets("nonsense=x", {}, parser)

    def test_a_value_without_a_key_is_refused(self, parser):
        with pytest.raises(ValueError, match="not KEY=VALUE"):
            probe_and_hold.parse_param_sets("none,medium", {}, parser)

    @pytest.mark.parametrize("owned", ["country", "sid"])
    def test_an_arm_may_not_set_what_the_run_owns(self, parser, owned):
        """`--countries` is its own axis and the sid is one per identity. An arm
        setting either would produce cells whose key does not describe them."""
        with pytest.raises(ValueError, match="owned by"):
            probe_and_hold.parse_param_sets(f"{owned}=us", {}, parser)

    def test_a_parameter_cannot_be_fixed_and_varied_at_once(self, parser):
        with pytest.raises(ValueError, match="both set"):
            probe_and_hold.parse_param_sets("filter=high", {"filter": "low"},
                                            parser)

    def test_a_repeated_arm_is_refused(self, parser):
        """Two arms with one parameter set are one slice of the pool, and they
        would collide in the cell key anyway."""
        with pytest.raises(ValueError, match="repeats the arm"):
            probe_and_hold.parse_param_sets("filter=high,filter=high", {},
                                            parser)

    def test_an_empty_axis_is_refused(self, parser):
        with pytest.raises(ValueError, match="no arm to run"):
            probe_and_hold.parse_param_sets(" , ", {}, parser)


def cell(engine="patchright", geo="off", level="L0", target="google_serp"):
    return probe_and_hold.Cell(engine, target, "any",
                               ("params-none", {}), level, "home", geo)


class Args:
    """The two attributes `warm_sequence` reads, and nothing else."""

    def __init__(self, warm_urls=None):
        self.warm_urls = warm_urls


class TestTheWarmUpLadder:
    """Rungs are only comparable if their labels mean what they say.

    Every test here is about a label. The run itself cannot check any of this:
    a rung that silently ran a shorter sequence produces rows that look exactly
    like the deeper warm-up not helping, and that is a wrong result rather than
    an error - the same failure shape as an unknown gateway parameter answered
    with 200.
    """

    def test_the_older_spellings_still_mean_the_same_two_arms(self, parser):
        assert probe_and_hold.WARM_LEVELS["off"] == "L0"
        assert probe_and_hold.WARM_LEVELS["on"] == "L1"
        # And they still produce the keys the rows on disk carry, so a ladder
        # run groups with the two-arm runs of 2026-08-26 rather than beside
        # them.
        assert "warm-off/" in cell(level="L0").key
        assert "warm-on/" in cell(level="L1").key
        assert "warm-L2/" in cell(level="L2").key

    def test_the_cold_rung_visits_nothing(self):
        from nmbench.targets import TARGETS
        assert probe_and_hold.warm_sequence(TARGETS["google_serp"], "L0",
                                            Args()) == []

    def test_every_rung_ends_on_the_front_page(self):
        from nmbench.targets import TARGETS
        for name, target in TARGETS.items():
            for level in probe_and_hold.warm_ladder(target):
                pages = probe_and_hold.warm_sequence(target, level, Args())
                assert pages[-1] == target.home_url, f"{name} {level}"
                assert pages.count(target.home_url) == 1, f"{name} {level}"

    def test_the_rungs_are_cumulative_and_strictly_deeper(self):
        """Otherwise a difference between two rungs is not a difference in what
        was added, and `warm_depth` stops being an ordering.

        The neutral rungs are skipped and that is not a loosening. They are a
        control on composition: `N1` is L1's depth with none of L1's pages, so
        being a superset of L1 is precisely what it must not be. The invariant
        below is about the L-chain, and applying it to a control would force the
        control to contain the thing it controls for.
        """
        from nmbench.targets import TARGETS
        for name, target in TARGETS.items():
            rungs = probe_and_hold.warm_ladder(target)
            previous = None
            for level in probe_and_hold.LADDER:
                if level not in rungs or level in probe_and_hold.NEUTRAL_RUNGS:
                    continue
                pages = list(rungs[level])
                assert len(pages) == len(set(pages)), f"{name} {level}"
                if previous is not None:
                    assert set(previous) < set(pages), f"{name} {level}"
                previous = pages

    def test_a_neutral_rung_matches_the_depth_of_the_rung_it_controls(self):
        """`N1` answers L1, `N3` answers L3, or they answer nothing.

        A control that ran one page deeper would differ from the rung it
        controls in depth as well as in composition, which is the confound the
        whole rung exists to remove, and the run would look like it had answered
        the question. Checked on the delivered sequence and not on the declared
        list, because the front page is appended afterwards and the declared
        lists could match while the delivered ones did not.

        Which rung each control answers is read from `NEUTRAL_RUNGS` rather than
        written here. Until 2026-09-01 this test named `N1` and `L1` directly,
        so adding N3 would have left it passing while checking nothing about the
        rung just added - a green test that had quietly stopped covering the
        thing it was written for.
        """
        from nmbench.targets import TARGETS
        for name, target in TARGETS.items():
            rungs = probe_and_hold.warm_ladder(target)
            for neutral_level, controlled in probe_and_hold.NEUTRAL_RUNGS.items():
                if neutral_level not in rungs:
                    continue
                assert controlled in rungs, (
                    f"{name} declares {neutral_level} with no {controlled} to "
                    f"control, so the arm has nothing to be compared against")
                neutral = probe_and_hold.warm_sequence(target, neutral_level,
                                                       Args())
                first = probe_and_hold.warm_sequence(target, controlled, Args())
                assert len(neutral) == len(first), f"{name}: {neutral} {first}"

    def test_a_neutral_rung_declares_no_page_belonging_to_the_target(self):
        """Which is the whole claim of a neutral rung, so it is checked directly.

        Except the front page, which every rung ends on and which the entry
        shape would navigate to anyway - see `ensure_entry`. It is appended by
        `warm_sequence` and is not in any declared list, so it is out of scope
        here by construction rather than by exemption.

        Until 2026-09-01 this was written the other way round: the test asserted
        that a neutral rung shared no URL with any chain rung, and then excused
        L3 outright because N1's page is in it. That excuse was load-bearing and
        it was too wide. N3 is `L3` with the four Google surfaces swapped out,
        so it *must* keep sharing theverge and wikihow with L3 while sharing
        none of `translate`, `scholar`, `imghp` or `trends` - and a blanket
        exemption on L3 permits all eight. Ownership is what the rung is about,
        so ownership is what is asserted.
        """
        from urllib.parse import urlsplit

        from nmbench.targets import TARGETS
        for name, target in TARGETS.items():
            rungs = probe_and_hold.warm_ladder(target)
            neutral = probe_and_hold.NEUTRAL_RUNGS.keys() & set(rungs)
            if not neutral:
                continue
            host = (urlsplit(target.home_url).hostname or "").lower()
            # Only `www.` is stripped, and deliberately: anything cleverer is a
            # guess at the public suffix list, and a wrong guess here would make
            # a target-owned page read as third-party, which is the one error
            # this test exists to catch.
            domain = host[4:] if host.startswith("www.") else host
            assert domain, f"{name} declares no usable home_url host"
            for level in sorted(neutral):
                for url in rungs[level]:
                    page = (urlsplit(url).hostname or "").lower()
                    owned = page == domain or page.endswith("." + domain)
                    assert not owned, (
                        f"{name} {level} declares {url}, which is the target's "
                        f"own - the rung's claim is that it visits none")

    def test_a_rung_the_target_does_not_declare_is_refused(self, parser):
        args = Args()
        cells = [cell(level="L2", target="amazon_search")]
        with pytest.raises(ValueError, match="declares no"):
            probe_and_hold.check_warm(parser, args, cells)

    def test_one_flat_override_cannot_stand_for_two_rungs(self, parser):
        args = Args("https://example.com/")
        cells = [cell(level="L1"), cell(level="L2")]
        with pytest.raises(ValueError, match="one flat list"):
            probe_and_hold.check_warm(parser, args, cells)


class TestWhatAWarmVisitCosts:
    """A rung's warm-up has a price, and until 2026-09-01 no row carried it.

    The ladder exists to say what depth buys. Without the price, the answer is
    half an answer - a rung that lifts yield by paying six pages of traffic is a
    different proposition from one that lifts it for free, and both look
    identical in a table of pass rates. Every test here is about a way the
    column could be present and wrong, which is worse than absent.
    """

    def test_one_visit_is_charged_what_it_spent_and_not_the_session(self):
        counter = {"bytes": 1000, "blocked": 2, "allowed": 30}
        base = {"bytes": 400, "blocked": 1, "allowed": 10}
        assert probe_and_hold.counter_delta(counter, base) == {
            "bytes": 600, "blocked": 1, "allowed": 20}

    def test_the_first_page_of_a_session_is_charged_from_zero(self):
        """The base is an empty dict on the first visit of a fresh page, and
        that has to read as "nothing spent yet" rather than as "no counter"."""
        assert probe_and_hold.counter_delta({"bytes": 250}, {})["bytes"] == 250

    def test_an_uninstrumented_engine_reports_nothing_rather_than_zero(self):
        """zendriver, botasaurus-driver and SeleniumBase take the counter dict
        and never write to it. A zero here would be indistinguishable from a
        page that genuinely cost nothing, and it would say so in exactly the
        arms where the warm-up's traffic is most worth knowing."""
        spent = probe_and_hold.counter_delta({}, {})
        assert spent == {"bytes": None, "blocked": None, "allowed": None}
        # Not absent either: a row written before this column existed is on
        # disk, and it must stay distinguishable from one written after.
        assert set(spent) == set(probe_and_hold.COUNTER_FIELDS)

    def test_a_retried_visit_is_charged_once_across_its_two_rows(self):
        """The retry is where this goes wrong quietly, and no unit test of
        `counter_delta` alone can see it - the bug is where the base is read,
        not what the subtraction does. Read once per URL instead of once per
        attempt and the retry row is charged for the failed attempt as well, so
        the pages that needed retrying - the slow ones, the ones worth
        knowing about - are exactly the ones reported at double. Nothing errors
        and the column stays plausible; it only stops adding up to the counter.

        No network: the page is a fake whose `goto` moves the counter the way
        `blocking.install_counter` does, and one URL fails on first sight.
        """
        import random
        import types

        from nmbench.targets import TARGETS

        target = TARGETS["google_serp"]
        pages = probe_and_hold.warm_sequence(target, "N3", Args())
        stumbles = pages[2]

        counter, seen = {}, []

        class FakePage:
            failed = False

            def goto(self, url, **kw):
                seen.append(url)
                counter["bytes"] = counter.get("bytes", 0) + 1_000_000
                if url == stumbles and not FakePage.failed:
                    FakePage.failed = True
                    raise TimeoutError("navigation timed out")

        class FakeActive:
            def search(self, page, target, query, *, rng, counter):
                counter["bytes"] = counter.get("bytes", 0) + 7
                return {"verdict": "ok", "error": None}

        rows = []
        probe_and_hold.run_identity(
            FakeActive(), FakePage(), counter, cell(level="N3"), target, ["q"],
            rng=random.Random(0),
            args=types.SimpleNamespace(dwell_range=(0, 0), gap_range=(0, 0),
                                       warm_urls=None),
            on_row=rows.append)

        warm = [row for row in rows if row["phase"] == "warm"]
        assert len(warm) == len(seen), "a navigation went unrecorded"
        assert [row["warm_attempt"] for row in warm].count(2) == 1
        assert sum(row["bytes"] for row in warm) == counter["bytes"] - 7, (
            "the warm rows do not add up to what the session spent, so a "
            "visit is being charged twice or not at all")

    def test_the_warm_row_is_priced_by_the_same_instrument_as_the_probe(self):
        """`run_search` writes these three names off the same page counter. If
        the two lists drift, a warm visit and the probe after it stop being
        addable and the price of a rung cannot be computed at all."""
        from nmbench.engines import base as engines_base

        source = inspect.getsource(engines_base.run_search)
        for field in probe_and_hold.COUNTER_FIELDS:
            assert f'"{field}"' in source, (
                f"`run_search` no longer prices {field}, so warm rows and "
                f"probe rows are being counted differently")


class TestTheGeoAxis:
    """Whether the browser's timezone was set from the exit address.

    The axis is read within one engine - patchright aligned against patchright
    unaligned in one window - so the only thing the harness has to guarantee is
    that a row can never claim an alignment that did not happen. Every test
    below is a way that claim could go wrong silently.
    """

    def test_both_arms_are_separable_in_the_key(self):
        assert cell(geo="off").key.endswith("/geo-off")
        assert cell(geo="align").key.endswith("/geo-align")
        assert cell(geo="off").key != cell(geo="align").key

    def test_an_engine_that_cannot_align_is_refused_rather_than_run_flat(
            self, parser, monkeypatch):
        """The `--humanize` failure, which happened: an option honoured by some
        columns and dropped for the rest reads as an engine difference."""
        monkeypatch.setattr(probe_and_hold.engines, "report_availability",
                            lambda: {})
        args = argparse.Namespace(headless=True, preset="none", direct=True,
                                  geo="align")
        with pytest.raises(ValueError, match="cannot align its timezone"):
            probe_and_hold.preflight(parser, args,
                                     [cell("chromium", geo="align")])

    def test_the_control_is_not_the_engine_that_gained_the_feature(self):
        """Aligning the unmodified control would harden it, and a matrix whose
        baseline has been improved has no baseline."""
        assert not probe_and_hold.engines.REGISTRY["chromium"] \
            .supports_geo_align
        assert probe_and_hold.engines.REGISTRY["patchright"].supports_geo_align

    def test_aligning_a_direct_arm_is_refused(self):
        """Direct leaves from this machine, whose timezone the browser already
        reports, so the arm would be labelled as something it is not."""
        source = (ROOT / "scripts" / "probes" / "probe_and_hold.py").read_text(
            encoding="utf-8")
        assert 'args.direct and "align" in geos' in source


class TestARedrawIsOnlyEverForTheProbe:
    """The one property of the redraw that cannot be recovered afterwards.

    A transport failure during *warming* is retried in place and the shortfall
    recorded on the row. It must never draw a new exit, and the reason is not
    tidiness: warming is where a bad exit announces itself, so replacing one
    there would silently pre-screen exits for the arms that warm and leave the
    cold arm to meet its first bad exit at the probe. The deepest rung would be
    handed the cleanest pool and the ladder would be measuring the screening.

    Read off the source because the property is structural. A run cannot check
    it - a ladder with pre-screened warm arms produces rows that look exactly
    like warming working, which is the result the run is trying to establish.
    """

    SOURCE = (Path(__file__).resolve().parent.parent / "scripts" / "probes"
              / "probe_and_hold.py").read_text(encoding="utf-8")

    def test_there_is_exactly_one_place_an_identity_is_put_back(self):
        assert self.SOURCE.count("work.appendleft(") == 1, (
            "A second requeue site has appeared. If it is in the warm loop the "
            "ladder is now comparing arms that were given different pools")

    def test_the_requeue_is_below_the_probe_and_not_in_the_warm_loop(self):
        """`run_identity` holds the warm loop and returns before the requeue is
        reachable, so the check is that the requeue is outside that function."""
        warm_loop = self.SOURCE.index("for url in planned:")
        end_of_run_identity = self.SOURCE.index("return probe_verdict, probe_error")
        requeue = self.SOURCE.index("work.appendleft(")
        assert warm_loop < end_of_run_identity < requeue

    def test_the_breaker_is_not_fed_an_attempt_that_never_arrived(self):
        """The bug this whole change is about. `CircuitBreaker.record` counts
        anything that is not `ok`, so a broken tunnel was a tick toward "the
        target is walling this cell" - which is what stopped three of the four
        rungs of `probehold_20260827T201123Z`."""
        assert "if is_transport_failure(probe_error):" in self.SOURCE
        after = self.SOURCE.split("if is_transport_failure(probe_error):", 1)[1]
        branch, _, rest = after.partition("\n                    else:")
        assert "breaker.record" not in branch, (
            "the transport branch feeds the breaker again")
        assert "breaker.record(verdict)" in rest
