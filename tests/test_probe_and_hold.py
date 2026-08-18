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
                                   True, "home")
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


def cell(engine="patchright", geo="off"):
    return probe_and_hold.Cell(engine, "google_serp", "any",
                               ("params-none", {}), False, "home", geo)


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
