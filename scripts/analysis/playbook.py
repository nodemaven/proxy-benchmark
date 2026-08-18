"""What an SDK should try first, and what it should try when that fails.

Sends nothing. Reads every run on disk.

`report.py` answers "which engine is better" and `held.py` answers "does this
axis move anything". Neither answers the question a client library actually
asks, which is narrower and has a different shape: **I have a target and a
budget, what do I attempt, and when it comes back empty what do I change.**

That question needs three things this repository has not printed together
before.

**A ranking rule that cannot be gamed by a small denominator.** Ordered by the
point estimate, a cell that passed 1 of 1 outranks one that passed 90 of 100,
and a fallback ladder built that way puts its most confident step on its
thinnest evidence. Everything here is ordered by the **lower bound** of the
Wilson interval, so a cell has to buy its rank with attempts. `1/1` scores 21%
and sorts below `90/100` at 82%, which is the intended behaviour and not a
rounding artefact. The point estimate is printed next to it so the reader can
see the price of the caution.

**The failure mode, not just the failure rate.** Two configurations at 30% are
not interchangeable if one is refused before it is shown a page and the other is
shown pages it cannot pass. They call for opposite reactions, and this is the
whole reason the ladder is worth computing at all:

    refused         the exit was turned away. Rotate the address and keep the
                    configuration - switching engines here spends a launch on a
                    problem the engine never had
    ok given served the target answered and the client failed it. Rotate the
                    configuration; a fresh exit will meet the same wall
    no reply        the CONNECT was never answered. Retry. It is neither the
                    address nor the engine, and this repository has never been
                    able to attribute it further than "the gateway or our line"

So each step in the ladder carries its own diagnosis, and the SDK's retry
policy reads off the column rather than off a global constant.

**The distinction between an axis that was measured and one that was merely
recorded.** A column varying across the corpus is not the same as a column
varying inside a window. Filter is the case that matters: it appears with three
values across 129 files and was interleaved inside exactly one of them, so a
pooled filter table is a table of which hours each value happened to run in.
`--strict` is the default for that reason - it keeps only cells whose axis was
varied within a single run - and `--pooled` prints the confounded version with
the confounding named on the line above it.

**One exclusion, and without it every number below is wrong in the same
direction.** A `hold` row is the second or later query of a series that only
exists because the probe before it was served, and 109 of 110 of them passed. A
configuration that ran the probe-and-hold protocol therefore carries a tail of
near-certain successes that a configuration doing one query per exit never had
the chance to earn, and pooling the two ranks the protocol rather than the
configuration. Everything here counts **unconditional attempts only** - probes,
and the single attempts of every run predating the protocol. The hold is not
discarded: it is printed on its own as a multiplier, because "what is one good
exit worth" is a different question from "how do I get one" and the two multiply
rather than average.

No p-values, for the reason `held.py` gives: at these counts the interval does
the work and a test would put a decision on top of a screen not powered to make
one.
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
from nmbench.stats import band, wilson

SENDS_REQUESTS = False
# The same directory `sink.py` writes to, named once. Spelling the path out here
# a second time is how a reader ends up with a script that keeps reading the old
# location after the writer has moved.
RUNS = RUNS_DIR

# Below this a cell is printed but never ranked. Not a significance threshold -
# there is no test here - but a floor under the Wilson lower bound's usefulness:
# at n=5 the bound is so far under the estimate that ordering by it is ordering
# by n alone, which tells the reader nothing they did not already know from the
# denominator column.
RANKABLE = 10


def load(paths) -> list:
    rows = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                row["_run"] = path.stem
                rows.append(row)
    return rows


def engine_base(row: dict) -> str:
    """The engine without its preset suffix or its arm.

    `camoufox/light` and `camoufox-direct/light` are one engine on two arms, and
    the arm is already a column. Keeping the suffix would split every engine
    into cells that differ by a label rather than by anything that ran.
    """
    return row["engine"].split("/")[0].replace("-direct", "")


def arm(row: dict) -> str:
    return "direct" if row.get("direct") else "pool"


def filter_of(row: dict) -> str:
    """The gateway's `filter` parameter, or what stands in for it.

    A direct row has no gateway at all, so it is named rather than folded into
    `none` - the two mean opposite things, one is "no quality filter requested"
    and the other is "no proxy involved".
    """
    if row.get("direct"):
        return "direct"
    params = row.get("params")
    if not isinstance(params, dict):
        return "unknown"
    return params.get("filter") or "none"


def country_of(row: dict) -> str:
    params = row.get("params")
    if not isinstance(params, dict):
        return "-"
    return params.get("country") or "default"


def mode_of(row: dict) -> str:
    headless = row.get("headless")
    if headless is None:
        return "?"
    return "headless" if headless else "headful"


def entry_of(row: dict) -> str:
    # Absent means the run predates the axis, and every one of those arrived by
    # navigation. Defaulting rather than dropping keeps 2326 rows in the table;
    # calling them "unknown" would hide the entire history behind a column that
    # was added on 2026-08-13.
    return row.get("entry") or "url"


def is_unconditional(row: dict) -> bool:
    """True for an attempt that was not bought by an earlier success.

    `phase` is absent on every row written before the probe-and-hold protocol
    existed, and those are all single attempts, so absence means unconditional
    and is the correct default rather than a gap.
    """
    return row.get("phase") != "hold"


def tally(rows) -> collections.Counter:
    counter = collections.Counter()
    for row in rows:
        counter["n"] += 1
        counter[classify(row)] += 1
        if row.get("verdict") == "ok":
            counter["ok"] += 1
    return counter


def within_run_axis(rows, axis, label, note) -> None:
    """An axis read only inside runs that varied it.

    This is the strict reading and it is the default. A value of an axis that
    only ever ran in its own file cannot be compared against another: the
    difference between them contains the hour, the target mix and every other
    setting that moved with it. Google's yield moved 17 points between two
    afternoons of one experiment, so that is not a hypothetical confound.
    """
    varying = set()
    per_run = collections.defaultdict(set)
    for row in rows:
        per_run[row["_run"]].add(axis(row))
    for run, values in per_run.items():
        if len({v for v in values if v != "direct"}) > 1:
            varying.add(run)

    inside = [r for r in rows if r["_run"] in varying]
    print(f"\n{label}")
    if not inside:
        print("  no run on disk varied this axis inside one window, so there is "
              "nothing here that is not a comparison between hours")
        return
    print(f"  {note}")
    print(f"  measured inside {len(varying)} run(s): "
          f"{', '.join(sorted(varying))}")
    buckets = collections.defaultdict(list)
    for row in inside:
        buckets[axis(row)].append(row)
    print(f"  {'':<16}{'n':>5}{'served':>8}{'refused':>9}{'no reply':>10}"
          f"{'P(ok)':>8}{'95%':>13}")
    for name in sorted(buckets):
        c = tally(buckets[name])
        print(f"  {str(name)[:16]:<16}{c['n']:>5}{c['served']:>8}"
              f"{c['refused']:>9}{c['no reply']:>10}{band(c['ok'], c['n'])}")


def pooled_axis(rows, axis, label) -> None:
    """The same axis over everything, with the confounding named.

    Printed because "we cannot read this" is a weaker answer than "here is what
    it looks like and here is why it is not a result", and because a reader who
    wants the pooled number will otherwise compute it themselves without the
    warning attached.
    """
    buckets = collections.defaultdict(list)
    for row in rows:
        buckets[axis(row)].append(row)
    if len(buckets) < 2:
        return
    print(f"\n{label}  - POOLED ACROSS RUNS, so each value carries the hours, "
          f"targets and engines it happened to run with")
    print(f"  {'':<16}{'n':>5}{'runs':>6}{'served':>8}{'refused':>9}"
          f"{'no reply':>10}{'P(ok)':>8}{'95%':>13}")
    for name in sorted(buckets):
        bucket = buckets[name]
        c = tally(bucket)
        runs = len({r["_run"] for r in bucket})
        print(f"  {str(name)[:16]:<16}{c['n']:>5}{runs:>6}{c['served']:>8}"
              f"{c['refused']:>9}{c['no reply']:>10}{band(c['ok'], c['n'])}")


def ladder(rows, target: str, top: int) -> None:
    """The ranked configurations for one target.

    The key is everything an SDK can flip at call time. Country is in it and
    `sid` is not: one is a decision, the other is the identity that decision is
    spent on.
    """
    def key(row):
        return (engine_base(row), arm(row), mode_of(row), entry_of(row),
                filter_of(row), country_of(row))

    buckets = collections.defaultdict(list)
    for row in rows:
        buckets[key(row)].append(row)

    scored = []
    for name, bucket in buckets.items():
        c = tally(bucket)
        low, _ = wilson(c["ok"], c["n"])
        scored.append((low, c, name, bucket))
    scored.sort(key=lambda s: (-s[0], -s[1]["n"]))

    rankable = [s for s in scored if s[1]["n"] >= RANKABLE]
    print(f"\n{'=' * 100}")
    print(f"{target.upper()}   {len(rows)} attempts, "
          f"{len(buckets)} configurations, "
          f"{len(rankable)} of them with n >= {RANKABLE}")
    print(f"{'=' * 100}")
    if not rankable:
        print("  no configuration reached the floor, so this target has no "
              "ladder - only observations")
        return

    print(f"  {'#':<3}{'engine':<22}{'arm':<7}{'mode':<9}{'entry':<6}"
          f"{'filter':<8}{'country':<8}{'n':>5}{'ok':>4}{'P(ok)':>7}"
          f"{'  95%':<14}{'served':>7}{'refus':>6}{'noRPL':>6}  what to change")
    for i, (low, c, name, _) in enumerate(rankable[:top], 1):
        engine, a, mode, entry, flt, country = name
        # The advice is derived, never typed in per engine. It reads off which
        # bucket the failures fell into, so a target that changes its refusal
        # shape changes the advice with it instead of leaving a stale sentence
        # in a table.
        lost = c["n"] - c["ok"]
        if not lost:
            advice = "nothing failed here"
        elif c["refused"] >= max(c["no reply"], lost - c["refused"]):
            advice = "rotate the exit, keep this config"
        elif c["no reply"] >= lost - c["no reply"]:
            advice = "retry, the CONNECT never landed"
        else:
            advice = "rotate the config, the page arrived"
        print(f"  {i:<3}{engine[:20]:<22}{a:<7}{mode:<9}{entry:<6}"
              f"{flt:<8}{country[:7]:<8}{c['n']:>5}{c['ok']:>4}"
              f"{100 * c['ok'] / c['n']:>6.0f}%"
              f"{100 * low:>7.0f}-{100 * wilson(c['ok'], c['n'])[1]:<6.0f}"
              f"{c['served']:>7}{c['refused']:>6}{c['no reply']:>6}  {advice}")

    windows = {n: len({r["_run"] for r in b}) for _, _, n, b in rankable[:top]}
    if any(w == 1 for w in windows.values()):
        print("  a configuration measured in one run is a reading of that hour. "
              "Google moved 17 points between two afternoons of one experiment")


def hold_multiplier(held) -> None:
    """What one served exit is worth after the first query.

    Kept out of every rate above and printed here instead, because it is a
    conditional quantity and censored twice over: each row follows a probe that
    was served, and a series stops at its first refusal, so position 4 is drawn
    from the identities still alive. Both facts belong next to the number rather
    than in the reader's head, and the number is the reason the ladder above is
    a ladder of how to *find* an exit rather than how to use one.
    """
    if not held:
        return
    print("\n\nWHAT A SERVED EXIT IS WORTH AFTER THE FIRST QUERY")
    print("  conditional on a served probe and censored by the stop rule, so "
          "this multiplies the ladder above rather than competing with it")
    by_position = collections.defaultdict(list)
    for row in held:
        by_position[row.get("position")].append(row)
    print(f"  {'position':<10}{'n':>5}{'ok':>5}{'P(ok)':>8}{'95%':>13}")
    for position in sorted(by_position, key=lambda p: (p is None, p)):
        bucket = by_position[position]
        good = sum(1 for r in bucket if r["verdict"] == "ok")
        print(f"  {'#' + str((position or 0) + 1):<10}{len(bucket):>5}"
              f"{good:>5}{band(good, len(bucket))}")
    good = sum(1 for r in held if r["verdict"] == "ok")
    print(f"  {'all':<10}{len(held):>5}{good:>5}{band(good, len(held))}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default=None,
                        help="one target; the default builds a ladder for each")
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--pooled", action="store_true",
                        help="also print the axes pooled over every run, which "
                             "is the confounded reading and is labelled as one")
    parser.add_argument("--min-attempts", type=int, default=25,
                        help="targets under this get no ladder")
    args = parser.parse_args()

    paths = sorted(RUNS.glob("*.jsonl"))
    if not paths:
        print("no runs on disk")
        return 1
    every = [r for r in load(paths)
             if r.get("engine") and r.get("query") and r.get("target")]
    if not every:
        print("no attempt on disk, so there is nothing to read")
        return 1
    rows = [r for r in every if is_unconditional(r)]
    held = [r for r in every if not is_unconditional(r)]

    print(f"{len(paths)} run(s), {len(every)} attempts with a target on them")
    print(f"{len(rows)} of them unconditional and read below; the other "
          f"{len(held)} are held queries that only exist because a probe was "
          f"served, and they are counted once at the end instead")

    print("\n\nTHE TWO AXES ASKED FOR, EACH WITH THE DENOMINATOR THAT BELONGS "
          "TO IT")
    within_run_axis(
        rows, filter_of, "FILTER",
        "the gateway's exit-quality parameter. Direct rows have no gateway and "
        "are excluded from the varying test rather than counted as `none`")
    within_run_axis(
        rows, lambda r: f"geo-{r.get('geo')}", "GEO ALIGNMENT",
        "the browser timezone set from the exit address. The language list is "
        "deliberately not aligned - every verdict marker is an English string")

    if args.pooled:
        pooled_axis(rows, filter_of, "FILTER")
        pooled_axis(rows, lambda r: f"geo-{r.get('geo')}", "GEO ALIGNMENT")

    print("\n\nENGINE AGAINST TARGET, POOL ARM ONLY, WITH THE DENOMINATOR "
          "SPLIT")
    print("`ok|served` is the only conditional worth quoting and `no status` "
          "is the warning that it is unavailable")
    pool = [r for r in rows if not r.get("direct")]
    engines = sorted({engine_base(r) for r in pool})
    targets = sorted({r["target"] for r in pool})
    for target in targets:
        print(f"\n  {target}")
        print(f"    {'':<14}{'n':>5}{'served':>8}{'refused':>9}{'no reply':>10}"
              f"{'no status':>11}{'ok':>5}{'  ok|served':<12}")
        for engine in engines:
            bucket = [r for r in pool
                      if r["target"] == target and engine_base(r) == engine]
            if not bucket:
                continue
            c = tally(bucket)
            served_ok = sum(1 for r in bucket
                            if classify(r) == "served" and r["verdict"] == "ok")
            cond = (f"{served_ok}/{c['served']}" if c["served"]
                    else ("blind" if c["no status"] else "-"))
            print(f"    {engine[:13]:<14}{c['n']:>5}{c['served']:>8}"
                  f"{c['refused']:>9}{c['no reply']:>10}{c['no status']:>11}"
                  f"{c['ok']:>5}  {cond:<12}")

    print("\n\nTHE LADDER - ordered by the lower bound, so rank is bought with "
          "attempts")
    chosen = ([args.target] if args.target
              else sorted({r["target"] for r in rows}))
    for target in chosen:
        bucket = [r for r in rows if r["target"] == target]
        if len(bucket) < args.min_attempts:
            print(f"\n{target}: {len(bucket)} attempts, under the floor of "
                  f"{args.min_attempts}, so no ladder")
            continue
        ladder(bucket, target, args.top)

    hold_multiplier(held)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
