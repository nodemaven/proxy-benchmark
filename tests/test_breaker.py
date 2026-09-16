"""Circuit breaker tests.

The breaker is an operational safety device, not an error handler. Its job is to
stop a cell that is failing rather than rotate a session and try again, because
every retry after a refusal confirms automation to the target and degrades the
exit ranges for every other customer on the account.
"""
from pathlib import Path
from typing import ClassVar

import pytest

from nmbench.breaker import (
    TRANSPORT_MARKERS,
    CircuitBreaker,
    SessionBreaker,
    TransportWatch,
    is_transport_failure,
)

ROOT = Path(__file__).resolve().parent.parent

# A real one, off a row. The classification these tests turn on is made by
# substring match against this, so a made-up string would test nothing.
TUNNEL = ("Error: Page.goto: net::ERR_TUNNEL_CONNECTION_FAILED at "
          "https://www.amazon.com/s?k=y")


def feed(breaker, verdicts):
    return [breaker.record(v) for v in verdicts]


def session(breaker, verdicts, error=None):
    """One batch: n attempts and then the session closing, which is the only
    moment a `SessionBreaker` judges anything."""
    for verdict in verdicts:
        breaker.record(verdict, error if verdict == "error" else None)
    return breaker.end_session()


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


class TestASessionIsTheUnitAndNotAnAttempt:
    """What the matrix runner stops on, and why it is not what the probe stops
    on.

    One batch is one browser holding one sticky session, which is one exit -
    measured, 24 of 24 sessions held exactly one exit prefix. So a run of
    failures inside a batch is one exit failing repeatedly and says nothing
    about the cell, and a cell can only be judged by how many *exits* it has
    been refused through.
    """

    def test_one_dead_exit_cannot_end_a_cell(self):
        """The 2026-09-11 defect exactly: `--batch 10 --breaker 10` made the
        limit one session, so ten of sixteen cells were ended by the first
        exit they drew and every cell in the file ended on a 0/10 session."""
        breaker = SessionBreaker("cell", limit=3)
        assert session(breaker, ["block"] * 10) == "dead"
        assert breaker.tripped is False

    def test_the_limit_counts_sessions(self):
        breaker = SessionBreaker("cell", limit=3)
        session(breaker, ["block"] * 10)
        session(breaker, ["block"] * 10)
        assert breaker.tripped is False
        session(breaker, ["block"] * 10)
        assert breaker.tripped is True
        assert "3 sessions in a row" in breaker.reason

    def test_one_served_attempt_keeps_the_session_alive(self):
        """`alive` is one answer out of ten, not ten. A session that served
        anything proves the exit reaches the target, which is the thing the
        stopping rule is about."""
        breaker = SessionBreaker("cell", limit=3)
        assert session(breaker, ["block"] * 9 + ["ok"]) == "alive"
        assert breaker.alive_sessions == 1

    def test_an_alive_session_clears_the_streak(self):
        breaker = SessionBreaker("cell", limit=2)
        session(breaker, ["block"] * 5)
        session(breaker, ["ok"])
        session(breaker, ["block"] * 5)
        assert breaker.tripped is False

    def test_nothing_inside_a_session_can_stop_it_early(self):
        """`record` returns a backoff and never trips. A batch that is cut
        short would leave the cell holding a half-measured session, and the
        exit it drew has been paid for either way."""
        breaker = SessionBreaker("cell", limit=1)
        for _ in range(50):
            breaker.record("block")
        assert breaker.tripped is False

    def test_the_backoff_still_grows_within_a_session(self):
        breaker = SessionBreaker("cell", base_pause=1.0, max_pause=8.0)
        waits = [breaker.record("block") for _ in range(5)]
        assert waits == [1.0, 2.0, 4.0, 8.0, 8.0]

    def test_a_success_resets_the_backoff(self):
        breaker = SessionBreaker("cell", base_pause=1.0)
        breaker.record("block")
        breaker.record("block")
        assert breaker.record("ok") == 1.0
        assert breaker.record("block") == 1.0


class TestACellThatWasNeverAnsweredHasNoVerdict:
    """The distinction that turns a published 0% back into an unmeasured cell.

    On 2026-09-11 six cells asked four gateways for a country called `any`.
    Three of the gateways refused the tunnel, so no attempt in those cells ever
    reached Amazon - and the file reports them at 0%, which reads as a target
    that refused every request. It is the difference between a measurement and
    the absence of one, and it is carried by the error string on the attempt.
    """

    def test_a_session_where_nothing_arrived_is_not_dead_but_unreachable(self):
        breaker = SessionBreaker("cell")
        assert session(breaker, ["error"] * 10, error=TUNNEL) == "unreachable"

    def test_a_target_that_refuses_everything_is_dead_and_not_unreachable(self):
        """`block` is the harness working. A breaker that treated a refusal as
        an absent measurement would delete the finding it exists to protect."""
        breaker = SessionBreaker("cell")
        assert session(breaker, ["block"] * 10) == "dead"
        assert breaker.no_verdict is False

    def test_an_error_that_could_be_the_target_still_counts_as_arrival(self):
        """A timeout is a finding about the target - stalling a client is a
        thing targets do - so it must not leave the denominator."""
        breaker = SessionBreaker("cell")
        assert session(breaker, ["error"] * 10,
                       error="TimeoutError: Page.goto: Timeout 60000ms "
                             "exceeded. Call log:") == "dead"
        assert breaker.no_verdict is False

    def test_an_unreachable_cell_stops_sooner_than_a_refused_one(self):
        """Retrying an attempt that never reached the target cannot produce a
        verdict, so the attempts buy nothing. Two sessions and not three."""
        breaker = SessionBreaker("cell", limit=3, unreachable_limit=2)
        session(breaker, ["error"] * 10, error=TUNNEL)
        assert breaker.tripped is False
        session(breaker, ["error"] * 10, error=TUNNEL)
        assert breaker.tripped is True
        assert "reached the target" in breaker.reason
        assert breaker.no_verdict is True

    def test_one_arrival_anywhere_in_the_cell_gives_it_a_denominator(self):
        """`no_verdict` is about the cell and not about its last session. A
        cell that was answered once and then lost its tunnel has a rate, badly
        measured; a cell that was never answered has none at all."""
        breaker = SessionBreaker("cell")
        session(breaker, ["block"] * 10)
        session(breaker, ["error"] * 10, error=TUNNEL)
        session(breaker, ["error"] * 10, error=TUNNEL)
        assert breaker.tripped is True
        assert breaker.no_verdict is False

    def test_the_session_in_flight_counts_towards_it(self):
        """A run stopped by the transport watchdog or by Ctrl-C never closes
        its last session, and the rows that session wrote are on disk."""
        breaker = SessionBreaker("cell")
        breaker.record("block")
        assert breaker.no_verdict is False


class TestABatchThatSentNothing:
    """A browser that would not launch is not a session of the run.

    It measured no exit and asked the target nothing, so counting it as a dead
    session would charge our own launcher to the target's refusal rate. The old
    runner did exactly that, by recording a synthetic `error` attempt - which
    also counted as having reached the target.
    """

    def test_it_is_not_counted_as_a_session(self):
        breaker = SessionBreaker("cell")
        assert breaker.end_session() == "abandoned"
        assert breaker.sessions == 0

    def test_a_run_of_them_still_stops_the_cell(self):
        """Relaunching a browser that will not start costs a minute a time and
        produces nothing to count."""
        breaker = SessionBreaker("cell", unreachable_limit=2)
        breaker.end_session()
        assert breaker.tripped is False
        breaker.end_session()
        assert breaker.tripped is True
        assert "sent nothing at all" in breaker.reason

    def test_a_session_that_ran_clears_the_streak(self):
        breaker = SessionBreaker("cell", unreachable_limit=2)
        breaker.end_session()
        session(breaker, ["block"])
        breaker.end_session()
        assert breaker.tripped is False


class TestTheTwoBreakersAreDeliberatelyDifferent:
    """N is not one number across this repository, and it is not even one unit.

    `google_429.py` draws a fresh exit per attempt, so its unit is the attempt
    and `CircuitBreaker` counts attempts. The matrix runner puts one browser on
    one sticky exit for a whole batch, so its unit is the session and it uses
    `SessionBreaker`. That reads as duplication, which is exactly the risk:
    somebody tidies the two into one class, nothing fails, and the meaning of
    every future row shifts.

    The attempt figure is measured. Read 2026-08-12 over every
    `benchmark_*.jsonl`, 129 cells and 1464 attempts, as the chance an attempt
    succeeds given the failures immediately before it in its own cell: 5.8% at
    five consecutive failures, 1.6% at six, 0.5% from seven to nine.

    **That table is what made the old runner default wrong, and it is worth
    saying how, because the number was not misread - it was applied to the
    wrong thing.** It says a sixth consecutive failure is nearly always
    followed by more, which is true of consecutive *attempts* down one exit.
    The runner then used it as a cell-stopping rule, where `--batch 10
    --breaker 10` makes the limit exactly one session: on 2026-09-11 ten of
    sixteen cells were ended by their first exit, and all sixteen ended on a
    session that scored 0/10 - the stopping rule printing itself. A statistic
    about when to stop retrying one exit is not a statistic about when to stop
    measuring a cell.

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

    def test_the_matrix_runner_counts_sessions(self):
        """The unit, not the size, is what this pins. An attempt-counting
        breaker in the matrix runner is the 2026-09-11 defect however its
        limit is set, because ten attempts down one dead exit is one
        observation repeated ten times."""
        source = self.RUNNER.read_text(encoding="utf-8")
        assert "SessionBreaker(c.key, limit=args.breaker" in source
        assert "CircuitBreaker" not in source, (
            "the matrix runner is counting attempts again. Its batch is one "
            "browser on one sticky exit, so a run of failed attempts inside "
            "one is a single exit and not evidence about the cell")

    def test_the_runner_default_is_a_number_of_sessions(self):
        """A benchmark is measuring the shape of a refusal, so it has to see
        enough of one - which now means enough exits, not enough retries. The
        runner is the only place that decides this and it decides it in an
        argparse default."""
        source = self.RUNNER.read_text(encoding="utf-8")
        assert '"--breaker", type=int, default=3' in source


class TestTellingATransportFailureFromARefusal:
    """The distinction the two breakers above cannot make, at one attempt.

    Every string below was taken off a row in `data/runs/` on 2026-08-28 and is
    quoted here whole rather than read from disk at test time. Quoting is the
    point: what is being pinned is that the classifier survives the prose each
    producer wraps its marker in, and a test that regenerated the strings from
    the same constant it is testing would pass on a classifier that matched
    nothing real.
    """

    ARRIVED_NOWHERE: ClassVar = [
        "Error: Page.goto: net::ERR_SSL_PROTOCOL_ERROR at "
        "https://www.google.com/search?q=x&hl=en",
        "Error: Page.goto: net::ERR_TUNNEL_CONNECTION_FAILED at "
        "https://www.amazon.com/s?k=y",
        "Error: net::ERR_TUNNEL_CONNECTION_FAILED",
        "WebDriverException: Message: unknown error: "
        "net::ERR_TUNNEL_CONNECTION_FAILED",
        "Error: Page.goto: net::ERR_PROXY_CONNECTION_FAILED at "
        "https://www.bing.com/search?q=z",
        "Error: Page.goto: NS_ERROR_PROXY_CONNECTION_REFUSED Call log: - "
        "navigating to \"https://www.google.com/\", waiting until "
        "\"domcontentloaded\"",
        "Error: Page.goto: NS_ERROR_PROXY_GATEWAY_TIMEOUT Call log: - "
        "navigating to \"https://www.google.com/\"",
        "ProxyError: Failed to perform, curl: (56) Proxy CONNECT aborted. "
        "See https://curl.se/libcurl/c/libcurl-errors.html",
    ]

    # The exclusions, and they are the load-bearing half. Each of these is a
    # failure, and none of them says which end of the tunnel failed.
    AMBIGUOUS: ClassVar = [
        # 509 rows, the largest single error class on disk. A connection closed
        # after the handshake with nothing sent. Both ends can do that.
        "Error: Page.goto: net::ERR_EMPTY_RESPONSE at https://www.google.com/",
        # 669 rows. A target that stalls a client on purpose produces exactly
        # this, and that is a finding about the target.
        "TimeoutError: Page.goto: Timeout 60000ms exceeded. Call log:",
        "Error: Page.goto: NS_ERROR_NET_TIMEOUT Call log: - navigating to",
        "TimeoutError: Document did not become ready within 60 seconds",
        # This machine's own network. Not a measurement either, but redrawing
        # an exit cannot help it and counting it as one would inflate the
        # redraw budget on a fault that is ours.
        "Error: Page.goto: net::ERR_NETWORK_CHANGED at https://www.google.com/",
        # A refusal by the target, which is the thing the harness exists to
        # measure. It must never leave the denominator.
        "Error: Page.goto: net::ERR_HTTP_RESPONSE_CODE_FAILURE",
    ]

    @pytest.mark.parametrize("error", ARRIVED_NOWHERE)
    def test_an_attempt_that_never_arrived_is_named_as_one(self, error):
        assert is_transport_failure(error)

    @pytest.mark.parametrize("error", AMBIGUOUS)
    def test_a_failure_that_could_be_the_target_is_left_alone(self, error):
        """A false positive here silently deletes evidence: the attempt leaves
        the denominator and its identity is drawn again, so a target refusing
        us would read as a pool with a transport problem."""
        assert not is_transport_failure(error)

    def test_a_row_that_succeeded_carries_no_error_and_is_not_a_failure(self):
        assert not is_transport_failure(None)
        assert not is_transport_failure("")

    def test_every_marker_was_seen_before_it_was_listed(self):
        """The constant is a list of things this gateway has actually done, and
        the comment beside it says so. A marker added because it plausibly
        could happen cannot be checked against anything and still decides what
        leaves the denominator - so the guard is that each one appears in a
        string quoted above, which is a string off a row."""
        seen = " ".join(self.ARRIVED_NOWHERE)
        for marker in TRANSPORT_MARKERS:
            assert marker in seen, (
                f"{marker} is in TRANSPORT_MARKERS with no observed row above "
                f"it. Add the row it came from, or take the marker out")
