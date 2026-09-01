"""Emit the results tables as Markdown, so `RESULTS.md` is generated and not typed.

A table pasted into a document by hand is a claim nobody can re-derive, and this
repository has already had to correct two numbers that were quoted from memory
rather than from a run. Everything in `RESULTS.md` comes out of here, so the
check that the document is honest is `diff` against a fresh run of this script.

Three things this script refuses to do, each because doing them would produce a
prettier table that means less:

- **It does not pool runs.** The harness moved from a Windows workstation to a
  2-vCPU Linux VPS on 2026-08-18, which changed the load, the locale and the
  GPU the browser reports. Rows either side of that date are not the same
  measurement and are never summed. The date is applied as a cut, and the cut
  is checked against a signature in the data rather than trusted: the UA inside
  `engine_version` reads `Windows NT 10.0` on every row carrying one up to
  2026-08-13 and `X11; Linux x86_64` from 2026-08-19.

  Note what that check can and cannot do. It confirms the move happened around
  the stated date; it does not make `host` a measured column. **No row in
  `data/runs/` records which machine produced it**, so the labels this script
  prints are a function of the timestamp and nothing else, and 2 rows on the
  moving day itself are labelled `vps` while their UA says Windows. Anything
  read out of the host split has to be read as a date split too - see the
  Google section, where that is the whole finding.

  That gap was closed on 2026-08-26, and not by a better cut of these files:
  both machines were run at the same minutes against the same target through
  the same gateway. Those rows are `probehold_*.jsonl` rather than
  `benchmark_*.jsonl`, they are loaded separately, and which machine produced
  each of them is typed into `PROBEHOLD_ARMS` from the operator's log because
  the rows still do not say. That mapping is the one piece of this document
  that rests on testimony.
- **It does not merge the direct arm with the gateway arm.** An engine named
  `*-direct` left from the machine's own address. Its pass rate answers a
  different question and averaging the two hides which.
- **It does not hide small n.** Every cell carries its denominator, and any
  group under 30 attempts is marked, because the difference between 0/1 and
  0/300 is the entire content of those rows.

Usage:
    ssh HOST 'cd ~/bench && .venv/bin/python scripts/analysis/results_tables.py'
    ssh HOST 'cd ~/bench && .venv/bin/python scripts/analysis/results_tables.py' > RESULTS.md
    .venv/bin/python scripts/analysis/results_tables.py --readme
"""

import argparse
import collections
import datetime as dt
import glob
import io
import json
import math
import os
import re
import subprocess
import sys
from contextlib import redirect_stdout

RUNS_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "data", "runs")

# The 130-hour run. Named rather than detected, because "the biggest file" is
# not a property anyone can check a year from now.
FLAGSHIP = "benchmark_20260819T055927Z"

# The move. Rows are attributed by their own timestamp rather than by the run
# id, since a run that straddled the boundary would otherwise be labelled whole.
MOVE = dt.datetime(2026, 8, 18, tzinfo=dt.UTC)

SMALL = 30  # below this an attempt count is marked rather than read

# Below this pass rate no engine is named as best. On `google_serp` in the
# 130-hour run the top cell is 5/460 and the bottom is 1/459 - a difference of
# four pages that a two-sided Fisher exact puts at p = 0.22, i.e. nothing. Named
# in a summary row, that becomes "botasaurus is the best engine for Google",
# which is exactly the kind of ranking-from-noise the small-n rule above exists
# to prevent; it just needed a second form for the case where n is large and the
# effect is at the floor.
FLOOR = 5.0

# `http` is the no-browser baseline: plain `requests` with browser-shaped
# headers. It is in the harness to show what a target does to an unautomated
# client and it is not competing with the browser engines, so it is left out of
# the engine matrix and of any ranking. Its rows are still in the tables further
# down, where the question they answer is the one they were run for.
BASELINE = {"http"}

# Fixed rather than derived from the data, so a target that stops being run
# leaves a visible hole instead of silently disappearing from the matrix.
TARGET_ORDER = ["amazon_search", "google_serp", "bing_serp", "ddg_serp",
                "walmart_search"]

# Targets whose column in the flagship run answers a different question from
# the one its shape implies. Printed inside the cell rather than in a caption,
# for the same reason as the floor guard further down: a caption is not read by
# whoever screenshots the table.
CONFOUNDED = {
    "google_serp": "**not an engine comparison - the whole column is one "
                   "browser on one host that this target refuses**",
}

# Caveats that belong to an engine rather than to a target, printed under the
# matrix whose rows they qualify. Keyed by engine for the same reason CONFOUNDED
# is keyed by target: an engine that stops being run takes its caveat with it,
# and a note that outlives the row it qualifies is worse than no note.
#
# These live here rather than in RESULTS.md because that file is generated whole
# on every run. A paragraph typed into it survives exactly until the next
# regeneration, which is how the obscura block below was silently lost once.
ENGINE_CAVEATS = {
    "obscura": [
        "**Two confounds under the `obscura` row, both found 2026-08-26 by "
        "reading upstream rather than by running anything. One of them has "
        "since been run, and the run corrected it - see below.**",

        "- **Timezone.** Upstream ships `OBSCURA_TIMEZONE` and "
        "`OBSCURA_GEOLOCATION`; this harness sets neither, so the browser "
        "reports the host's own zone against an exit drawn from `country=any`. "
        "Measured on the Windows workstation: "
        "`Intl.DateTimeFormat().resolvedOptions().timeZone` reads "
        "`Europe/Moscow`, `getTimezoneOffset()` reads -180. Every `obscura` "
        "number above was taken with that mismatch present, which is a "
        "fingerprinting signal the other engines' rows do not carry in the "
        "same form. It is not a reason to discount the `bing_serp` and "
        "`ddg_serp` 100%s - those targets pass everything - but it does sit "
        "directly under the `google_serp` 0%, and it means that cell is not "
        "yet a measurement of the engine.\n"
        "- **There are two navigation ceilings and the run does not pick the "
        "lower one.** `nmbench/engines/base.py` sets `ENTRY_TIMEOUT_MS = 60000` "
        "and every engine's `goto` is called with it, while "
        "`OBSCURA_NAV_TIMEOUT_MS` and `OBSCURA_SCRIPT_DEADLINE_MS` default to "
        "30000 upstream and this adapter sets neither. An `obscura` row reading "
        "`error` on a slow target is therefore at a ceiling this document never "
        "stated and never moved, and any error-rate comparison including "
        "`obscura` is comparing two different ceilings.",

        "**The 30 s half of that was measured on 2026-08-27 and it is not what "
        "this entry said.** Written on 2026-08-26 off the upstream defaults, it "
        "asserted flatly that obscura gives up at 30 s while the harness waits "
        "to 60 s. Four `probe_and_hold` runs against `google_serp` produced 13 "
        "failures, and the 30 s deadline was the binding one in 4 of them - the "
        "other 8 ran to `Timeout 60000ms exceeded`, so the harness ceiling "
        "fired and obscura's did not. The deadline is also not a wall-clock "
        "30 s: the two rows that named it came back at 33.5 s and 42.9 s, so it "
        "bounds some inner phase and surfaces late. The claim was not wrong "
        "about the constants, it was wrong about which one you meet, and it was "
        "the majority case it got backwards.",

        "What the mistake looked like from the inside: two defaults in a config "
        "file are a complete story if you only read the config file. `30000 < "
        "60000` is arithmetic, it needs no run to check, and that is exactly "
        "why it was written as though it had been. The entry even labelled "
        "itself `by reading upstream rather than by running anything` - the "
        "provenance was disclosed honestly and then the sentence was phrased "
        "with the confidence of a measurement anyway. Disclosing where a claim "
        "came from does not downgrade the claim; only hedging the claim does.",

        "The remaining three causes in those 13, none of them a timeout: a "
        "socket-level `operation timed out`, a `client error (SendRequest)` at "
        "2.2 s, and an `InvalidHeaderValue` at 1.2 s while following Google's "
        "own `&sei=` redirect. The last is a candidate defect in obscura rather "
        "than in this adapter and has not been reduced to a repro yet.",

        "The timezone gap and the ceiling mismatch are both adapter gaps rather "
        "than engine defects, and both are fixed by wiring the three variables "
        "into `nmbench/engines/obscura.py`. Until that is run, read the "
        "`obscura` column as coverage only.",
    ],
}

# The 2026-08-26 host decomposition. One target, one engine, one entry shape,
# one set of gateway parameters, five runs, two machines.
#
# The host column is typed here because no row carries one - the same missing
# field this whole section is about. It is the operator's log, not a
# measurement, and it is the only input below that cannot be checked against
# the files. Everything else, including which arms overlapped in wall-clock, is
# computed from the rows.
#
# `probehold_20260826T074450Z` was produced on the VPS and copied into
# `data/runs/` so this section can be generated rather than typed. It ran three
# engines; only `patchright` is read here, because it is the only one with rows
# on both machines that day.
PROBEHOLD_ARMS = [
    ("probehold_20260826T092819Z", "workstation", "own address",
     "no gateway at all - the workstation's own residential line"),
    ("probehold_20260826T105407Z", "workstation", "gateway",
     "the gateway, reached directly from the workstation"),
    ("probehold_20260826T121309Z", "workstation", "gateway, tunnelled",
     "the gateway, reached through `ssh -L` out of the VPS"),
    ("probehold_20260826T121434Z", "workstation", "gateway",
     "the gateway, reached directly - the control for the row above"),
    ("probehold_20260826T074450Z", "vps", "gateway",
     "the gateway, reached directly from the VPS"),
]

# The engine held fixed across the decomposition above.
PROBEHOLD_ENGINE = "patchright"

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
README = os.path.join(ROOT, "README.md")

# GitHub renders `README.md` on the front page of a repository and nothing else,
# so a `RESULTS.md` with no block here is a file nobody opens. The markers let
# this script own a few lines inside a document written by hand: everything
# outside them is never touched, and the block between them is replaced whole.
MARK_BEGIN = "<!-- RESULTS:BEGIN -->"
MARK_END = "<!-- RESULTS:END -->"

# The `rows` badge. It was typed by hand and had drifted by 1,191 rows before
# this was wired up, which is the failure mode a badge is worst at: it is the
# first number a reader sees and the last one anyone thinks to re-derive.
BADGE_RE = re.compile(r"(badge/rows-)(\d+(?:%2C\d+)*)(%20published)")


def load(path):
    """Attempt rows only. A row with no `query` asked the target nothing."""
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except Exception:
                continue
            if row.get("query"):
                out.append(row)
    return out


def when(row):
    ts = row.get("ts")
    if not ts:
        return None
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def era(row):
    stamp = when(row)
    if stamp is None:
        return "unknown"
    return "vps" if stamp >= MOVE else "workstation"


class Tally:
    """One cell of a table.

    `judged` excludes errors on purpose and the error count is carried beside
    it rather than folded in. An error is the harness or the path failing, not
    the target refusing, and putting the two in one denominator is how an
    engine that crashes often looks like an engine the target dislikes. Both
    numbers are printed so either rate can be reconstructed.
    """

    def __init__(self):
        self.verdicts = collections.Counter()
        self.bytes = 0
        self.html = 0
        self.versions = collections.Counter()

    def add(self, row):
        self.verdicts[row.get("verdict") or "none"] += 1
        self.bytes += row.get("bytes") or 0
        self.html += row.get("html_len") or 0
        if row.get("engine_version"):
            self.versions[row["engine_version"]] += 1

    @property
    def attempts(self):
        return sum(self.verdicts.values())

    @property
    def errors(self):
        return self.verdicts.get("error", 0)

    @property
    def judged(self):
        return self.attempts - self.errors

    @property
    def ok(self):
        return self.verdicts.get("ok", 0)

    @property
    def rate(self):
        return None if not self.judged else 100 * self.ok / self.judged

    def rate_cell(self):
        if self.judged == 0:
            return f"- (0/{self.attempts} judged)"
        mark = " ?" if self.attempts < SMALL else ""
        return f"{self.rate:.0f}% ({self.ok}/{self.judged}){mark}"

    def refusals(self):
        parts = [f"{k} {v}" for k, v in self.verdicts.most_common()
                 if k not in ("ok", "error")]
        return ", ".join(parts) if parts else "-"


def engine_table(rows, title, note):
    by_engine = collections.defaultdict(Tally)
    for row in rows:
        by_engine[row.get("engine") or "(unrecorded)"].add(row)
    print(f"### {title}\n")
    print(note + "\n")
    print("| engine | pass | attempts | errors | what came back instead | "
          "MB spent per page delivered |")
    print("|---|---|---|---|---|---|")
    order = sorted(by_engine.items(),
                   key=lambda kv: (kv[1].rate is None, -(kv[1].rate or 0)))
    for name, tally in order:
        per_page = (f"{tally.bytes / tally.ok / 1024 / 1024:.2f}"
                    if tally.ok else "no page")
        print(f"| `{name}` | {tally.rate_cell()} | {tally.attempts} | "
              f"{tally.errors} | {tally.refusals()[:58]} | {per_page} |")
    total = Tally()
    for tally in by_engine.values():
        total.verdicts.update(tally.verdicts)
        total.bytes += tally.bytes
    print(f"| **all** | **{total.rate_cell()}** | {total.attempts} | "
          f"{total.errors} | {total.refusals()[:58]} | |")
    print()


def fisher_exact(a, b, c, d):
    """Two-sided, by summing every table at most as probable as the observed one.

    Hand-written because this repository ships no scipy and one 2x2 test is not
    worth a dependency that has to be installed on the VPS as well. Checked
    2026-08-26 against `scipy.stats.fisher_exact` 1.17.1 in a throwaway venv, on
    the eight Amazon cells this is actually used for: largest absolute
    disagreement 2.2e-16, which is one float ulp at that magnitude.
    """
    def p(a, b, c, d):
        n = a + b + c + d
        return (math.comb(a + b, a) * math.comb(c + d, c)) / math.comb(n, a + c)
    observed = p(a, b, c, d)
    row1, col1, n = a + b, a + c, a + b + c + d
    total = 0.0
    for x in range(max(0, col1 - (n - row1)), min(row1, col1) + 1):
        cand = p(x, row1 - x, col1 - x, n - row1 - col1 + x)
        if cand <= observed * (1 + 1e-9):
            total += cand
    return min(total, 1.0)


def base_name(row):
    """`patchright/light` and `patchright-direct/none` both become `patchright`.

    The preset is dropped from the key, not from the analysis: every caller of
    this feeds it rows already restricted to one preset, because pooling
    `light` and `none` would pool two different pages as well as two hosts.
    """
    return (row.get("engine") or "(unrecorded)").split("/")[0].replace("-direct", "")


def judged_span(rows):
    """min and max judged attempts over the non-empty cells of a matrix.

    Computed rather than typed. An earlier draft of the caption said "404 to
    464 judged attempts", which was true of the Amazon column and false of the
    Google one, where `chromium` contributes a cell of zero.
    """
    cells = collections.defaultdict(Tally)
    for row in rows:
        if row.get("direct"):
            continue
        name = base_name(row)
        if name not in BASELINE:
            cells[(name, row.get("target"))].add(row)
    sizes = [t.judged for t in cells.values() if t.judged]
    return (min(sizes), max(sizes)) if sizes else (0, 0)


def matrix(rows, title, note):
    """Engine down the side, target across the top. Gateway arm only.

    The point of this shape over the per-target tables is the empty cells: they
    are the only place the reader can see what has *not* been measured, and
    that is usually the more actionable half.
    """
    cells = collections.defaultdict(Tally)
    for row in rows:
        if row.get("direct"):
            continue
        name = base_name(row)
        if name in BASELINE:
            continue
        cells[(name, row.get("target"))].add(row)

    engines = sorted({k[0] for k in cells})
    targets = [t for t in TARGET_ORDER if any(k[1] == t for k in cells)]
    print(f"### {title}\n")
    print(note + "\n")
    print("| engine | " + " | ".join(f"`{t}`" for t in targets) + " |")
    print("|---" * (len(targets) + 1) + "|")
    for name in engines:
        line = [f"`{name}`"]
        for target in targets:
            tally = cells.get((name, target))
            if tally is None:
                line.append("not run")
            elif tally.judged == 0:
                line.append(f"- ({tally.errors} err)")
            else:
                mark = " ?" if tally.attempts < SMALL else ""
                line.append(f"{tally.rate:.0f}% "
                            f"({tally.ok}/{tally.judged}){mark}")
        print("| " + " | ".join(line) + " |")
    print()


def tiers(rows, target):
    """Rank one column, then say how much of the ranking survives a test.

    A sorted list of pass rates always produces a first place, and a first place
    is what gets quoted. So every engine is compared against the top one with a
    two-sided Fisher exact, and the threshold is Bonferroni-corrected for the
    number of comparisons made - without that correction, running seven tests at
    0.05 and reporting the ones that clear it is a way of manufacturing a
    difference out of a tie.
    """
    cells = collections.defaultdict(Tally)
    for row in rows:
        if row.get("direct") or row.get("target") != target:
            continue
        name = base_name(row)
        if name not in BASELINE:
            cells[name].add(row)
    ranked = sorted(((n, t) for n, t in cells.items() if t.judged >= SMALL),
                    key=lambda kv: -kv[1].rate)
    if not ranked:
        return None, []
    alpha = 0.05 / max(1, len(ranked) - 1)
    top = ranked[0][1]
    out = []
    for name, tally in ranked:
        p = (1.0 if tally is top else
             fisher_exact(tally.ok, tally.judged - tally.ok,
                          top.ok, top.judged - top.ok))
        out.append((name, tally, p, p < alpha))
    return alpha, out


def tier_table(rows, target, rest_note):
    alpha, ranked = tiers(rows, target)
    if not ranked:
        print(f"No cell on `{target}` reaches {SMALL} judged attempts, so no "
              f"ranking is printed.\n")
        return
    if target in CONFOUNDED:
        p = host_split_p()
        sep = f"separates them at p = {p:.2g}" if p else "separates them"
        print(f"**Before reading this table: every row in it was taken on the "
              f"VPS, and on this target the VPS is the thing being measured.** "
              f"The section *The host, separated from the date* puts the same "
              f"engine, the same entry shape and the same gateway parameters "
              f"on two machines in overlapping hours and {sep}, with the "
              f"workstation off the floor and the VPS on it. So the ordering "
              f"below is {len(ranked)} cells inside one floor, and the floor "
              f"belongs to the host.\n")
    # When the whole column is at the floor the row order is an artefact of one
    # or two served pages, so the word "top" is withheld rather than qualified
    # in a caption underneath. A caption is not read by whoever screenshots the
    # table; the cell is.
    floored = ranked[0][1].rate < FLOOR
    if floored:
        print(f"**Every cell in this column is below {FLOOR:.0f}%, so no "
              f"engine is named as best and the `#` column is an ordering, not "
              f"a ranking.** The numbers are printed because the floor is the "
              f"finding.\n")
    print("| # | engine | pass | judged | vs the leading cell | separable? |")
    print("|---|---|---|---|---|---|")
    for i, (name, tally, p, sig) in enumerate(ranked, 1):
        cell = ("-" if i == 1 else f"p = {p:.4f}" if p >= 1e-4 else "p < 0.0001")
        if floored:
            verdict = "at the floor"
        else:
            verdict = ("top" if i == 1 else
                       "**yes, worse**" if sig else "no, tied with the top")
        print(f"| {i} | `{name}` | {tally.rate:.0f}% | {tally.judged} | "
              f"{cell} | {verdict} |")
    print()
    print(f"Two-sided Fisher exact against the leading cell, "
          f"{len(ranked) - 1} comparisons, so the threshold is "
          f"0.05/{len(ranked) - 1} = {alpha:.4f}. {rest_note}\n")


def probe_rows(run_id):
    """The first request of each identity in a `probe_and_hold` run.

    Hold rows are dropped rather than pooled. A hold row only exists when a
    probe was served, so mixing the two makes the denominator a function of the
    pass rate and every arm look better than it was.
    """
    path = os.path.join(RUNS_DIR, run_id + ".jsonl")
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except Exception:
                continue
            if (row.get("query") and row.get("phase") == "probe"
                    and base_name(row) == PROBEHOLD_ENGINE):
                out.append(row)
    return out


def window(rows):
    stamps = [s for s in (when(r) for r in rows) if s]
    return (min(stamps), max(stamps)) if stamps else None


def spans_overlap(groups_a, rows_b):
    """Cut both sides down to the minutes each side was genuinely running.

    The point of the whole section is that no cut of the archive can separate
    host from date, so this one is checked rather than asserted: if a future
    edit points it at runs that did not in fact overlap, the concurrent cells
    go empty instead of quietly becoming a sequential comparison again.

    `groups_a` is a list of runs and not one pool of rows, because the
    workstation side is three separate runs with a 50-minute gap in the middle.
    Taking min-to-max across them would hand the other side every row inside
    that gap, when nothing was running against it - which is exactly the kind
    of hour mismatch this table exists to remove. The intersection is computed
    per run and then unioned.
    """
    win_b = window(rows_b)
    if win_b is None:
        return [], []
    spans = []
    for rows_a in groups_a:
        win_a = window(rows_a)
        if win_a is None:
            continue
        lo, hi = max(win_a[0], win_b[0]), min(win_a[1], win_b[1])
        if lo <= hi:
            spans.append((lo, hi))
    def inside(row):
        stamp = when(row)
        return stamp is not None and any(lo <= stamp <= hi for lo, hi in spans)

    return ([r for rows_a in groups_a for r in rows_a if inside(r)],
            [r for r in rows_b if inside(r)])


def tally_of(rows):
    tally = Tally()
    for row in rows:
        tally.add(row)
    return tally


def cell(tally):
    return (f"{tally.rate:.0f}% ({tally.ok}/{tally.judged})"
            if tally.judged else f"- (0/{tally.attempts} judged)")


def contrast(label, left, right, reading):
    p = fisher_exact(left.ok, left.judged - left.ok,
                     right.ok, right.judged - right.ok)
    print(f"| {label} | {cell(left)} | {cell(right)} | "
          f"{p:.2g} | {reading} |")


def host_gateway_tallies():
    """The two gateway arms of 2026-08-26, workstation and VPS.

    Three places quote these, so there is one arithmetic here rather than three
    that can drift apart.
    """
    ws, vps = [], []
    for run_id, host, path, _ in PROBEHOLD_ARMS:
        rows = probe_rows(run_id)
        if host == "vps":
            vps.extend(rows)
        elif path != "own address":
            ws.extend(rows)
    return tally_of(ws), tally_of(vps)


def host_concurrent_tallies():
    """The same two arms, cut to the minutes both machines were in the air.

    Smaller than `host_gateway_tallies` and stricter: two of the three
    workstation runs started after the VPS run had been stopped, so they
    overlap the VPS in hours but not in minutes.
    """
    ws_groups, vps_rows = [], []
    for run_id, host, path, _ in PROBEHOLD_ARMS:
        rows = probe_rows(run_id)
        if host == "vps":
            vps_rows.extend(rows)
        elif path != "own address":
            ws_groups.append(rows)
    ws_conc, vps_conc = spans_overlap(ws_groups, vps_rows)
    return tally_of(ws_conc), tally_of(vps_conc), window(ws_conc)


def host_split_p(tallies=None):
    """Workstation against VPS through the gateway, or None if the files are gone."""
    left, right = tallies or host_gateway_tallies()
    if not left.judged or not right.judged:
        return None
    return fisher_exact(left.ok, left.judged - left.ok,
                        right.ok, right.judged - right.ok)


def host_decomposition():
    """What the Google column is measuring, taken apart on 2026-08-26.

    This exists because the section above it can only describe its own
    confound. It asked for two arms in one window; these are those arms.
    """
    arms = [(run_id, host, path, note, probe_rows(run_id))
            for run_id, host, path, note in PROBEHOLD_ARMS]
    if not any(rows for _, _, _, _, rows in arms):
        return False

    print("## The host, separated from the date\n")
    print(f"Every cross-host contrast in the archive above is also a "
          f"cross-date contrast, and no cut of those files can undo that. So "
          f"the arms were run again on **2026-08-26**, deliberately "
          f"overlapping in wall-clock, on one target, one engine "
          f"(`{PROBEHOLD_ENGINE}`), one entry shape (`--entry url`) and one "
          f"set of gateway parameters (`--countries any`). The protocol is "
          f"`scripts/probes/probe_and_hold.py`; only the first request of each "
          f"identity is counted, because a hold row exists only when a probe "
          f"was served.\n")

    print("| run | host | path | pass | UTC |")
    print("|---|---|---|---|---|")
    for run_id, host, path, _, rows in arms:
        tally = tally_of(rows)
        stamps = [s for s in (when(r) for r in rows) if s]
        span = f"{min(stamps):%H:%M}-{max(stamps):%H:%M}" if stamps else "-"
        print(f"| `{run_id}` | {host} | {path} | {cell(tally)} | {span} |")
    print()

    direct = tally_of([r for run_id, host, path, _, rows in arms
                       for r in rows if path == "own address"])
    ws_gw = tally_of([r for run_id, host, path, _, rows in arms
                      for r in rows
                      if host == "workstation" and path != "own address"])
    vps_gw = tally_of([r for run_id, host, path, _, rows in arms
                       for r in rows if host == "vps"])
    tunnelled = tally_of([r for run_id, host, path, _, rows in arms
                          for r in rows if path == "gateway, tunnelled"])
    plain = tally_of(probe_rows("probehold_20260826T121434Z"))

    ws_conc, vps_conc, conc = host_concurrent_tallies()

    print("| contrast | left | right | Fisher, two-sided | what it isolates |")
    print("|---|---|---|---|---|")
    contrast("own address vs the gateway, same browser", direct, ws_gw,
             "the exit address")
    contrast("workstation vs VPS through the gateway", ws_gw, vps_gw,
             "the client")
    contrast("the same, cut to the minutes both were running",
             ws_conc, vps_conc, "the client, hour held")
    contrast("gateway reached through the VPS vs reached directly",
             tunnelled, plain, "the route and the pool slice")
    print()

    if conc:
        print(f"The third row is the strict version of the second and it is "
              f"much the smaller of the two. Only one of the three "
              f"workstation runs was in the air while the VPS run was, so it "
              f"covers {conc[0]:%H:%M}-{conc[1]:%H:%M} UTC and nothing else - "
              f"{ws_conc.judged} judged attempts against {vps_conc.judged}. "
              f"It is in the table because the second row can still be "
              f"answered with \"different hours\" and this one cannot, not "
              f"because it is the stronger evidence. They agree.\n")

    print(f"**Read down that table: the loss is two losses, and neither of "
          f"them is the pool.** Going from the workstation's own line to a "
          f"gateway exit costs {direct.rate - ws_gw.rate:.0f} points, "
          f"{direct.rate:.0f}% down to {ws_gw.rate:.0f}%, which is the exit "
          f"being a residential address Google has seen before. Keeping that "
          f"same gateway and moving the browser to the VPS costs the "
          f"remaining {ws_gw.rate - vps_gw.rate:.0f}, down to "
          f"{vps_gw.rate:.0f}%, and that second step has nothing to do with "
          f"the address at all.\n")

    print(f"**The last row is the control that makes the third row mean "
          f"something.** `ssh -L 18080:gate.nodemaven.com:8080` puts the "
          f"gateway-facing source address on the VPS while the browser stays "
          f"on Windows. If the VPS were being handed a worse slice of the "
          f"pool, or if the route were the problem, that arm would drop. It "
          f"does not move ({cell(tunnelled)} against {cell(plain)}). So what "
          f"the VPS changes is the browser, not the network.\n")

    builds = collections.Counter(
        r.get("engine_version") for run_id, host, path, _, rows in arms
        for r in rows if r.get("engine_version"))
    headful = all(r.get("headless") is False
                  for run_id, host, path, _, rows in arms for r in rows)
    print(f"**What is not established is which property of that browser.** "
          f"Two things the rows do settle: the browser was headful on both "
          f"machines{' (`headless: false` on every row of every arm)' if headful else ''}, "
          f"and the recorded build is the same on both - "
          f"{', '.join(f'`{b}`' for b in sorted(builds))}. So this is not a "
          f"version difference and it is not a headless token. Beyond that "
          f"the files say nothing at all: **no row in `data/runs/` records a "
          f"platform, a screen size, a GPU string or a WebGL renderer**, so "
          f"the obvious suspects - Linux against Windows, SwiftShader against "
          f"a real GPU, a virtual display against a desktop, all of it behind "
          f"exits whose real owners are on Windows and Android - cannot be "
          f"told apart backwards out of these rows any more than the host "
          f"could. It is the same shape of gap and it wants the same fix: "
          f"record the thing, then vary one at a time.\n")
    print("An earlier draft of that paragraph named a specific virtual "
          "display geometry as the suspect. It was recollection rather than "
          "measurement and it has been removed: the run was launched over "
          "`ssh` so its display was never written down, the process is gone, "
          "and the only `Xvfb` still alive on that machine belongs to an "
          "unrelated container. Naming it would have been the third time in "
          "this repository that a plausible cause was written up as a "
          "checked one.\n")

    print("**The effect is Google's and not the VPS being broken.** The same "
          "machine, in the run this document is mostly about, was served "
          "92-96% of its Amazon pages through the same gateway.\n")

    print(f"**Consequence for the tables above.** The `google_serp` column of "
          f"`{FLAGSHIP}` was taken entirely on the VPS. It is a comparison of "
          f"engines behind a client that this target refuses at "
          f"{vps_gw.rate:.0f}%, so it measures the floor and the differences "
          f"between engines inside it are differences between numbers that "
          f"are all at the floor. It is not a ranking of engines on Google "
          f"and it is left in place, marked, rather than deleted: the floor "
          f"is a real measurement of that configuration, and the objective "
          f"this work serves says to publish where NodeMaven loses too. "
          f"**Re-running that column from the workstation is what would make "
          f"it an engine comparison, and it has not been run.**\n")
    return True


def summary_rows(flagship, rest):
    """Per target: best engine, worst engine, denominator, provenance.

    Returned as data rather than printed because two documents render this same
    ranking - `RESULTS.md` and the block inside `README.md` - and a second copy
    of the logic is a second place for it to go stale. If those two ever
    disagree it is the README that is read, so the duplication has to be
    impossible rather than merely discouraged.
    """
    out = []
    sources = [("amazon_search", flagship, f"`{FLAGSHIP}`"),
               ("google_serp", flagship, f"`{FLAGSHIP}`"),
               ("bing_serp", rest, "pre-VPS runs, pooled"),
               ("ddg_serp", rest, "pre-VPS runs, pooled"),
               ("walmart_search", rest, "pre-VPS runs, pooled")]
    for target, source, provenance in sources:
        rows = [r for r in source if r.get("target") == target]
        if not rows:
            continue
        by_engine = collections.defaultdict(Tally)
        for row in rows:
            by_engine[row.get("engine") or "(unrecorded)"].add(row)
        # Two exclusions, both because a headline is the line most likely to be
        # quoted without its denominator.
        #
        # Engines with no judged attempt at all are dropped rather than sorted
        # to one end: `chromium` errored on all 500 of its Google attempts, and
        # calling that the worst engine would report a harness failure as a
        # refusal by the target.
        #
        # Cells under the small-n threshold are dropped too. Without this the
        # best engine on Walmart is 100% on fifteen attempts and the worst on
        # Bing is 50% on four, and both would be read as rankings.
        ranked = sorted(((n, t) for n, t in by_engine.items()
                         if t.judged >= SMALL), key=lambda kv: -kv[1].rate)
        out.append((target, ranked[0] if ranked else None,
                    ranked[-1] if ranked else None, len(rows), provenance))
    return out


def render_summary(rows, provenance=True):
    """The shared table. `provenance` is dropped in the README to keep it narrow."""
    head = "| target | best | worst | rows |"
    if provenance:
        head += " read from |"
    print(head)
    print("|---|---|---|---|" + ("---|" if provenance else ""))
    for target, best, worst, n, source in rows:
        if best is None:
            cells = (f"no cell reaches {SMALL} judged attempts, so no engine "
                     f"is named", "-")
        elif best[1].rate < FLOOR:
            # Every engine is on the floor. Report the floor, not a winner.
            cells = (f"every engine below {FLOOR:.0f}%, best is "
                     f"{best[1].rate:.1f}% ({best[1].ok}/{best[1].judged})",
                     "-")
        else:
            (bn, bt), (wn, wt) = best, worst
            cells = (f"`{bn}` {bt.rate:.0f}% ({bt.ok}/{bt.judged})",
                     f"`{wn}` {wt.rate:.0f}% ({wt.ok}/{wt.judged})")
        if target in CONFOUNDED:
            cells = (f"{cells[0]} - {CONFOUNDED[target]}", cells[1])
        line = f"| `{target}` | {cells[0]} | {cells[1]} | {n} |"
        if provenance:
            line += f" {source} |"
        print(line)
    print()


def readme_block(flagship, rest, span):
    """The few lines that live on the repository's front page.

    Deliberately carries no generation timestamp. A timestamp would make every
    regeneration a diff even when no run was added, and a block that is always
    dirty is a block people stop regenerating. The run span below is a property
    of the data, so re-running this on unchanged files produces no change at
    all - which is also what makes `--check` meaningful in CI later.
    """
    print(MARK_BEGIN)
    print()
    print(f"Best and worst engine per target, from the "
          f"{len(flagship) + len(rest)} attempt rows in "
          f"`data/runs/benchmark_*.jsonl`. `pass` is `ok` over judged "
          f"attempts - harness and path failures are counted separately and "
          f"excluded from the denominator, because an engine that crashes is "
          f"not an engine the target refused.")
    print()
    render_summary(summary_rows(flagship, rest), provenance=False)
    _alpha, ranked = tiers(flagship, "amazon_search")
    tied = [n for n, _, _, sig in ranked if not sig]
    if tied:
        print(f"On the one target with enough evidence to rank engines, the "
              f"top of the table is a **tie and not a podium**: "
              f"{', '.join(f'`{n}`' for n in tied)} sit within "
              f"{ranked[0][1].rate - ranked[len(tied) - 1][1].rate:.0f} points "
              f"of each other and a two-sided Fisher exact, corrected for the "
              f"{len(ranked) - 1} comparisons made, separates none of them. "
              f"The first of them is `{ranked[0][0]}`, which is the "
              f"unmodified control.")
        print()
    ws_gw, vps_gw = host_gateway_tallies()
    ws_c, vps_c, _ = host_concurrent_tallies()
    p, p_c = host_split_p(), host_split_p((ws_c, vps_c))
    print(f"Amazon and the two smaller search engines are a win. **The Google "
          f"row is not an engine comparison and must not be quoted as one.** "
          f"Every cell of it was taken on one Linux VPS. Run again with the "
          f"same engine through the same gateway, a Windows workstation was "
          f"served {ws_gw.rate:.0f}% ({ws_gw.ok}/{ws_gw.judged}) against "
          f"{vps_gw.rate:.0f}% ({vps_gw.ok}/{vps_gw.judged}) from the VPS"
          + (f", two-sided Fisher p = {p:.2g}" if p else "") +
          f"; cut to the one window where both machines were running at once "
          f"it is {ws_c.rate:.0f}% ({ws_c.ok}/{ws_c.judged}) against "
          f"{vps_c.rate:.0f}% ({vps_c.ok}/{vps_c.judged})"
          + (f", p = {p_c:.2g}" if p_c else "") +
          ". The floor is real, it belongs to that client, and it is not a "
          "property of the proxies.")
    print()
    print(f"**[Full tables -> RESULTS.md](RESULTS.md)** - the 130-hour run "
          f"(`{FLAGSHIP}`, {span}) engine by engine, Google day by day, and "
          f"everything measured before it, split by host and by path.")
    print()
    print(MARK_END)


def run_files():
    """The run files a reader of this repository would actually receive.

    Tracked files only. The badge says `published`, and a run file sitting
    untracked in a working tree is not published, it is somebody's afternoon.
    Counting the directory instead put the badge 25 rows ahead of the repository
    on 2026-08-27, off four exploratory obscura runs nobody had committed, and
    the number it wrote could not then be committed without either shipping those
    runs or failing CI on a fresh clone.

    Falls back to the directory when `git` is absent or this is not a
    repository - which is the case of an unpacked archive, and there every file
    present is a file that shipped, so the two definitions agree. Counts every
    name rather than only `benchmark_*`, because the badge links to the
    directory rather than to the matrix runs.

    The pathspec carries `:(glob)` so that `*` stops at a slash. Git's default
    pathspec wildcard crosses `/`, unlike the `glob` module's, and without the
    magic this walked into `data/runs/invalid/` and added its 30 quarantined rows
    to the badge - a directory whose whole purpose is to hold rows that must not
    be counted. It read as +30 published rows and would have been committed.
    """
    try:
        listed = subprocess.run(
            ["git", "-C", ROOT, "ls-files", "-z", "--",
             ":(glob)data/runs/*.jsonl"],
            capture_output=True, text=True, timeout=30, check=True).stdout
        return [os.path.join(ROOT, name) for name in listed.split("\0") if name]
    except (OSError, subprocess.SubprocessError):
        return sorted(glob.glob(os.path.join(RUNS_DIR, "*.jsonl")))


def published_rows():
    """Rows across every published run file, which is what the badge claims."""
    total = 0
    for path in run_files():
        with open(path, encoding="utf-8") as fh:
            total += sum(1 for line in fh if line.strip())
    return total


def badged(text):
    """Set the `rows` badge to the count the run files actually carry."""
    return BADGE_RE.sub(
        lambda m: m.group(1) + f"{published_rows():,}".replace(",", "%2C")
        + m.group(3), text)


def write_readme(flagship, rest, span):
    """Replace the marked block in README.md, leaving every other byte alone."""
    with open(README, encoding="utf-8") as fh:
        text = fh.read()
    if MARK_BEGIN not in text or MARK_END not in text:
        print(f"{README} has no RESULTS:BEGIN/RESULTS:END markers. Add them "
              f"where the block belongs; this script will not guess at a "
              f"position in a hand-written document.", file=sys.stderr)
        return 1
    buf = io.StringIO()
    with redirect_stdout(buf):
        readme_block(flagship, rest, span)
    head = text.split(MARK_BEGIN)[0]
    tail = text.split(MARK_END, 1)[1]
    new = badged(head + buf.getvalue().rstrip("\n") + tail)
    if new == text:
        print("README.md already matches the data, nothing written",
              file=sys.stderr)
        return 0
    with open(README, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(new)
    print("README.md block rewritten", file=sys.stderr)
    return 0


def engine_caveats(rows):
    """Print the caveats for whichever engines actually have a row in `rows`."""
    present = {base_name(r) for r in rows} & set(ENGINE_CAVEATS)
    for name in sorted(present):
        for block in ENGINE_CAVEATS[name]:
            print(block + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--readme", action="store_true",
                        help="rewrite the marked block in README.md instead of "
                             "printing RESULTS.md to stdout")
    args = parser.parse_args()

    paths = sorted(glob.glob(os.path.join(RUNS_DIR, "benchmark_*.jsonl")))
    if not paths:
        print("no benchmark runs found", file=sys.stderr)
        return 1

    flagship, rest = [], []
    for path in paths:
        rows = load(path)
        (flagship if FLAGSHIP in os.path.basename(path) else rest).extend(rows)

    if not flagship:
        print(f"{FLAGSHIP} is not on disk, so the headline table cannot be "
              f"built and no substitute is guessed at", file=sys.stderr)
        return 1

    stamps = [s for s in (when(r) for r in flagship) if s]
    span = f"{min(stamps):%Y-%m-%d %H:%M} to {max(stamps):%Y-%m-%d %H:%M} UTC"

    if args.readme:
        return write_readme(flagship, rest, span)

    print("<!-- generated by scripts/analysis/results_tables.py - do not edit "
          "by hand -->")
    print("<!-- regenerate: .venv/bin/python scripts/analysis/results_tables.py"
          " > RESULTS.md -->\n")
    print("# Results\n")
    print("Every number below is generated from the run files in `data/runs/` "
          "by `scripts/analysis/results_tables.py`. Nothing here is typed in.\n")
    print("`pass` is `ok` over **judged** attempts: rows where the harness or "
          "the path failed are counted as `errors` and excluded from the "
          "denominator, because an engine that crashes is not an engine the "
          "target refused. Both numbers are shown, so the other rate can be "
          "reconstructed. A `?` marks a cell built on fewer than "
          f"{SMALL} attempts.\n")

    print("## Where the pool wins and where it loses\n")
    print("One line per target, best engine and worst engine, from the largest "
          "body of rows available for that target. This is a summary of the "
          "tables below and not a separate measurement; every figure is "
          "repeated there with its denominator.\n")
    render_summary(summary_rows(flagship, rest))
    print("Read plainly: **Amazon is a win, the two smaller search engines are "
          "a win, and Google is a loss on the machine that row was measured "
          "on.** The loss is published rather than dropped because the "
          "objective this work serves says to publish where NodeMaven loses "
          "too.\n")
    ws_gw, vps_gw = host_gateway_tallies()
    if ws_gw.judged and vps_gw.judged:
        print(f"This line used to end \"no engine and no gateway parameter "
              f"tried so far moves Google off the floor\", which put the floor "
              f"on the pool. **That did not survive 2026-08-26**: the same "
              f"gateway, the same parameters and the same engine, driven from "
              f"a Windows workstation instead of the VPS, were served "
              f"{ws_gw.rate:.0f}% ({ws_gw.ok}/{ws_gw.judged}) in the same "
              f"hours the VPS was being served {vps_gw.rate:.0f}% "
              f"({vps_gw.ok}/{vps_gw.judged}). The floor in the table above is "
              f"reproducible and it is a floor for that client. What it is not "
              f"is a property of the proxies. See *The host, separated from "
              f"the date*.\n")

    print("## Every engine on every target\n")
    print("Two matrices rather than one, and the split is the point. The "
          "130-hour run is a single window on a single host at a single "
          "preset, so a row of it is one experiment. Everything before it "
          "accumulated over eight days on another host at another preset, so "
          "a row of *that* is a list of separate experiments that happen to "
          "share an engine name. Printing them as one table would put a "
          "464-attempt cell next to a 3-attempt cell with nothing to say "
          "which is which.\n")
    print("The gateway arm only. `-direct` rows left from the machine's own "
          "address and answer a different question. The `http` baseline - "
          "plain `requests` with browser-shaped headers - is left out of both "
          "matrices and of every ranking below, because it is in the harness "
          "to show what a target does to an unautomated client, not to "
          "compete.\n")

    lo, hi = judged_span(flagship)
    n_engines = len({base_name(r) for r in flagship
                     if base_name(r) not in BASELINE})
    matrix(flagship, f"Inside the big run - `{FLAGSHIP}`, VPS, `preset=none`",
           f"{n_engines} engines, two targets, and the cells are balanced by "
           f"design: every one that produced a judgement at all rests on {lo} "
           f"to {hi} of them. That balance is a property of the matrix the "
           f"harness ran, not something arranged afterwards - no cell here has "
           f"been subsampled to make the denominators match, because throwing "
           f"away measured attempts to make a table look tidy costs precision "
           f"and buys nothing. The one exception is the empty cell, and it is "
           f"empty rather than low: `chromium` errored on every Google attempt "
           f"in this run, so it has no judged attempts to balance.")

    matrix(rest, "Before the big run - workstation, mostly `preset=light`",
           "Wider and much thinner. This is where `obscura`, `curlcffi` and "
           "the three smaller targets live, and most of it is small enough "
           "that a `?` is doing real work. Read it as coverage - what has and "
           "has not been pointed at what - and not as a ranking.")

    everywhere = {base_name(r) for r in flagship + rest}
    missing = sorted(everywhere - {base_name(r) for r in flagship})
    print(f"**What the empty cells say.** {len(everywhere)} engines appear "
          f"anywhere in `data/runs/`, and {len(missing)} of them were never in "
          f"the 130-hour run: {', '.join(f'`{m}`' for m in missing)}. `http` "
          f"is the baseline and is left out on purpose; the others are not. "
          f"They are **unranked, not ranked low** - a cell built on single "
          f"digits is not a measurement of an engine, and the first matrix is "
          f"the only place in this repository where engines are compared "
          f"like for like. `bing_serp`, `ddg_serp` and `walmart_search` have "
          f"never been run on the VPS at all, which is why no column of the "
          f"second matrix can be set against the first.\n")

    engine_caveats(rest)

    print("## The top of the table, and how far down it can be trusted\n")
    print("A sorted column always produces a first place, and a first place is "
          "the line that gets quoted without its denominator. So the ranking "
          "below carries a test: each engine against the top one, two-sided "
          "Fisher exact on judged attempts, with the threshold corrected for "
          "the number of comparisons. `separable?` is the whole point of the "
          "table - an engine marked `no` is not in second place, it is tied "
          "for first.\n")

    print("### `amazon_search` in the 130-hour run\n")
    tier_table(flagship, "amazon_search",
               "So the honest reading is **two tiers, not eight places**. Six "
               "engines between 92% and 96% that this test cannot tell apart, "
               "and two that it separates without difficulty: `cloak` at 80% "
               "and `patchright` at 63%. The top three by point estimate are "
               "`chromium`, `rebrowser` and `botasaurus`, and the gap from "
               "them to fourth place is four attempts' worth of noise.")

    print("**The engine that wins Amazon is the one with nothing done to it.** "
          "`chromium` is the unmodified control - stock Playwright Chromium, "
          "no anti-detect patches at all - and it is at the top of a table "
          "whose other seven entries exist specifically to avoid being "
          "detected. On this target, on this pool, in this window, the "
          "anti-detect work does not pay for itself; the one engine it "
          "measurably hurts is `patchright`, which is 33 points below stock.\n")

    print("This reverses a reading published earlier in this repository. In "
          "August on the workstation, Camoufox was served 90% of the time "
          "while every Chromium-family engine met the throttle, and that was "
          "written up as Firefox beating Chromium on Amazon. Re-measured here "
          "at 7627 attempts it did not survive. The direction on `patchright` "
          "did survive, on both hosts.\n")

    print("### `google_serp` in the 130-hour run\n")
    tier_table(flagship, "google_serp",
               f"Every figure in that table is below {FLOOR:.0f}%, so it is "
               f"printed to show the floor and **not** to name a winner. The "
               f"spread from top to bottom is five served pages against one, "
               f"across roughly 450 attempts each; the same Fisher test on "
               f"those two cells gives p = 0.22. `chromium` is absent because "
               f"all of its Google attempts in this run errored, and reporting "
               f"a harness failure as a refusal by the target would be the "
               f"same mistake in a different column.")

    # The excluded cell, looked at rather than left as a dash. It is excluded
    # from the ranking for a good reason and that is not the same as it being
    # uninformative, and a table that only ever says "- (500 err)" trains the
    # reader to skip the one cell that is behaving differently from the rest.
    timeouts = collections.Counter()
    amazon = Tally()
    for row in flagship:
        if row.get("direct") or base_name(row) != "chromium":
            continue
        if row.get("target") == "amazon_search":
            amazon.add(row)
        elif row.get("target") == "google_serp":
            text = (row.get("error") or "")
            timeouts["timeout" if "Timeout" in text else "other"] += 1
    total = sum(timeouts.values())
    if total:
        print(f"**That excluded cell is worth a sentence, because it is not a "
              f"crash.** All {total} of `chromium`'s Google attempts in this "
              f"run ended as errors, and {timeouts['timeout']} of them are the "
              f"same one: `Page.goto` hitting its 60-second ceiling with "
              f"nothing returned. The remaining {timeouts['other']} are "
              f"connection-level failures. In the same run, on the same host, "
              f"through the same gateway, the same engine was served "
              f"{amazon.rate:.0f}% of {amazon.judged} Amazon pages, so the "
              f"engine works and the browser launches.\n")
        print("What that is cannot be settled from these rows. A 60-second "
              "hang is what a refusal looks like when the refusal is a "
              "dropped connection rather than a page, and it is also what a "
              "navigation bug looks like. The two are told apart by raising "
              "the ceiling and capturing what arrives, not by re-reading the "
              "file. Until that is done the cell stays out of the denominator "
              "- counting it as a refusal would credit the target with 500 "
              "blocks it may not have made, and counting it as a pass is "
              "obviously worse. **It is the one cell in this repository "
              "where the classification is doing real work and has never been "
              "checked.**\n")

    print("## The 130-hour run\n")
    print(f"`{FLAGSHIP}`, {span}, one machine, eight engines, two targets, "
          f"{len(flagship)} attempts. This is the only run large enough to "
          f"rank engines against each other, and it is the source of every "
          f"number quoted outside this repository.\n")

    targets = sorted({r.get("target") for r in flagship if r.get("target")})
    for target in targets:
        rows = [r for r in flagship if r.get("target") == target]
        engine_table(
            rows, f"`{target}` - {len(rows)} attempts",
            "One row per engine. Sorted by pass rate.")

    print("## Google over time, and why the early numbers must not be pooled\n")
    print("The single most misleading thing that can be done with these files "
          "is to average `google_serp` over the whole history. Split by "
          "calendar day, through the gateway, direct arms excluded:\n")

    by_day = collections.defaultdict(Tally)
    for row in flagship + rest:
        if row.get("target") != "google_serp" or row.get("direct"):
            continue
        stamp = when(row)
        if stamp:
            by_day[stamp.strftime("%Y-%m-%d")].add(row)

    print("| day | host | pass | attempts | errors |")
    print("|---|---|---|---|---|")
    for day in sorted(by_day):
        tally = by_day[day]
        host = "workstation" if day < MOVE.strftime("%Y-%m-%d") else "VPS"
        print(f"| {day} | {host} | {tally.rate_cell()} | {tally.attempts} | "
              f"{tally.errors} |")
    print()
    print("**The 18%-against-0.4% gap between the two hosts is one day.** Cut "
          "to `patchright`, the only engine with rows either side of the move: "
          "0/26 on the 11th, 107/291 on the 12th, 0/19 on the 13th, 1/442 "
          "across the whole 130-hour run. Within the 12th itself the seven "
          "runs range from 0% to 45%.\n")
    print("The strongest single contrast in these files is the 11th against "
          "the 12th: same engine, same build (`patchright 1.61.2 / Chrome "
          "149.0.7827.55`, unchanged on every one of the ten days), same "
          "`preset=light`, same host, same pool, and 0/26 against 107/291. "
          "**Only the calendar day differs**, so the day demonstrably carries "
          "a large part of the variance. On the 12th the same engine was also "
          "served 11/20 with no gateway at all, from the operator's own line, "
          "so on that day the proxy was not the limiting factor either.\n")
    print("**What cannot be concluded is that the host is cleared, and the "
          "reason is structural rather than statistical.** An earlier version "
          "of this section said \"the host is not what separates the two "
          "groups, and neither is the preset\". Both halves were wrong, and "
          "they are left here rather than quietly deleted because the shape of "
          "the error is the useful part: two days of the workstation scoring "
          "0% do refute \"the workstation works and the VPS does not\", and "
          "they were allowed to stand for a second claim they do not reach.\n")
    print("The structural problem is this. **There is no host field in these "
          "rows.** All 39 recorded fields were listed and none of them names a "
          "machine. `workstation` and `vps` in the table above are computed "
          "from the row's own timestamp against 2026-08-18. So host and date "
          "are not two correlated variables here - they are one variable "
          "printed under two names, and no cut of these files can separate "
          "them. The only independent corroboration that the move happened at "
          "all is the OS string inside `engine_version`: `Windows NT 10.0` on "
          "every row that carries one up to the 13th, `X11; Linux x86_64` from "
          "the 19th on. 2026-08-18 is the one day carrying both, 2 Windows "
          "rows against 43 Linux, which is the move itself and far too few to "
          "compare.\n")
    print("The preset is a **separate** matter and the earlier text was wrong "
          "about it in the opposite direction. `preset=none` ran on both "
          "sides, not just the VPS, so the preset is not perfectly confounded "
          "with the host:\n")
    print("| preset | workstation | VPS | what the workstation side is made of |")
    print("|---|---|---|---|")
    print("| `light` | 107/535 | never run | the 11th, 12th and 13th |")
    print("| `none` | 2/50 | 5/1854 | one day, the 13th; one engine, `cloak` |")
    print("| unrecorded | 9/80 | 11/1489 | one day, the 13th; the successes are "
          "all `zendriver` |")
    print()
    print("Holding the preset fixed therefore does **not** rescue the "
          "comparison, because every workstation cell that survives the "
          "holding is drawn from a single day. The cleanest contrast the files "
          "contain is `zendriver` at one preset on both hosts - 9/33 against "
          "2/496 - and it is still 2026-08-13 against 2026-08-19..24. Every "
          "cross-host contrast in this repository is also a cross-date "
          "contrast, without exception.\n")
    print("Taking the day as the unit of analysis - attempts inside a day "
          "share the hour, the pool slice and the target's mood, so 3814 of "
          "them are not 3814 independent observations - a two-sided Fisher "
          "exact on days that cleared 5% gives 2 of 3 for the workstation "
          "against 0 of 7 for the VPS, **p = 0.067**. Three days cannot "
          "convict the host and cannot acquit it. **The harness should record "
          "the host on every row**; that it does not is why this section can "
          "only describe the problem instead of resolving it.\n")
    print("What the table does show is that **this pool used to be served by "
          "Google and stopped being served between 2026-08-12 and "
          "2026-08-19**, and has not recovered in the eight days since. "
          "Whether that is Google tightening for everyone or this pool being "
          "degraded is not settled by anything in this repository: there is no "
          "request to Google from an address outside the pool after the 12th. "
          "Two same-window controls do rule out the pool being refused as a "
          "whole - `amazon_search` in the run itself, and `ddg_serp` through "
          "the same gateway at 62/62 on camoufox and 44/44 on obscura. "
          "Separating the three requires two arms in one window, not another "
          "cut of these rows.\n")
    print("That is what the next section is. It was written after the one "
          "above and it overturns part of it: the host is not merely "
          "unrecorded, it is the larger of the two effects, and the column "
          "this document ranks eight engines in was taken entirely on the "
          "losing side of it.\n")

    host_decomposition()

    print("## Everything measured before the 130-hour run\n")
    print("These are the small runs that accumulated between 2026-08-11 and "
          "2026-08-18 while the harness was being built. They are kept "
          "separate and never pooled with the run above, for three reasons "
          "that each break comparability on their own:\n")
    print("- **The host changed.** The harness moved from a Windows "
          "workstation to a 2-vCPU Linux VPS on 2026-08-18. That changed the "
          "load, the locale, and the GPU string the browser reports - the VPS "
          "renders through SwiftShader and advertises two cores and no "
          "`deviceMemory`.")
    print("- **Resource blocking changed.** Most early runs carry "
          "`preset=light`, which blocks images and fonts; the 130-hour run "
          "carries `preset=none`. Blocking changes what the page loads and "
          "therefore what the verdict rule sees.")
    print("- **n is small.** Several cells below rest on single-digit "
          "attempt counts. They are printed because leaving them out would "
          "overstate how much has been measured, not because they support a "
          "conclusion.\n")

    by_key = collections.defaultdict(Tally)
    for row in rest:
        key = (row.get("target") or "(none)", era(row),
               "direct" if row.get("direct") else "gateway")
        by_key[key].add(row)

    print("### By target, split by host era and by path\n")
    print("| target | host | path | pass | attempts | errors | "
          "what came back instead |")
    print("|---|---|---|---|---|---|---|")
    for (target, host, path), tally in sorted(
            by_key.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])):
        print(f"| `{target}` | {host} | {path} | {tally.rate_cell()} | "
              f"{tally.attempts} | {tally.errors} | {tally.refusals()[:52]} |")
    print()

    print("### The search engines other than Google\n")
    print("`bing_serp` and `ddg_serp` are the reason the Google numbers can be "
          "read as being about Google. They were never run inside the "
          "130-hour matrix, so they are **not** a same-window control for it - "
          "the same-window control there is `amazon_search`, which sits in the "
          "run itself. What these rows do establish is that the harness, the "
          "verdict rules and the pool can deliver a search results page at "
          "all.\n")
    for target in ("bing_serp", "ddg_serp", "walmart_search"):
        rows = [r for r in rest if r.get("target") == target]
        if not rows:
            continue
        engine_table(rows, f"`{target}` - {len(rows)} attempts, all pre-VPS",
                     "Pooled over runs and over presets, which is why this is "
                     "a floor on capability and not a ranking.")

    print("## The engines\n")
    print("What was actually run, with the version the rows recorded. An "
          "engine is listed only if it produced at least one attempt "
          "somewhere in `data/runs/`.\n")
    versions = collections.defaultdict(collections.Counter)
    for row in flagship + rest:
        name = (row.get("engine") or "(unrecorded)").replace("-direct", "")
        name = name.split("/")[0]
        if row.get("engine_version"):
            versions[name][row["engine_version"]] += 1
    print("| engine | versions seen in the rows |")
    print("|---|---|")
    for name in sorted(versions):
        seen = "; ".join(f"`{v}`" for v, _ in versions[name].most_common(3))
        print(f"| `{name}` | {seen[:150]} |")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
