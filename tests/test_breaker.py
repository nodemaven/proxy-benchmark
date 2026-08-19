"""Circuit breaker tests.

The breaker is an operational safety device, not an error handler. Its job is to
stop a cell that is failing rather than rotate a session and try again, because
every retry after a refusal confirms automation to the target and degrades the
exit ranges for every other customer on the account.
"""
from pathlib import Path

import pytest

from nmbench.breaker import CircuitBreaker, TransportWatch

ROOT = Path(__file__).resolve().parent.parent


def feed(breaker, verdicts):
    return [breaker.record(v) for v in verdicts]


class TestTripping:
    def test_trips_on_the_limit(self):
        breaker = CircuitBreaker("cell", limit=3)
        feed(breaker, ["captcha", "captcha"])
        assert breaker.tripped is False
        breaker.record("captcha")
        assert breaker.tripped is True

    def test_a_success_clears_the_streak(self):
        breaker = CircuitBreaker("cell", limit=3)
        feed(breaker, ["captcha", "captcha", "ok", "captcha", "captcha"])
        assert breaker.tripped is False

    def test_stays_tripped(self):
        """No verdict re-opens it. The run reports the cell as stopped."""
        breaker = CircuitBreaker("cell", limit=2)
        feed(breaker, ["block", "block", "ok"])
        assert breaker.tripped is True

    @pytest.mark.parametrize("verdict", ["captcha", "block", "empty", "error",
                                         "consent"])
    def test_every_non_ok_verdict_counts(self, verdict):
        """Including `error`: a cell that cannot complete an attempt must stop
        too, even though the error is ours and not the target's."""
        breaker = CircuitBreaker("cell", limit=2)
        feed(breaker, [verdict, verdict])
        assert breaker.tripped is True


class TestReason:
    def test_a_fresh_breaker_has_no_reason(self):
        breaker = CircuitBreaker("cell")
        assert breaker.reason is None

    def test_a_run_of_failures_records_why(self):
        breaker = CircuitBreaker("cell", limit=2)
        feed(breaker, ["captcha", "captcha"])
        assert breaker.reason == "2 consecutive failures"

    def test_trip_stops_the_cell_without_a_run_of_verdicts(self):
        """An engine that will not launch, or a session that left from the
        operator's own address, repeats on every remaining batch."""
        breaker = CircuitBreaker("cell")
        breaker.trip("proxy not applied, browser went direct")
        assert breaker.tripped is True
        assert breaker.reason == "proxy not applied, browser went direct"

    def test_the_first_reason_wins(self):
        """What stopped a cell is the first thing that went wrong with it."""
        breaker = CircuitBreaker("cell", limit=2)
        breaker.trip("engine could not start")
        feed(breaker, ["block", "block"])
        assert breaker.reason == "engine could not start"


class TestBackoff:
    def test_pause_doubles_while_failing(self):
        breaker = CircuitBreaker("cell", limit=10, base_pause=5.0, max_pause=100.0)
        assert feed(breaker, ["block"] * 4) == [5.0, 10.0, 20.0, 40.0]

    def test_pause_is_capped(self):
        breaker = CircuitBreaker("cell", limit=10, base_pause=5.0, max_pause=30.0)
        assert feed(breaker, ["block"] * 5) == [5.0, 10.0, 20.0, 30.0, 30.0]

    def test_success_returns_to_the_base_pause(self):
        """Never to zero: this is a shared production pool, not a lab."""
        breaker = CircuitBreaker("cell", limit=10, base_pause=5.0)
        feed(breaker, ["block", "block"])
        assert breaker.record("ok") == 5.0

    def test_a_passing_cell_still_pauses(self):
        breaker = CircuitBreaker("cell", base_pause=3.0)
        assert all(w >= 3.0 for w in feed(breaker, ["ok"] * 5))


class TestTransportWatch:
    """The run-level breaker.

    Written after a run on 2026-08-11 spent four and a half hours recording
    errors from a broken tunnel. Every cell breaker did its job and stopped its
    own cell, which is precisely why nothing stopped the run.
    """

    def test_errors_across_several_cells_stop_the_run(self):
        watch = TransportWatch(window=10, share=0.6, spread=3)
        for index in range(10):
            watch.record(f"cell{index % 5}", "error")
        assert watch.tripped is True
        assert "cells" in watch.reason

    def test_refusals_are_a_measurement_and_never_stop_the_run(self):
        """A night where every target refuses is a finding, not a fault."""
        watch = TransportWatch(window=10, share=0.6, spread=3)
        for index in range(40):
            watch.record(f"cell{index % 5}", "block")
            watch.record(f"cell{index % 5}", "captcha")
        assert watch.tripped is False

    def test_one_broken_cell_is_left_to_its_own_breaker(self):
        """One engine that cannot start is that cell's problem. Stopping the
        whole matrix for it would throw away the columns that are working."""
        watch = TransportWatch(window=10, share=0.6, spread=3)
        for _ in range(50):
            watch.record("the-one-bad-cell", "error")
        assert watch.tripped is False

    def test_it_waits_for_a_full_window(self):
        """Three errors at the start of a run are not a pattern yet."""
        watch = TransportWatch(window=20, share=0.6, spread=3)
        for index in range(3):
            watch.record(f"cell{index}", "error")
        assert watch.tripped is False

    def test_it_stays_tripped(self):
        watch = TransportWatch(window=10, share=0.6, spread=3)
        for index in range(10):
            watch.record(f"cell{index % 5}", "error")
        for index in range(10):
            watch.record(f"cell{index % 5}", "ok")
        assert watch.tripped is True

    def test_the_gateways_own_error_floor_does_not_stop_a_four_day_run(self):
        """The default share, against the baseline it has to survive.

        The gateway accepts a fifth to a third of CONNECTs and never answers
        them, measured on two unrelated networks, and every one of those is an
        `error` row spread across every cell. At share 0.6 that floor tripped a
        healthy eight-engine run at attempt 564 of 8000 on 2026-08-19 - the
        error rate had been 33% in the first hour and 34% in the eighth, so
        nothing had changed except which twenty attempts the window held.

        Deterministic rather than random: this reproduces the worst case the
        floor can produce at 34%, which is what the threshold has to survive,
        instead of sampling and hoping. Seven errors in every twenty is 35%.
        """
        watch = TransportWatch()
        for index in range(8000):
            verdict = "error" if index % 20 < 7 else "captcha"
            watch.record(f"cell{index % 16}", verdict)
        assert watch.tripped is False

    def test_a_transport_failure_is_still_caught_in_one_window(self):
        """What the watchdog is for, and it is not a 60% error rate.

        An intercepted line or a dead gateway fails essentially everything, so
        raising the share to 0.85 costs nothing in detection: this trips inside
        a single window, about 17 minutes at the pace of the 2026-08-19 run.
        """
        watch = TransportWatch()
        for index in range(20):
            watch.record(f"cell{index % 8}", "error")
        assert watch.tripped is True


class TestTheTwoDefaultsAreDeliberatelyDifferent:
    """N is not one number across this repository, and the difference is load
    bearing in both directions.

    The class default is 5 and the matrix runner passes 10. That reads as an
    oversight, which is exactly the risk: somebody tidies the two into one
    constant, nothing fails, and the meaning of every future row shifts.

    Both halves are measured. Read 2026-08-12 over every `benchmark_*.jsonl`,
    129 cells and 1464 attempts, as the chance an attempt succeeds given the
    failures immediately before it in its own cell: 5.8% at five consecutive
    failures, 1.6% at six, 0.5% from seven to nine. So stopping at 5 records a
    partial refusal as a total one, and going far past 10 buys almost nothing at
    a price of one confirmed-automation retry each, on a shared production pool.

    Read off the source rather than by running a matrix, because what is being
    pinned is a default nobody passes.
    """

    RUNNER = ROOT / "scripts" / "benchmark.py"
    PROBE = ROOT / "scripts" / "probes" / "google_429.py"

    def test_the_class_default_is_the_one_the_committed_rows_were_taken_at(self):
        """Every `google_429_*.jsonl` in `data/runs/` was measured at 5, and
        `google_429.py` still constructs its breaker with no limit. Raising the
        default here does not break anything visibly; it silently makes the next
        run incomparable with the ones already published."""
        assert CircuitBreaker("cell").limit == 5
        source = self.PROBE.read_text(encoding="utf-8")
        assert "CircuitBreaker(cell)" in source, (
            "google_429.py no longer takes the class default, so the reason "
            "that default cannot move has gone with it. Either restore the "
            "call or move the constant and say so here")

    def test_the_matrix_runner_asks_for_more(self):
        """A benchmark is measuring the shape of a refusal, so it has to see
        enough of one. The runner is the only place that decides this, and it
        decides it in an argparse default."""
        source = self.RUNNER.read_text(encoding="utf-8")
        assert '"--breaker", type=int, default=10' in source

    def test_the_runner_never_falls_back_to_the_class_default(self):
        """The split only holds while the runner passes its own number. A
        `CircuitBreaker(c.key)` here would take 5 and nothing would say so."""
        source = self.RUNNER.read_text(encoding="utf-8")
        assert "CircuitBreaker(c.key, limit=args.breaker" in source
