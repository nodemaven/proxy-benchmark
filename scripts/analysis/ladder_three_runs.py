"""What the three ladder runs say about warm-up, with the transport noise out.

2026-09-01. Three runs of very unequal length, two of them stopped by hand:

  run 1  20260828T213141Z  free public wi-fi  ran to completion, all cells tripped
  run 3  20260831T084544Z  secured 5G         stopped, ~6 identities per cell
  run 2  20260831T222129Z  free public wi-fi  stopped, ~33 identities per cell

A yield is `ok / judged`, and `judged` deliberately excludes attempts that
failed below the application layer - those carry no answer from the target. The
question this file exists to answer is whether, after that exclusion, the three
runs agree about the ladder, or whether the run with the clean near-end says
something different from the two without it.

Also prints a Wilson interval per rung. Without one, a 6-probe cell reading
83% and a 38-probe cell reading 71% look like a finding, and they are the same
number.
"""
import collections
import json
import math
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]

RUNS = [
    ("run 1  free wi-fi  complete", "probehold_20260828T213141Z.jsonl"),
    ("run 3  secured 5G  stopped", "probehold_20260831T084544Z.jsonl"),
    ("run 2  free wi-fi  stopped", "probehold_20260831T222129Z.jsonl"),
]
JUDGED = {"ok", "block", "captcha", "empty"}
RUNGS = ["L0", "L1", "N1", "L2", "L3"]


def load(name):
    out = []
    with open(ROOT / "data" / "runs" / name, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def wilson(k, n, z=1.96):
    if not n:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


print("=" * 78)
print("Yield per rung. `judged` excludes transport failures by construction.")
print("=" * 78)
table = {}
for label, name in RUNS:
    rows = load(name)
    per = collections.defaultdict(lambda: collections.Counter())
    for r in rows:
        if r.get("phase") != "probe":
            continue
        per[r.get("warm_level")][r.get("verdict")] += 1
    print(f"\n  {label}")
    print(f"    {'rung':<6}{'probes':>8}{'judged':>8}{'ok':>5}"
          f"{'yield':>8}   95% interval")
    row = {}
    for rung in RUNGS:
        c = per.get(rung)
        if not c:
            continue
        probes = sum(c.values())
        judged = sum(v for k, v in c.items() if k in JUDGED)
        ok = c["ok"]
        lo, hi = wilson(ok, judged)
        y = 100 * ok / judged if judged else 0
        row[rung] = (ok, judged, y, lo, hi)
        print(f"    {rung:<6}{probes:>8}{judged:>8}{ok:>5}{y:>7.0f}%"
              f"   {100*lo:5.0f} - {100*hi:<5.0f}")
    table[label] = row

print()
print("=" * 78)
print("Do the three runs even agree on the ordering of the rungs?")
print("=" * 78)
for label, _ in RUNS:
    row = table[label]
    order = sorted(row, key=lambda r: -row[r][2])
    print(f"  {label:<30} " + " > ".join(
        f"{r}({row[r][2]:.0f}%)" for r in order))

print()
print("=" * 78)
print("Widest 95% interval in each run - the resolution the run actually had")
print("=" * 78)
for label, _ in RUNS:
    row = table[label]
    if not row:
        continue
    widest = max(row, key=lambda r: row[r][4] - row[r][3])
    lo, hi = row[widest][3], row[widest][4]
    print(f"  {label:<30} {widest} spans {100*(hi-lo):.0f} points "
          f"({100*lo:.0f} - {100*hi:.0f}) on {row[widest][1]} judged")

print()
print("=" * 78)
print("Attempts lost below the application layer, per rung")
print("=" * 78)
print("  These are not evidence about Google. They are the cost of the run.")
for label, name in RUNS:
    rows = load(name)
    per = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        lvl = r.get("warm_level")
        if lvl is None:
            continue
        per[lvl][1] += 1
        if r.get("transport_failure"):
            per[lvl][0] += 1
    tot = [sum(v[0] for v in per.values()), sum(v[1] for v in per.values())]
    line = "  ".join(f"{r}:{per[r][0]}/{per[r][1]}" for r in RUNGS if r in per)
    print(f"  {label:<30} {line}   total {tot[0]}/{tot[1]} "
          f"= {100*tot[0]/tot[1]:.0f}%")
