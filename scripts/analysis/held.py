"""The probe-and-hold axes, over every `probehold_*.jsonl` on disk.

Sends nothing. This is the counterpart to `report.py`, which reads the matrix
runs: those rows arrive at a search URL, these ones land on the target's front
page and type, and the two are not the same client.

Three questions, each with the denominator that belongs to it.

**The probe.** One fresh exit, one query. This is the only row in a series that
is not conditional on anything, so it is the only place a rate can be quoted
flat. Split served against refused for the reason `report.py` documents: a
`/sorry/` diversion is the address being turned away and charging it to the
engine hands the pool's problem to the framework.

**The hold.** Every held row follows a probe that was served, so its rate is
conditional and says so. It is also censored: a series stops at its first
refusal, so a row at position 4 is drawn from the identities still alive and
reads better than the protocol as a whole. Both facts are printed next to the
number rather than left to the reader.

**The axes** - `warm`, the parameter arm, `geo`, the engine. Each is pooled over
the others, which is legitimate here only because the runner interleaves at
identity granularity: every cell is spread across the whole window, so an axis
is not confounded with the hour it ran in. It is still pooling, and a difference
that appears in one axis and not in the per-cell table above it is a difference
worth distrusting.

No p-values. The counts are small enough that the interval does the work, and a
test would put a decision on top of a screen that was not powered to make one.
"""
import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from report import classify

from nmbench.sink import RUNS_DIR
from nmbench.stats import band

SENDS_REQUESTS = False
# The same directory `sink.py` writes to, named once. Spelling the path out here
# a second time is how a reader ends up with a script that keeps reading the old
# location after the writer has moved.
RUNS = RUNS_DIR


def shorten(keys) -> dict:
    """Cell keys with the segments that never vary taken out."""
    split = {k: k.split("/") for k in keys}
    depth = min(len(v) for v in split.values())
    varying = [i for i in range(depth)
               if len({v[i] for v in split.values()}) > 1]
    if not varying:
        varying = [0]
    return {k: "/".join(v[i] for i in varying) for k, v in split.items()}


def load(paths) -> list:
    rows = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def probe_table(rows, key, label) -> None:
    probes = [r for r in rows if r.get("phase") == "probe"]
    buckets = collections.defaultdict(collections.Counter)
    for row in probes:
        counter = buckets[key(row)]
        counter[classify(row)] += 1
        counter["n"] += 1
        if row.get("verdict") == "ok":
            counter["ok"] += 1

    if len(buckets) < 2:
        return
    print(f"\n{label}")
    print(f"  {'':<34}{'n':>5}{'served':>8}{'refused':>9}{'no reply':>10}"
          f"{'P(ok)':>8}{'95%':>13}")
    for name in sorted(buckets):
        c = buckets[name]
        print(f"  {str(name)[:34]:<34}{c['n']:>5}{c['served']:>8}"
              f"{c['refused']:>9}{c['no reply']:>10}{band(c['ok'], c['n'])}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", default=None,
                        help="one file; the default reads every probehold run "
                             "on disk, which mixes windows and is the weaker "
                             "reading")
    parser.add_argument("--target", default=None)
    args = parser.parse_args()

    paths = ([Path(args.run)] if args.run
             else sorted(RUNS.glob("probehold_*.jsonl")))
    if not paths:
        print("no probehold runs on disk")
        return 1
    rows = [r for r in load(paths) if r.get("query")]
    if args.target:
        rows = [r for r in rows if r.get("target") == args.target]
    if not rows:
        print("no attempt in those files, so there is nothing to read")
        return 1

    print(f"{len(paths)} run(s), {len(rows)} attempts")
    if len(paths) > 1:
        print("pooled across runs and therefore across hours. Google's yield "
              "moved 17 points between two afternoons of one experiment, so an "
              "axis read here and not inside a single run is a weak reading")

    print("\nPER CELL, probes only - the flat rate, conditional on nothing")
    cells = collections.defaultdict(collections.Counter)
    for row in rows:
        if row.get("phase") != "probe":
            continue
        c = cells[row["cell"]]
        c[classify(row)] += 1
        c["n"] += 1
        if row.get("verdict") == "ok":
            c["ok"] += 1

    # Segments identical across every cell are the run's fixed settings, not
    # part of the comparison, and they are already printed above. Dropping them
    # is not cosmetic: the keys are long enough that truncating instead cut the
    # engine name off the front, which is the one segment always worth reading.
    labels = shorten(list(cells))
    width = max([len(v) for v in labels.values()] + [10])
    print(f"  {'':<{width}}{'n':>5}{'served':>8}{'refused':>9}"
          f"{'no reply':>10}{'P(ok)':>8}{'95%':>13}")
    for name in sorted(cells):
        c = cells[name]
        print(f"  {labels[name]:<{width}}{c['n']:>5}{c['served']:>8}"
              f"{c['refused']:>9}{c['no reply']:>10}{band(c['ok'], c['n'])}")

    probe_table(rows, lambda r: r["engine"].split("/")[0], "BY ENGINE")
    probe_table(rows, lambda r: "warm-on" if r.get("warm") else "warm-off",
                "BY WARM-UP - the claim is 20% to 75%, and this is its "
                "denominator")
    probe_table(rows, lambda r: r["cell"].split("/")[3], "BY PARAMETER ARM")
    probe_table(rows, lambda r: f"geo-{r.get('geo')}", "BY GEO ALIGNMENT")
    probe_table(rows, lambda r: r.get("entry"), "BY ENTRY SHAPE")

    held = [r for r in rows if r.get("phase") == "hold"]
    if held:
        print("\nHOW LONG A GOOD EXIT LASTS - every row conditional on a "
              "served probe, and censored")
        print(f"  {'position':<10}{'n':>5}{'ok':>5}{'P(ok)':>8}{'95%':>13}")
        by_position = collections.defaultdict(list)
        for row in held:
            by_position[row.get("position")].append(row)
        for position in sorted(by_position, key=lambda p: (p is None, p)):
            bucket = by_position[position]
            good = sum(1 for r in bucket if r["verdict"] == "ok")
            print(f"  {'#' + str((position or 0) + 1):<10}{len(bucket):>5}"
                  f"{good:>5}{band(good, len(bucket))}")
        print("  n falls with position because a series stops at its first "
              "refusal, so the later rows are the survivors")

    print("\nWHY THE NON-OK VERDICTS HAPPENED")
    reasons = collections.Counter(
        (r["verdict"], r.get("verdict_reason") or r.get("error"))
        for r in rows if r["verdict"] != "ok")
    for (verdict, reason), count in reasons.most_common(15):
        print(f"  {count:>4}  {verdict:<9}{(reason or '')[:74]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
