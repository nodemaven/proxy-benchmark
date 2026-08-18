"""The two decisions that decide whether the fallback ladder is worth reading.

`playbook.py` prints an ordered list, and an ordered list is the one output
shape whose mistakes do not look like mistakes: a wrong row still sorts, still
carries an interval, and still reads as a recommendation. Both properties pinned
here were arrived at by getting them wrong first, and neither would break
anything else in the repository if it were reverted.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"
                       / "analysis"))

import playbook


class TestRankIsBoughtWithAttempts:
    """Ordering is by the lower bound, never by the point estimate.

    Ranked on the estimate, `1/1` is 100% and outranks `90/100`, so the first
    step of the ladder - the one an SDK reaches for before it has learned
    anything - would rest on the thinnest evidence on the screen. This is the
    same failure as publishing `us = 0%` from one window, in the direction that
    flatters instead of the one that alarms.
    """

    def test_a_perfect_single_attempt_does_not_outrank_a_large_sample(self):
        thin, _ = playbook.wilson(1, 1)
        thick, _ = playbook.wilson(90, 100)
        assert thin < thick, (
            "1/1 is sorting above 90/100, so the ladder is ordered by the point "
            "estimate and its top step is its weakest measurement")

    def test_the_bound_is_wilson_and_not_normal(self):
        # The normal interval puts 0/10 at exactly zero and 10/10 at exactly
        # one. Both are claims the data cannot support, and this repository has
        # already had to withdraw one of them.
        assert playbook.wilson(0, 10)[1] > 0.0
        assert playbook.wilson(10, 10)[0] < 1.0

    def test_more_attempts_at_the_same_rate_ranks_higher(self):
        assert playbook.wilson(6, 10)[0] < playbook.wilson(60, 100)[0]


class TestAConditionalRowIsNotAnAttempt:
    """A held query only exists because a probe before it was served.

    109 of 110 held rows passed, so a configuration that ran the probe-and-hold
    protocol carries a tail of near-certain successes that a configuration doing
    one query per exit never had the chance to earn. Pooling them ranks the
    protocol and calls it the configuration, and the error is invisible in the
    output because every column stays internally consistent.
    """

    def test_a_hold_is_excluded(self):
        assert not playbook.is_unconditional({"phase": "hold"})

    def test_a_probe_is_counted(self):
        assert playbook.is_unconditional({"phase": "probe"})

    def test_a_row_from_before_the_protocol_is_counted(self):
        # Absence means the run predates probe-and-hold, and every one of those
        # is a single attempt. Treating a missing column as conditional would
        # drop 2326 rows - the entire history - out of every table.
        assert playbook.is_unconditional({})


class TestTheDenominatorSplitStaysHonest:
    """`no status` is an answer, not a missing value.

    `was_served` needs the HTTP status, so a row that carries none cannot be
    split and any served-versus-refused figure over it is instrument blindness
    rather than a result. Folding those rows into `refused` would put a defect
    of our own adapter into a column that reads as the target refusing an
    address.
    """

    def test_a_row_without_a_status_is_named_rather_than_refused(self):
        assert playbook.classify({"verdict": "ok", "status": None}) \
            == "no status"

    def test_an_error_outranks_the_status_test(self):
        # An attempt that never completed has no status either, and calling it
        # blind would hide the CONNECT floor inside an adapter defect.
        assert playbook.classify({"verdict": "error", "status": None}) \
            == "no reply"


class TestAnEngineIsNotSplitByItsLabel:

    @pytest.mark.parametrize("label", [
        "camoufox/light", "camoufox-direct/light", "camoufox/none",
        "camoufox-direct/none", "camoufox/aggressive"])
    def test_the_preset_and_the_arm_are_not_part_of_the_engine(self, label):
        # Both are already columns. Leaving them on the name splits one engine
        # into five cells that differ by a label rather than by anything that
        # ran, and every one of them lands under the rankable floor.
        assert playbook.engine_base({"engine": label}) == "camoufox"


class TestDirectIsNotAFilterValue:
    """A direct row has no gateway, so it has no `filter` either.

    Folding it into `none` merges "no quality filter was requested" with "no
    proxy was involved", which are opposite claims, and it would put the direct
    arm's pass rate inside a gateway-parameter comparison.
    """

    def test_a_direct_row_is_named(self):
        assert playbook.filter_of({"direct": True, "params": {}}) == "direct"

    def test_a_pool_row_without_the_parameter_is_none(self):
        assert playbook.filter_of(
            {"direct": False, "params": {"country": "us"}}) == "none"

    def test_a_pool_row_carries_its_value(self):
        assert playbook.filter_of(
            {"direct": False, "params": {"filter": "medium"}}) == "medium"
