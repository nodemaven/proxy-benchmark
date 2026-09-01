"""Fourth pass: is it the same failure, and does it fall evenly across the arms.

2026-09-01. Passes 1-3 established that the SSL rate differs across the three
ladder runs by about 9x stratified on navigation index, and that the obvious
"the 5G run was too short" objection goes the wrong way. Two things left that
change what to do about it:

  - Timing. The note in `nmbench/breaker.py` records a median of 90 ms and a
    64-209 ms range over the three August runs. If the free-wi-fi failures have
    the same signature it is one phenomenon; if they are slow, it is two and
    the earlier note describes something else.
  - Arm balance. The ladder compares warm levels against each other. If the
    failures land unevenly on the rungs, they are not merely lost attempts -
    they bias the comparison the run exists to make, and the yields cannot be
    read across runs on different networks.
"""
import collections
import json
import pathlib
import statistics

ROOT = pathlib.Path(__file__).resolve().parents[2]

RUNS = [
    ("run 1  free wi-fi", "probehold_20260828T213141Z.jsonl"),
    ("run 3  secured 5G", "probehold_20260831T084544Z.jsonl"),
    ("run 2  free wi-fi", "probehold_20260831T222129Z.jsonl"),
    ("aug 26  network ?", "probehold_20260826T152748Z.jsonl"),
    ("aug 27  network ?", "probehold_20260827T201123Z.jsonl"),
    ("aug 28am network ?", "probehold_20260828T085727Z.jsonl"),
]


def load(name):
    p = ROOT / "data" / "runs" / name
    if not p.exists():
        return None
    rows = []
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def is_ssl(r):
    return "ERR_SSL_PROTOCOL_ERROR" in (r.get("error") or "")


print("=" * 76)
print("A. Timing signature of the SSL failures, ms")
print("=" * 76)
for label, name in RUNS:
    rows = load(name)
    if rows is None:
        print(f"  {label:<20} file absent")
        continue
    times = sorted(r["elapsed_ms"] for r in rows
                   if is_ssl(r) and r.get("elapsed_ms") is not None)
    if not times:
        print(f"  {label:<20} no SSL rows")
        continue
    print(f"  {label:<20} n={len(times):<4} median {statistics.median(times):>6.0f}"
          f"   min {times[0]:>5}  p90 {times[int(0.9*(len(times)-1))]:>6}"
          f"  max {times[-1]:>7}")

print()
print("=" * 76)
print("B. SSL rate by warm level - does it bias the arms the run compares")
print("=" * 76)
for label, name in RUNS[:3]:
    rows = load(name)
    per = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        lvl = r.get("warm_level")
        if lvl is None:
            continue
        per[lvl][1] += 1
        if is_ssl(r):
            per[lvl][0] += 1
    line = "   ".join(f"{lvl}:{v[0]}/{v[1]}={100*v[0]/v[1]:.0f}%"
                      for lvl, v in sorted(per.items()))
    print(f"  {label:<20} {line}")

print()
print("=" * 76)
print("C. Same, restricted to probe rows - the ones a yield is computed from")
print("=" * 76)
for label, name in RUNS[:3]:
    rows = load(name)
    per = collections.defaultdict(lambda: [0, 0, 0])
    for r in rows:
        if r.get("phase") != "probe":
            continue
        lvl = r.get("warm_level")
        per[lvl][1] += 1
        if is_ssl(r):
            per[lvl][0] += 1
        if r.get("verdict") == "ok":
            per[lvl][2] += 1
    line = "   ".join(
        f"{lvl}: ssl {v[0]}/{v[1]}  ok {v[2]}"
        for lvl, v in sorted(per.items(), key=lambda kv: str(kv[0])))
    print(f"  {label:<20} {line}")

print()
print("=" * 76)
print("D. Do warm navigations fail more than probe navigations")
print("=" * 76)
print("  A warm page and a probe page are the same navigation through the same")
print("  tunnel, so a difference here is about position and not about kind.")
for label, name in RUNS[:3]:
    rows = load(name)
    per = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        ph = r.get("phase") or "?"
        per[ph][1] += 1
        if is_ssl(r):
            per[ph][0] += 1
    line = "   ".join(f"{ph}: {v[0]}/{v[1]} = {100*v[0]/v[1]:.1f}%"
                      for ph, v in sorted(per.items()))
    print(f"  {label:<20} {line}")

print()
print("=" * 76)
print("E. Does a run record anything at all about the near end of the path")
print("=" * 76)
rows = load(RUNS[0][1])
keys = sorted(rows[0].keys())
print(f"  columns on a row: {len(keys)}")
print(f"  {', '.join(keys)}")
near = [k for k in keys if any(w in k for w in
                               ("client", "local", "host", "net", "wifi", "iface"))]
print(f"\n  columns describing the client side: {near or 'none'}")
