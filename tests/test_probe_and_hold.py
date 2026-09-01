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
        """`N1` answers L1 or it answers nothing.

        A control that ran one page deeper would differ from L1 in depth as
        well as in composition, which is the confound the whole rung exists to
        remove, and the run would look like it had answered the question.
        Checked on the delivered sequence and not on the declared list, because
        the front page is appended afterwards and the declared lists could match
        while the delivered ones did not.
        """
        from nmbench.targets import TARGETS
        for name, target in TARGETS.items():
            rungs = probe_and_hold.warm_ladder(target)
            if "N1" not in rungs:
                continue
            assert "L1" in rungs, (
                f"{name} declares N1 with no L1 to control, so the arm has "
                f"nothing to be compared against")
            neutral = probe_and_hold.warm_sequence(target, "N1", Args())
            first = probe_and_hold.warm_sequence(target, "L1", Args())
            assert len(neutral) == len(first), f"{name}: {neutral} {first}"

    def test_a_neutral_rung_shares_no_page_with_the_chain_it_controls(self):
        """Except the front page, which every rung ends on and which the entry
        shape would navigate to anyway - see `ensure_entry`. Any other shared
        page would put a target-owned visit in the arm whose claim is that it
        has none before the last one."""
        from nmbench.targets import TARGETS
        for name, target in TARGETS.items():
            rungs = probe_and_hold.warm_ladder(target)
            for level in probe_and_hold.NEUTRAL_RUNGS & set(rungs):
                pages = set(rungs[level])
                for other, urls in rungs.items():
                    if other in probe_and_hold.NEUTRAL_RUNGS:
                        continue
                    if other == "L3":
                        # L3 is the one rung that legitimately contains these:
                        # it is the chain plus the neutral pages, which is what
                        # makes N1 readable as L3's increment on its own.
                        continue
                    assert not (pages & set(urls)), f"{name} {level}/{other}"

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
