"""Correction to `ssl_repeat_origin.py`: hold the host fixed before comparing.

2026-09-01. The previous pass reported 4.6% for a new origin against 13.6% for
a revisited one, pooled over every origin, with a Fisher p of 6e-12. That
comparison is confounded and the number should not be used.

`ctx+/org+` is 1224 of 1254 `www.google.com`, because `www.google.com` is the
only origin the ladder visits more than once per context. `ctx+/org0` is mostly
scholar, translate, theverge and wikihow, which have a combined SSL rate near
zero. So the contrast measured "google versus not google" and reported it as
"repeat versus new". The identical mistake is already recorded one level up in
`nmbench/breaker.py` - a test whose two arms were the same partition of the
data, written up as though it had discriminated.

This pass restricts to `www.google.com` and compares only within it.
"""
import collections
import json
import math
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]

RUNS = [
    ("run 1  free wi-fi", "probehold_20260828T213141Z.jsonl"),
    ("run 2  free wi-fi", "probehold_20260831T222129Z.jsonl"),
    ("run 3  secured 5G", "probehold_20260831T084544Z.jsonl"),
    ("aug 26", "probehold_20260826T152748Z.jsonl"),
    ("aug 27", "probehold_20260827T201123Z.jsonl"),
    ("aug 28am", "probehold_20260828T085727Z.jsonl"),
]
HOST = "www.google.com"

K1 = "1. context's first navigation"
K2 = "2. later, but first visit to this origin"
K3 = "3. later, and origin already visited"


def load(name):
    p = ROOT / "data" / "runs" / name
    if not p.exists():
        return None
    out = []
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def is_ssl(r):
    return "ERR_SSL_PROTOCOL_ERROR" in (r.get("error") or "")


def origin(r):
    u = r.get("url")
    return u.split("//", 1)[-1].split("/", 1)[0] if u else None


def fisher(a, b, c, d):
    lf = lambda n: math.lgamma(n + 1)  # noqa: E731

    def lp(a, b, c, d):
        n = a + b + c + d
        return (lf(a + b) + lf(c + d) + lf(a + c) + lf(b + d)
                - lf(n) - lf(a) - lf(b) - lf(c) - lf(d))
    n, obs = a + b + c + d, lp(a, b, c, d)
    r1, c1 = a + b, a + c
    tot = 0.0
    for x in range(max(0, c1 - (c + d)), min(r1, c1) + 1):
        v = lp(x, r1 - x, c1 - x, n - r1 - c1 + x)
        if v <= obs + 1e-9:
            tot += math.exp(v)
    return min(tot, 1.0)


cells = collections.defaultdict(lambda: [0, 0])
per_run = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
other = collections.defaultdict(lambda: [0, 0])

for label, name in RUNS:
    rows = load(name)
    if rows is None:
        continue
    seen = collections.defaultdict(set)
    count = collections.defaultdict(int)
    for r in rows:
        o = origin(r)
        if o is None:
            continue
        ident = r.get("identity")
        i = count[ident]
        first_here = o not in seen[ident]
        count[ident] += 1
        seen[ident].add(o)
        if i == 0:
            key = K1
        elif first_here:
            key = K2
        else:
            key = K3
        bucket = cells if o == HOST else other
        bucket[key][1] += 1
        if is_ssl(r):
            bucket[key][0] += 1
        if o == HOST:
            per_run[label][key][1] += 1
            if is_ssl(r):
                per_run[label][key][0] += 1

print("=" * 78)
print(f"A. {HOST} navigations only, pooled over six runs")
print("=" * 78)
for k in sorted(cells):
    s, n = cells[k]
    print(f"  {k:<44} {s:>5} / {n:<6} {100*s/n if n else 0:5.1f}%")
a, na = cells[K2]
b, nb = cells[K3]
print(f"\n  Within {HOST}, does a repeat visit differ from a first visit?")
print(f"  {a}/{na} vs {b}/{nb}   Fisher p = {fisher(a, na - a, b, nb - b):.3f}")
print("  The previous pass reported this contrast as 6e-12 by pooling hosts.")

print()
print("=" * 78)
print(f"B. Every origin except {HOST}, same three buckets")
print("=" * 78)
for k in sorted(other):
    s, n = other[k]
    print(f"  {k:<44} {s:>5} / {n:<6} {100*s/n if n else 0:5.1f}%")

print()
print("=" * 78)
print(f"C. {HOST} per run, so the six are not silently pooled")
print("=" * 78)
print(f"  {'run':<20}{'ctx first':>14}{'later, new':>14}{'later, repeat':>16}")
for label, _ in RUNS:
    d = per_run[label]

    def fmt(k, d=d):
        s, n = d[k]
        return f"{s}/{n}={100*s/n:.0f}%" if n else "-"
    # Python concatenates adjacent string literals, so writing this key with a
    # doubled apostrophe silently looked up a name with no apostrophe at all
    # and printed "-" for a cell holding 272 navigations. Keys are constants
    # now for that reason.
    print(f"  {label:<20}{fmt(K1):>14}{fmt(K2):>14}{fmt(K3):>16}")

print()
print("=" * 78)
print("D. The one claim that survives every cut")
print("=" * 78)
tot_s, tot_n = 0, 0
for _label, name in RUNS:
    rows = load(name)
    if rows is None:
        continue
    count = collections.defaultdict(int)
    for r in rows:
        if origin(r) is None:
            continue
        ident = r.get("identity")
        if count[ident] == 0:
            tot_n += 1
            if is_ssl(r):
                tot_s += 1
        count[ident] += 1
print("  SSL failures on the first navigation of a browser context, over all")
print(f"  six runs, every origin, warm and probe alike: {tot_s} of {tot_n}.")
