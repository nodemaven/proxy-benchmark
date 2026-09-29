"""Does the `filter` gateway parameter buy a better slice of the pool?

Each run file is a stratum, not a replicate, and that is the whole design.

The question was asked twice as a single long run and both times the answer was
spoiled by the same mechanism. The circuit breaker stops a cell after six
consecutive failures, so a cell that passes more often survives longer - which
means `n` is an outcome variable and the luckiest arm is also the best measured
one. Worse, when one cell stops the others keep going, so the interleaving that
was supposed to hold the hour fixed dissolves partway through: on 2026-09-17 the
arms lived 0-26.5, 1.9-77.1 and 3.0-121.1 minutes into the run. The hour is the
largest effect in NOTEBOOK.md, so an arm that outlives another is being measured
at a different time of day.

The fix is not a bigger run, it is many short ones. Since 2026-09-18 the OVH
Windows VPS fires `run_filter.ps1` three times a day at 02:00, 10:00 and 18:00
local, capped at 25 identities a cell. Inside one short session the arms cannot
drift far apart; across sessions the hour varies on purpose. Cochran-Mantel-
Haenszel then pools the sessions while holding the session fixed, which is what
turns the hour from a confounder into a stratum.

Two things this file prints that are not the answer and are not decoration:

  Countries per arm. `--countries any` is the user's call, made 2026-09-18
  against the recommendation on this side. On 2026-09-17 the three arms drew 12,
  26 and 32 distinct countries over 16, 39 and 66 probes, so part of any
  difference between arms is a difference between baskets of countries. This
  stays uncontrolled, so it gets printed every time rather than remembered.

  Probes per arm per stratum. If one arm consistently carries the larger n, that
  is the breaker asymmetry showing, and it is also - weakly - evidence for the
  effect, since surviving the breaker longer means failing less. It is reported
  as a separate line rather than folded into the estimate.

The probe phase is the signal. A probe is the first contact of a fresh exit; a
hold is a repeat visit on an address the target has already served, and on
2026-08-13 every arm held 100%, so holds carry no information about the pool and
only cost 5.37 MB apiece. They are counted here anyway, because the day they
stop reading 100% is the day that assumption expires.

Usage:
    python scripts/analysis/filter_arms.py [run.jsonl ...]

With no arguments it takes every probehold file in data/runs that contains more
than one distinct `params-` arm.
"""
import collections
import datetime
import json
import math
import pathlib
import sys


def _parse(ts):
    return datetime.datetime.fromisoformat(ts)

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNS = ROOT / "data" / "runs"

# `judged` excludes anything that failed below the application layer: a
# transport failure carries no answer from the target, so counting it as a
# failure would charge the arm for the network. Same convention as
# ladder_three_runs.py.
#
# `throttle` was split out of `block` on 2026-09-28 and has to be listed here
# explicitly, or the split silently shrinks the denominator: rows the target
# answered would stop being counted and the pass rate of every arm would rise
# for no reason anybody could see in the output. That is the whole hazard of
# renaming a verdict, and it lands on set membership rather than on any rule.
JUDGED = {"ok", "block", "throttle", "captcha", "empty"}

ARM_ORDER = ["none", "filter=medium", "filter=high"]


def load(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def arm_of(row):
    """The arm label, read off `cell` rather than off `params`.

    `params` holds the resolved dict, which also carries the per-identity `sid`,
    so it is unique per identity and useless as a group key. `cell` carries the
    axis label the runner assigned.

    The runner sanitises `=` to `-` on its way into the cell name, so the arm
    passed on the command line as `filter=medium` is written `params-filter-medium`.
    It is put back here so that what this file prints is what you would type,
    and so that the check below - that `filter` really did reach the gateway -
    can be made against the parameter name rather than against the label.
    """
    for part in str(row.get("cell", "")).split("/"):
        if part.startswith("params-"):
            label = part[len("params-"):]
            return label.replace("filter-", "filter=", 1)
    return None


def check_params_reached(strata):
    """Did `filter` actually get sent on the arms that are named for it?

    Worth a check and not an assumption: this gateway answers an unrecognised
    parameter with 200 and silently drops it - measured in nodemaven.toml - so
    an arm that never sent `filter` is indistinguishable from one that sent it
    and got nothing. The label lives in the cell name and the parameter lives in
    `params`, and nothing in the runner guarantees they agree.
    """
    bad = []
    for name, arms in strata:
        for arm, rows in arms.items():
            want = arm.split("=")[1] if "=" in arm else None
            for r in rows:
                # Rows with no phase are bookkeeping and carry no params:
                # `cell_stopped` is the breaker tripping, `identity_redrawn` is
                # an identity thrown away after a tunnel failure. Neither is an
                # attempt and neither sent anything to the target.
                if r.get("phase") not in ("probe", "hold"):
                    continue
                got = (r.get("params") or {}).get("filter")
                if got != want:
                    bad.append((name, arm, got))
                    break
    return bad


def wilson(k, n, z=1.96):
    if not n:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def cmh(tables):
    """Cochran-Mantel-Haenszel over 2x2 tables, one per stratum.

    tables: list of (ok_a, n_a, ok_b, n_b). Returns (chi2, z, or_mh, strata_used).

    Strata where an arm has no judged attempts contribute nothing and are
    dropped - with n=0 the table is not 2x2. That is reported rather than
    silently done, because a day on which one arm never got a judged probe is
    itself worth seeing.
    """
    sum_a = sum_e = sum_v = 0.0
    num = den = 0.0
    used = 0
    for ok_a, n_a, ok_b, n_b in tables:
        if n_a == 0 or n_b == 0:
            continue
        n = n_a + n_b
        if n < 2:
            continue
        a = ok_a
        b = n_a - ok_a
        c = ok_b
        d = n_b - ok_b
        sum_a += a
        sum_e += (a + b) * (a + c) / n
        sum_v += (a + b) * (c + d) * (a + c) * (b + d) / (n * n * (n - 1))
        num += a * d / n
        den += b * c / n
        used += 1
    if sum_v <= 0:
        return (float("nan"), float("nan"), float("nan"), used)
    # Continuity-corrected, the conventional form. The sign of the z is carried
    # separately because the chi-square throws it away and the direction is
    # half of what anybody reads off this.
    chi2 = (abs(sum_a - sum_e) - 0.5) ** 2 / sum_v
    z = (sum_a - sum_e) / math.sqrt(sum_v)
    or_mh = (num / den) if den > 0 else float("inf")
    return (chi2, z, or_mh, used)


def main(paths):
    strata = []
    for path in paths:
        rows = [r for r in load(path) if arm_of(r) in ARM_ORDER]
        if not rows:
            continue
        arms = {a: [r for r in rows if arm_of(r) == a] for a in ARM_ORDER}
        if sum(1 for a in arms if arms[a]) < 2:
            continue
        strata.append((path.name, arms))

    if not strata:
        print("no run files carrying more than one params arm")
        return 1

    bad = check_params_reached(strata)
    if bad:
        print("REFUSING TO REPORT: an arm's label and its sent parameters disagree.")
        for name, arm, got in bad:
            print(f"  {name}  arm {arm}  params carried filter={got!r}")
        print()
        print("This gateway answers an unrecognised parameter with 200 and drops it,")
        print("so an arm that never sent `filter` reads exactly like one that sent it")
        print("and got nothing. The two are not separable after the fact.")
        return 1

    print("=" * 78)
    print("Probe phase, per session. `judged` excludes transport failures.")
    print("=" * 78)
    print(f"{'session':<34} {'arm':<14} {'ok/judged':>11} {'rate':>7}  95% Wilson")
    probe_tables = collections.defaultdict(list)
    for name, arms in strata:
        first = True
        for arm in ARM_ORDER:
            rows = [r for r in arms.get(arm, []) if r.get("phase") == "probe"]
            judged = [r for r in rows if r.get("verdict") in JUDGED]
            ok = sum(1 for r in judged if r.get("verdict") == "ok")
            n = len(judged)
            lo, hi = wilson(ok, n)
            label = name if first else ""
            first = False
            rate = f"{ok / n:.1%}" if n else "-"
            span = f"{lo:.1%} - {hi:.1%}" if n else ""
            print(f"{label:<34} {arm:<14} {ok:>5}/{n:<5} {rate:>7}  {span}")
            probe_tables[arm].append((ok, n))
        print()

    print("=" * 78)
    print("Pooled, Cochran-Mantel-Haenszel, session as stratum.")
    print("=" * 78)
    names = [n for n, _ in strata]
    for i, a in enumerate(ARM_ORDER):
        for b in ARM_ORDER[i + 1:]:
            tables = [
                (probe_tables[a][k][0], probe_tables[a][k][1],
                 probe_tables[b][k][0], probe_tables[b][k][1])
                for k in range(len(names))
            ]
            chi2, z, or_mh, used = cmh(tables)
            ok_a = sum(t[0] for t in tables)
            n_a = sum(t[1] for t in tables)
            ok_b = sum(t[2] for t in tables)
            n_b = sum(t[3] for t in tables)
            verdict = "separates" if chi2 == chi2 and chi2 > 3.84 else "nothing separates"
            print(f"{a:>14} vs {b:<14} "
                  f"{ok_a}/{n_a} vs {ok_b}/{n_b}  "
                  f"z = {z:+.2f}  chi2 = {chi2:.2f}  OR = {or_mh:.2f}  "
                  f"[{used} strata]  {verdict}")
    print()
    print("chi2 > 3.84 is p < 0.05 on 1 df. The z carries the direction: positive")
    print("means the first arm passed more often than the second.")
    print()

    print("=" * 78)
    print("The confounder that stays uncontrolled: --countries any.")
    print("=" * 78)
    for arm in ARM_ORDER:
        rows = [r for s in strata for r in s[1].get(arm, []) if r.get("phase") == "probe"]
        countries = collections.Counter(r.get("exit_country") for r in rows if r.get("exit_country"))
        top = ", ".join(f"{c} {k}" for c, k in countries.most_common(4))
        print(f"{arm:<14} {len(countries):>3} distinct over {len(rows):>4} probes   {top}")
    print()
    print("An arm's pass rate is partly a fact about which basket it drew. Nothing")
    print("here corrects for that, so nothing written from these numbers may be")
    print("quoted as a pass rate with the country held fixed.")
    print()

    print("=" * 78)
    print("The breaker asymmetry, and the hold phase.")
    print("=" * 78)
    for arm in ARM_ORDER:
        per = [len([r for r in s[1].get(arm, []) if r.get("phase") == "probe"]) for s in strata]
        hold = [r for s in strata for r in s[1].get(arm, []) if r.get("phase") == "hold"]
        hjudged = [r for r in hold if r.get("verdict") in JUDGED]
        hok = sum(1 for r in hjudged if r.get("verdict") == "ok")
        hrate = f"{hok / len(hjudged):.1%}" if hjudged else "-"
        print(f"{arm:<14} probes per session {per}   hold {hok}/{len(hjudged)} = {hrate}")
    print()
    print("Probes per session is an outcome, not a setting: the breaker stops a cell")
    print("after six consecutive failures, so the arm that passes more often is also")
    print("the arm with the larger n. Hold was 100% in every arm on 2026-08-13; the")
    print("day it is not, holds stop being 5.37 MB of no information.")
    print()

    print("=" * 78)
    print("Did the arms share the hour? Minutes into the session, first to last.")
    print("=" * 78)
    for name, arms in strata:
        starts = [r.get("ts") for a in arms.values() for r in a if r.get("ts")]
        if not starts:
            continue
        t0 = min(starts)
        print(f"  {name}")
        for arm in ARM_ORDER:
            ts = sorted(r["ts"] for r in arms.get(arm, []) if r.get("ts"))
            if not ts:
                continue
            lo = (_parse(ts[0]) - _parse(t0)).total_seconds() / 60
            hi = (_parse(ts[-1]) - _parse(t0)).total_seconds() / 60
            print(f"    {arm:<14} {lo:6.1f} - {hi:6.1f} min")
    print()
    print("Arms are drawn interleaved, so on paper they share the hour. They stop")
    print("sharing it the moment one cell trips the breaker and the others keep")
    print("going: the surviving arm spends its tail alone, at a different time of")
    print("day, and the hour is the largest effect in NOTEBOOK.md. Windows that")
    print("diverge badly inside one session are why the session is short.")

    total_bytes = sum(r.get("bytes") or 0 for s in strata for a in s[1].values() for r in a)
    print()
    print(f"{len(strata)} sessions, {total_bytes / 1e6:.0f} MB of proxy traffic behind this table.")
    return 0


if __name__ == "__main__":
    # `--help` has to exit 0 without reading a run file. That is not a courtesy:
    # tests/test_repository.py runs every analysis script this way with each
    # third-party package refused at the import finder, which is how it proves
    # the promise that these scripts work on a bare clone. Without this branch
    # the argument is taken for a path and the gate fails on FileNotFoundError.
    if "--help" in sys.argv[1:] or "-h" in sys.argv[1:]:
        print(__doc__)
        sys.exit(0)
    args = [pathlib.Path(a) for a in sys.argv[1:]]
    if not args:
        args = sorted(RUNS.glob("probehold_*.jsonl"))
    sys.exit(main(args))
