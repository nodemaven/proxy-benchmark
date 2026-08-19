"""What a run says, aggregated into the numbers a decision rests on.

The runner prints one line per cell while it works. That is the right shape for
watching a run and the wrong shape for reading one afterwards: a pass rate per
cell does not say what a delivered page cost, whether a refusal came from the
address or from the browser, or whether the pool was the variable rather than
the engine. This reads the raw rows and answers those. It sends nothing.

It takes several files on purpose. A night is usually more than one run - a
headless matrix and a headful one, or a run and its resume - and those are one
measurement only if they are read as one.

Three rules it does not bend, because each of them is a way a benchmark flatters
itself:

  an error is not a verdict    `error` means the attempt never reached a
                               judgement, so it stays out of the pass rate
                               denominator and gets its own column. Counting it
                               as a failure lets a flaky local network lower a
                               provider's score. Dropping it silently is worse:
                               a target that closes the connection produces
                               nothing but errors, so any engine and target
                               whose attempts are mostly errors is named
                               instead of averaged away
  bytes are a floor            the counters sum Content-Length, which misses
                               headers, TLS overhead and every chunked response.
                               html.duckduckgo.com sends no Content-Length at
                               all. Cost per page is a lower bound and is
                               labelled as one everywhere it appears
  a stopped cell is not a      the breaker stops a cell after N consecutive
  small sample                 failures, so its remaining queries were never
                               sent. Its n is smaller than the others by way of
                               having failed, and a table that does not say so
                               reads as if the cell was merely sampled less
  a stopped cell that was      the breaker cannot tell a refused address from a
  never served a body          failed page, so a run of burned exits stops a
  measured the pool            cell that the target never once answered. That
                               cell reads `unmeasured`, never 0%: it carries no
                               observation of the engine at all

Usage:
    python scripts/analysis/report.py
    python scripts/analysis/report.py data/runs/benchmark_20260812T0100Z.jsonl
    python scripts/analysis/report.py --all
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nmbench.sink import RUNS_DIR

VERDICT_ORDER = ["ok", "captcha", "consent", "block", "empty", "error"]
LABEL_WIDTH = 30
CELL_WIDTH = 16


def resolve(args) -> list:
    if args.paths:
        return [Path(p) for p in args.paths]
    found = sorted(RUNS_DIR.glob("benchmark_*.jsonl"))
    if not found:
        raise SystemExit("no benchmark run in data/runs/")
    return found if args.all else found[-1:]


def load(paths: list) -> list:
    rows = []
    for path in paths:
        if not path.exists():
            raise SystemExit(f"{path} does not exist")
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                # An interrupted run ends in a partial line. That is the normal
                # way a long run ends and it must not cost the rest of the file.
                continue
    return rows


def engine_label(row: dict, countries: set, strip_direct: bool = False) -> str:
    """The engine as the report names it, carrying everything that makes a row
    a different measurement.

    The engine field already distinguishes the build and the proxy side. What it
    does not carry is headful, geo alignment and the exit country, and two rows
    differing in any of those are answers to different questions. The country is
    appended only when the run held more than one, so the common case stays
    readable.
    """
    name, sep, rest = (row.get("engine") or "?").partition("/")
    if strip_direct and name.endswith("-direct"):
        name = name[:-len("-direct")]
    label = name + sep + rest
    if row.get("headless") is False:
        label += " headful"
    if row.get("geo") == "align":
        label += " geo"
    if not strip_direct and len(countries) > 1 and not row.get("direct"):
        label += " " + ((row.get("params") or {}).get("country") or "?")
    return label


def tally(rows: list) -> dict:
    counts = Counter(r.get("verdict") for r in rows)
    errors = counts.get("error", 0)
    return {
        "n": len(rows),
        "judged": len(rows) - errors,
        "ok": counts.get("ok", 0),
        "errors": errors,
        "counts": counts,
        "bytes": sum(r.get("bytes") or 0 for r in rows),
        "html": sum(r.get("html_len") or 0 for r in rows),
        "counted": sum(1 for r in rows if r.get("bytes")),
        "seconds": sum(r.get("elapsed_ms") or 0 for r in rows) / 1000,
    }


def was_served(row: dict) -> bool:
    """True when the target answered with the page that was asked for.

    Two conditions, and the second one cost a wrong headline before it existed.
    The status has to be 200, because a 429 or a 503 lands before any markup is
    built. And the response has to still be on the path that was requested: a
    refusal aimed at the address diverts the request elsewhere, and Google
    serves that diversion with a 200 as often as with a 429.

    Reading the status alone therefore counted every `/sorry/` page as a live
    exit that the engine then failed, which is precisely the attribution this
    report exists to prevent. Measured 2026-08-12 over every run on disk: of
    the 16 Google responses camoufox ever got with a 200, 14 were `/sorry/`, so
    its denominator against Google was 2 and not 16. In the other direction
    patchright read 46 of 62 when 16 of those 62 were diversions it never had a
    page to fail; on pages actually served it is 46 of 46.

    The rule is a property of the exchange and not knowledge about a target, so
    nothing here has to know a target's name. Checked against every row on
    disk: it fires on 386 of 386 Google captchas and on nothing else at all -
    Amazon, Bing, DuckDuckGo and Walmart all serve their refusals inline on the
    requested path, which is correct, because those are refusals aimed at the
    client rather than diversions aimed at the address.
    """
    if row.get("status") != 200:
        return False
    asked, got = row.get("url"), row.get("final_url")
    if not asked or not got:
        return True
    a, b = urlparse(asked), urlparse(got)
    return (a.netloc, a.path) == (b.netloc, b.path)


def classify(row: dict) -> str:
    """served, refused, no reply or no status - never a pass rate over attempts.

    Lives here rather than in the scripts that print it, because it is
    `was_served` plus the two cases that rule cannot answer, and a second copy
    drifts away from it. There was one: `held.py` carried these five lines with
    no docstring and with the row annotated as a string, so the reasoning below
    existed in one of the two places the rule was being applied.

    `no reply` and `no status` are separate buckets and neither is a missing
    value. `no reply` is verdict `error` - the attempt never reached a page, so
    counting it against the address or the engine charges one of them for a
    CONNECT that was never answered. `no status` is a row that has a body and no
    HTTP status: `was_served` needs the status, so such a row cannot be split at
    all, and a figure computed over it is instrument blindness rather than a
    measurement. Naming it keeps those rows in the verdict tables and out of the
    split.

    `no status` is a property of rows, not of engines, and that distinction is
    load bearing: Obscura's adapter stopped losing the status on 2026-08-12, so
    its 114 blank rows all predate the fix and its 19 later ones are complete.
    Reading the bucket as an engine defect is what made this repository report a
    fixed bug as a rate for two days.
    """
    if row.get("verdict") == "error":
        return "no reply"
    if row.get("status") is None:
        return "no status"
    return "served" if was_served(row) else "refused"


def live_responses(rows: list) -> int:
    """How many attempts the target actually served a page for."""
    return sum(1 for r in rows if was_served(r))


def per_page(stats: dict, field: str) -> str:
    """Megabytes of `field` per page actually delivered, or nothing.

    A zero counter is absent evidence and never printed as 0.00: Amazon serves
    its result list chunked with no Content-Length, so a fully arrived 1.4 MB
    page can be recorded as zero wire bytes. Rendering that as a cost would
    claim the cheapest engine in the table was the one whose traffic we failed
    to measure.
    """
    if not stats["ok"] or not stats[field]:
        return "-"
    value = stats[field] / stats["ok"] / 1e6
    # A cell where the counter fired on one attempt out of ten rounds to 0.00,
    # which reads as the cheapest engine in the table rather than the least
    # measured one.
    return f"{value:.2f}" if value >= 0.005 else "<0.01"


def rate(stats: dict) -> str:
    if not stats["judged"]:
        return "-"
    return f"{100 * stats['ok'] / stats['judged']:.0f}%"


def group(rows: list, keyfn) -> dict:
    out = defaultdict(list)
    for row in rows:
        out[keyfn(row)].append(row)
    return out


def print_matrix(title: str, note: str, labels: list, targets: list, cellfn):
    print(f"\n{title}")
    if note:
        print(f"  {note}")
    print("-" * (LABEL_WIDTH + CELL_WIDTH * len(targets)))
    print(f"{'engine':<{LABEL_WIDTH}}"
          + "".join(f"{t:<{CELL_WIDTH}}" for t in targets))
    for label in labels:
        print(f"{label:<{LABEL_WIDTH}}"
              + "".join(f"{cellfn(label, t):<{CELL_WIDTH}}" for t in targets))


def scope(paths: list, attempts: list, bookkeeping: list) -> None:
    stamps = [datetime.fromisoformat(r["ts"]) for r in attempts if r.get("ts")]
    span = ""
    if len(stamps) > 1:
        hours = (max(stamps) - min(stamps)).total_seconds() / 3600
        span = (f", {hours:.1f} h from {min(stamps):%Y-%m-%d %H:%M} to "
                f"{max(stamps):%H:%M} UTC")
    sessions = len({(r.get("cell"), r.get("batch_index")) for r in attempts})
    print("=" * (LABEL_WIDTH + CELL_WIDTH * 4))
    print("SCOPE")
    for path in paths:
        print(f"  {path.name}")
    print(f"  {len(attempts)} attempts over "
          f"{len({r.get('cell') for r in attempts})} cells, "
          f"{sessions} sessions{span}")
    if bookkeeping:
        print(f"  {len(bookkeeping)} bookkeeping rows (cells stopped early)")


def coverage(attempts: list, labels: list, targets: list, cells: dict) -> None:
    """Name any engine that did not face the whole target set.

    Every table below reads across a row, and that is only legitimate when every
    engine was asked the same questions. An engine missing a target would
    otherwise look better or worse for having skipped the hard one.
    """
    gaps = []
    for label in labels:
        missing = [t for t in targets if not cells.get((label, t))]
        if missing:
            gaps.append(f"{label} never ran {', '.join(missing)}")
    if gaps:
        print("\n  INCOMPLETE: totals across targets are not comparable for "
              "these engines")
        for line in gaps:
            print(f"    {line}")


def transport(attempts: list) -> None:
    """Whether this run measured the targets or the path to them.

    First in the report, because everything after it is a count of verdicts and
    an attempt that never completed is not one. On 2026-08-11 a broken tunnel
    produced 46% errors through the gateway against 0% direct, uniformly across
    five unrelated engine stacks, and the pass rates underneath were
    survivorship over that failure: one cell read 100% (10/10) next to ten
    errors it did not count. Every table was arithmetically correct and the
    report as a whole was wrong, because nothing in it asked this question.

    The proxied and direct sides are printed against each other because that
    comparison is the diagnosis. Both sides failing is the machine or the
    targets; one side failing is the path that side uses, and it is not a
    finding about any engine that happened to be running at the time.
    """
    print("\nDID THE RUN MEASURE THE TARGETS OR THE PATH TO THEM")
    print("-" * (LABEL_WIDTH + CELL_WIDTH * 4))
    errors = [r for r in attempts if r.get("verdict") == "error"]
    if not errors:
        print("  every attempt completed and was judged, so no number below is "
              "survivorship over attempts that never happened")
        return

    print(f"  {len(errors)} of {len(attempts)} attempts never completed "
          f"({len(errors) / len(attempts):.0%}), over "
          f"{len({r.get('cell') for r in errors})} of "
          f"{len({r.get('cell') for r in attempts})} cells")

    for name, rows in [("through the gateway",
                        [r for r in attempts if not r.get("direct")]),
                       ("direct", [r for r in attempts if r.get("direct")])]:
        if not rows:
            continue
        failed = sum(1 for r in rows if r.get("verdict") == "error")
        print(f"    {name:<22}{failed:>4} of {len(rows):<6}"
              f"{failed / len(rows):>4.0%}")

    hours = defaultdict(list)
    for row in attempts:
        if row.get("ts"):
            hours[datetime.fromisoformat(row["ts"]).strftime("%H:00")].append(row)
    if len(hours) > 2:
        # A transport that degrades is invisible in a total: an hour at 80%
        # averages with an hour at 20% into a number that describes neither and
        # looks survivable.
        print("  error share by hour, UTC")
        for hour in sorted(hours):
            rows = hours[hour]
            failed = sum(1 for r in rows if r.get("verdict") == "error")
            bar = "#" * round(20 * failed / len(rows))
            print(f"    {hour}  {failed / len(rows):>4.0%}  {bar}")

    proxied = [r for r in attempts if not r.get("direct")]
    failed = [r for r in proxied if r.get("verdict") == "error"]
    # 0.50, and it was 0.15 until 2026-08-19. This is the analysis-side copy of
    # the mistake `TransportWatch` made at 0.6: an absolute line cannot tell
    # "the errors are new" from "the errors have always been this high", and on
    # this gateway the second is the normal condition. The unanswered-CONNECT
    # floor is 21.8% on the workstation and 25% on the server, and it reaches
    # 34-37% in a matrix. Read over every committed run, 0.15 fired on eight,
    # and six of them sat inside that floor - including `20260811T154830Z` and
    # `...T213030Z`, which are the two runs the DuckDuckGo finding rests on. So
    # the line told a reader not to quote the rows this repository quotes.
    #
    # 0.50 keeps the two that are genuinely broken (74% and 50%) and drops the
    # six that are the gateway behaving as documented. It costs nothing in
    # detection for the same reason 0.85 does not: a broken transport is not
    # half errors, it is nearly all of them.
    if proxied and len(failed) / len(proxied) >= 0.50 \
            and len({r.get("cell") for r in failed}) >= 3:
        print("\n  WARNING: errors at this level across this many cells are not "
              "a property of any engine or of any target. Unrelated engine "
              "stacks do not fail together except through what they share, "
              "which is the path. This is well above the gateway's own "
              "unanswered-CONNECT floor of 21.8-25%, which is why it reads as "
              "a broken path rather than as a bad afternoon. Read every pass "
              "rate below as survivorship over that, and fix the transport "
              "before quoting one.")


def yield_split(attempts: list, label_of) -> None:
    """Pass rate split into the address failing and the engine failing.

    A pass rate is a product of two independent things:

        success = P(the exit is still alive) x P(the engine passes | it is)

    and they belong to different owners. The first is the pool the provider
    sold us, the second is the framework we chose. Collapsed into one number
    they are unattributable, and the collapse is not academic: measured
    2026-08-12, patchright on Google was served 0 of 12 completed attempts from
    US exits and 9 of 13 from `any`, while the conditional term was 25 of 25
    across every country in the sweep. Reported as one number that is an engine
    that works and an engine that does not; reported as two it is one engine
    and two very different address pools.

    A live exit is one the target served the requested page for, which is what
    `was_served` decides: a 200 that is still on the path that was asked for.
    An earlier version of this read the status alone, and it was wrong in both
    directions at once, because Google serves its `/sorry/` diversion with a
    200 about a quarter of the time. See `was_served` for the sizes.

    Targets that refuse with a 200 and a stub body on the requested path still
    do not decompose this way and are deliberately left out rather than shown
    wrong: Walmart and Amazon both answer 200 for refusals without diverting,
    so for them every attempt looks like a live exit and the split would
    silently report the pass rate twice.
    """
    proxied = [r for r in attempts if not r.get("direct")
               and r.get("status") is not None]
    if not proxied:
        return
    # Only where the exchange actually separates the two, which is where some
    # refusals arrive without the requested page being served at all. A target
    # that always answers 200 on the requested path, refusals included, tells
    # us nothing here and is skipped rather than guessed at.
    usable = sorted({r["target"] for r in proxied
                     if any(x["target"] == r["target"] and not was_served(x)
                            and x.get("verdict") != "ok" for x in proxied)})
    if not usable:
        return

    print("\nWHOSE FAILURE WAS IT: THE ADDRESS OR THE ENGINE")
    print("  live means the target served the page that was asked for. Only "
          "targets that sometimes do not can be split this way, so one that "
          "answers 200 on the requested path even for refusals is omitted "
          "rather than shown wrong")
    print("-" * (LABEL_WIDTH + CELL_WIDTH * 4))
    for target in usable:
        rows = [r for r in proxied if r["target"] == target]
        print(f"\n  {target}")
        print(f"    {'cell':<34}{'live':>10}{'P(live)':>9}"
              f"{'ok|live':>9}{'P(pass|live)':>14}")
        # Grouped by cell, never by exit. At --batch 1 every attempt draws its
        # own exit, so grouping on the exit label produces one row per attempt
        # and a column of 0% and 100% that reads like a measurement.
        buckets = defaultdict(list)
        for row in rows:
            buckets[label_of(row)].append(row)
        for label in sorted(buckets):
            bucket = buckets[label]
            live = [r for r in bucket if was_served(r)]
            ok = sum(1 for r in live if r.get("verdict") == "ok")
            p_live = f"{len(live) / len(bucket):.0%}"
            p_pass = f"{ok / len(live):.0%}" if live else "-"
            print(f"    {label[:34]:<34}{len(live):>4}/{len(bucket):<5}"
                  f"{p_live:>9}{ok:>5}/{len(live):<3}{p_pass:>14}")

        live = [r for r in rows if was_served(r)]
        ok = sum(1 for r in live if r.get("verdict") == "ok")
        if live:
            print(f"    {'all cells':<34}{len(live):>4}/{len(rows):<5}"
                  f"{len(live) / len(rows):>8.0%}{ok:>5}/{len(live):<3}"
                  f"{ok / len(live):>13.0%}")
            if len(live) < 10:
                print(f"    the conditional term rests on {len(live)} live "
                      f"responses, which is too few to quote as a rate")

        # A target can refuse both ways. Amazon answered 503 on 47 refusals and
        # 200 on 7 in the 2026-08-12 runs, and every one of those 7 is counted
        # above as a live exit the engine then failed - which moves blame from
        # the address to the framework, the exact error this section exists to
        # prevent. Printed rather than corrected for: the split is still the
        # best available reading, and the size of what it cannot see is the
        # thing a reader needs.
        leaked = sum(1 for r in live if r.get("verdict") not in ("ok", "error"))
        if leaked:
            print(f"    CAVEAT: {leaked} of {len(live)} live responses were "
                  f"refusals served as 200, so they are counted as a live exit "
                  f"the engine failed. This target refuses both ways and the "
                  f"conditional term above is a floor by that much")


def headline(labels: list, targets: list, cells: dict) -> None:
    """One row per engine over the whole target set, controls kept apart.

    Readable only because every engine was asked the same targets, which
    `coverage` has just checked. A mean over a different set of targets per
    engine would be an average of different questions.

    The direct rows are printed below the ranking and never inside it. They
    bypassed the gateway, so they are not a product anyone can buy and not a
    competitor to the proxied engines: a table that sorted them together would
    put "this office line, no proxy" at the top and read as a verdict on the
    provider. Their job is the pair in `attribution`, where the same engine is
    compared with itself.
    """
    print("\nOVERALL, ACROSS EVERY TARGET")
    print("  pass rate is over judged attempts: errors are excluded from the "
          "denominator and shown separately")
    print("-" * (LABEL_WIDTH + CELL_WIDTH * 4))
    print(f"{'engine':<{LABEL_WIDTH}}{'pages':>8}{'judged':>8}{'pass':>7}"
          f"{'err':>6}{'req/page':>10}{'html MB':>9}{'wire MB':>9}")

    def emit(entry):
        _, label, stats, spent = entry
        print(f"{label:<{LABEL_WIDTH}}{stats['ok']:>8}{stats['judged']:>8}"
              f"{rate(stats):>7}{stats['errors']:>6}{spent:>10}"
              f"{per_page(stats, 'html'):>9}{per_page(stats, 'bytes'):>9}")

    ranked, controls = [], []
    for label in labels:
        merged = [r for t in targets for r in cells.get((label, t), [])]
        if not merged:
            continue
        stats = tally(merged)
        spent = f"{stats['n'] / stats['ok']:.1f}" if stats["ok"] else "-"
        entry = (stats["ok"] / stats["judged"] if stats["judged"] else -1,
                 label, stats, spent)
        (controls if merged[0].get("direct") else ranked).append(entry)

    for entry in sorted(ranked, reverse=True):
        emit(entry)
    if controls:
        banner = " controls, not products: these bypassed the gateway "
        pad = LABEL_WIDTH + CELL_WIDTH * 4 - len(banner)
        print("-" * (pad // 2) + banner + "-" * (pad - pad // 2))
        for entry in sorted(controls, reverse=True):
            emit(entry)
        print("  a direct row is one office line in one country. It is the "
              "baseline the proxied rows are read against, never a rival to "
              "them")
    print("  req/page counts every attempt spent, including the ones that "
          "failed, per page actually delivered")
    print("  html MB is the decoded markup, always measured. wire MB is the "
          "local counter and is a floor: it sums Content-Length, so a chunked "
          "response counts as nothing and shows as '-'")


def verdict_shape(labels: list, targets: list, cells: dict,
                  stopped: dict) -> None:
    """What each engine met at each target, in full.

    The pass rate matrix says how often, and this says what happened the rest of
    the time. `empty` and `block` are the pair that has to stay separate: one is
    our client coming up short of a page that was served, the other is the
    target refusing.
    """
    print("\nWHAT CAME BACK, PER ENGINE AND TARGET")
    print("-" * 100)
    for target in targets:
        print(f"\n  {target}")
        for label in labels:
            rows = cells.get((label, target))
            if not rows:
                continue
            stats = tally(rows)
            shown = " ".join(f"{v}={stats['counts'][v]}" for v in VERDICT_ORDER
                             if stats["counts"][v])
            key = (label, target)
            note = f"   STOPPED: {stopped[key]}" if key in stopped else ""
            if key in stopped and not live_responses(rows):
                note += ", and never served a body, so this cell measured the "
                note += "pool and not the engine"
            print(f"    {label:<{LABEL_WIDTH}}{rate(stats):>5}  {shown}{note}")


def attribution(attempts: list, countries: set, targets: list) -> None:
    """The proxy side against the direct side, for the engines that ran both.

    This is the pair that turns a refusal into a location. The same engine in
    the same time window, differing only in whether it went through the gateway:
    a gap means the address was the variable, no gap means the browser was, and
    the two together bracket what the gateway contributes. Without it, a report
    can say a target refused and cannot say what it refused.
    """
    paired = group(attempts, lambda r: (engine_label(r, countries, True),
                                        r.get("target")))
    bases = sorted({label for label, _ in paired})
    interesting = []
    for base in bases:
        for target in targets:
            rows = paired.get((base, target), [])
            through = [r for r in rows if not r.get("direct")]
            around = [r for r in rows if r.get("direct")]
            if through and around:
                interesting.append((base, target, tally(through), tally(around)))
    if not interesting:
        print("\nPROXY AGAINST DIRECT")
        print("  no engine ran on both sides of the gateway in these files, so "
              "nothing here can say whether a refusal was aimed at the address "
              "or at the browser. Add ':direct' to an engine spec to get it")
        return

    print("\nPROXY AGAINST DIRECT, SAME ENGINE AND TIME WINDOW")
    print("  the gap is what the exit address cost or bought")
    print("-" * 100)
    print(f"{'engine':<{LABEL_WIDTH}}{'target':<16}{'proxied':>10}"
          f"{'direct':>10}{'gap':>8}   reading")
    for base, target, through, around in interesting:
        if not through["judged"] or not around["judged"]:
            continue
        left = 100 * through["ok"] / through["judged"]
        right = 100 * around["ok"] / around["judged"]
        gap = left - right
        if abs(gap) < 10:
            reading = "the address is not the variable here"
        elif gap > 0:
            reading = "the pool exit is accepted where this line is not"
        else:
            reading = "the pool exit is refused where this line is not"
        print(f"{base:<{LABEL_WIDTH}}{target:<16}"
              f"{left:>9.0f}%{right:>9.0f}%{gap:>+7.0f}   {reading}")
    print("  direct rows left from the operator's own address, which is one "
          "line in one country and not a sample of anything")


def pool(attempts: list) -> None:
    """The exits themselves, because the provider is one of the axes.

    An engine comparison assumes the pool was not the variable. That assumption
    is worth printing rather than making: if a handful of exits served most of
    the run, or if pass rate splits by network, the engine column was measuring
    the pool.
    """
    proxied = [r for r in attempts if not r.get("direct")]
    if not proxied:
        return
    print("\nTHE EXITS THE GATEWAY GAVE US")
    print("-" * 100)
    prefixes = Counter(r.get("exit_prefix") for r in proxied
                       if r.get("exit_prefix"))
    sessions = defaultdict(set)
    for row in proxied:
        if row.get("exit_prefix"):
            sessions[row["exit_prefix"]].add((row.get("cell"),
                                              row.get("batch_index")))
    repeated = sum(1 for p, s in sessions.items() if len(s) > 1)
    print(f"  {len(proxied)} proxied attempts from {len(prefixes)} distinct /24 "
          f"prefixes")
    if repeated:
        print(f"  {repeated} of those prefixes served more than one session, "
              f"so those sessions were not independent identities")
    elif sessions:
        print("  no prefix served two sessions, so every session was a "
              "distinct /24")
    if not prefixes:
        print("  no exit address was recorded, so nothing here can be said "
              "about the pool")
        return

    by_org = group([r for r in proxied if r.get("exit_org")],
                   lambda r: r["exit_org"])
    sizeable = [(org, tally(rows)) for org, rows in by_org.items()
                if len(rows) >= 10]
    if not sizeable:
        print("  no network served 10 attempts, which is too few to compare")
        return
    print(f"\n  {'network':<52}{'n':>5}{'pass':>7}")
    ranked = sorted(sizeable, key=lambda item: -item[1]["n"])[:12]
    for org, stats in ranked:
        print(f"  {org[:50]:<52}{stats['n']:>5}{rate(stats):>7}")
    print("  every network here served a mix of engines, so a low rate is only "
          "the network's fault if the mix was even. Read it as a flag for a "
          "targeted re-run, never as a provider verdict on its own")


def decay(attempts: list) -> None:
    """Pass rate by position inside the browser.

    A session is the unit of measurement, so the question a scraper actually
    asks is how long an identity lasts. If the tenth query in a browser passes
    as often as the first, the batch size is free; if it does not, every number
    in this report belongs to the batch size it was measured at.
    """
    by_index = group([r for r in attempts if r.get("session_index") is not None],
                     lambda r: r["session_index"])
    if len(by_index) < 2:
        return
    print("\nHOW LONG AN IDENTITY LASTS INSIDE ONE BROWSER")
    print("-" * 100)
    print(f"  {'query in session':<20}{'n':>6}{'pass':>7}")
    for index in sorted(by_index):
        stats = tally(by_index[index])
        print(f"  {'#' + str(index + 1):<20}{stats['n']:>6}{rate(stats):>7}")
    print("  n falls with position wherever a cell was stopped early, so a "
          "later row is drawn from the cells that were still alive and reads "
          "better than the run as a whole")


def systematic_errors(labels: list, targets: list, cells: dict) -> None:
    """Name the cells that mostly threw instead of answering.

    Errors are out of the pass rate by design, and that design has a hole: a
    target that closes the connection produces nothing else, and its refusal
    would leave no trace in any table above. A cell that is mostly errors is a
    finding to chase, not noise to average away.
    """
    flagged = []
    for label in labels:
        for target in targets:
            rows = cells.get((label, target), [])
            if len(rows) >= 5:
                stats = tally(rows)
                if stats["errors"] / stats["n"] >= 0.2:
                    sample = next((r.get("error") for r in rows
                                   if r.get("error")), "")
                    flagged.append((label, target, stats, sample))
    if not flagged:
        return
    print("\nCELLS THAT MOSTLY DID NOT COMPLETE")
    print("  excluded from every pass rate above, so they are printed here or "
          "they leave no trace at all")
    print("-" * 100)
    for label, target, stats, sample in flagged:
        share = 100 * stats["errors"] / stats["n"]
        print(f"  {label:<{LABEL_WIDTH}}{target:<16}{share:>4.0f}% of "
              f"{stats['n']} attempts")
        print(f"      {(sample or '')[:88]}")
    print("  a connection closed on every attempt is a refusal with no body to "
          "judge. Read the kept bodies before calling it a harness fault")


def caveats(attempts: list, countries: set) -> None:
    """The limits of the claim, read off the data rather than remembered.

    A written report inherits whatever the run actually did, and the parts most
    likely to be forgotten are the ones that were never chosen consciously: the
    host timezone, the headless flag, which side of the gateway a row came from.
    """
    print("\nWHAT THESE NUMBERS DO NOT COVER")
    print("-" * 100)
    geo = {r.get("geo") for r in attempts}
    if geo <= {"off", None}:
        print("  geo alignment was off for every row, so each engine reported "
              "this machine's own timezone and language list while leaving "
              "from the exit country. That inconsistency is identical across "
              "the engines, so the comparison holds, but the absolute rates "
              "are a floor")
    else:
        print(f"  geo alignment varies across these rows ({sorted(geo)}), so "
              f"the engine columns are not all answering the same question")
    if countries:
        print(f"  exit countries requested: {', '.join(sorted(countries))}")
    headless = {r.get("headless") for r in attempts if r.get("headless")
                is not None}
    if headless == {True}:
        print("  every browser ran headless. The Chromium family sends a "
              "HeadlessChrome token in its User-Agent that no driver patch "
              "reaches, so its columns carry a marker the headful ones do not")
    elif headless == {False}:
        print("  every browser ran headful")
    if any(r.get("direct") for r in attempts):
        print("  the direct rows reached the targets from the operator's own "
              "address. They are a control, not a second provider")
    counted = sum(1 for r in attempts if r.get("bytes"))
    print(f"  the local byte counter recorded traffic for {counted} of "
          f"{len(attempts)} attempts. It sums Content-Length, so a chunked "
          f"response counts as nothing: wire cost is a floor and the missing "
          f"cells are unmeasured, not free")
    print("  a verdict is a claim about a body, and the bodies are kept. Any "
          "line here can be rechecked offline without asking a target again")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*",
                        help="run files, default the newest benchmark run")
    parser.add_argument("--all", action="store_true",
                        help="read every benchmark run in data/runs/. Only "
                             "meaningful when they share a matrix: rows from "
                             "different weeks aggregate into a number that "
                             "belongs to neither")
    args = parser.parse_args()

    paths = resolve(args)
    rows = load(paths)
    attempts = [r for r in rows if r.get("query")]
    bookkeeping = [r for r in rows if not r.get("query")]
    if not attempts:
        raise SystemExit(f"{', '.join(p.name for p in paths)} holds no attempts")

    countries = {(r.get("params") or {}).get("country") for r in attempts
                 if not r.get("direct")}
    countries.discard(None)

    cells = group(attempts, lambda r: (engine_label(r, countries),
                                       r.get("target")))
    labels = sorted({label for label, _ in cells})
    targets = sorted({target for _, target in cells})

    stopped = {}
    for row in bookkeeping:
        if row.get("verdict") != "cell_stopped":
            continue
        for (label, target), group_rows in cells.items():
            if group_rows[0].get("cell") == row.get("cell"):
                stopped[(label, target)] = row.get("verdict_reason")

    scope(paths, attempts, bookkeeping)
    coverage(attempts, labels, targets, cells)
    transport(attempts)
    # Before the pass rates, deliberately. A reader who sees one number first
    # anchors on it, and the whole point of the split is that the single number
    # is the one that cannot be attributed to anybody.
    yield_split(attempts, lambda r: engine_label(r, countries))

    def pass_cell(label, target):
        rows = cells.get((label, target))
        if not rows:
            return "-"
        stats = tally(rows)
        if (label, target) in stopped and not live_responses(rows):
            # The breaker counts a refused address exactly like a failed page,
            # so a cell can be stopped by ten burned exits in a row without the
            # target ever having served it anything. Printing 0% there hands
            # the pool's condition to the framework, which is the single
            # attribution mistake this report exists to prevent. Measured
            # 2026-08-12: on a night when `any` yielded 27% live exits against
            # 77% the previous afternoon, Google cells were reaching the
            # breaker holding zero live responses.
            return f"unmeasured n={stats['judged']}*"
        marker = "*" if (label, target) in stopped else ""
        return f"{rate(stats)} ({stats['ok']}/{stats['judged']}){marker}"

    print_matrix(
        "PASS RATE: HOW OFTEN THE PAGE ACTUALLY ARRIVED",
        "ok over judged attempts. * marks a cell the breaker stopped, whose "
        "remaining queries were never sent. `unmeasured` is such a cell that "
        "was never served a single body, so it says nothing about the engine",
        labels, targets, pass_cell)

    def cost_cell(label, target):
        rows = cells.get((label, target))
        if not rows:
            return "-"
        stats = tally(rows)
        if not stats["ok"]:
            return "no page"
        return f"{stats['n'] / stats['ok']:.1f} req {per_page(stats, 'html')}MB"

    print_matrix(
        "COST OF ONE DELIVERED PAGE",
        "attempts spent per page that arrived, and the decoded markup with "
        "them. Wire bytes are in the table below",
        labels, targets, cost_cell)

    headline(labels, targets, cells)
    verdict_shape(labels, targets, cells, stopped)
    attribution(attempts, countries, targets)
    pool(attempts)
    decay(attempts)
    systematic_errors(labels, targets, cells)
    caveats(attempts, countries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
