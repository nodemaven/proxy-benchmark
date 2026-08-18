"""What a benchmark run actually cost, so the next plan is measured and not guessed.

The estimate printed before a run multiplies attempts by a constant for seconds
and a constant for bytes. Those constants age: a matrix with a target that
refuses, a breaker that backs off exponentially, and a 60 second navigation
timeout all spend wall clock that no per-attempt constant predicts. A plan built
on a stale constant is not a small error - it is the difference between a run
that fits in an evening and one that does not finish.

This reads a finished or interrupted run and reports what it really cost. Wall
clock is the number to plan with, not `elapsed_ms`: the gap between them is the
pauses, the backoff and the browser launches, and those are most of the budget.

Usage:
    python scripts/analysis/calibrate.py
    python scripts/analysis/calibrate.py data/runs/benchmark_<stamp>.jsonl
"""
import argparse
import json
import statistics
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nmbench.sink import RUNS_DIR

VERDICT_ORDER = ["ok", "captcha", "consent", "block", "empty", "error"]


def newest_run() -> Path:
    runs = sorted(RUNS_DIR.glob("benchmark_*.jsonl"))
    if not runs:
        raise SystemExit("no benchmark run in data/runs/")
    return runs[-1]


def load(path: Path) -> list:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            # An interrupted run ends in a partial line. That is the normal way
            # a long run ends, and it must not cost the rest of the file.
            continue
    return rows


def span_hours(rows: list) -> float:
    stamps = [datetime.fromisoformat(r["ts"]) for r in rows if r.get("ts")]
    if len(stamps) < 2:
        return 0.0
    return (max(stamps) - min(stamps)).total_seconds() / 3600


def per_cell(attempts: list, bookkeeping: list) -> None:
    stopped = {r["cell"]: r.get("verdict_reason") for r in bookkeeping
               if r.get("cell")}
    print(f"{'cell':<48} {'n':>4}  {'sec':>5}  verdicts")
    for cell in sorted({r["cell"] for r in attempts}):
        rows = [r for r in attempts if r["cell"] == cell]
        counts = Counter(r.get("verdict") for r in rows)
        shown = " ".join(f"{v}={counts[v]}" for v in VERDICT_ORDER if counts[v])
        seconds = statistics.mean(r.get("elapsed_ms", 0) for r in rows) / 1000
        note = f"  STOPPED: {stopped[cell]}" if cell in stopped else ""
        print(f"{cell:<48} {len(rows):>4}  {seconds:>5.1f}  {shown}{note}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", nargs="?", type=Path,
                        help="run file, default the newest benchmark run")
    args = parser.parse_args()

    path = args.path or newest_run()
    rows = load(path)
    attempts = [r for r in rows if r.get("query")]
    bookkeeping = [r for r in rows if not r.get("query")]
    if not attempts:
        raise SystemExit(f"{path.name} holds no attempts")

    hours = span_hours(attempts)
    megabytes = sum(r.get("bytes") or 0 for r in attempts) / 1e6
    cells = len({r["cell"] for r in attempts})
    # A session is one browser, identified by its batch. `session_index` is the
    # position of the attempt inside that browser, not the browser's own number,
    # and counting it here would report one session per attempt.
    sessions = len({(r["cell"], r.get("batch_index")) for r in attempts})

    print(f"file    : {path.name}")
    print(f"scope   : {len(attempts)} attempts over {cells} cells, "
          f"{sessions} sessions")
    print()
    per_cell(attempts, bookkeeping)
    print()

    counts = Counter(r.get("verdict") for r in attempts)
    total = len(attempts)
    print("verdicts: " + "  ".join(
        f"{v}={counts[v]} ({100 * counts[v] / total:.0f}%)"
        for v in VERDICT_ORDER if counts[v]))

    inside = statistics.mean(r.get("elapsed_ms", 0) for r in attempts) / 1000
    wall = hours * 3600 / total
    print()
    print(f"measured: {wall:.1f} s per attempt of wall clock, of which "
          f"{inside:.1f} s was inside the engine")
    print(f"          {megabytes / total * 1000:.0f} KB per attempt, "
          f"{megabytes:.1f} MB over {hours:.2f} h")
    print()
    print("Local byte counters sum Content-Length and are a floor, not a "
          "measurement: html.duckduckgo.com sends none at all.")
    print()
    print(f"planning: {wall:.0f} s per attempt is the number to multiply. "
          f"1000 queries per cell")
    for n in (1, 5, 9, 18):
        print(f"          {n:>2} cells -> {n * 1000:>6} attempts, "
              f"{n * 1000 * wall / 3600:>5.1f} h, "
              f"{n * 1000 * megabytes / total / 1000:>5.2f} GB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
