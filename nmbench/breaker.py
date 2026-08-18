"""Circuit breakers, at two scopes.

A run of consecutive failures heats the pool: every retry confirms automation to
the target and degrades the exit ranges for every other customer on the account.
A cell that has failed `limit` times in a row is stopped and stays stopped. The
caller records that it was stopped rather than rotating a sid and trying again.

`TransportWatch` is the same idea one level up, and it exists because the cell
breaker provably cannot see the failure that matters most. On 2026-08-11 a
broken tunnel produced 46% errors for four and a half hours: the cell breaker
fired 28 times, once per cell, and every one of those stops was written down as
a target refusing us. Nothing stopped the run.
"""
from collections import deque


class CircuitBreaker:
    # The default stays 5 because `probes/google_429.py` relies on it and every
    # google_429 run committed to data/runs/ was measured at 5: raising it here
    # would silently make new rows incomparable with the recorded ones. The
    # matrix runner passes its own limit and does not read this value.
    def __init__(self, cell: str, limit: int = 5,
                 base_pause: float = 5.0, max_pause: float = 30.0):
        self.cell = cell
        self.limit = limit
        self.base_pause = base_pause
        self.max_pause = max_pause
        self.consecutive = 0
        self.tripped = False
        self.reason = None

    def record(self, verdict: str) -> float:
        """Feed one verdict in, get the seconds to wait before the next attempt."""
        if verdict == "ok":
            self.consecutive = 0
            return self.base_pause

        self.consecutive += 1
        if self.consecutive >= self.limit:
            self.trip(f"{self.limit} consecutive failures")
        return min(self.base_pause * (2 ** (self.consecutive - 1)), self.max_pause)

    def trip(self, reason: str) -> None:
        """Stop the cell for a reason that is not a run of verdicts.

        An engine whose binary will not start, or a session that left from the
        operator's own address instead of the pool, will do exactly the same on
        the next batch. Without this the cell is retried once per remaining
        batch - hundreds of browser launches that produce no measurement and,
        in the second case, hundreds of requests to the target from an address
        that was never supposed to reach it.

        The first reason wins. What stopped a cell is the first thing that went
        wrong with it, not the last.
        """
        if not self.tripped:
            self.tripped = True
            self.reason = reason


class TransportWatch:
    """Stops the whole run once the errors stop being about the targets.

    Two conditions, and both are needed. The error share over a recent window
    has to be high, and the errors have to be spread over several cells. The
    second is what separates a transport failure from a real measurement: one
    engine that cannot start, or one target that refuses everything, is a
    finding and belongs to that cell's breaker. Five unrelated engine stacks
    failing in the same minutes are not five findings, they are one, and it is
    not about the targets.

    `error` is the only verdict counted. A cell answering `block` or `captcha`
    is the benchmark working, however unhappy the number looks, and a watchdog
    that stopped on refusals would delete the measurement it was built to
    protect.

    The window is deliberately short. The cost of stopping a healthy run by
    mistake is one command to restart it with `--resume`; the cost of not
    stopping a broken one is a night.
    """

    def __init__(self, window: int = 20, share: float = 0.6, spread: int = 3):
        self.window = window
        self.share = share
        self.spread = spread
        self.recent = deque(maxlen=window)
        self.tripped = False
        self.reason = None

    def record(self, cell: str, verdict: str) -> None:
        if self.tripped:
            return
        self.recent.append((cell, verdict))
        if len(self.recent) < self.window:
            return

        errors = [c for c, v in self.recent if v == "error"]
        if len(errors) < self.share * self.window:
            return
        if len(set(errors)) < self.spread:
            return

        self.tripped = True
        self.reason = (
            f"{len(errors)} of the last {self.window} attempts errored, across "
            f"{len(set(errors))} cells. Attempts that never completed are not "
            f"evidence about the targets, so the run stopped rather than "
            f"spending the night recording them"
        )
