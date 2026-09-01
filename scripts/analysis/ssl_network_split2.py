"""Is the SSL rate difference across the three ladder runs separable from time.

Second pass, 2026-09-01. The first pass established the rates differ. This one
asks whether anything other than the operator's stated network could produce
them, because three runs is three points and the runs differ in more than one
way at once:

  run 1  20260828T213141Z  free public wi-fi   21:32-04:09 UTC   old 6-page L3
  run 3  20260831T084544Z  secured 5G          08:45-10:06 UTC   new 7-page L3
  run 2  20260831T222129Z  free public wi-fi   22:21-05:46 UTC   new 7-page L3

Note the calendar order: free, 5G, free. That alternation is what makes this
worth testing at all - a monotone improvement over three runs would have been
indistinguishable from a trend.

Two confounds are visible without any arithmetic and are not removable from
these three runs:

  - the 5G run is the only daytime one, so time-of-day rides with the network;
  - it is also the shortest by a factor of five, so it never reaches the hours
    where the other two accumulate most of their failures.

The second is testable here: truncate the long runs to the 5G run's own
duration and per-cell depth and re-compare.
"""
import collections
import datetime as dt
import json
import math
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]

RUNS = [
    ("run 1  free wi-fi", "probehold_20260828T213141Z.jsonl"),
    ("run 3  secured 5G", "probehold_20260831T084544Z.jsonl"),
    ("run 2  free wi-fi", "probehold_20260831T222129Z.jsonl"),
]


def load(name):
    path = ROOT / "data" / "runs" / name
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def is_ssl(row):
    return "ERR_SSL_PROTOCOL_ERROR" in (row.get("error") or "")


def host_of(url):
    if not url:
        return None
    return url.split("//", 1)[-1].split("/", 1)[0]


def ts(row):
    return dt.datetime.fromisoformat(row["ts"])


def fisher(a, b, c, d):
    """Two-sided Fisher exact on [[a,b],[c,d]]. Small tables, exact is cheap."""
    def logfact(n):
        return math.lgamma(n + 1)

    def logp(a, b, c, d):
        n = a + b + c + d
        return (logfact(a + b) + logfact(c + d) + logfact(a + c)
                + logfact(b + d) - logfact(n) - logfact(a) - logfact(b)
                - logfact(c) - logfact(d))

    n = a + b + c + d
    p_obs = logp(a, b, c, d)
    total = 0.0
    row1, col1 = a + b, a + c
    lo = max(0, col1 - (c + d))
    hi = min(row1, col1)
    for x in range(lo, hi + 1):
        y, z, w = row1 - x, col1 - x, n - row1 - col1 + x
        lp = logp(x, y, z, w)
        if lp <= p_obs + 1e-9:
            total += math.exp(lp)
    return min(total, 1.0)


def google_nav_stats(rows):
    """SSL count and denominator over www.google.com navigations only.

    Restricted to the one host that carries the failure, so a run that spent a
    different share of its navigations on other origins is not credited or
    penalised for it.
    """
    navs = [r for r in rows if host_of(r.get("url")) == "www.google.com"]
    return sum(1 for r in navs if is_ssl(r)), len(navs)


print("=" * 74)
print("1. www.google.com navigations only, whole run")
print("=" * 74)
stats = {}
for label, name in RUNS:
    rows = load(name)
    s, n = google_nav_stats(rows)
    stats[label] = (s, n, rows)
    print(f"  {label:<20} {s:>4} / {n:<5} {100 * s / n:5.1f}%")

s5, n5, _ = stats["run 3  secured 5G"]
for label in ("run 1  free wi-fi", "run 2  free wi-fi"):
    s, n, _ = stats[label]
    p = fisher(s, n - s, s5, n5 - s5)
    print(f"  {label} vs 5G: Fisher two-sided p = {p:.2e}")

print()
print("=" * 74)
print("2. Truncated to the 5G run's own length, so depth is not the difference")
print("=" * 74)
# The 5G run reached 170 rows in 81 minutes. Cut the other two at the same
# elapsed time from their own first row, and separately at the same row count.
_, _, rows5 = stats["run 3  secured 5G"]
minutes5 = (ts(rows5[-1]) - ts(rows5[0])).total_seconds() / 60
rows5_n = len(rows5)
print(f"  5G run: {rows5_n} rows over {minutes5:.0f} minutes")
print()
for label, _name in RUNS:
    _, _, rows = stats[label]
    t0 = ts(rows[0])
    by_time = [r for r in rows
               if (ts(r) - t0).total_seconds() / 60 <= minutes5]
    by_count = rows[:rows5_n]
    for kind, subset in (("first 81 min", by_time), ("first 170 rows", by_count)):
        navs = [r for r in subset if host_of(r.get("url")) == "www.google.com"]
        s = sum(1 for r in navs if is_ssl(r))
        n = len(navs) or 1
        print(f"  {label:<20} {kind:<15} {s:>4} / {n:<5} {100 * s / n:5.1f}%")
    print()

print("=" * 74)
print("3. Within-run time course, hourly, www.google.com only")
print("=" * 74)
for label, _name in RUNS:
    _, _, rows = stats[label]
    t0 = ts(rows[0])
    buckets = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        if host_of(r.get("url")) != "www.google.com":
            continue
        h = int((ts(r) - t0).total_seconds() // 3600)
        buckets[h][1] += 1
        if is_ssl(r):
            buckets[h][0] += 1
    line = "  ".join(
        f"h{h}:{b[0]}/{b[1]}" for h, b in sorted(buckets.items()))
    print(f"  {label:<20} {line}")

print()
print("=" * 74)
print("4. Rows with no url - what are they")
print("=" * 74)
for label, _name in RUNS:
    _, _, rows = stats[label]
    noname = [r for r in rows if not r.get("url")]
    kinds = collections.Counter(
        (r.get("verdict"), r.get("phase")) for r in noname)
    print(f"  {label:<20} {len(noname)} rows  {dict(kinds)}")

print()
print("=" * 74)
print("5. Exit country and ASN mix, in case the pools differ across runs")
print("=" * 74)
for label, _name in RUNS:
    _, _, rows = stats[label]
    countries = collections.Counter(
        r.get("exit_country") for r in rows if r.get("exit_country"))
    ident = {r.get("identity") for r in rows}
    exits = {r.get("exit_prefix") for r in rows if r.get("exit_prefix")}
    print(f"  {label:<20} identities {len(ident):<4} exits {len(exits):<4} "
          f"top countries {dict(countries.most_common(6))}")

print()
print("=" * 74)
print("6. Does SSL cluster by exit, once sessions are held apart")
print("=" * 74)
for label, _name in RUNS:
    _, _, rows = stats[label]
    per_exit = collections.defaultdict(set)
    for r in rows:
        if r.get("exit_prefix"):
            per_exit[r["exit_prefix"]].add(r.get("identity"))
    shared = sum(1 for v in per_exit.values() if len(v) > 1)
    print(f"  {label:<20} exits seen by >1 identity: {shared} of {len(per_exit)}")
