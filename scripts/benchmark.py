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
  restraint      a cell whose last `--breaker` sessions were all refused stops
                 and stays stopped, and the run says so rather than rotating a
                 session and trying again. Sessions and not attempts, because a
                 batch is one browser on one sticky exit and ten attempts down a
                 dead exit is one observation repeated. A failure shared by many
                 cells at once stops the whole run instead, because that one is
                 not about the targets and a night of it is worth nothing
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
from nmbench.breaker import SessionBreaker, TransportWatch
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
    parser.add_argument("--breaker", type=int, default=3,
                        help="consecutive SESSIONS with nothing served that "
                             "stop a cell. One session is one browser on one "
                             "sticky exit, so this is also the number of "
                             "distinct exits that have to fail before the cell "
                             "is given up, and a cell costs roughly this many "
                             "times --batch confirmed-automation retries "
                             "against a target that refuses everything: a "
                             "pool-safety setting and not a patience setting. "
                             "It counted attempts and not sessions until "
                             "2026-09-14, where --batch 10 --breaker 10 made "
                             "the limit exactly one session and a single bad "
                             "exit ended a cell for good")
    parser.add_argument("--direct", action="store_true",
                        help="no proxy: the targets see this machine's own "
                             "address. A control, not a normal mode")
    parser.add_argument("--headful", action="store_true",
                        help="a real window with a real compositor and GPU. "
                             "Not every engine can do it")
    parser.add_argument("--humanize", default="off",
                        choices=["off", "engine", "trueman"],
                        help="humanized cursor movement. `engine` is the "
                             "engine's own, which Camoufox and cloak declare "
                             "and the rest do not, so the runner refuses the "
                             "flag for a matrix holding any engine without it "
                             "rather than applying it to some columns and not "
                             "others. `trueman` is our own mover and is "
                             "refused here outright - see the preflight")
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
    parser.add_argument("--chrome-binary", default=None, metavar="PATH",
                        help="run every engine on this one browser, so the "
                             "engine is the variable instead of the browser it "
                             "happens to bundle. Off by default: every row on "
                             "disk was measured with each engine on its own "
                             "build, and a silent default would make new rows "
                             "incomparable with them without any column saying "
                             "so. Engines that cannot take a binary are refused "
                             "rather than run unpinned")
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
    # Refused here and offered in `probe_and_hold.py`, and the difference is
    # mechanical rather than a policy: this runner calls `active.fetch()` and
    # nothing else, which navigates straight to a URL and clicks nothing, so
    # `nmbench.humanize` would have no click to walk to and the cursor would
    # never move. Every row would still carry `humanize_mode=trueman`. That is
    # the `--humanize` failure exactly - an option accepted at the command line
    # and honoured by nobody - and it is cited three times in this tree, so it
    # is refused rather than documented. The typed entry path lives in
    # `scripts/probes/probe_and_hold.py --entry home`; that is where the flag
    # works.
    if args.humanize == "trueman":
        parser.error(
            "--humanize trueman is not available in this runner: it only ever "
            "calls fetch(), which navigates to a search URL and clicks "
            "nothing, so no pointer would move while every row still recorded "
            "humanize_mode=trueman. Use "
            "scripts/probes/probe_and_hold.py --entry home --humanize trueman, "
            "which types into the box and clicks the button")

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
                         f"--preset {args.preset} would be dropped for it while "
                         f"every row it writes still recorded the preset as "
                         f"asked for. That is not a small difference: measured "
                         f"2026-08-13 on one google_serp attempt each, a blocked "
                         f"engine spent 4 KB and an unblocked one spent 9.9 MB "
                         f"on the same refusal page, so the byte column would "
                         f"show a 2000x engine difference produced entirely by "
                         f"this flag. Blocking can also change the verdict, since "
                         f"a page that never loads its script is judged on "
                         f"markup that was never finished. It is refused even "
                         f"when this engine is alone in the matrix, because the "
                         f"rows are compared against other runs and the column "
                         f"would claim a blocking that never happened. Add "
                         f"--preset none, or drop the engine and measure "
                         f"blocking as its own axis against one that has it")
        if args.chrome_binary and not engine.supports_chrome_binary:
            parser.error(f"engine {name!r} cannot be pointed at a browser "
                         f"binary, so --chrome-binary would pin the engines "
                         f"that can take it and leave this one on whatever it "
                         f"bundles. That is the failure this flag exists to "
                         f"remove: measured 2026-09-02 in "
                         f"tls_clienthello_20260902T180555Z.jsonl, the TLS "
                         f"fingerprint of these engines is decided by the "
                         f"Chrome major and not by the library, and the engines "
                         f"in the registry span majors 136 to 151. A matrix "
                         f"mixing a pinned engine with an unpinned one prints "
                         f"the browser difference in the engine column. Drop "
                         f"the engine, or drop the flag and accept that the "
                         f"build is uncontrolled")
        if args.humanize not in engine.humanize_modes:
            parser.error(f"engine {name!r} has no {args.humanize!r} "
                         f"humanization - it offers "
                         f"{sorted(engine.humanize_modes)}. Applying it would "
                         f"move the cursor for the engines that have it and "
                         f"change nothing for this one. The run would then "
                         f"compare humanized Camoufox against unhumanized "
                         f"everything else and read as an engine difference. "
                         f"Drop the engine, or drop the flag and measure "
                         f"humanize as its own axis against the same engine")

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
        # The reason is taken from the exception rather than assumed to be a
        # missing login. `credentials` refuses for two different causes, and a
        # definition copied from `_template.toml` ships `host = ""` and
        # `port = 0`, so the *likely* first failure for anyone adding their own
        # gateway is the address and not the account. Telling that reader to set
        # a login they have already set is the shape of error this repository's
        # own convention exists to prevent: say what will happen, and say which
        # value caused it.
        try:
            config.credentials(provider)
        except (config.MissingCredentials, providers.ProviderError) as exc:
            parser.error(
                f"provider {name!r} cannot reach a gateway, so none of its "
                f"cells could leave while the other cells in this matrix ran, "
                f"and the file would hold a full column for one provider and an "
                f"error column for the other. {exc} Drop {name!r} from "
                f"--providers, or pass --direct to run the control instead.")
        # The country reaches the gateway as a username parameter, so a cell may
        # not carry one its provider cannot take. `build_cells` no longer builds
        # such a cell - it collapses the axis for a definition that sells no
        # country - so this is a backstop on that invariant rather than a check
        # an operator will trip. It stays because the invariant is the one this
        # whole layer rests on and the failure is invisible: the measured gateway
        # answers an unknown parameter with 200 and the setting silently dropped,
        # so a cell that slipped through would complete and every row would claim
        # a country that was never applied.
        #
        # Scoped to this provider's own cells rather than to the whole matrix,
        # and that is the difference between refusing a mistake and refusing a
        # valid run. In a mixed matrix the country axis exists for one gateway
        # and not for the other, so checking every country against every provider
        # would take `--providers custom,nodemaven --countries us,de` - your own
        # proxy against a pool in one window, which is the comparison worth
        # running - and refuse it for a parameter that was never going to be sent
        # to the gateway that cannot take it.
        #
        # The empty country is skipped for the same reason it is never sent:
        # `build_cells` writes it for a gateway that sells none, and building a
        # username with `country=""` would be refused for having an empty value,
        # which is a true statement about a parameter nobody asked for.
        #
        # `c.provider` is empty on the default provider, because the key of a
        # single-provider run has to stay byte-identical to the 132 already on
        # disk. Read it back through that default rather than comparing to "".
        #
        # The wire value and not the axis value, because `matrix.ANY` is a
        # keyword of this harness: on a gateway that spells it, the spelling is
        # what has to survive validation, and on one that does not, nothing is
        # sent and there is nothing here to check.
        default = providers.default_name()
        for country in sorted({c.country for c in cells
                               if not c.direct and c.country
                               and (c.provider or default) == name}):
            wire = (provider.country_any if country == matrix.ANY else country)
            if not wire:
                continue
            try:
                proxy.build_username("check", provider=provider,
                                     country=wire)
            except proxy.ParamError as exc:
                parser.error(f"provider {name!r}: {exc}")

    # Two or more countries and nothing in the matrix that sells one. The axis
    # collapses for every provider, so the run builds a single cell and answers
    # a different question than the one typed - and it answers it successfully,
    # which is the failure mode that costs a night rather than a minute.
    #
    # Refused at two and merely noted at one, because `--countries` carries a
    # default. One country is what an operator gets for saying nothing, and
    # stopping a perfectly good bring-your-own-proxy run over a value nobody
    # typed would make the default of an unrelated flag the thing that blocks
    # the common case. Two is not a default and not an accident: it is a
    # comparison, and this matrix cannot make it.
    asked = [c.strip() for c in args.countries.split(",") if c.strip()]
    sells_country = [name for name, provider in chosen.items()
                     if "country" in provider.known_params]
    if len(asked) > 1 and not sells_country and any(not c.direct for c in cells):
        parser.error(
            f"--countries {args.countries} asks for a comparison across "
            f"{len(asked)} countries, and no gateway in this matrix takes a "
            f"country: {sorted(chosen)}. Every cell would leave from the same "
            f"exit and the run would finish looking like a country comparison "
            f"that never happened. Drop --countries, or add a provider whose "
            f"definition lists it")


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
    countries = sorted({c.country for c in cells if not c.direct and c.country})
    # A gateway that sells no country produces cells with none, and saying
    # `country=` with nothing after it would read as a flag that failed to
    # arrive rather than as an axis that does not exist here.
    where = (f"country={','.join(countries)}" if countries
             else "whatever exit the gateway has, no country asked for")
    if not proxied:
        print("gateway : none, direct control")
    elif not bypassing:
        print(f"gateway : {where}")
    else:
        # Both in one window is the point of the suffix, so the plan has to show
        # which engines are on which side rather than one summary line.
        print(f"gateway : {where} for {proxied}")
        print(f"          bypassed for {bypassing}, which will reach the "
              f"targets from this machine's own address")
    if proxied:
        # `status` is on the line because it is the difference between a column
        # that can be quoted and one that cannot. A `documented` definition was
        # transcribed from the vendor's own documentation and never run from
        # here, so its username DSL is a claim about their docs: the first thing
        # to suspect if its cells fail in a way the measured provider's do not.
        #
        # Read off the command line rather than off the cells, because the cells
        # are what is left after the collapse and the question here is what was
        # asked for before it.
        asked_countries = [c.strip() for c in args.countries.split(",")
                           if c.strip()]
        for name, provider in sorted(chosen.items()):
            # A definition with no parameters has no transcription to be wrong
            # about, so pointing at the vendor's docs would send the reader to
            # look for a mistake that cannot exist there. What `documented`
            # still means for it is the part worth saying: no row on disk came
            # through this gateway.
            if provider.measured:
                note = ""
            elif provider.known_params:
                note = "  <- DSL read off the vendor's docs, never measured here"
            else:
                note = "  <- no row in data/runs/ came through it"
            # The definition's address is a documented default and the
            # environment overrides it, so a definition that ships none - which
            # is every proxy somebody already owns, because the address is
            # theirs - would print `:0` and read as a gateway pointing nowhere.
            # Say where the address comes from instead of printing a zero.
            variables = config.variables(provider)
            address = (f"{provider.host}:{provider.port}"
                       if provider.host and provider.port
                       else f"address from {variables['host']} and "
                            f"{variables['port']}")
            print(f"provider: {name} ({provider.status}), {address}{note}")
            # Said before the run rather than discovered in the numbers. With no
            # session parameter there is nothing to ask a fresh exit with, so
            # every session in the whole matrix leaves from one address: the
            # engine and target axes still mean what they say, and anything that
            # reads as a property of the pool - yield, rotation, how fast an
            # address is burned - is a property of that one address instead.
            if not provider.session_param:
                print(f"          {name}: no session parameter, so every "
                      f"attempt leaves from the same exit. Engines and targets "
                      f"are still comparable; exit yield and rotation are not "
                      f"measurable through it")
            # `--countries` has a default, so it arrives whether or not anybody
            # typed it, and a gateway that does not sell one collapses the axis
            # to a single cell. That collapse is correct and it is also a
            # setting the operator asked for and did not get, which is the exact
            # shape of failure this repository refuses everywhere else. It is
            # said rather than refused because refusing would make the default
            # value of an unrelated flag stop a run that is perfectly valid.
            if "country" not in provider.known_params and asked_countries:
                print(f"          {name}: sells no country, so "
                      f"{','.join(asked_countries)} was not asked for and its "
                      f"cells are built once. The address is whatever single "
                      f"exit it has")
            # `any` is the harness's keyword for an unpinned country and the
            # gateways disagree about whether it can be asked for at all, so
            # what each one is actually sent is printed before the run rather
            # than left to be worked out from the rows. Until 2026-09-14 the
            # keyword went out as written and three gateways refused the tunnel.
            elif matrix.ANY in asked_countries:
                if provider.country_any:
                    print(f"          {name}: unpinned is sent as "
                          f"country={provider.country_any}")
                else:
                    print(f"          {name}: unpinned sends no country "
                          f"parameter at all - its definition names no "
                          f"spelling for one, so the exit is whatever the "
                          f"gateway's default is")
            if not config.available(provider):
                print(f"          {name}: credentials not set "
                      f"({variables['login']}), so its cells "
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
    if args.humanize != "off":
        cannot = sorted({c.engine for c in cells
                         if args.humanize
                         not in engines.REGISTRY[c.engine].humanize_modes})
        print(f"humanize: {args.humanize}"
              + (f", but {cannot} have no {args.humanize!r} humanization and "
                 f"will be refused" if cannot else ""))
    if args.chrome_binary:
        cannot = sorted({c.engine for c in cells
                         if not engines.REGISTRY[c.engine]
                         .supports_chrome_binary})
        print(f"browser : pinned to {args.chrome_binary}"
              + (f", but {cannot} cannot take a binary and will be refused: "
                 f"they would run whatever they bundle while the rest ran this, "
                 f"and the browser difference would print in the engine column"
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

    Two rates and not one, added 2026-09-14. `pass` is per attempt and `sess`
    is how many of the cell's sessions served anything at all, and they answer
    different questions because a batch is one browser on one sticky exit: a
    cell that draws nine good exits and one dead one reads about 90% either
    way, and a cell where every exit serves the first query and then gets
    blocked reads 10% per attempt and 10/10 per session. Only the second pair
    tells a caller whether the problem is the pool or the target, and the
    attempt rate alone is what the 2026-09-11 file was read through.
    """
    print("\n" + "=" * 108)
    print(f"{'engine':<20}{'target':<14}{'exit':<16}{'n':>5}{'pass':>7}"
          f"{'sess':>8}  verdicts")
    print("-" * 108)
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
        # Not a zero, and the distinction is the reason this column can be
        # empty at all. If no attempt in the cell reached the target's
        # application layer there is nothing to take a percentage of: `0%`
        # reads as "the target refused us every time" and what happened is that
        # the target was never asked. Six cells of the 2026-09-11 run were
        # published at 0% on exactly this.
        no_verdict = breaker is not None and breaker.no_verdict
        rate = "-" if no_verdict else f"{100 * passed / len(cell_rows):.0f}%"
        # Sessions that served at least one attempt, over sessions run. A
        # session is one exit, so this is also the cell's exit yield.
        sessions = (f"{breaker.alive_sessions}/{breaker.sessions}"
                    if breaker and breaker.sessions else "-")
        # Two cells can share an engine and a target and differ only in where
        # they left from, which is the whole point of the country axis. Printing
        # them as one line would average two measurements into neither.
        params = first.get("params") or {}
        # `gateway` and not `-`, for the same reason the cell key says it: a
        # gateway selling no country is a place these rows left from, and a dash
        # would read as a column that failed to be filled in.
        # The axis value first and the wire value behind it. An unpinned cell
        # sends no country to three of the four gateways here, so reading the
        # wire alone would print `gateway` for a cell that did vary the axis and
        # collapse it into the row of a provider that sells no country at all.
        where = ("direct" if first.get("direct")
                 else first.get("country") or params.get("country")
                 or "gateway")
        if first.get("geo") == "align":
            where += "+geo"
        if many_providers and first.get("provider"):
            where = f"{first['provider'][:8]}/{where}"
        print(f"{first['engine']:<20}{first['target']:<14}{where:<16}"
              f"{len(cell_rows):>5}{rate:>7}{sessions:>8}  "
              f"{dict(counts)}{stopped}")

    silent = sorted(key for key, cell_rows in grouped.items()
                    if breakers.get(key) is not None
                    and breakers[key].no_verdict)
    if silent:
        print(f"\n{len(silent)} of {len(grouped)} cells have no pass rate at "
              f"all: every attempt in them failed below the application layer, "
              f"so the target never answered and there is nothing to be a "
              f"percentage of. Read these as unmeasured, not as zero.")
        for key in silent:
            print(f"  {key}")

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

    if args.chrome_binary and args.channel:
        parser.error("--channel and --chrome-binary both name the browser to "
                     "launch, and the path wins silently. Read 2026-09-02 in "
                     "the bundled driver, coreBundle.js: `resolveExecutablePath` "
                     "returns the path whenever one is given, and the registry "
                     "lookup that reads the channel is the `else` branch. So a "
                     "run passing both records a channel that decided nothing. "
                     "Pass the path alone")
    if args.chrome_binary and not Path(args.chrome_binary).is_file():
        parser.error(f"--chrome-binary {args.chrome_binary!r} is not a file. "
                     f"Checked here rather than at launch, because a bad path "
                     f"surfaces once per cell as an engine error and the run "
                     f"would spend the matrix discovering it")

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
                               chosen=chosen)
    resumed = resolve_resume(args.resume)
    # The one place a provider can change without `build_cells` seeing it. A cell
    # on the default gateway carries no provider segment in its key, so a run
    # made through one gateway and a run made through another produce byte
    # identical keys: resuming across them would skip attempts the second gateway
    # never made and file its answers under a key that names the first. Refused
    # rather than merged, because both files are then wrong and neither says so.
    inherited = matrix.providers_named(resumed) - set(provider_names)
    if inherited and not args.direct:
        parser.error(
            f"--resume points at rows measured through "
            f"{sorted(inherited)} and this matrix runs "
            f"{sorted(set(provider_names))}. The cell keys do not carry the "
            f"default provider, so the two are indistinguishable in the file: "
            f"resuming would treat the other gateway's answers as this one's "
            f"and skip the queries it never asked. Start a fresh run, or pass "
            f"--providers naming the gateway those rows were measured through.")
    done = matrix.load_completed(resumed)
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
    breakers = {c.key: SessionBreaker(c.key, limit=args.breaker,
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
                                                **cell.params_for(provider)))

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
                       "chrome_binary": args.chrome_binary,
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
                                        "provider": cell.provider_id,
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
                            # What the axis asked for, beside the `params`
                            # column which is what went on the wire. The two
                            # differ for an unpinned cell and that difference is
                            # the whole of the 2026-09-11 defect: with only the
                            # wire value on the row, three providers' unpinned
                            # cells are indistinguishable from a gateway that
                            # sells no country.
                            "country": cell.country,
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
                            # How many requests the relay refused for this
                            # attempt, so the byte figure says whether the
                            # browser asked for the vendor fetch and was
                            # refused, or never asked. Without it those are one
                            # row and the saving cannot be attributed.
                            row["relay_blocked"] = spent["blocked"]
                            # The session's handshake, not this attempt's - see
                            # `Relay.since`. The first is taken rather than the
                            # last so the value is stable across a session; a
                            # second distinct one would be a finding, and
                            # `Relay.ja4` keeps the whole list for the run that
                            # goes looking.
                            if spent["ja4"]:
                                row["tls_ja4"] = spent["ja4"][0]
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

                        # The error string and not only the verdict: `error`
                        # covers both a target that would not answer and a
                        # tunnel that never opened, and telling those apart is
                        # the whole of what this breaker adds. Nothing here can
                        # stop the cell - a session is judged when it ends, so
                        # the rest of a batch is always sent and the exit is
                        # always given its ten attempts.
                        time.sleep(breaker.record(row["verdict"], row["error"]))
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
                # engine's error rate.
                #
                # Nothing is fed to the breaker here, which is a change of
                # 2026-09-14 and not an omission. The old runner recorded a
                # synthetic `error` attempt so that an engine which cannot start
                # would stop its own cell; a session breaker gets that for free
                # and more honestly, because a batch that sent nothing closes as
                # `abandoned` below and a run of those stops the cell under a
                # reason that names the launcher rather than the target. The
                # synthetic attempt would also have been counted as having
                # reached the target, which is the one thing it certainly did
                # not do.
                reason = f"{type(exc).__name__}: {exc}".strip()
                # zendriver's launch failure is a multi-line banner, so the
                # console gets the first line and the row keeps all of it.
                headline = (reason.splitlines() or [""])[0][:160]
                print(f"  {cell.key}: the session did not start or did not "
                      f"survive. This batch is lost, the run continues. "
                      f"{headline}")
                sink.write({"cell": cell.key, "target": cell.target,
                            "verdict": "session_failed",
                            "verdict_reason": reason})

            # One place, after every path out of the session above, because a
            # session is the unit this breaker judges and there is no other
            # moment at which it is over. The handlers report what went wrong
            # with the batch; whether the cell continues is decided here.
            outcome = breaker.end_session()
            sink.write({"cell": cell.key, "target": cell.target,
                        "batch_index": batch.index, "geo": cell.geo,
                        "country": cell.country,
                        # The id string, which is what an attempt row carries in
                        # this column too. Same name has to mean the same thing
                        # or the two row kinds cannot be read together, and
                        # reading them together is the point of the column.
                        #
                        # `provider_id` and not `provider`: the field is a key
                        # segment and is empty for the default gateway, so
                        # writing it here made this row disagree with the
                        # attempt rows of its own cell. Measured on
                        # `benchmark_20260915T070057Z`, where 28 of these say ""
                        # and the attempts beside them say `nodemaven`.
                        "provider": cell.provider_id,
                        "verdict": "session_closed",
                        "verdict_reason": outcome,
                        "exit_prefix": identity.get("exit_prefix"),
                        "exit_label": identity.get("exit_label"),
                        "exit_org": identity.get("org")})
            if outcome == "unreachable":
                # Said out loud at the session that produced it rather than
                # only in the summary, because it is the reading the console
                # otherwise gets wrong: a wall of `error` lines looks like a
                # target refusing us and this one never got that far.
                print(f"  {cell.key}: nothing in this session reached the "
                      f"target, so it scores nothing rather than zero")
            if breaker.tripped:
                print(f"  {cell.key}: stopped, {breaker.reason}")
                sink.write({"cell": cell.key, "target": cell.target,
                            "provider": cell.provider_id,
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
