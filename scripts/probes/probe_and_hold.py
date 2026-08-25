"""Probe and hold: spend one exit to test it, then keep the one that answered.

Every number in `data/runs/` before this script was taken the same way - a fresh
browser navigating straight to `/search?q=`, one query, next address. That is a
measurement of one shape of client, and it is not the shape anyone scrapes with.
This runs the other one, as an operator described it:

    1. new session, one sticky exit held for the whole run
    2. open the front page, not the search URL
    3. type the query into the box and press enter
    4. refused - drop that exit, new session, back to step 1
    5. served - the address is good, hold it and run the series
    6. 8 to 20 seconds between queries on the same working exit

plus, separately claimed and separately measured here: warm the exit by opening
a page or two on the target before the first query.

Three claims, and the script is built so each one has a denominator:

  the entry shape   `--entry home,url` runs both, holding everything else fixed.
                    Without the `url` arm this run could only be compared
                    against rows taken on other days, and Google's yield moves
                    by 20 points between two afternoons
  the warm-up       `--warm on,off` is an axis for the same reason, and it is
                    the biggest claimed effect here: 20% to 75% is larger than
                    any engine difference this repository has measured, which is
                    exactly why it gets a control rather than being switched on
  the hold          every held query carries `position`, so P(pass) at position
                    k is read off the rows. The claimed 88% is a conditional -
                    given a probe that was served - and the probe is the only
                    attempt in an identity that is not

What it does not do is retry a burned exit. Step 4 is "drop the IP", and a
script that instead tried again on the same one would confirm automation to the
target and heat a shared production pool. A cell whose probes keep failing is
stopped by the breaker, and each of those failures already cost a fresh exit.

Usage:
    python scripts/probes/probe_and_hold.py --dry-run
    python scripts/probes/probe_and_hold.py --engines patchright,zendriver \\
        --targets google_serp --identities 8 --series 5 --warm on,off
"""
import argparse
import random
import sys
import time
import uuid
from collections import Counter, defaultdict
from contextlib import ExitStack
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nmbench.console import tolerate_unencodable_output

# Ahead of the engines: zendriver installs colorama's stdout wrapper on import,
# and the wrapper has no reconfigure of its own.
tolerate_unencodable_output()

from nmbench import config, engines, gateway, providers, proxy
from nmbench import queries as querylist
from nmbench.artifacts import ArtifactStore
from nmbench.breaker import CircuitBreaker, TransportWatch
from nmbench.relay import Relay
from nmbench.sink import JsonlSink
from nmbench.targets import TARGETS

SENDS_REQUESTS = True


class TransportLost(RuntimeError):
    """Unwind the run when the failure stops being about the targets."""


class Cell:
    """One question: this engine, this target, this country, entered this way.

    A plain structure with a key, the same shape the matrix runner uses. The key
    reaches every row, so two cells that differ only in whether the exit was
    warmed stay separable in the output - which is the whole point of running
    both in one window.
    """

    def __init__(self, engine, target, country, arm, warm, entry, geo="off"):
        self.engine = engine
        self.target = target
        self.country = country
        self.params_label, self.params = arm
        self.warm = warm
        self.entry = entry
        self.geo = geo
        self.key = (f"{engine}/{target}/{country}/{self.params_label}/"
                    f"warm-{'on' if warm else 'off'}/entry-{entry}/geo-{geo}")


def parse_args():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=__doc__)
    parser.add_argument("--engines", default="patchright",
                        help=f"known: {engines.names()}. An engine that cannot "
                             f"type into a page is refused rather than run "
                             f"through the URL path beside the others")
    parser.add_argument("--targets", default="google_serp",
                        help="only targets declaring a front page and a search "
                             "box can be entered by typing")
    parser.add_argument("--countries", default="any",
                        help="comma separated, an axis. `us` is the worst "
                             "country this repository has measured for Google "
                             "and is the one worth pairing against. NOTE: `any` "
                             "is a value NodeMaven's gateway understands as 'do "
                             "not pin a country', not a harness keyword - it is "
                             "sent through as written. On another provider it is "
                             "an ordinary country code, so it will be refused, or "
                             "worse accepted and silently dropped. Pass a real "
                             "code, or whatever that gateway spells this as")
    parser.add_argument("--identities", type=int, default=6,
                        help="fresh exits per cell. Each one is a probe, and a "
                             "probe that fails has spent an address")
    parser.add_argument("--series", type=int, default=5,
                        help="queries to run on an exit whose probe was served, "
                             "after the probe. The hold stops at the first "
                             "refusal and that position is the burn depth")
    parser.add_argument("--warm", default="on",
                        help="on, off, or on,off to run both in one window")
    parser.add_argument("--entry", default="home",
                        help="home, url, or home,url. `home` lands on the front "
                             "page and types; `url` navigates to the search URL, "
                             "which is how every row already on disk was taken")
    parser.add_argument("--geo", default="off",
                        help="off, align, or off,align to run both in one "
                             "window. `align` sets the browser's timezone from "
                             "the exit address, which is otherwise this "
                             "machine's and contradicts every foreign exit the "
                             "run draws. The language list is left alone on "
                             "purpose: verdict markers are English strings and "
                             "a localised page would be judged by a rule that "
                             "cannot match it")
    parser.add_argument("--gap", default="8,20", metavar="LOW,HIGH",
                        help="seconds between queries on a held exit, uniform")
    parser.add_argument("--dwell", default="3,8", metavar="LOW,HIGH",
                        help="seconds spent on each warm-up page")
    parser.add_argument("--pause", type=float, default=5.0,
                        help="seconds between identities, and the breaker's "
                             "base back-off")
    parser.add_argument("--breaker", type=int, default=6,
                        help="consecutive failed probes that stop a cell. Lower "
                             "than the matrix runner's 10 on purpose: here a "
                             "failed probe has already burned a fresh exit, so "
                             "the cost of waiting to be sure is paid in "
                             "addresses rather than in retries")
    parser.add_argument("--preset", default="none",
                        choices=["none", "light", "aggressive"],
                        help="none by default. This run is about what the "
                             "client looks like, and blocking changes what the "
                             "page loads; it is also unavailable on some "
                             "engines, so a mixed matrix would be blocked for "
                             "half its columns")
    parser.add_argument("--headless", action="store_true",
                        help="the protocol under test is a human-shaped one and "
                             "the default here is headful, which is the "
                             "opposite of the matrix runner. Playwright's "
                             "headless build sends a HeadlessChrome token no "
                             "patching reaches, so a headless arm would be "
                             "measuring that token rather than the protocol")
    parser.add_argument("--direct", action="store_true",
                        help="no gateway. The protocol is about rotating exits, "
                             "so this holds the address constant and answers a "
                             "different question: whether the entry shape alone "
                             "moves anything")
    parser.add_argument("--param", action="append", default=[],
                        metavar="KEY=VALUE",
                        help="extra gateway parameter, repeatable. Held fixed "
                             "across every cell, which is what makes it "
                             "different from --params")
    parser.add_argument("--params", default="none",
                        help="the gateway parameter axis: comma separated arms, "
                             "each a '+' joined set of KEY=VALUE, and 'none' for "
                             "the arm that passes no parameters at all. "
                             "`none,filter=medium,filter=high` runs three slices "
                             "of the pool inside one window. An axis rather than "
                             "three runs because the sticky session key is the "
                             "whole recognised parameter set, so these are "
                             "genuinely different slices, and because a run per "
                             "arm measures the hour: `country=any` moved 69 to "
                             "52 points between two windows of one afternoon")
    parser.add_argument("--seed", type=int, default=None,
                        help="seed for the gaps, the dwells and the typing "
                             "delays. Set it to make a run repeatable; left "
                             "unset the run records nothing that would let a "
                             "target key on the timing")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan, send nothing")
    parser.add_argument("--no-bodies", action="store_true")
    parser.add_argument("--sample-ok", type=int, default=3)
    return parser, parser.parse_args()


def parse_range(text: str, flag: str, parser) -> tuple:
    try:
        low, high = (float(x) for x in text.split(","))
    except ValueError:
        parser.error(f"{flag} wants LOW,HIGH in seconds, got {text!r}")
    if low > high:
        parser.error(f"{flag} has low above high: {text!r}")
    return low, high


def parse_flags(text: str, flag: str, allowed: dict, parser) -> list:
    values = [v.strip() for v in text.split(",") if v.strip()]
    unknown = [v for v in values if v not in allowed]
    if unknown:
        parser.error(f"{flag} does not know {unknown}, known: {sorted(allowed)}")
    if not values:
        parser.error(f"{flag} is empty")
    # Order preserved and duplicates dropped, so `--warm on,on` is one arm.
    seen = []
    for value in values:
        if allowed[value] not in seen:
            seen.append(allowed[value])
    return seen


def parse_param_sets(text: str, extra: dict, parser) -> list:
    """The gateway parameter axis, as (label, params) pairs.

    Each arm is a distinct slice of the pool rather than a formatting choice:
    the sticky session key is the whole recognised parameter set, so an arm that
    adds `filter=high` draws its exits from somewhere else entirely, even with
    the same `sid`. That is the thing being measured.

    Values are checked here rather than at the gateway, because the gateway's
    own reaction is not usable as a check: an unknown *name* answers 200 with
    the setting silently dropped, which would give an arm that looks like a
    result and is the baseline repeated. A bad *value* answers 407, which reads
    as a credential problem. Confirmed 2026-08-13 - `filter=strict` answers 407
    while `low`, `medium` and `high` open a tunnel.
    """
    arms = []
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        params = {}
        if chunk != "none":
            for item in chunk.split("+"):
                if "=" not in item:
                    parser.error(
                        f"--params arm {chunk!r} holds {item!r}, which is not "
                        f"KEY=VALUE. Join a set with '+', separate arms with "
                        f"',', and write 'none' for the arm that passes no "
                        f"parameters at all")
                key, value = item.split("=", 1)
                params[key.strip()] = value.strip()
        for owned, by in (("country", "--countries"),
                          (providers.load().session_param, "the run")):
            if owned in params:
                parser.error(
                    f"--params arm {chunk!r} sets {owned!r}, which is owned by "
                    f"{by}. Two places setting one parameter is how an arm ends "
                    f"up running a set nobody meant and no row can be "
                    f"attributed afterwards")
        clash = sorted(set(params) & set(extra))
        if clash:
            parser.error(
                f"--params arm {chunk!r} and --param both set {clash}. --param "
                f"is what stays fixed across the matrix and --params is what "
                f"varies, so a parameter cannot be in both without one of them "
                f"silently losing")
        try:
            proxy.build_username("check", **params)
        except proxy.ParamError as exc:
            parser.error(str(exc))
        label = "params-" + ("none" if not params else
                             "+".join(f"{k}-{v}" for k, v in params.items()))
        if any(label == seen for seen, _ in arms):
            parser.error(
                f"--params repeats the arm {chunk!r}. Two arms with one "
                f"parameter set are one slice of the pool asked for twice, and "
                f"they would land in one cell key anyway")
        arms.append((label, params))
    if not arms:
        parser.error("--params is empty, so there is no arm to run")
    return arms


def preflight(parser, args, cells) -> None:
    """Everything checkable without sending a request."""
    availability = engines.report_availability()
    for name in sorted({c.engine for c in cells}):
        if availability.get(name):
            parser.error(availability[name])
        engine = engines.REGISTRY[name]
        if not engine.supports_typing:
            parser.error(
                f"engine {name!r} cannot type into a page, so it has no way to "
                f"enter a target through its own front page. Running it through "
                f"the search URL beside engines that typed would put two "
                f"different clients in one entry column and read as an engine "
                f"difference. Drop the engine, or run --entry url for the whole "
                f"matrix and compare the entry shapes with engines that have "
                f"both")
        if not args.headless and not engine.supports_headful:
            parser.error(f"engine {name!r} has no display and cannot run "
                         f"headful, so it would be headless beside headful "
                         f"peers, which is not a comparison. Pass --headless "
                         f"for the whole matrix or drop the engine")
        if any(c.geo == "align" for c in cells) \
                and not engine.supports_geo_align:
            parser.error(
                f"engine {name!r} cannot align its timezone with the exit, so "
                f"the align arm would run it on this machine's own timezone "
                f"while its neighbours ran on the exit's. An option honoured by "
                f"some columns and silently dropped for others reads as an "
                f"engine difference, which is the failure --humanize already "
                f"caused once. Drop the engine, or run --geo off")
        if args.preset != "none" and not engine.supports_blocking:
            parser.error(f"engine {name!r} has no resource blocking, so "
                         f"--preset {args.preset} would apply to some columns "
                         f"and be silently dropped for this one. Use "
                         f"--preset none")

    for name in sorted({c.target for c in cells}):
        target = TARGETS[name]
        if not getattr(target, "home_url", None) \
                or not getattr(target, "search_box", None):
            parser.error(
                f"target {name!r} declares no home_url and search_box, so it "
                f"cannot be entered through its own front door. Add both to the "
                f"target rather than guessing a selector here: a wrong one "
                f"fails as an empty query and is recorded as the target "
                f"refusing")

    if not args.direct and not config.available():
        parser.error("gateway credentials are not set, so nothing can leave "
                     "through the proxy. Copy .env.example to .env, or pass "
                     "--direct to hold the address constant instead.")


def describe(args, cells, per_identity: int) -> None:
    availability = engines.report_availability()
    named = [n if not availability.get(n) else f"{n} (CANNOT RUN)"
             for n in sorted({c.engine for c in cells})]
    print(f"engines   : {named}")
    for name in sorted({c.engine for c in cells}):
        if availability.get(name):
            print(f"            {name}: {availability[name]}")
    print(f"targets   : {sorted({c.target for c in cells})}")
    print(f"cells     : {len(cells)}")
    for cell in cells:
        print(f"            {cell.key}")
    attempts = len(cells) * args.identities * per_identity
    print(f"identities: {args.identities} per cell, "
          f"{len(cells) * args.identities} fresh exits in total")
    print(f"attempts  : at most {attempts}, and fewer whenever a probe is "
          f"refused - a refused probe costs one attempt and ends the identity")
    print(f"entry     : {sorted({c.entry for c in cells})}")
    print(f"warm      : {sorted({'on' if c.warm else 'off' for c in cells})}")
    print(f"geo       : {sorted({c.geo for c in cells})}, timezone only, the "
          f"language list stays the host's")
    print(f"browser   : {'headless' if args.headless else 'headful'}, "
          f"preset {args.preset}")
    if args.direct:
        print("gateway   : none, the address is this machine's own and is held "
              "constant. The rotation half of the protocol is not being tested")
    else:
        print(f"gateway   : country={sorted({c.country for c in cells})}, one "
              f"sid per identity")
        print(f"params    : {sorted({c.params_label for c in cells})}, "
              f"interleaved rather than run one after another")
    # No cost line. `matrix.estimate` prices per attempt from runs taken the
    # other way, and this run's attempt count is decided by how often the probe
    # passes, which is the thing being measured. A number here would be a guess
    # wearing the authority of the calibrated one.
    print("cost      : not estimated. The attempt count depends on the probe "
          "pass rate, which is what this run is measuring, and the byte "
          "constants were calibrated on url-entry runs")


def visit(page, url: str, timeout_ms: int = 60000) -> None:
    """A plain navigation, on whatever page object the engine handed back.

    `goto` is the one method every session's page has, Playwright and zendriver
    alike, which is why warming needs no engine support of its own and no
    capability flag.
    """
    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)


def run_identity(active, page, counter, cell, target, queries, *, rng, args,
                 on_row) -> str:
    """One exit: warm it, probe it, and hold it if the probe was served.

    Reports every row through `on_row` including the warm-up visits, because
    what the caller does after one - byte accounting, the transport watch,
    printing - is the same for all of them and duplicating it per phase is how
    one phase ends up counted differently from another.

    Returns the probe's verdict, which is the only one the breaker is fed.
    """
    low_dwell, high_dwell = args.dwell_range
    if cell.warm:
        # The front page last, always. The warm pages carry search boxes of
        # their own and typing into one of those would ask a different question
        # of a different endpoint.
        for url in [*getattr(target, "warm_urls", ()), target.home_url]:
            started = time.perf_counter()
            error = None
            try:
                visit(page, url)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            # A bookkeeping row: no `query`, so every analysis in this
            # repository treats it as not an attempt and it can never reach a
            # pass rate. It is written anyway because a warm arm whose warming
            # silently failed would read as the warm-up not working, and because
            # warming costs traffic that belongs in the price of the protocol.
            on_row({"phase": "warm", "url": url, "verdict": "warm_visit",
                    "error": error, "position": None, "query": None,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000)})
            time.sleep(rng.uniform(low_dwell, high_dwell))

    low_gap, high_gap = args.gap_range
    probe_verdict = "error"
    for position, query in enumerate(queries):
        if cell.entry == "home":
            row = active.search(page, target, query, rng=rng, counter=counter)
        else:
            # The control arm. `fetch` opens its own page, so this arm holds the
            # browser, the profile and the exit rather than a tab - which is
            # what an identity is, and the tab is the part the entry shape is
            # about. `blank_row` records it as `entry=url`, so the two arms stay
            # separable in the output.
            row = active.fetch(target, query)
        row.update({"phase": "probe" if position == 0 else "hold",
                    "position": position})
        on_row(row)
        if position == 0:
            probe_verdict = row["verdict"]
        # Step 4, and the reason this is not the matrix runner: a refused exit
        # ends the identity instead of being asked again. It is spent either
        # way, and asking it again is what heats a shared production pool.
        if row["verdict"] != "ok":
            break
        time.sleep(rng.uniform(low_gap, high_gap))
    return probe_verdict


def report(row: dict) -> None:
    """One line per row while the run works, warm-up visits included.

    The warm visits are printed because they are the axis with the largest
    claimed effect in this run, and an arm whose warming was silently failing
    would look exactly like the warm-up not working.
    """
    if not row.get("query"):
        note = row.get("error") or "ok"
        print(f"      warm       {(row.get('url') or '')[:44]:<46}"
              f"{row.get('elapsed_ms', 0):>6} ms  {note[:40]}")
        return
    ready = {True: "ready", False: "no-markup", None: "-"}[row.get("ready")]
    print(f"      {row['phase']:<6}#{row['position'] + 1:<4}"
          f"{row['query'][:26]:<28}{row['status'] or '-'!s:<5}"
          f"{row['verdict']:<8}{ready:<10}{row['elapsed_ms']:>6} ms")
    if row.get("error"):
        print(f"        -> {row['error'][:92]}")


def summarise(rows: list) -> None:
    """The three claims, each against the denominator that belongs to it."""
    attempts = [r for r in rows if r.get("query")]
    if not attempts:
        print("\nno attempt completed, so there is nothing to read")
        return

    print("\n" + "=" * 100)
    print(f"{'cell':<52}{'probes':>9}{'served':>9}{'held':>7}{'ok|held':>10}")
    print("-" * 100)
    by_cell = defaultdict(list)
    for row in attempts:
        by_cell[row["cell"]].append(row)
    for key in sorted(by_cell):
        cell_rows = by_cell[key]
        probes = [r for r in cell_rows if r["phase"] == "probe"]
        good = sum(1 for r in probes if r["verdict"] == "ok")
        held = [r for r in cell_rows if r["phase"] == "hold"]
        held_ok = sum(1 for r in held if r["verdict"] == "ok")
        rate = f"{held_ok}/{len(held)}" if held else "-"
        print(f"{key[:52]:<52}{len(probes):>9}{good:>9}{len(held):>7}{rate:>10}")

    # The hold claim, read at the only place it can be: position by position,
    # every row of which is conditional on the probe having been served.
    held = [r for r in attempts if r["phase"] == "hold"]
    if held:
        print("\nHOW LONG A GOOD EXIT LASTS")
        print("  every row here follows a probe that was served, which is the "
              "conditional the 88% claim is about")
        print(f"  {'position':<12}{'n':>6}{'ok':>6}{'pass':>7}")
        by_position = defaultdict(list)
        for row in held:
            by_position[row["position"]].append(row)
        for position in sorted(by_position):
            bucket = by_position[position]
            good = sum(1 for r in bucket if r["verdict"] == "ok")
            print(f"  {'#' + str(position + 1):<12}{len(bucket):>6}{good:>6}"
                  f"{100 * good / len(bucket):>6.0f}%")
        print("  n falls with position because a series stops at its first "
              "refusal, so a later row is drawn from the identities still "
              "alive and reads better than the protocol as a whole")

    print("\nwhy the non-ok verdicts happened")
    print("-" * 100)
    reasons = Counter((r["verdict"], r.get("verdict_reason"))
                      for r in attempts if r["verdict"] != "ok")
    for (verdict, reason), count in reasons.most_common():
        print(f"  {count:>4}  {verdict:<9}{(reason or '')[:70]}")

    counted = sum(r.get("bytes") or 0 for r in attempts)
    print(f"\nattempts: {len(attempts)}   locally counted: "
          f"{counted / 1024 / 1024:.2f} MB (lower bound)")


def main() -> int:
    parser, args = parse_args()

    engine_names = [e.strip() for e in args.engines.split(",") if e.strip()]
    unknown = [n for n in engine_names if n not in engines.REGISTRY]
    if unknown:
        parser.error(f"unknown engines {unknown}, known: {engines.names()}")
    target_names = [t.strip() for t in args.targets.split(",") if t.strip()]
    unknown = [t for t in target_names if t not in TARGETS]
    if unknown:
        parser.error(f"unknown targets {unknown}, known: {sorted(TARGETS)}")
    countries = [c.strip() for c in args.countries.split(",") if c.strip()]
    if not countries:
        parser.error("--countries is empty, so there is nothing to ask for")

    warms = parse_flags(args.warm, "--warm", {"on": True, "off": False}, parser)
    entries = parse_flags(args.entry, "--entry", {"home": "home", "url": "url"},
                          parser)
    geos = parse_flags(args.geo, "--geo", {"off": "off", "align": "align"},
                       parser)
    if args.direct and "align" in geos:
        parser.error("--direct leaves from this machine's own address, and the "
                     "browser already reports this machine's timezone, so "
                     "aligning it changes nothing and would label a cell as an "
                     "arm it is not")
    args.gap_range = parse_range(args.gap, "--gap", parser)
    args.dwell_range = parse_range(args.dwell, "--dwell", parser)
    if args.series < 1:
        parser.error("--series below 1 leaves nothing to hold, which makes this "
                     "the matrix runner with extra steps")

    try:
        extra = proxy.parse_params(args.param)
    except proxy.ParamError as exc:
        parser.error(str(exc))
    if args.direct and extra:
        parser.error("--direct sends nothing through the gateway, so --param "
                     "has nothing to act on")

    arms = parse_param_sets(args.params, extra, parser)
    if args.direct and any(params for _, params in arms):
        parser.error("--direct sends nothing through the gateway, so --params "
                     "has no slice of the pool to select")

    cells = [Cell(engine, target, country, arm, warm, entry, geo)
             for engine in engine_names
             for target in target_names
             for country in (["direct"] if args.direct else countries)
             for arm in arms
             for warm in warms
             for entry in entries
             for geo in geos]

    per_identity = 1 + args.series
    needed = args.identities * per_identity
    try:
        query_sets = {
            name: querylist.load(getattr(TARGETS[name], "query_list",
                                         "serp_1000"), None)
            for name in target_names}
    except (ValueError, FileNotFoundError) as exc:
        parser.error(str(exc))
    for name, available in query_sets.items():
        if len(available) < needed:
            parser.error(
                f"{name} draws from a list of {len(available)} queries and this "
                f"plan wants {needed} distinct ones per cell. Lower "
                f"--identities or --series: repeating a query inside a run "
                f"would let a cached answer count as a pass")

    describe(args, cells, per_identity)
    preflight(parser, args, cells)
    if args.dry_run:
        print("\ndry run: nothing was sent")
        return 0

    rng = random.Random(args.seed)
    local_ip = None if args.direct else gateway.identify_direct().get("exit_ip")
    aligning = any(c.geo == "align" for c in cells)

    sink = JsonlSink("probehold")
    store = ArtifactStore("probehold", run_id=sink.run_id,
                          sample_ok=args.sample_ok,
                          enabled=not args.no_bodies)
    registry = gateway.ExitRegistry()
    breakers = {c.key: CircuitBreaker(c.key, limit=args.breaker,
                                      base_pause=args.pause) for c in cells}
    watch = TransportWatch()
    rows = []
    print(f"\nraw rows -> {sink.path}\n")

    try:
        # Round-robin at identity granularity, so every cell is spread over the
        # whole window. A cell run start to finish would be measuring its own
        # hour: Google's yield moved 17 points between two afternoons of the
        # same experiment.
        for round_index in range(args.identities):
            for cell in cells:
                breaker = breakers[cell.key]
                if breaker.tripped:
                    continue

                target = TARGETS[cell.target]
                first = round_index * per_identity
                queries = query_sets[cell.target][first:first + per_identity]

                sid = f"ph{uuid.uuid4().hex[:8]}"
                params = ({} if args.direct
                          else proxy.session_params(sid, country=cell.country,
                                                    **extra, **cell.params))
                if args.direct:
                    identity = gateway.identify_direct()
                elif aligning:
                    # `identify` prefers the CONNECT reply header, which carries
                    # the address and no timezone, so an aligned cell has to ask
                    # the echo service. Every cell in the run asks it, including
                    # the unaligned arm that has no use for the answer, because
                    # the call is a request through the exit before the browser
                    # opens - which is warming, and warming is a whole axis
                    # here. Paying it in one arm only would put the warm-up
                    # inside the geo comparison.
                    identity = gateway.echo(**params)
                else:
                    identity = gateway.identify(**params)
                identity.update(registry.record(identity.get("exit_ip")))

                timezone_id = identity.get("timezone")
                if cell.geo == "align" and not timezone_id:
                    # The exit is live enough to have been asked and the answer
                    # did not name a zone. Running anyway would write rows
                    # claiming an alignment that never happened, which is worse
                    # than the unaligned arm and unfixable afterwards. The
                    # identity is dropped and said so; the breaker is not fed,
                    # because this is our lookup falling short rather than the
                    # target refusing anything.
                    print(f"  {cell.key}  #{round_index}  skipped: the exit "
                          f"lookup named no timezone, so this identity cannot "
                          f"be aligned and will not be labelled as if it were")
                    sink.write({"cell": cell.key, "target": cell.target,
                                "verdict": "identity_skipped",
                                "verdict_reason": "no timezone for the exit",
                                "geo": cell.geo,
                                "exit_prefix": identity.get("exit_prefix"),
                                "exit_label": identity.get("exit_label")})
                    continue

                options = {"direct": args.direct, "params": params,
                           "preset": args.preset,
                           "headless": args.headless, "humanize": False,
                           "store": store, "geoip": False,
                           "timezone_id": (timezone_id if cell.geo == "align"
                                           else None)}

                wants_relay = (not args.direct
                               and engines.REGISTRY[cell.engine].needs_relay)
                extra_columns = {
                    "identity": f"{cell.key}#{round_index}",
                    "warm": bool(cell.warm),
                    "batch_index": round_index,
                    "geo": cell.geo,
                    "relayed": wants_relay,
                    "exit_prefix": identity.get("exit_prefix"),
                    "exit_label": identity.get("exit_label"),
                    "exit_org": identity.get("org"),
                    # What the exit's own timezone is, on every row and in both
                    # arms. In the aligned arm it is what the browser was set
                    # to; in the unaligned one it is what the browser was
                    # contradicting, which is the quantity the axis is about and
                    # is otherwise unrecoverable once the exit is gone.
                    "exit_timezone": timezone_id,
                }

                print(f"  {cell.key}  #{round_index}  exit "
                      f"{identity.get('exit_ip') or '-'} "
                      f"{(identity.get('org') or '')[:32]}")

                try:
                    with ExitStack() as stack:
                        hop = None
                        if wants_relay:
                            hop = stack.enter_context(Relay(params=params))
                            options["relay_address"] = hop.address
                        active = stack.enter_context(
                            engines.session(cell.engine, **options))
                        if not args.direct and hasattr(active, "exit_ip"):
                            seen = active.exit_ip()
                            if seen and local_ip and seen == local_ip:
                                reason = ("proxy not applied, browser went "
                                          "direct")
                                breaker.trip(reason)
                                print(f"    {cell.key}: the browser left from "
                                      f"this machine's own address. Cell "
                                      f"stopped rather than recorded as a pool "
                                      f"measurement.")
                                sink.write({"cell": cell.key,
                                            "target": cell.target,
                                            "verdict": "cell_stopped",
                                            "verdict_reason": reason})
                                continue

                        counter = {}
                        page = active.new_page(counter)
                        # Differenced across each row rather than reset, because
                        # a browser holds tunnels open on keep-alive and a reset
                        # would post one page's bytes to the next row.
                        meter = {"at": hop.snapshot() if hop else None}

                        def on_row(row, hop=hop, meter=meter, cell=cell,
                                   extra_columns=extra_columns):
                            row.update(extra_columns)
                            row["cell"] = cell.key
                            row["target"] = cell.target
                            if hop is not None:
                                spent = hop.since(meter["at"])
                                row["bytes"] = spent["bytes"]
                                for seen_exit in spent["exits"]:
                                    row["session_exit_prefix"] = (
                                        registry.record(seen_exit)
                                        .get("exit_prefix"))
                                meter["at"] = hop.snapshot()
                            sink.write(row)
                            rows.append(row)
                            report(row)
                            watch.record(cell.key, row["verdict"])
                            if watch.tripped:
                                sink.write({"cell": cell.key,
                                            "target": cell.target,
                                            "verdict": "run_stopped",
                                            "verdict_reason": watch.reason})
                                raise TransportLost(watch.reason)

                        verdict = run_identity(active, page, counter, cell,
                                               target, queries, rng=rng,
                                               args=args, on_row=on_row)
                        # The breaker is fed the probe only. A held query that
                        # fails is the exit burning out, which is the thing
                        # being measured and not a reason to stop asking; a
                        # probe that fails has spent a fresh address for
                        # nothing, and that is the cost worth limiting.
                        breaker.record(verdict)
                        if breaker.tripped:
                            print(f"    {cell.key}: stopped, {breaker.reason}, "
                                  f"each of them a fresh exit spent on a probe "
                                  f"that was refused")
                            sink.write({"cell": cell.key,
                                        "target": cell.target,
                                        "verdict": "cell_stopped",
                                        "verdict_reason": breaker.reason})
                except TransportLost:
                    # The transport, not this identity. It ends the run, and it
                    # must reach the handler below rather than the catch-all.
                    raise
                except engines.EngineUnavailable as exc:
                    breaker.trip(f"engine could not start: {exc}")
                    print(f"    {cell.key}: {exc}")
                    sink.write({"cell": cell.key, "target": cell.target,
                                "verdict": "cell_stopped",
                                "verdict_reason": breaker.reason})
                except Exception as exc:
                    # A browser that would not launch, a relay that would not
                    # bind, a session that died between two queries. All of
                    # these are this machine rather than the target, and one of
                    # them costs one identity - measured 2026-08-13, zendriver
                    # answered `Failed to connect to browser` on one launch and
                    # started normally on the next attempt a minute later, and
                    # the unhandled version of it ended a run that had eleven
                    # other cells still to answer.
                    #
                    # Written as its own verdict rather than as `error`, because
                    # `error` is an attempt that reached the network and this
                    # never did. It is fed to the breaker all the same: an engine
                    # that cannot start at all should stop its own cell rather
                    # than take a fresh exit every round for nothing.
                    reason = f"{type(exc).__name__}: {exc}".strip()
                    # zendriver's launch failure is a multi-line banner, so the
                    # console gets the first line and the row keeps all of it.
                    headline = (reason.splitlines() or [""])[0][:160]
                    breaker.record("error")
                    print(f"    {cell.key}: the session did not start or did "
                          f"not survive. This identity is lost, the cell "
                          f"continues. {headline}")
                    sink.write({"cell": cell.key, "target": cell.target,
                                "verdict": "session_failed",
                                "verdict_reason": reason})
                time.sleep(args.pause)
    except TransportLost as exc:
        print(f"\nrun stopped: {exc}")
        print("Check the transport before restarting: "
              "python scripts/probes/gateway_health.py")
    except KeyboardInterrupt:
        print("\ninterrupted. The answered queries are recorded.")

    summarise(rows)
    print(f"raw rows: {sink.path}")
    print(store.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
