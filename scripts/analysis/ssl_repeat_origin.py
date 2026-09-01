"""Is the SSL failure about position in the context, or about revisiting an origin.

2026-09-01. `nmbench/breaker.py` currently records the invariant as "0 of 198
first navigations of a browser context failed". That is true and it has now
replicated over three more runs. But pass 4 turned up something the invariant
does not explain: in run 1 the N1 and L1 rungs are both two pages deep and they
took 38% and 18%.

The two rungs differ in one way. L1 is [imghp, home] and both pages are
`www.google.com`, so the context's first navigation is already to that origin.
N1 is [theverge, home], so the first `www.google.com` navigation is the second
navigation of the context.

Under "the first navigation of a context is safe" those two should be equal.
Under "the first navigation to a given origin is safe" N1's should be safe too,
and it is the worst rung in the run - so that reading is refuted outright.

This asks the remaining version: is a navigation at risk exactly when the
context has already been to that origin before. If so the invariant is about
per-origin state and not about context age, which is a different thing to look
for and a different thing to work around.
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


print("=" * 78)
print("A. Cross-tabulate: first-of-context vs first-to-this-origin")
print("=" * 78)
print("  Each navigation is classified twice. `ctx0` is the first navigation")
print("  the context makes at all; `org0` is the first it makes to that origin.")
print()
print(f"  {'run':<20}{'ctx0 & org0':>14}{'ctx>0 & org0':>14}"
      f"{'ctx>0 & org>0':>16}")
grand = collections.defaultdict(lambda: [0, 0])
for label, name in RUNS:
    rows = load(name)
    if rows is None:
        continue
    seen = collections.defaultdict(set)
    count = collections.defaultdict(int)
    cells = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        o = origin(r)
        if o is None:
            continue
        ident = r.get("identity")
        ctx0 = count[ident] == 0
        org0 = o not in seen[ident]
        count[ident] += 1
        seen[ident].add(o)
        key = ("ctx0" if ctx0 else "ctx+") + "/" + ("org0" if org0 else "org+")
        cells[key][1] += 1
        grand[key][1] += 1
        if is_ssl(r):
            cells[key][0] += 1
            grand[key][0] += 1

    def fmt(k, cells=cells):
        s, n = cells[k]
        return f"{s}/{n}={100*s/n:.0f}%" if n else "-"
    print(f"  {label:<20}{fmt('ctx0/org0'):>14}{fmt('ctx+/org0'):>14}"
          f"{fmt('ctx+/org+'):>16}")

print()
print("  pooled over all six runs:")
for k in ("ctx0/org0", "ctx+/org0", "ctx+/org+", "ctx0/org+"):
    s, n = grand[k]
    if n:
        print(f"    {k:<12} {s:>5} / {n:<6} {100*s/n:5.1f}%")

a, na = grand["ctx+/org0"]
b, nb = grand["ctx+/org+"]
print("\n  Among navigations that are NOT the context's first, does having been")
print(f"  to the origin before matter?  {a}/{na} vs {b}/{nb}, "
      f"Fisher p = {fisher(a, na - a, b, nb - b):.2e}")

print()
print("=" * 78)
print("B. The N1 / L1 comparison that raised the question, run 1")
print("=" * 78)
rows = load(RUNS[0][1])
for lvl in ("L1", "N1"):
    seen = collections.defaultdict(set)
    count = collections.defaultdict(int)
    per = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        o = origin(r)
        if o is None:
            continue
        ident = r.get("identity")
        i, first = count[ident], o not in seen[ident]
        count[ident] += 1
        seen[ident].add(o)
        if r.get("warm_level") != lvl:
            continue
        key = f"nav{min(i, 3)} {'new' if first else 'repeat'} {o[:22]}"
        per[key][1] += 1
        if is_ssl(r):
            per[key][0] += 1
    print(f"  {lvl}:")
    for k, v in sorted(per.items()):
        print(f"    {k:<40} {v[0]:>3}/{v[1]:<4} {100*v[0]/v[1]:5.1f}%")

print()
print("=" * 78)
print("C. Same-origin repeats on hosts other than www.google.com")
print("=" * 78)
print("  If revisiting an origin is what matters, other origins should fail")
print("  when they are revisited too. Do any of them ever get revisited?")
pooled = collections.defaultdict(lambda: [0, 0])
for _label, name in RUNS:
    rows = load(name)
    if rows is None:
        continue
    seen = collections.defaultdict(set)
    for r in rows:
        o = origin(r)
        if o is None:
            continue
        ident = r.get("identity")
        repeat = o in seen[ident]
        seen[ident].add(o)
        if repeat:
            pooled[o][1] += 1
            if is_ssl(r):
                pooled[o][0] += 1
for o, v in sorted(pooled.items(), key=lambda kv: -kv[1][1]):
    print(f"    {o:<30} repeats {v[1]:>5}   ssl {v[0]:>4}  "
          f"{100*v[0]/v[1]:5.1f}%")
