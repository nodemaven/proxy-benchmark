"""Stopping a run that is already in flight, and deciding what its rows become.

Written in `scripts/benchmark.py` on 2026-09-22 and moved here on 2026-09-23,
when the warm-up ladder needed the same thing. The ladder is the reason it is a
module rather than a second copy: `scripts/run_ladder.py` supervises a probe
that runs for nine to thirteen hours at its default power, which is the single
longest thing this repository launches and therefore the one most worth being
able to end. A duplicate of `dispose` would have been a duplicate of the rule
about where a partial run is allowed to sit, and that rule is the only thing
standing between a truncated matrix and every chart on the dashboard.

**A stop is not a kill, and that is the whole design.** Killing the process
leaves a browser session the gateway still counts and bills, a relay that never
closed its books, and a run file cut in the middle of a line. So the runner is
asked rather than signalled: `StopWatch` polls a path between attempts and the
run leaves by its own summary path, through the same context managers it would
have left through at the end. The price is latency - the request is read
between attempts and an attempt can be a slow page load - and it was measured
rather than assumed: 30 seconds on a chromium/amazon_search run on the VPS,
2026-09-22.

**The two answers about the rows are both irreversible, in opposite
directions.** `JsonlSink.write` appends per attempt, so by the time anybody
presses the button the measured attempts are already on disk: the file is a
complete record of an incomplete matrix. `discard` deletes measurements that
cost metered proxy traffic. `keep` that left the file where it was would pool a
truncated matrix into every chart, silently - which is worse, because a
truncated matrix is not a smaller one. The cells a stopped run never reached
are exactly the ones the round-robin had not got to yet, so what is missing is
chosen by position in the plan rather than at random, and pooling it moves the
numbers instead of only blurring them.
"""
import json
import shutil
from pathlib import Path

from .sink import RUNS_DIR


class StoppedByOperator(RuntimeError):
    """Raised to unwind a run when somebody asked it to stop.

    An exception rather than a flag, because the loop it has to leave is two
    levels deep inside an open browser session and the context manager closing
    that browser on the way out is the behaviour we want.

    Deliberately not a sibling of `benchmark.TransportLost`: the two say
    opposite things about the machine. One is a transport that has gone away
    and is worth a gateway probe before restarting; the other is a run that was
    working and was ended on purpose. They unwind identically and are reported
    differently, and sharing a class would put "the operator pressed stop" in
    the bucket the transport watch exists to keep clean.
    """


# Exit code for that case, and it is deliberately neither 0 nor 1. Zero means
# the run did what it was asked, which a stopped run did not; 1 is what every
# ordinary failure leaves and a stop is not a failure. The queue does not rely
# on this - it reads the stop mark on the job - but a hand-run benchmark has
# nothing else to go on, and a supervisor that only sees the code should be able
# to tell a stop from a crash.
EXIT_STOPPED = 3

STOPPED_DIR = RUNS_DIR / "stopped"


class StopWatch:
    """Polls a path for a stop request, cheaply enough to ask after every attempt.

    One `stat` per attempt, against attempts that take seconds - so the cost is
    not worth caching and the latency is worth nothing else. It is checked
    between attempts rather than inside one because there is no safe point
    inside: an attempt abandoned half way has sent a request the gateway will
    bill and produced no row to account for it.

    A file that will not parse is treated as a stop with the default answer
    about the rows, not as no stop at all. The alternative is a run that carries
    on because the operator's answer was malformed, which is the failure mode
    where the button looks pressed and nothing happens - and it costs the rest
    of the run before asking him again.
    """

    def __init__(self, path: str = None):
        self.path = Path(path) if path else None
        self.tripped = False
        self.keep = True
        self.requested = ""

    def check(self) -> bool:
        if self.tripped or not self.path:
            return self.tripped
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            return False
        self.tripped = True
        try:
            asked = json.loads(raw)
        except ValueError:
            asked = {}
        self.keep = str(asked.get("keep", "keep")) != "discard"
        self.requested = str(asked.get("requested") or "")
        return True

    @property
    def reason(self) -> str:
        when = f" at {self.requested}" if self.requested else ""
        rows = "kept" if self.keep else "discarded"
        return f"stopped on request{when}, rows {rows}"


def dispose(sink, store, keep: bool) -> str:
    """Put a stopped run's file where it belongs, and say where that is.

    Not where it was. Every consumer in this repository finds runs by globbing
    `data/runs/*.jsonl` without recursing - the dashboard, `report.py`,
    `calibrate.py`, `playbook.py`, `results_tables.py` - so a partial file left
    in place is pooled into the corpus by the next page build, with nothing on
    any chart saying a matrix had been cut off part way through.

    `keep` files it under `data/runs/stopped/`, one directory down and therefore
    invisible to every one of those globs while still being on disk and still
    readable by anybody who passes the path explicitly. `discard` deletes it,
    along with the bodies, which is the answer for a run that was started wrong.

    Failures here are reported and not raised. The run is already over and its
    exit code is already decided; a file that will not move is a mess for
    somebody to clean up by hand, and turning it into a traceback would take the
    summary printed above it along with it.
    """
    if not keep:
        gone = []
        try:
            sink.path.unlink(missing_ok=True)
            gone.append(str(sink.path))
        except OSError as exc:
            return f"rows could not be deleted: {exc}"
        if store is not None and store.dir.exists():
            try:
                shutil.rmtree(store.dir)
                gone.append(str(store.dir))
            except OSError as exc:
                return f"rows deleted, bodies could not be: {exc}"
        return "discarded on request: " + ", ".join(gone)

    try:
        STOPPED_DIR.mkdir(parents=True, exist_ok=True)
        moved = STOPPED_DIR / sink.path.name
        sink.path.replace(moved)
    except OSError as exc:
        return (f"kept, but could not be filed: {exc}. It is still at "
                f"{sink.path}, where the charts will read it - move it under "
                f"{STOPPED_DIR} by hand")
    return (f"kept raw at {moved}. Nothing charts it: every reader here globs "
            f"data/runs/*.jsonl and does not recurse, and a matrix stopped part "
            f"way through is missing whichever cells the plan had not reached")
