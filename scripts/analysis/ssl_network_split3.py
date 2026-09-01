"""Third pass: strip the confounds one at a time from the SSL-rate difference.

2026-09-01. Pass 2 found that truncating the two free-wi-fi runs to the 5G
run's own 81 minutes makes the gap wider, not narrower - 23.8% and 18.2%
against 1.6% - so "the 5G run was too short to reach the bad part" is refuted
by the data rather than left open. This pass takes the remaining candidates:

  - navigation index inside the session, which the ladder change to a 7-page L3
    shifts, and which the SSL rate rises with;
  - wall-clock hour, since the 5G run is the only daytime one;
  - engine, exit pool, and the rest of the error profile.

Each is either matched or reported so a reader can see it was not matched.
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
    rows = []
    with open(ROOT / "data" / "runs" / name, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def is_ssl(r):
    return "ERR_SSL_PROTOCOL_ERROR" in (r.get("error") or "")


def host_of(url):
    return url.split("//", 1)[-1].split("/", 1)[0] if url else None


def ts(r):
    return dt.datetime.fromisoformat(r["ts"])


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


DATA = {label: load(name) for label, name in RUNS}


def google_navs(rows):
    return [r for r in rows if host_of(r.get("url")) == "www.google.com"]


def nav_index(rows):
    """Index of each row within its identity, i.e. within one browser context."""
    seq = collections.defaultdict(int)
    out = {}
    for i, r in enumerate(rows):
        ident = r.get("identity")
        out[i] = seq[ident]
        seq[ident] += 1
    return out


print("=" * 76)
print("A. Matched length: first 81 minutes, www.google.com, run 2 vs run 3")
print("=" * 76)
five_g = DATA["run 3  secured 5G"]
mins = (ts(five_g[-1]) - ts(five_g[0])).total_seconds() / 60
s5 = sum(1 for r in google_navs(five_g) if is_ssl(r))
n5 = len(google_navs(five_g))
for label in ("run 1  free wi-fi", "run 2  free wi-fi"):
    rows = DATA[label]
    t0 = ts(rows[0])
    cut = [r for r in rows if (ts(r) - t0).total_seconds() / 60 <= mins]
    g = google_navs(cut)
    s, n = sum(1 for r in g if is_ssl(r)), len(g)
    p = fisher(s, n - s, s5, n5 - s5)
    print(f"  {label}  {s}/{n} = {100*s/n:.1f}%   vs 5G {s5}/{n5} = "
          f"{100*s5/n5:.1f}%   Fisher p = {p:.2e}")

print()
print("=" * 76)
print("B. Matched on navigation index inside the session")
print("=" * 76)
print("  The ladder gained a 7th L3 page between run 1 and runs 2/3, which")
print("  moves www.google.com later in the session, and the rate rises with")
print("  index - so the change pushes runs 2/3 up, against the effect seen.")
print()
strata = {}
for label, _ in RUNS:
    rows = DATA[label]
    idx = nav_index(rows)
    per = collections.defaultdict(lambda: [0, 0])
    for i, r in enumerate(rows):
        if host_of(r.get("url")) != "www.google.com":
            continue
        k = min(idx[i], 5)
        per[k][1] += 1
        if is_ssl(r):
            per[k][0] += 1
    strata[label] = per
hdr = "  nav      " + "".join(f"{lab:>22}" for lab, _ in RUNS)
print(hdr)
for k in range(6):
    cells = ""
    for lab, _ in RUNS:
        s, n = strata[lab][k]
        cells += f"{s:>8}/{n:<4}{100*s/n if n else 0:5.1f}%" if n else f"{'-':>22}"
    print(f"  {'>=5' if k == 5 else k:<8} {cells}")

# Mantel-Haenszel over the index strata, free wi-fi pooled vs 5G.
num = den = 0.0
for k in range(6):
    a, n1 = strata["run 1  free wi-fi"][k]
    a2, n2 = strata["run 2  free wi-fi"][k]
    a, n1 = a + a2, n1 + n2
    c, n0 = strata["run 3  secured 5G"][k]
    b, d = n1 - a, n0 - c
    t = n1 + n0
    if t:
        num += a * d / t
        den += b * c / t
print(f"\n  Mantel-Haenszel odds ratio, free wi-fi vs 5G, stratified on nav "
      f"index: {num / den if den else float('inf'):.2f}"
      f"{'  (infinite: no discordant pair)' if not den else ''}")

print()
print("=" * 76)
print("C. Wall-clock hour, www.google.com only")
print("=" * 76)
for label, _ in RUNS:
    per = collections.defaultdict(lambda: [0, 0])
    for r in google_navs(DATA[label]):
        h = ts(r).hour
        per[h][1] += 1
        if is_ssl(r):
            per[h][0] += 1
    line = "  ".join(f"{h:02d}h:{v[0]}/{v[1]}" for h, v in sorted(per.items()))
    print(f"  {label:<20} {line}")

print()
print("=" * 76)
print("D. Engine, and the rest of the error profile")
print("=" * 76)
for label, _ in RUNS:
    rows = DATA[label]
    per = collections.defaultdict(lambda: [0, 0])
    for r in google_navs(rows):
        e = (r.get("engine") or "?").split("/")[0]
        per[e][1] += 1
        if is_ssl(r):
            per[e][0] += 1
    eng = "  ".join(f"{e}:{v[0]}/{v[1]}" for e, v in sorted(per.items()))
    errs = collections.Counter()
    for r in rows:
        e = r.get("error")
        if not e:
            continue
        for marker in ("ERR_SSL_PROTOCOL_ERROR", "ERR_TUNNEL_CONNECTION_FAILED",
                       "ERR_EMPTY_RESPONSE", "Timeout", "ERR_CONNECTION",
                       "ERR_PROXY", "press:"):
            if marker in e:
                errs[marker] += 1
                break
        else:
            errs["(other)"] += 1
    print(f"  {label:<20} {eng}")
    print(f"  {'':<20} errors {dict(errs.most_common())} "
          f"of {len(rows)} rows")

print()
print("=" * 76)
print("E. What the runs measured, for context - these are not matched")
print("=" * 76)
for label, _ in RUNS:
    rows = DATA[label]
    v = collections.Counter(r.get("verdict") for r in rows)
    judged = v["ok"] + v["block"] + v["captcha"] + v["empty"]
    print(f"  {label:<20} ok {v['ok']:<4} captcha {v['captcha']:<4} "
          f"judged {judged:<4} yield "
          f"{100*v['ok']/judged if judged else 0:.0f}%   "
          f"identities {len({r.get('identity') for r in rows})}")
