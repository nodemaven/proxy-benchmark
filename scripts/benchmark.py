"""The benchmark: several frameworks against several targets, one time window.

This is the matrix runner. Everything it knows about a framework comes from the
engine registry, so adding one is a module and a line, never an edit here - a
runner that can name a framework is a runner that can treat it differently.

What it guarantees:

  interleaving   cells take turns at batch granularity, so no framework is
                 judged by a different hour than another
  sessions       queries are grouped into one browser per batch, because a
                 session is the unit the numbers describe
  attribution    every row carries the full parameter set, the engine version
                 and the reason behind its verdict
  resume         an interrupted run continues instead of re-sending queries the
                 targets have already answered
  restraint      a cell that fails `--breaker` times in a row stops and stays
                 stopped, and the run says so rather than rotating a session
                 and trying again. A failure shared by many cells at once stops
                 the whole run instead, because that one is not about the
                 targets and a night of it is worth nothing
  disclosure     the cost is printed before anything is sent, and --dry-run
                 prints it without sending at all

Usage:
    python scripts/benchmark.py --dry-run
    python scripts/benchmark.py --engines camoufox --targets bing_serp --queries 50
    python scripts/benchmark.py --engines camoufox,obscura --queries 1000 --batch 10
    python scripts/benchmark.py --resume data/runs/benchmark_20260811T120000Z.jsonl
"""
import argparse
import sys
import time
import uuid
from collections import Counter, defaultdict
from contextlib import ExitStack
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nmbench.console import tolerate_unencodable_output

# Ahead of the engines: zendriver installs colorama's stdout wrapper on import,
# and the wrapper has no reconfigure of its own.
tolerate_unencodable_output()

from nmbench import config, engines, gateway, matrix, providers, proxy
from nmbench import queries as querylist
from nmbench.artifacts import ArtifactStore
from nmbench.breaker import CircuitBreaker, TransportWatch
from nmbench.engines.base import validate_preset
from nmbench.relay import Relay
from nmbench.sink import RUNS_DIR, JsonlSink
from nmbench.targets import TARGETS


class TransportLost(RuntimeError):
    """Raised to unwind the run when the failure is no longer about the targets.

    An exception rather than a flag, because the loop it has to leave is two
    levels deep inside an open browser session, and the context manager closing
    that browser on the way out is the behaviour we want.
    """


DEFAULT_ENGINES = ["camoufox"]
DEFAULT_TARGETS = ["google_serp", "bing_serp", "ddg_serp"]


def parse_args():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=__doc__)
    parser.add_argument("--engines", default=",".join(DEFAULT_ENGINES),
                        help=f"known: {engines.names()}. Add ':direct' to an "
                             f"engine to run it without the gateway in the same "
                             f"matrix, for example chromium,chromium:direct - "
                             f"the pair brackets what the gateway contributes, "
                             f"and only means something when both run in one "
                             f"time window")
    parser.add_argument("--targets", default=",".join(DEFAULT_TARGETS))
    parser.add_argument("--queries", default="30",
                        help="how many queries from the list, or 'all'")
    parser.add_argument("--query-list", default=None,
                        help=f"known: {querylist.available()}. By default each "
                             f"target draws from the list it declares, so a "
                             f"shop and a search engine can run in one time "
                             f"window without being sent each other's inputs. "
                             f"Setting this forces one list on all of them")
    parser.add_argument("--batch", type=int, default=10,
                        help="queries per session. One browser is launched per "
                             "batch and every query inside it is the same "
                             "device to the target")
    parser.add_argument("--preset", default="light",
                        choices=["none", "light", "aggressive"])
    parser.add_argument("--countries", default="us",
                        help="comma separated, an axis of the matrix. The "
                             "browser reports this machine's own timezone and "
                             "language list, so an exit in the country the "
                             "machine is in is a consistent identity and an "
                             "exit anywhere else is not. Running both in one "
                             "time window is what measures the difference")
    parser.add_argument("--providers", default=None,
                        help=f"comma separated, an axis of the matrix. "
                             f"Defined: {providers.names()}, and adding one is a "
                             f"file in data/providers/. Interleaved at batch "
                             f"granularity like every other axis, because "
                             f"provider A at 10:00 against provider B at 14:00 "
                             f"measures the afternoon. Defaults to "
                             f"{providers.default_name()!r}")
    parser.add_argument("--geo", default="off", choices=["off", "align"],
                        help="'align' gives the browser the exit's own timezone "
                             "instead of this machine's. Not every engine "
                             "declares the capability, and a matrix holding one "
                             "that does not is refused rather than run with the "
                             "flag dropped for that column. Off by default: it "
                             "was measured on 2026-08-14 and buys nothing on "
                             "patchright while costing zendriver most of its "
                             "yield")
    parser.add_argument("--pause", type=float, default=5.0)
    parser.add_argument("--breaker", type=int, default=10,
                        help="consecutive failures that stop a cell. Higher "
                             "sees more of a partial refusal before giving up "
                             "and costs that many confirmed-automation retries "
                             "against the target, so it is a pool-safety "
                             "setting and not a patience setting")
    parser.add_argument("--direct", action="store_true",
                        help="no proxy: the targets see this machine's own "
                             "address. A control, not a normal mode")
    parser.add_argument("--headful", action="store_true",
                        help="a real window with a real compositor and GPU. "
                             "Not every engine can do it")
    parser.add_argument("--humanize", action="store_true",
                        help="humanized cursor movement, where the engine has "
                             "it. Camoufox and cloak declare it and the rest "
                             "do not, so the runner refuses the flag for a "
                             "matrix holding any engine without it rather than "
                             "applying it to some columns and not others")
    parser.add_argument("--param", action="append", default=[],
                        metavar="KEY=VALUE",
                        help="extra gateway parameter, repeatable. Every "
                             "recognised parameter belongs to the sticky "
                             "session key, so adding one selects a different "
                             "session and possibly a different slice of the pool")
    parser.add_argument("--resume", nargs="?", const="auto", default=None,
                        metavar="PATH",
                        help="skip attempts already judged, in the given file "
                             "or in the newest benchmark run")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan and its cost, send nothing")
    parser.add_argument("--no-bodies", action="store_true",
                        help="do not keep response bodies. Saves disk and gives "
                             "up the ability to re-judge this run offline: every "
                             "verdict here is a claim about a body, and a "
                             "classifier fix cannot be applied backwards to rows "
                             "whose bodies were thrown away")
    parser.add_argument("--sample-ok", type=int, default=2,
                        help="how many passing bodies to keep per engine and "
                             "target. Failures are always kept in full; "
                             "successes only have to prove the classifier still "
                             "recognises a good page")
    parser.add_argument("--channel", default=None,
                        help="browser build for the Chromium engines, for "
                             "example chrome. It reaches the cell key, so two "
                             "builds of one engine stay separable in the output")
    return parser, parser.parse_args()


def resolve_resume(value: str) -> list:
    if value is None:
        return []
    if value != "auto":
        return [Path(value)]
    found = sorted(RUNS_DIR.glob("benchmark_*.jsonl"))
    return found[-1:] if found else []


def preflight(parser, args, cells, chosen: dict) -> None:
    """Everything that can be checked without sending a request."""
    availability = engines.report_availability()
    for name in {c.engine for c in cells}:
        if availability.get(name):
            parser.error(availability[name])
        engine = engines.REGISTRY[name]
        if args.headful and not engine.supports_headful:
            parser.error(f"engine {name!r} has no display and cannot run "
                         f"headful: the run would silently be headless for it "
                         f"and headful for the others, which is not a comparison")
        if args.geo == "align" and not engine.supports_geo_align:
            parser.error(f"engine {name!r} cannot align its timezone and "
                         f"locale with the exit address, so --geo align would "
                         f"be applied to some engines and silently dropped for "
                         f"this one. Either drop the engine or run --geo off "
                         f"and use --countries to put the machine's own country "
                         f"in the matrix, which aligns every engine at once")
        if args.preset != "none" and not engine.supports_blocking:
            parser.error(f"engine {name!r} has no resource blocking, so "
                         f"--preset {args.preset} would be applied to the "
                         f"engines driven through Playwright and silently "
                         f"dropped for this one. That is not a small "
                         f"difference: measured 2026-08-13 on one google_serp "
                         f"attempt each, a blocked engine spent 4 KB and an "
                         f"unblocked one spent 9.9 MB on the same refusal page, "
                         f"and the byte column would show a 2000x engine "
                         f"difference that is entirely our own flag. Blocking "
                         f"can also change the verdict, since a page that never "
                         f"loads its script is judged on markup that was never "
                         f"finished. Run --preset none for a mixed matrix, or "
                         f"drop the engine and measure blocking as its own axis "
                         f"against an engine that has it")
        if args.humanize and not engine.supports_humanize:
            parser.error(f"engine {name!r} has no humanized input, so "
                         f"--humanize would move the cursor for the engines "
                         f"that do and change nothing for this one. The run "
                         f"would then compare humanized Camoufox against "
                         f"unhumanized everything else and read as an engine "
                         f"difference. Drop the engine, or drop the flag and "
                         f"measure humanize as its own axis against the same "
                         f"engine")

    for cell in cells:
        try:
            validate_preset(cell.preset, TARGETS[cell.target])
        except ValueError as exc:
            parser.error(str(exc))

    if args.direct:
        return

    # Per provider and named, because a matrix interleaving two of them holds
    # both credential sets in one process: a run that started with one missing
    # would produce a full column for the provider that answered and an error
    # column for the one that could not, in one file, and the comparison would
    # read as a provider difference.
    for name, provider in sorted(chosen.items()):
        if not config.available(provider):
            wanted = config.variables(provider)
            parser.error(
                f"no credentials for provider {name!r}, so none of its cells "
                f"could leave through a gateway while the other cells in this "
                f"matrix ran. Set {wanted['login']} and {wanted['password']} "
                f"in .env, drop {name!r} from --providers, or pass --direct to "
                f"run the control instead. Nothing has been sent.")
        # The country reaches the gateway as a username parameter, so a provider
        # that does not carry one refuses the axis rather than the flag. Checked
        # the only way it can be: the gateway answers an unknown parameter with
        # 200 and the setting silently dropped.
        for country in sorted({c.country for c in cells if not c.direct}):
            try:
                proxy.build_username("check", provider=provider,
                                     country=country)
            except proxy.ParamError as exc:
                parser.error(f"provider {name!r}: {exc}")


def describe(plan_estimate: dict, args, cells, batches, lists_used: dict,
             chosen: dict) -> None:
    """The plan and its cost, printed before anything is sent.

    Engine availability is reported here and not only in preflight, because
    preflight does not run under --dry-run. A dry run whose whole purpose is to
    tell you what will happen should not let you discover a missing binary
    afterwards, halfway into the real run.
    """
    availability = engines.report_availability()
    described = []
    for name in sorted({c.engine for c in cells}):
        problem = availability.get(name)
        described.append(name if not problem else f"{name} (CANNOT RUN)")
    print(f"engines : {described}")
    for name in sorted({c.engine for c in cells}):
        if availability.get(name):
            print(f"          {name}: {availability[name]}")
    print(f"targets : {sorted({c.target for c in cells})}")
    # Named per target rather than once for the run: which strings a target was
    # asked is part of what its column means, and a mixed matrix draws from more
    # than one list.
    drawn = ", ".join(f"{t}={lists_used[t]}" for t in sorted(lists_used))
    print(f"queries : {drawn}, {plan_estimate['attempts']} attempts "
          f"left over {plan_estimate['cells']} cells")
    print(f"sessions: {plan_estimate['sessions']} "
          f"({args.batch} queries per browser)")
    if plan_estimate["cells"] > 1 and plan_estimate["sessions"] == \
            plan_estimate["cells"]:
        # One batch per cell leaves the round-robin nothing to alternate, so the
        # cells run end to end and whatever the window does over time is charged
        # to whichever cell went first. Interleaving is what makes two cells
        # comparable, and this loses it to a pair of flags rather than to a code
        # path, which is why it is said here instead of guarded: the sequential
        # shape is correct when the cells share nothing, and wrong when they
        # share an address that degrades. `benchmark_20260814T102344Z` spent 20
        # attempts finding that out on Walmart.
        print("          NOTE: one batch per cell, so the cells run in "
              "sequence rather than interleaved. Anything that drifts over the "
              "window - an address being burned, the target's mood - lands on "
              "the cell that ran first. Use a smaller --batch to interleave.")
    proxied = sorted({c.engine for c in cells if not c.direct})
    bypassing = sorted({c.engine for c in cells if c.direct})
    countries = sorted({c.country for c in cells if not c.direct})
    if not proxied:
        print("gateway : none, direct control")
    elif not bypassing:
        print(f"gateway : country={','.join(countries)}")
    else:
        # Both in one window is the point of the suffix, so the plan has to show
        # which engines are on which side rather than one summary line.
        print(f"gateway : country={','.join(countries)} for {proxied}")
        print(f"          bypassed for {bypassing}, which will reach the "
              f"targets from this machine's own address")
    if proxied:
        # `status` is on the line because it is the difference between a column
        # that can be quoted and one that cannot. A `documented` definition was
        # transcribed from the vendor's own documentation and never run from
        # here, so its username DSL is a claim about their docs: the first thing
        # to suspect if its cells fail in a way the measured provider's do not.
        for name, provider in sorted(chosen.items()):
            note = ("" if provider.measured else
                    "  <- DSL read off the vendor's docs, never measured here")
            print(f"provider: {name} ({provider.status}), "
                  f"{provider.host}:{provider.port}{note}")
            if not config.available(provider):
                print(f"          {name}: credentials not set "
                      f"({config.variables(provider)['login']}), so its cells "
                      f"will be refused")
    print(f"geo     : {args.geo}"
          + ("" if args.geo == "align" else
             ", so every engine reports this machine's own timezone and "
             "language list"))
    # Reported here and not only in preflight for the same reason availability
    # is: preflight does not run under --dry-run, and a dry run whose purpose is
    # to say what will happen should not hide a combination that will be refused.
    #
    # --headful was missing from this list until 2026-08-12 and the omission
    # cost a launch: a dry run priced and approved a four-engine matrix, and the
    # real run was refused a minute later because one engine has no display.
    # Every option preflight can refuse belongs here, so adding one to preflight
    # means adding it here in the same edit.
    if args.headful:
        cannot = sorted({c.engine for c in cells
                         if not engines.REGISTRY[c.engine].supports_headful})
        if cannot:
            print(f"headful : on, but {cannot} have no display and will be "
                  f"refused: the run would be headless for them and headful "
                  f"for the rest, which is not a comparison")
    if args.geo == "align":
        cannot = sorted({c.engine for c in cells
                         if not engines.REGISTRY[c.engine].supports_geo_align})
        if cannot:
            print(f"          {cannot} cannot align and will be refused: they "
                  f"would report the host timezone while the others matched "
                  f"the exit, which is not a comparison")
    if args.humanize:
        cannot = sorted({c.engine for c in cells
                         if not engines.REGISTRY[c.engine].supports_humanize})
        print("humanize: on"
              + (f", but {cannot} have no humanized input and will be refused"
                 if cannot else ""))
    print(f"cost    : about {plan_estimate['megabytes']} MB and "
          f"{plan_estimate['hours']} h")
    # Named per target, because the byte figure is a per-target measurement and
    # a target with no run behind it is priced from a floor. Printing one number
    # for a mixed matrix would hide which half of it is a guess.
    basis = plan_estimate.get("basis", {})
    for target in sorted(basis):
        per = (matrix.MEASURED_BYTES.get(target, matrix.DEFAULT_BYTES)
               if basis[target] != "override" else None)
        note = {"measured": "measured", "default": "NOT measured, a floor",
                "override": "overridden on the command line"}[basis[target]]
        size = f"{round(per / 1000)} KB, " if per else ""
        print(f"          {target}: {size}{note}")
    # The two directions were the wrong way round here until 2026-08-18, and
    # that is the one error in an estimate an operator cannot discount: it
    # invites a run longer than the budget allows while presenting itself as
    # conservative. The constant blends a per-session browser launch into a
    # per-attempt figure, so it falls short exactly where the launch is paid on
    # every attempt. Measured 2026-08-12, the two runs calibrate.py read:
    # google_serp at --batch 1 cost 40.2 s per attempt against this 25 s, and
    # walmart_search at --batch 5 cost 12.8 s.
    print("          time: 25 s per attempt, blended over the 2026-08-11 run. "
          "It runs short at --batch 1 and long at larger batches, because a "
          "browser launch is per session and is counted here per attempt")
    if not batches:
        print("\nnothing left to run: every attempt in this plan is already "
              "recorded. Drop --resume to start a fresh run.")


def summarise(rows: list, breakers: dict) -> None:
    """One line per cell, because a pass rate only means something inside one.

    Rows are grouped by the cell key they carry rather than by their engine and
    target columns: two cells can share both and differ only in a gateway
    parameter, and averaging those together would report a number belonging to
    neither.
    """
    print("\n" + "=" * 100)
    print(f"{'engine':<20}{'target':<14}{'exit':<16}{'n':>5}{'pass':>7}  verdicts")
    print("-" * 100)
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["cell"]].append(row)

    # Only when the axis was varied. A single-provider run has said which one on
    # the plan line already, and repeating it on every row of every table would
    # push the country - the axis that is varied - out of a column it shares.
    many_providers = len({r.get("provider") for r in rows
                          if r.get("provider")}) > 1

    for cell_key, cell_rows in sorted(grouped.items()):
        first = cell_rows[0]
        passed = sum(1 for r in cell_rows if r["verdict"] == "ok")
        counts = Counter(r["verdict"] for r in cell_rows)
        breaker = breakers.get(cell_key)
        stopped = f"  STOPPED: {breaker.reason}" if breaker and breaker.tripped else ""
        rate = f"{100 * passed / len(cell_rows):.0f}%"
        # Two cells can share an engine and a target and differ only in where
        # they left from, which is the whole point of the country axis. Printing
        # them as one line would average two measurements into neither.
        params = first.get("params") or {}
        where = "direct" if first.get("direct") else params.get("country", "-")
        if first.get("geo") == "align":
            where += "+geo"
        if many_providers and first.get("provider"):
            where = f"{first['provider'][:8]}/{where}"
        print(f"{first['engine']:<20}{first['target']:<14}{where:<16}"
              f"{len(cell_rows):>5}{rate:>7}  {dict(counts)}{stopped}")

    print("\nwhy the non-ok verdicts happened")
    print("-" * 92)
    reasons = Counter((r["verdict"], r.get("verdict_reason"))
                      for r in rows if r["verdict"] != "ok")
    for (verdict, reason), count in reasons.most_common():
        print(f"  {count:>4}  {verdict:<9}{(reason or '')[:70]}")

    total = sum(r.get("bytes") or 0 for r in rows)
    print(f"\nattempts: {len(rows)}   locally counted: "
          f"{total / 1024 / 1024:.2f} MB (lower bound)")


def main() -> int:
    parser, args = parse_args()

    engine_names = [e.strip() for e in args.engines.split(",") if e.strip()]
    try:
        parsed = [matrix.parse_engine(spec) for spec in engine_names]
    except ValueError as exc:
        parser.error(str(exc))
    unknown = [name for name, _ in parsed if name not in engines.REGISTRY]
    if unknown:
        parser.error(f"unknown engines {unknown}, known: {engines.names()}")

    target_names = [t.strip() for t in args.targets.split(",") if t.strip()]
    unknown = [t for t in target_names if t not in TARGETS]
    if unknown:
        parser.error(f"unknown targets {unknown}, known: {sorted(TARGETS)}")

    provider_names = [p.strip() for p in (args.providers or "").split(",")
                      if p.strip()] or [providers.default_name()]
    if args.direct and args.providers:
        parser.error("--direct sends nothing through any gateway, so "
                     "--providers has nothing to select between: the cells "
                     "would be identical copies of one direct experiment "
                     "presented as a comparison")
    try:
        chosen = {name: providers.load(name) for name in provider_names}
    except providers.ProviderError as exc:
        parser.error(str(exc))

    extra = {}
    try:
        # Every provider in the matrix, not only the first. The split is
        # provider-independent so each call returns the same dict; the
        # validation is not, and a parameter one gateway knows and another does
        # not would otherwise raise mid-run, after the earlier providers had
        # already been asked - traffic spent to learn something that was local.
        for provider in chosen.values():
            extra = proxy.parse_params(args.param, provider=provider)
    except proxy.ParamError as exc:
        parser.error(str(exc))
    if args.direct and extra:
        parser.error("--direct sends nothing through the gateway, so --param "
                     "has nothing to act on")

    # One list per target, not one per run. Amazon asked "playwright vs
    # puppeteer" answers that it sells nothing of the sort, and that verdict
    # would be read as the shop letting us in or not.
    lists_used = {name: args.query_list or getattr(TARGETS[name], "query_list",
                                                   "serp_1000")
                  for name in target_names}
    try:
        limit = None if args.queries == "all" else int(args.queries)
        query_sets = {name: querylist.load(list_name, limit)
                      for name, list_name in lists_used.items()}
    except (ValueError, FileNotFoundError) as exc:
        parser.error(str(exc))

    countries = [c.strip() for c in args.countries.split(",") if c.strip()]
    if not countries:
        parser.error("--countries is empty, so there is nothing to ask the "
                     "gateway for")

    cells = matrix.build_cells(engine_names, target_names, preset=args.preset,
                               countries=countries, direct=args.direct,
                               headful=args.headful, geo=args.geo, extra=extra,
                               provider_names=provider_names)
    done = matrix.load_completed(resolve_resume(args.resume))
    batches = matrix.plan(cells, query_sets, args.batch, done)
    plan_estimate = matrix.estimate(batches)

    describe(plan_estimate, args, cells, batches, lists_used, chosen)
    # Before the dry-run return, not after. Everything preflight checks is
    # local - installed packages, declared capabilities, whether credentials
    # exist - so it sends nothing and costs nothing here, and a dry run that
    # printed a cost estimate for a matrix the runner will refuse is a dry run
    # that answered the wrong question. That was live until 2026-08-13: a mixed
    # blocking matrix passed the dry run and was rejected only once a real run
    # was launched.
    preflight(parser, args, cells, chosen)
    if args.dry_run:
        print("\ndry run: nothing was sent")
        return 0
    if not batches:
        return 0

    # The address this machine speaks from, so a session that quietly failed to
    # use the proxy can be caught instead of being recorded as a pool exit.
    local_ip = None
    if not args.direct:
        local_ip = gateway.identify_direct().get("exit_ip")

    sink = JsonlSink("benchmark")
    # Same run id as the rows, so a body and the row that judged it can be put
    # back together from the filenames alone.
    store = ArtifactStore("benchmark", run_id=sink.run_id,
                          sample_ok=args.sample_ok,
                          enabled=not args.no_bodies)
    registry = gateway.ExitRegistry()
    breakers = {c.key: CircuitBreaker(c.key, limit=args.breaker,
                                      base_pause=args.pause) for c in cells}
    # One level above the cell breakers, and the only thing in the run that can
    # see a transport failure. A cell breaker stops a cell and moves on, which
    # is the wrong reaction when what broke is shared by every cell.
    watch = TransportWatch()
    rows = []
    print(f"\nraw rows -> {sink.path}\n")

    try:
        for batch in batches:
            cell = batch.cell
            breaker = breakers[cell.key]
            if breaker.tripped:
                continue

            target = TARGETS[cell.target]
            # The cell names the provider only when the axis is varied, so the
            # default is resolved here rather than stored on every cell: the key
            # is a resume identity and reading a name out of it would make the
            # keys already on disk mean something they did not say.
            provider = None if cell.direct else chosen[
                cell.provider or providers.default_name()]
            sid = f"bm{uuid.uuid4().hex[:8]}"
            params = ({} if cell.direct
                      else proxy.session_params(sid, provider=provider,
                                                **cell.params))

            aligning = cell.geo == "align"
            if cell.direct:
                identity = gateway.identify_direct()
            elif aligning:
                # `identify` prefers the CONNECT reply header, which carries the
                # address and no timezone, so an aligned cell has to ask the
                # echo service instead - about 330 bytes. Only the aligned arm
                # pays it here, which is the opposite of what `probe_and_hold`
                # does and is right for a different reason: that run varies
                # `geo` inside one matrix and the extra request is a warm-up
                # that would land in one arm only, while a matrix run varies it
                # across cells that already differ in engine and target.
                identity = gateway.echo(provider=provider, **params)
            else:
                identity = gateway.identify(provider=provider, **params)
            identity.update(registry.record(identity.get("exit_ip")))

            timezone_id = identity.get("timezone")
            if aligning and not timezone_id:
                # The exit answered and the answer named no zone. Running anyway
                # would write rows labelled `geo=align` for a browser that was
                # aligned to nothing, and once the exit is gone that claim is
                # unfalsifiable. The breaker is deliberately not fed: this is our
                # lookup falling short, not the target refusing anything.
                print(f"  {cell.key}: the exit lookup named no timezone, so "
                      f"this identity cannot be aligned and will not be "
                      f"labelled as if it were")
                sink.write({"cell": cell.key, "target": cell.target,
                            "verdict": "identity_skipped",
                            "verdict_reason": "no timezone for the exit",
                            "geo": cell.geo,
                            "exit_prefix": identity.get("exit_prefix"),
                            "exit_label": identity.get("exit_label")})
                continue

            # Two names for one axis, because the engines source the data two
            # ways: Camoufox looks the exit up in a bundled database and needs
            # only a boolean, and the Chromium-family engines have to be handed
            # the zone, which the echo above is what obtains. Engines without
            # the feature swallow both, and preflight has already refused the
            # matrix where that would matter.
            #
            # Passing only `geoip` is what this runner did until 2026-08-14, and
            # it was the `--humanize` failure a third time: preflight accepts
            # `--geo align` for patchright, cloak and zendriver because all
            # three declare the capability, and all three then ran unaligned
            # while their rows said `geo=align`. Worse than a dropped option,
            # because the label survives into analysis. The geo finding on disk
            # is unaffected - it was produced by `probe_and_hold.py`, which has
            # always passed the zone.
            options = {"direct": cell.direct, "params": params,
                       "preset": cell.preset, "headless": not args.headful,
                       "humanize": args.humanize, "store": store,
                       "channel": args.channel,
                       "provider": provider,
                       "geoip": aligning,
                       "timezone_id": timezone_id if aligning else None}

            # An authenticating forwarder for the engines that cannot send
            # proxy credentials themselves. One relay per session and not one
            # per run, because the username carries the gateway parameters and
            # the parameter set is the sticky session key: two sessions sharing
            # a relay would share an exit address without either row saying so.
            #
            # Asked for by the engine's own declaration rather than by its name,
            # so adding a tenth framework that needs one is still a module and a
            # registry line. On the direct arm there is no gateway to
            # authenticate to, so there is no relay and the byte column is empty
            # for these engines - stated rather than filled with a zero.
            wants_relay = (not cell.direct
                           and engines.REGISTRY[cell.engine].needs_relay)
            try:
                with ExitStack() as stack:
                    hop = None
                    if wants_relay:
                        hop = stack.enter_context(Relay(params=params,
                                                        provider=provider))
                        options["relay_address"] = hop.address
                    active = stack.enter_context(
                        engines.session(cell.engine, **options))
                    # A relayed session cannot have gone direct: the relay is
                    # what dials the gateway, so a browser that ignored the
                    # proxy would reach nothing at all rather than reach the
                    # target from this machine's address. The guard below is for
                    # the engines that dial it themselves.
                    if not cell.direct and hasattr(active, "exit_ip"):
                        seen = active.exit_ip()
                        active.session_exit_prefix = registry.record(seen).get(
                            "exit_prefix")
                        if seen and local_ip and seen == local_ip:
                            reason = "proxy not applied, browser went direct"
                            breaker.trip(reason)
                            print(f"  {cell.key}: the browser left from this "
                                  f"machine's own address, so the proxy was "
                                  f"not applied. Cell stopped rather than "
                                  f"recorded as a pool measurement, and rather "
                                  f"than sending the rest of this cell's "
                                  f"queries from the operator's own line.")
                            sink.write({"cell": cell.key, "target": cell.target,
                                        "verdict": "cell_stopped",
                                        "verdict_reason": reason})
                            continue

                    for query in batch.queries:
                        # Differenced across the attempt rather than reset,
                        # because a browser holds tunnels open on keep-alive and
                        # a reset would post one page's bytes to the next row.
                        meter = hop.snapshot() if hop else None
                        row = active.fetch(target, query)
                        spent = hop.since(meter) if hop else None
                        row.update({
                            "cell": cell.key, "target": cell.target,
                            "batch_index": batch.index, "geo": cell.geo,
                            "relayed": bool(hop),
                            "exit_prefix": identity.get("exit_prefix"),
                            "exit_label": identity.get("exit_label"),
                            "exit_org": identity.get("org"),
                        })
                        if spent is not None:
                            # Sockets, not framework events, so this counts the
                            # same way for every engine and includes the header
                            # and TLS overhead that Content-Length summing
                            # misses. It is the only byte figure these engines
                            # can have: page.route is a Playwright API.
                            row["bytes"] = spent["bytes"]
                            # The gateway names an exit on only some of its
                            # CONNECT replies, so an empty list here means it did
                            # not say, never that the address was reused.
                            for seen_exit in spent["exits"]:
                                row["session_exit_prefix"] = registry.record(
                                    seen_exit).get("exit_prefix")
                        sink.write(row)
                        rows.append(row)

                        ready = {True: "ready", False: "no-markup",
                                 None: "-"}[row.get("ready")]
                        # 26, because the label carries the arm and the preset
                        # as well as the engine: `patchright-direct/light` is
                        # 23 characters and ran into the target column, which
                        # made the live output unreadable exactly when a direct
                        # run was the only readable arm left.
                        print(f"  {row['engine']:<26}{cell.target:<14}"
                              f"{query[:24]:<26}"
                              f"{row['status'] or '-'!s:<5}"
                              f"{row['verdict']:<8}{ready:<10}"
                              f"{identity.get('exit_ip') or '-'!s:<16}"
                              f"{(identity.get('org') or '')[:26]}")
                        if row["error"]:
                            print(f"      -> {row['error'][:96]}")

                        watch.record(cell.key, row["verdict"])
                        if watch.tripped:
                            print(f"\nrun stopped: {watch.reason}.")
                            print("Check the transport before restarting: "
                                  "python scripts/probes/gateway_health.py")
                            print("Nothing is lost, the answered queries are "
                                  f"recorded. Continue with\n  --resume {sink.path}")
                            sink.write({"cell": cell.key, "target": cell.target,
                                        "verdict": "run_stopped",
                                        "verdict_reason": watch.reason})
                            raise TransportLost(watch.reason)

                        wait = breaker.record(row["verdict"])
                        if breaker.tripped:
                            print(f"  {cell.key}: stopped, {breaker.reason}")
                            sink.write({"cell": cell.key, "target": cell.target,
                                        "verdict": "cell_stopped",
                                        "verdict_reason": breaker.reason})
                            break
                        time.sleep(wait)
            except TransportLost:
                # The transport, not this session. It ends the run, and it must
                # reach the handler below rather than the catch-all added under
                # it - a catch-all that swallowed this would leave the guard
                # printing its verdict and the run carrying on regardless.
                raise
            except engines.EngineUnavailable as exc:
                # Preflight already refused the engines that cannot run at all,
                # so reaching here means the launch failed at run time. It will
                # fail the same way on this cell's remaining batches.
                breaker.trip(f"engine could not start: {exc}")
                print(f"  {cell.key}: {exc}")
                sink.write({"cell": cell.key, "target": cell.target,
                            "verdict": "cell_stopped",
                            "verdict_reason": breaker.reason})
            except Exception as exc:
                # A browser that would not launch, a relay that would not bind,
                # a session that died between two queries. All of these are this
                # machine rather than the target, and one of them costs one
                # batch rather than the run.
                #
                # `probe_and_hold.py` has carried this since 2026-08-13 and this
                # runner did not, which is the asymmetry worth naming: the cheap
                # probe survived what the expensive matrix could not. Measured
                # 2026-08-18, a 16-attempt smoke run on the server died here at
                # cell 9 of 16 on zendriver's `Failed to connect to browser`,
                # the same transient that starts normally a minute later. Over
                # the tens of hours this matrix runs for, a launch that hiccups
                # once is not an edge case, it is a certainty.
                #
                # Written as its own verdict rather than as `error`, because
                # `error` is an attempt that reached the network and this never
                # did. Pooling them would charge our own launcher to the
                # engine's error rate. It is fed to the breaker all the same: an
                # engine that cannot start should stop its own cell rather than
                # draw a fresh exit every batch for nothing.
                reason = f"{type(exc).__name__}: {exc}".strip()
                # zendriver's launch failure is a multi-line banner, so the
                # console gets the first line and the row keeps all of it.
                headline = (reason.splitlines() or [""])[0][:160]
                breaker.record("error")
                print(f"  {cell.key}: the session did not start or did not "
                      f"survive. This batch is lost, the run continues. "
                      f"{headline}")
                sink.write({"cell": cell.key, "target": cell.target,
                            "verdict": "session_failed",
                            "verdict_reason": reason})
                if breaker.tripped:
                    print(f"  {cell.key}: stopped, {breaker.reason}")
                    sink.write({"cell": cell.key, "target": cell.target,
                                "verdict": "cell_stopped",
                                "verdict_reason": breaker.reason})
    except TransportLost:
        # Already reported where it was detected, with the state that justified
        # it. Reaching here only means the browser has been closed.
        pass
    except KeyboardInterrupt:
        print("\ninterrupted. Nothing is lost: rerun the same command with")
        print(f"  --resume {sink.path}")

    if rows:
        summarise(rows, breakers)
    print(f"raw rows: {sink.path}")
    print(store.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
