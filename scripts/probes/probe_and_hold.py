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

Three parts of that protocol are unpriced, and the script is built so each one
gets a denominator here rather than being taken on trust:

  the entry shape   `--entry home,url` runs both, holding everything else fixed.
                    Without the `url` arm this run could only be compared
                    against rows taken on other days, and Google's yield moves
                    by 20 points between two afternoons
  the warm-up       `--warm off,L1,L2,L3` is an axis for the same reason. The
                    operator reports 75% on a warmed exit; that is one number
                    from one pool and hour, it attributes nothing to any
                    particular rung, and it is not the far end of an arm. So the
                    warm-up gets a control here rather than being switched on and
                    credited. It is a ladder rather than a switch because "warmed"
                    is not
                    one treatment, and because running the rungs one after
                    another would measure the hour: the same gateway, the same
                    country and the same browser moved 69 points to 52 between
                    two windows of one afternoon. All rungs interleave in one
                    process so that the hour is held across them and not only
                    inside each
  the hold          every held query carries `position`, so P(pass) at position
                    k is read off the rows. The operator's 88% is a conditional -
                    given a probe that was served - and the probe is the only
                    attempt in an identity that is not

What it does not do is retry a burned exit. Step 4 is "drop the IP", and a
script that instead tried again on the same one would confirm automation to the
target and heat a shared production pool. A cell whose probes keep failing is
stopped by the breaker, and each of those failures already cost a fresh exit.

Usage:
    python scripts/probes/probe_and_hold.py --dry-run
    python scripts/probes/probe_and_hold.py --engines patchright,zendriver \\
        --targets google_serp --identities 8 --series 5 --warm off,L1,L2,L3
"""
import argparse
import random
import sys
import time
import uuid
from collections import Counter, defaultdict, deque
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nmbench.console import tolerate_unencodable_output

# Ahead of the engines: zendriver installs colorama's stdout wrapper on import,
# and the wrapper has no reconfigure of its own.
tolerate_unencodable_output()

from nmbench import config, engines, gateway, providers, proxy, warm
from nmbench import queries as querylist
from nmbench.artifacts import ArtifactStore
from nmbench.breaker import CircuitBreaker, TransportWatch, is_transport_failure
from nmbench.humanize import fine_timer
from nmbench.relay import Relay
from nmbench.sink import JsonlSink
from nmbench.stats import band
from nmbench.targets import TARGETS

SENDS_REQUESTS = True

# What each rung of the warm-up ladder asks, in words and without a domain in
# them. The URLs live on the targets - a probe that knew a domain would be a
# probe that could warm one target better than another - but the *question* is
# the experiment's and belongs here, next to the flag that selects it. A target
# that declares a level is promising it built a list answering this description.
LADDER = {
    "L0": "cold. The exit meets the target for the first time at the probe, "
          "which is how every row taken before 2026-08-26 was measured",
    "L1": "one page of the target's own, before the probe",
    "L2": "several of the target's own surfaces, on more than one host",
    "L3": "L2, preceded by third-party pages that report the exit to the "
          "target's infrastructure without a navigation to the target itself",
    "N1": "L1's depth with none of L1's pages: one third-party page instead of "
          "one of the target's own, so the two differ in whose page it was and "
          "in nothing else",
    "N3": "L3's depth with none of L3's target-owned pages: the same six "
          "visits, four of them swapped for third-party pages that carry the "
          "same check. N1's design at the depth where the ladder actually "
          "separates",
}

# Rungs that are a control on composition rather than a step in depth, each
# mapped to the rung it controls. They sit outside the L-chain's cumulativeness
# - a control that were a superset of the rung it controls would be that rung
# plus something, which is the confound it exists to remove - so anything
# reasoning about the chain has to skip them.
#
# Named here and not inferred from the leading letter. A convention carried in
# a string is exactly the kind of thing that survives until someone adds `N2`
# meaning something else, and the cost of getting it wrong is an invariant that
# quietly stops being checked.
#
# It is a mapping and not a set as of 2026-09-01, when N3 was added. The depth
# invariant - a control matches the delivered depth of what it controls - lived
# as `N1` and `L1` written into `tests/test_probe_and_hold.py`, so N3 would have
# been added with that check silently applying to nothing. Which rung a control
# answers is a property of the control, so it is declared with it.
NEUTRAL_RUNGS = {"N1": "L1", "N3": "L3"}

# Spellings accepted by --warm, mapped to the level they mean. `off` and `on`
# are kept because they are what the rows already on disk were run with, and
# they mean exactly L0 and L1 - the same two treatments under new names.
WARM_LEVELS = {"off": "L0", "on": "L1"}
WARM_LEVELS.update({level: level for level in LADDER})
WARM_LEVELS.update({level.lower(): level for level in LADDER})

# The label that reaches every row's `identity` string. L0 and L1 keep the
# spellings the 2026-08-26 runs carry: an arm that renamed its key would stop
# grouping with the rows it is most worth being compared against, and the
# rename would be doing that for cosmetic consistency alone.
LEVEL_KEYS = {"L0": "off", "L1": "on"}


class TransportLost(RuntimeError):
    """Unwind the run when the failure stops being about the targets."""


class ArmInert(RuntimeError):
    """Unwind the run when an arm's label has stopped describing its treatment.

    Separate from `TransportLost` because the operator's next move is the
    opposite one: nothing is wrong with the network, the run is producing rows
    that will be read as an answer and are the baseline repeated.
    """


class InteractWatch:
    """Stop a run whose interaction arm never interacts.

    `--warm-interact on` rests on CSS selectors declared against a live page,
    and this repository cannot verify one before a run: the host is behind a VPN
    gateway and may not touch a Google surface. A wrong selector costs a whole
    night of fresh exits and produces rows that look exactly like interaction
    not helping - which is a wrong answer rather than an error, and the third
    time that shape has appeared here after the unknown gateway parameter
    answered with 200 and `check_feature_names_live.py` printing ABSENT for
    every name because it was scanning an empty file list.

    Only trips while nothing has ever landed. One successful interaction proves
    the selector, and blanks after that are pages varying, which is data. The
    rule that matters: a probe whose negative and positive arms agree is
    reporting on itself rather than on its subject.
    """

    def __init__(self, limit: int = 3):
        self.limit = limit
        self.blank = 0
        self.landed = 0
        self.arms = set()
        self.reason = None

    def record(self, planned: int, done: int, arm: str = None) -> None:
        if not planned:
            return
        if done:
            self.landed += 1
            return
        self.blank += 1
        if arm:
            self.arms.add(arm)
        if not self.landed and self.blank >= self.limit:
            # The arm is named rather than assumed to be `on`, which is what
            # this message said until 2026-09-04. A run of `off,scroll` that
            # tripped would have printed advice about `on` and about a selector,
            # and a scroll declares no selector at all - so the operator would
            # have gone looking for a CSS fault in an arm that has none.
            self.reason = (
                f"--warm-interact {sorted(self.arms) or ['on']} has delivered "
                f"0 actions on {self.blank} pages and has never delivered one, "
                f"so the arm is the inert warm-up under a label saying "
                f"otherwise. Read `interact_detail` on the warm rows: "
                f"`type:miss` is a selector matching nothing, and anything else "
                f"names the step that raised. A scroll has no selector to miss, "
                f"so a `scroll:` entry is the page or the driver and never a "
                f"declaration this repository can fix by reading it")

    @property
    def tripped(self) -> bool:
        return self.reason is not None


class Cell:
    """One question: this engine, this target, this country, entered this way.

    A plain structure with a key, the same shape the matrix runner uses. The key
    reaches every row, so two cells that differ only in whether the exit was
    warmed stay separable in the output - which is the whole point of running
    both in one window.
    """

    def __init__(self, engine, target, country, arm, level, entry, geo="off",
                 hand="off", interact="off", name_hand=False,
                 name_interact=False):
        self.engine = engine
        self.target = target
        self.country = country
        self.params_label, self.params = arm
        self.level = level
        self.warm = level != "L0"
        self.entry = entry
        self.geo = geo
        self.hand = hand
        self.interact = interact
        # Which action kinds this cell runs, or None when it runs none.
        # `warm.SETS` owns the mapping, so the flag's vocabulary and the filter
        # that acts on it cannot disagree - a name accepted by one and unknown
        # to the other would be an arm that parsed and then did nothing.
        self.kinds = warm.SETS.get(interact)
        self.key = (f"{engine}/{target}/{country}/{self.params_label}/"
                    f"warm-{LEVEL_KEYS.get(level, level)}/entry-{entry}/"
                    f"geo-{geo}"
                    # Named only when the axis is varied, which is the provider
                    # precedent rather than a new rule: a run that does not ask
                    # the cursor question keeps the key every probehold file on
                    # disk already uses, so the ladder stays comparable against
                    # the nights before this axis existed. `humanize_mode` is on
                    # every row regardless, so a row whose key is silent about
                    # the cursor still says which one it got.
                    + (f"/hand-{hand}" if name_hand else "")
                    # Same precedent and the same protection. The protection is
                    # the load-bearing half here: a run of `--warm-interact on`
                    # alone writes a key that groups with every inert L3 row on
                    # disk, which is only safe because `interact` is on every
                    # row regardless and the warm rows carry what was actually
                    # delivered rather than what was asked for.
                    + (f"/warm-interact-{interact}" if name_interact else ""))


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
                        help="rungs of the warm-up ladder, comma separated, all "
                             "interleaved in one window. "
                             + "; ".join(f"{k}: {v}" for k, v in LADDER.items())
                             + ". `off` and `on` are accepted as the older "
                               "spellings of L0 and L1 and mean the same two "
                               "treatments. Which pages a rung opens is the "
                               "target's to declare; a target that does not "
                               "declare a rung is refused rather than quietly "
                               "given a shorter one")
    parser.add_argument("--warm-urls", default=None, metavar="URL,URL",
                        help="pages visited before the probe, comma separated, "
                             "replacing the rung the target declares. The front "
                             "page is always appended last and must not be "
                             "repeated here. It is one flat list, so it can only "
                             "be used with a single warm rung selected: with two "
                             "it would make both of them the same sequence under "
                             "two labels. Every row records `warm_level` and "
                             "`warm_depth`, so an overridden rung stays "
                             "recognisable after the fact")
    parser.add_argument("--warm-interact", default="off",
                        help="off, scroll, type, on, or any comma-separated "
                             "mix to run them interleaved. Whether a warm-up "
                             "page is used or only loaded. `off` is `goto` and "
                             "a sleep, which is what every warm row on disk was "
                             "taken with; the rest run the actions the target "
                             "declares for that URL, filtered to the named "
                             "kind, and `on` is all of them. The axis is "
                             "decomposable on purpose: `on` alone would be one "
                             "treatment made of reading two articles and typing "
                             "into three Google surfaces, and a difference in "
                             "it would not say which part moved - which is "
                             "exactly what the warm ladder did and what N1 and "
                             "N3 had to be added to untangle afterwards. "
                             "`scroll` is the half with no selector in it, so "
                             "it is the half that cannot silently miss. It "
                             "crosses the ladder rather than deepening it: "
                             "folding this into L3 would make depth and "
                             "interaction one treatment and every L3 row "
                             "already measured would change what it is a "
                             "comparison of. Time on the page is held across "
                             "the arms - the interaction is subtracted from the "
                             "dwell - so they differ in what was done and not "
                             "in how long it took. Refused with --warm off, and "
                             "refused when the selected rungs name no page the "
                             "target declares an action of that kind for, "
                             "because either would be the inert arm running "
                             "twice under two labels")
    parser.add_argument("--entry", default="home",
                        help="home, url, or home,url. `home` lands on the front "
                             "page and types; `url` navigates to the search URL, "
                             "which is how every row already on disk was taken")
    parser.add_argument("--geo", default="off",
                        help="off, align, or off,align to run both in one "
                             "window. `align` makes the browser agree with the "
                             "exit address, which it otherwise contradicts on "
                             "every foreign exit the run draws. NOTE: the arm "
                             "is not the same treatment on every engine. The "
                             "Chromium family is handed a timezone and nothing "
                             "else - the language list stays the host's, "
                             "because verdict markers are English strings. "
                             "Camoufox and cloak take a boolean instead and "
                             "resolve the address themselves, which moves the "
                             "locale and the geolocation as well as the zone. "
                             "Google's page language is pinned by `hl=en` in "
                             "the URL and the captcha verdict is read off the "
                             "`/sorry/` path, so judging survives it, but the "
                             "two arms are not one treatment and a cross-engine "
                             "geo comparison is not a like-for-like one")
    parser.add_argument("--humanize", default="off",
                        help="off, engine, trueman, or a comma list of them to "
                             "run several interleaved in one window. How the "
                             "cursor reaches the search box and the consent "
                             "button: `off` is Playwright's own click, which "
                             "teleports; `engine` is the browser's own input "
                             "synthesis, which only Camoufox and cloak have; "
                             "`trueman` drives the model in nmbench.pointer, "
                             "which every Playwright engine can take. Refused "
                             "for a matrix holding an engine without a mode "
                             "asked for, so the arms differ in the cursor and "
                             "not in which engines got one. `off,trueman` is "
                             "the shape that answers the question - run "
                             "sequentially instead and the two arms are "
                             "separated by the hour, which on this target "
                             "moves the yield further than any flag here has. "
                             "NOTE: trueman only reaches `--entry home` - the "
                             "url entry shape clicks nothing, so a trueman row "
                             "there would record a hand that never moved")
    parser.add_argument("--humanize-seed", type=int, default=None,
                        help="seed for the pointer alone, so a run can repeat "
                             "its cursor without repeating its queries. "
                             "Defaults to --seed, and to nothing when neither "
                             "is given, which is the production shape: a fleet "
                             "whose pointer statistics are identical is a tell "
                             "whatever value they share")
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
    parser.add_argument("--redraws", type=int, default=2,
                        help="how many times one identity may be drawn again "
                             "after its probe failed below the application "
                             "layer - a refused tunnel, a handshake that did "
                             "not complete. Those attempts carry no answer "
                             "from the target, so without this they enter the "
                             "denominator as refusals and feed the breaker. "
                             "0 restores the behaviour every run before "
                             "2026-08-28 had")
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


def warm_ladder(target) -> dict:
    return dict(getattr(target, "warm_ladder", {}))


def warm_levels() -> list:
    """Every rung any target declares, plus the cold one, in ladder order.

    Read off the targets rather than hardcoded, so that adding a rung to a
    target is the whole change: a list here would be a second place to forget.
    """
    declared = {"L0"}
    for target in TARGETS.values():
        declared.update(warm_ladder(target))
    return [level for level in LADDER if level in declared]


def warm_sequence(target, level, args) -> list:
    """Pages visited before the probe, front page last, for one rung.

    The front page is appended here rather than being left to the caller so
    that the two callers - the run and `describe` - cannot disagree about what
    the warm-up is, which is the way the printed plan stops matching the run.

    An override is one flat list for every target rather than one per target,
    because the arms being compared have to differ in depth and in nothing
    else; a per-target list would make `warm_depth` mean a different sequence
    in each column and the comparison would not be between depths any more.
    Preflight refuses an override across more than one rung for the same
    reason: it would give two arms one sequence under two labels.
    """
    if level == "L0":
        return []
    if args.warm_urls is None:
        pages = list(warm_ladder(target).get(level, ()))
    else:
        pages = [u.strip() for u in args.warm_urls.split(",") if u.strip()]
    return [*pages, target.home_url]


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
        # Only when a `home` arm is present. Until 2026-08-26 this fired on the
        # engine alone, which refused the very command its own message tells
        # you to run: `--entry url` for the whole matrix puts no engine in an
        # entry column it cannot fill, because there is only one column and
        # every engine navigates. The guard is about mixing entry shapes, and
        # it was written as though it were about the engine. Two of the three
        # engines this repository ranks highest on Amazon - seleniumbase and
        # botasaurus - cannot type, so the old form made the top three
        # unrunnable together on any target.
        if not engine.supports_typing and any(c.entry == "home" for c in cells):
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
        missing = sorted({c.hand for c in cells}
                         - set(engine.humanize_modes))
        if missing:
            parser.error(
                f"engine {name!r} has no {missing} humanization - it "
                f"offers {sorted(engine.humanize_modes)}. The flag would move "
                f"the cursor for the engines that have it and change nothing "
                f"for this one, and an option honoured by some columns and "
                f"dropped for others reads as an engine difference. Drop the "
                f"engine, or pick modes every engine in the matrix has. Note "
                f"that 'engine' and 'trueman' are different clients and not "
                f"two settings of one: 'engine' is the browser synthesising "
                f"its own input, 'trueman' is the model in nmbench.pointer "
                f"driven from outside it")

    # A pointer only exists where something is clicked, and only the `home`
    # entry shape clicks: `url` navigates straight to the search URL and touches
    # no element. A trueman row in a url arm would carry `humanize_mode=trueman`
    # and a hand that never moved, which is the label-without-the-treatment
    # failure the geo check above names.
    #
    # **The same is true of `engine` and it is not refused here.** Camoufox's
    # and cloak's own humanization acts on the browser's input synthesis, so on
    # a url arm it is equally inert - every `--entry url --humanize on` row ever
    # written by this script carries a humanize label for a cursor that had
    # nothing to do. That is a defect and it is stated rather than fixed in
    # passing: refusing it would break existing commands on the strength of an
    # argument from mechanism, and nothing here has measured whether Camoufox
    # does something else with the flag. Worth an hour with `--entry url` and
    # the two modes before deciding.
    if any(c.hand == "trueman" and c.entry == "url" for c in cells):
        parser.error(
            "--humanize trueman with --entry url: the url entry shape "
            "navigates straight to the search URL and clicks nothing, so the "
            "cursor would never move while every row still recorded "
            "humanize_mode=trueman. Run --entry home, or drop the flag. "
            "Refused rather than dropped even though the cursor is now an "
            "interleaved axis and this is only one cell of the matrix: "
            "dropping it would leave a home,url by off,trueman run three cells "
            "wide and looking like a 2x2, which is worse than not starting")

    check_entry(parser, cells)
    check_warm(parser, args, cells)
    check_interact(parser, args, cells)

    if not args.direct and not config.available():
        parser.error("gateway credentials are not set, so nothing can leave "
                     "through the proxy. Copy .env.example to .env, or pass "
                     "--direct to hold the address constant instead.")


def check_entry(parser, cells) -> None:
    """A target may only be asked for a front door it declares.

    **Only the cells that actually use one.** This check was unconditional from
    the first commit and was wrong for as long: `--entry url` navigates straight
    to `target.url(query)` and never reads `home_url` or `search_box`, so
    requiring them of every target in the matrix refused runs that could not
    have failed the way the message describes.

    Measured 2026-09-05: it blocked **7 of the 9 targets** on `--entry url` -
    ipinfo, ddg_serp, bing_serp, walmart_search, google_maps, lazada_search and
    shopee_search - which is every target except the two that happen to declare
    a front door. The `--entry` help says url "is how every row already on disk
    was taken", so the guard contradicted the shape the repository was built
    with, and nothing noticed because the runs that were actually being made all
    used the two targets that pass it.

    It reads as a harmless over-restriction and is not. The two marketplace
    targets exist to have their bodies read - neither can return `ok` yet and
    both say so in `judge()` - and this was the check standing between the run
    and the archived bodies that would let an `ok` rule be written from a
    measurement instead of from their public markup.

    Split out of `preflight` for the reason `check_warm` gives below: it is
    reachable from a test without an installed browser, and `preflight` is not.
    """
    for name in sorted({c.target for c in cells if c.entry == "home"}):
        target = TARGETS[name]
        if not getattr(target, "home_url", None) \
                or not getattr(target, "search_box", None):
            parser.error(
                f"target {name!r} declares no home_url and search_box, so it "
                f"cannot be entered through its own front door with --entry "
                f"home. Add both to the target rather than guessing a selector "
                f"here - a wrong one fails as an empty query and is recorded as "
                f"the target refusing - or run this target with --entry url, "
                f"which navigates to the search URL and needs neither")


def check_warm(parser, args, cells) -> None:
    """Everything that could make a rung's label a lie.

    Split out of `preflight` because it is the only part of it a test can reach
    without an installed browser, and because the failure it guards against is
    the expensive kind: a rung that quietly ran a shorter sequence produces rows
    that look exactly like the deeper warm-up not helping. That is a wrong
    result rather than an error, and the run costs hours of fresh exits before
    it can be read.
    """
    # A rung the target has not declared is refused rather than answered with a
    # shorter one. Silently falling back would produce a row labelled `L3` whose
    # warm-up was L1's, and the label is the only thing an analysis has.
    if args.warm_urls is None:
        for name in sorted({c.target for c in cells}):
            declared = warm_ladder(TARGETS[name])
            missing = sorted({c.level for c in cells
                              if c.target == name and c.warm
                              and c.level not in declared},
                             key=list(LADDER).index)
            if missing:
                parser.error(
                    f"target {name!r} declares no {missing} in its "
                    f"warm_ladder, only {sorted(declared) or 'nothing'}. The "
                    f"rungs are the target's to write because the probe must "
                    f"not know a domain, and answering a missing rung with a "
                    f"shorter one would label a row as a depth it never ran. "
                    f"Add the rung to the target, or drop it from --warm")
        return

    warm_cells = [c for c in cells if c.warm]
    if not warm_cells:
        parser.error("--warm-urls with no warm arm: --warm off visits nothing, "
                     "so the list would be accepted and silently dropped, and "
                     "the run would be labelled as a depth it never ran")
    levels = {c.level for c in warm_cells}
    if len(levels) > 1:
        parser.error(
            f"--warm-urls is one flat list and {sorted(levels)} are "
            f"{len(levels)} rungs, so every one of them would run the same "
            f"sequence under a different label. Select one rung, or drop "
            f"--warm-urls and let each rung use the target's own list")
    given = [u.strip() for u in args.warm_urls.split(",") if u.strip()]
    if not given:
        parser.error("--warm-urls is empty. Pass --warm off to run cold rather "
                     "than a warm arm with nothing in it")
    bad = [u for u in given if not u.startswith(("http://", "https://"))]
    if bad:
        parser.error(f"--warm-urls wants absolute URLs, got {bad}. `goto` "
                     f"resolves a bare path against the blank page and the "
                     f"visit fails, which is recorded as a warm-up error rather "
                     f"than as a typo here")
    for name in sorted({c.target for c in warm_cells}):
        home = TARGETS[name].home_url
        if home in given:
            parser.error(
                f"--warm-urls repeats {name}'s front page {home!r}, which is "
                f"appended last regardless. Two visits to it are a different "
                f"warm-up from the one being labelled, and the second lands on "
                f"a page the first already set cookies on")


def check_interact(parser, args, cells) -> None:
    """Everything that would make an interacting arm the inert arm renamed.

    Split out beside `check_warm` and for the same reason: it is reachable from
    a test without an installed browser, and every fault it catches produces a
    plausible run rather than an error. An `on` arm with nothing to do writes
    the same rows the `off` arm writes, and the analysis afterwards reads a
    difference of zero as an answer to the question. That is the gateway's
    unknown-parameter failure - 200 and the setting dropped - in a different
    place.

    Every check below is per selected kind and not per cell, because that is
    where the axis can now go wrong: a target that declares scrolls and no
    typing is a perfectly good `--warm-interact scroll` arm and a silently
    inert `--warm-interact type` one.

    What it cannot check is the only thing that really decides the typing half:
    whether the declared selector matches the live page. Nothing here may touch
    a Google surface from this host, so that check is `InteractWatch` at runtime
    and `interact_detail` in the output. The scroll half names nothing on the
    page and needs no such check, which is the point of it being selectable
    alone.
    """
    active = [c for c in cells if c.kinds]
    if not active:
        return

    for name in sorted({c.engine for c in active}):
        engine = engines.REGISTRY[name]
        # What the interaction needs is a Playwright page: `wait_for_selector`
        # and `handle.type` for the typing half, `page.mouse.wheel` for the
        # scroll half. `visit` needs only `goto`, which every session's page
        # has, and that is why warming itself takes no capability flag - the
        # interaction is the first thing here that does.
        #
        # **Read off `humanize_modes` and not off `supports_typing`, which is
        # what this check used until 2026-09-04 and which admitted exactly the
        # engine that cannot run it.** `supports_typing` says the engine can
        # enter a query somehow; zendriver declares it True and speaks raw CDP,
        # and its page is `zendriver._Page`, whose own docstring says it is
        # deliberately not a Playwright imitation and carries `goto`, `evaluate`
        # and nothing else. So `--warm-interact on --engines zendriver` was
        # accepted by preflight and would have raised `AttributeError` on the
        # first warm page of every identity, recorded as `type:AttributeError`
        # and read afterwards as an interaction that did not help.
        #
        # This is a proxy and is named as one: `trueman` in `humanize_modes` is
        # the declaration this repository already keeps, per engine, for "the
        # harness drives this page's Playwright mouse" - see the comment at
        # `zendriver.py:449` that gives that as the reason its own set is
        # `{"off"}` alone. It is not a direct test that the page is Playwright.
        if "trueman" not in engine.humanize_modes:
            parser.error(
                f"engine {name!r} has no Playwright page for the harness to "
                f"drive - it offers {sorted(engine.humanize_modes)} "
                f"humanization - so --warm-interact would load the warm pages, "
                f"fail on the first action, and leave every row saying the arm "
                f"was on. Unlike --entry, this is refused whatever the entry "
                f"shape: the warm-up runs before the entry and does not depend "
                f"on it")

    cold = sorted({c.level for c in active if not c.warm})
    if cold:
        parser.error(
            f"--warm-interact {sorted({c.interact for c in active if not c.warm})} "
            f"with --warm {cold}: the cold rung visits no page at all, so there "
            f"is nothing to interact with and the cell would be the cold arm "
            f"under a second label. The shape that answers this question holds "
            f"the rung and varies the interaction: --warm L3 --warm-interact "
            f"off,on. L0 against L3 is already measured - 25% to 73% on probe "
            f"rows, Fisher p=0.039, probehold_20260904T000605Z")

    for name in sorted({c.target for c in active}):
        target = TARGETS[name]
        faults = warm.problems(target)
        if faults:
            parser.error(f"target {name!r} declares warm_actions that cannot "
                         f"run: {faults}")
        anything = warm.pages_with_actions(target)
        for interact in sorted({c.interact for c in active if c.target == name}):
            declared = warm.pages_with_actions(target, warm.SETS[interact])
            # Checked against the sequence that will actually be delivered, so a
            # `--warm-urls` override is covered by the same test rather than by
            # a second one that could disagree with it.
            for level in sorted({c.level for c in active
                                 if c.target == name and c.interact == interact}):
                pages = set(warm_sequence(target, level, args))
                if pages & declared:
                    continue
                # Both sets are named, and the second is the one that turns a
                # refusal into an instruction: a target that declares scrolls
                # and no typing should send you to `--warm-interact scroll`
                # rather than to writing a selector for a page you cannot see
                # from this host.
                parser.error(
                    f"target {name!r} rung {level} visits {sorted(pages)} and "
                    f"declares no {interact} action for any of them. Under "
                    f"{interact} it declares {sorted(declared) or 'nothing'}; "
                    f"across every kind it declares "
                    f"{sorted(anything) or 'nothing'}. The arm would run the "
                    f"inert warm-up and label it {interact!r}. Pick a rung that "
                    f"includes a declared page, pick a kind the rung declares, "
                    f"or add the actions to the target")


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
    levels = sorted({c.level for c in cells}, key=list(LADDER).index)
    print(f"warm      : {levels}, interleaved rather than run one after another")
    for level in levels:
        print(f"            {level} - {LADDER[level]}")
    low, high = args.dwell_range
    # Printed page by page, not as a count. A depth comparison is worth nothing
    # if the plan cannot be checked against what was intended, and a typo in one
    # URL of six is invisible in the number 6.
    for name in sorted({c.target for c in cells if c.warm}):
        for level in levels:
            pages = warm_sequence(TARGETS[name], level, args)
            if not pages:
                continue
            print(f"warm-up   : {name} {level}, {len(pages)} pages, "
                  f"{low:.0f}-{high:.0f}s each, "
                  f"{len(pages) * (low + high) / 2:.0f}s on average"
                  f"{' (target default)' if args.warm_urls is None else ''}")
            # Which pages each interaction arm will actually act on, marked per
            # URL for the same reason the URLs are printed at all: with
            # translate the only declared page, an `on` arm at L3 differs from
            # `off` on one page of six, and that is a much weaker treatment
            # than the flag name suggests. It should be visible before the run
            # rather than inferred from the rows after it.
            #
            # Per arm since 2026-09-04, because the arms no longer act on the
            # same pages: `scroll` and `type` can each be the whole treatment on
            # a different subset, and one merged count would hide a `type` arm
            # that touches nothing behind a `scroll` arm that touches two.
            arms = sorted({c.interact for c in cells
                           if c.kinds and c.target == name})
            for url in pages:
                counted = [(arm, len(warm.actions_for(TARGETS[name], url,
                                                      warm.SETS[arm])))
                           for arm in arms]
                marks = ", ".join(f"{arm} {n}" for arm, n in counted if n)
                print(f"            {url}"
                      + (f"   <- actions: {marks}" if marks else ""))
            for arm in arms:
                touched = len(set(pages) & warm.pages_with_actions(
                    TARGETS[name], warm.SETS[arm]))
                print(f"            --warm-interact {arm} acts on {touched} of "
                      f"{len(pages)} pages in this rung")
    # Spelled out per engine rather than as one sentence, because it is not one
    # treatment: the boolean engines resolve the address themselves and move the
    # locale and the geolocation with the zone.
    print(f"interact  : {sorted({c.interact for c in cells})}, and the dwell "
          f"is spent on it rather than added to it, so both arms sit on a warm "
          f"page for the same {low:.0f}-{high:.0f}s")
    # Printed for the half that has a selector to get wrong, and not for the
    # other one. A caution attached to an arm that cannot trip it is a caution
    # the operator learns to skip, and `scroll` names nothing on the page - that
    # is the reason it is selectable on its own from a host that may not look at
    # a Google surface.
    if any("type" in (c.kinds or ()) for c in cells):
        print("            the typing selectors are declared against a live "
              "page and cannot be verified from this host. Read "
              "`interact_detail` on the warm rows first: type:miss is the "
              "selector, anything else is the page. The run stops itself if "
              "nothing ever lands")
    if any(c.kinds and "type" not in c.kinds for c in cells):
        print("            the scroll arm names no element, so it cannot miss "
              "a selector. What it can still be is unnoticed: the detector "
              "reads wheel magnitude and interval and excludes both as device, "
              "so nothing here scores whether a driven scroll looks human")
    print(f"geo       : {sorted({c.geo for c in cells})}")
    if any(c.geo == "align" for c in cells):
        for name in sorted({c.engine for c in cells}):
            how = ("a boolean, engine-resolved: zone, locale and geolocation"
                   if engines.REGISTRY[name].supports_geoip
                   else "the timezone only, the language list stays the host's")
            print(f"            {name}: {how}")
    print(f"browser   : {'headless' if args.headless else 'headful'}, "
          f"preset {args.preset}")
    # Printed even when off, unlike `geo`, because the seed only exists in one
    # of the three modes and a plan that omitted the line would leave a reader
    # unable to tell an unhumanized run from an older build that had no such
    # flag. It is also the axis most easily believed to have been on.
    hand_seed = args.seed if args.humanize_seed is None else args.humanize_seed
    hands = sorted({c.hand for c in cells})
    print(f"humanize  : {hands}"
          + (", interleaved rather than run one after another, which is the "
             "whole point of the axis: run sequentially and the two arms are "
             "separated by the hour" if len(hands) > 1 else ""))
    if "trueman" in hands:
        print("            " + (
            "unseeded: every walk is drawn fresh and the run is not "
            "reproducible, which is the default for the same reason --seed's "
            "is" if hand_seed is None else
            f"seed {hand_seed} mixed with each identity name, so a walk is "
            f"reproducible from the string in its own rows"))
    if hands != ["off"]:
        for name in sorted({c.engine for c in cells}):
            print(f"            {name}: offers "
                  f"{sorted(engines.REGISTRY[name].humanize_modes)}")
    # Said rather than refused, and the difference is that the label stays true:
    # under `--headless` the mover's geometry is delivered exactly as it is
    # headful, so a row reading `humanize_mode=trueman` is not lying the way a
    # `trueman` row on a `--entry url` arm would be. What is lost is the timing.
    # Measured 2026-09-03, `lab/probes/humanize_smoke.py`, 18 paced points an
    # arm: headful the pacer overran 1 point and delivered a 7.00-7.15 ms median
    # against a model asking 7.11-7.22; headless it overran 14-16 and delivered
    # 16.65, because an awaited `page.mouse.move` is frame-bound with no window.
    # Two of the detector's 19 metrics are interval metrics, so this is half the
    # model, and the operator has to know which experiment he started.
    if "trueman" in hands and args.headless:
        print("            headless: the pointer's positions are the model's "
              "and its intervals are the transport's - an awaited mouse.move "
              "is frame-bound at ~16.6 ms with no window, against a model "
              "asking ~7.1, so the pacer overruns almost every point. This is "
              "a geometry arm. Drop --headless to run the timing too, and see "
              "`pointer_overruns` on the rows either way")
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


# The three fields the page counter carries, named once so a warm visit and a
# probe cannot drift apart in what they report.
COUNTER_FIELDS = ("bytes", "blocked", "allowed")


def counter_delta(counter: dict, base: dict) -> dict:
    """What one navigation cost, on the byte counter the engine installed.

    Differenced rather than read, and for the same reason as in
    `nmbench.engines.base.run_search`: the counter belongs to the page and the
    page outlives the navigation, so reading it whole would charge the sixth
    warm page for the five before it. Mirroring that function is the point -
    priced by the same instrument, a warm visit and a probe are addable and a
    rung's warm-up has a price in the same units as what it buys.

    **`None` rather than `0` when no counter was installed.** `new_page` takes
    the dict on every engine and only the Playwright-driven ones write to it;
    zendriver, botasaurus-driver and SeleniumBase accept it and never touch it.
    A zero there would read as a page that cost nothing, which is the one wrong
    answer this column can give, and it would read that way in exactly the arms
    where warm-up traffic is most worth knowing.

    This is the page-level counter and not the CONNECT relay. It sees what the
    page reported receiving, so it is under what the socket carried - no
    headers, no TLS, nothing outside the page's own network events. Where a
    relay is running, `on_row` overwrites `bytes` with its socket-level delta,
    and it does that for probe rows already, so the precedence is the same in
    both phases. It is not the same on the ladder: `patchright.needs_relay` is
    False, so no relay runs and every byte below is the page counter's.
    """
    if "bytes" not in counter:
        return dict.fromkeys(COUNTER_FIELDS)
    return {field: counter.get(field, 0) - base.get(field, 0)
            for field in COUNTER_FIELDS}


def hand_delta(hand, walked, paced) -> dict:
    """What one warm visit cost the cursor, in the probe rows' own columns.

    Named the same as `run_search` writes them, so `pointer_ms` on a warm row
    and `pointer_ms` on a probe row are the same quantity and add up over a
    session. Without this the walking done during an interactive warm-up would
    be in no column at all: `run_search` reads `hand.walk_ms` when it starts, so
    anything spent before it is silently outside every total.

    `None` and not `0` when there is no hand, matching the rest of this
    repository's absent-versus-zero rule - a zero would read as a cursor that
    walked instantly.
    """
    if hand is None:
        return {"pointer_ms": None, "pointer_device": None,
                "pointer_overruns": None, "pointer_points": None}
    now = hand.stats()
    return {"pointer_ms": round(hand.walk_ms - walked),
            "pointer_device": now["device"],
            "pointer_overruns": now["overruns"] - paced["overruns"],
            "pointer_points": now["paced_points"] - paced["paced_points"]}


def run_identity(active, page, counter, cell, target, queries, *, rng, args,
                 on_row) -> tuple:
    """One exit: warm it, probe it, and hold it if the probe was served.

    Reports every row through `on_row` including the warm-up visits, because
    what the caller does after one - byte accounting, the transport watch,
    printing - is the same for all of them and duplicating it per phase is how
    one phase ends up counted differently from another.

    Returns the probe's verdict and the probe's error string. The verdict is
    the only one the breaker is fed; the error is what the caller needs to tell
    a refusal from an attempt that never arrived, and it is returned rather
    than re-derived from `rows` because the caller would have to guess which
    row was the probe.
    """
    low_dwell, high_dwell = args.dwell_range
    planned = warm_sequence(target, cell.level, args) if cell.warm else []
    delivered = 0
    # The session's cursor, or None. Read off the session rather than rebuilt,
    # so a warm-up click and a probe click are the same hand at the same
    # position - a hand that started fresh here would teleport between the last
    # warm page and the search box, which is the shape `PointerHand` exists to
    # avoid.
    hand = getattr(active, "hand", None)
    # (planned, done) per warm visit, for the identity-level columns below.
    interacted = []
    if cell.warm:
        # The front page last, always. The warm pages carry search boxes of
        # their own and typing into one of those would ask a different question
        # of a different endpoint.
        for url in planned:
            # Retried once, and only once. Measured 2026-08-26 in
            # `probehold_20260826T152748Z`: 30 of 144 warm visits failed, and
            # the failures were per-host rather than per-window - a failure did
            # not predict the next visit failing (0.13 against a 0.21 base
            # rate) and the ten slices of the run carried 5, 3, 6, 2, 3, 4, 1,
            # 2, 4 and 0 of them. A transient that is not clustered is what a
            # single retry is for. A second retry would start to be a different
            # client - three navigations to one URL inside a minute is a shape
            # no session produces - so the shortfall is recorded instead.
            for attempt in (1, 2):
                started = time.perf_counter()
                # Per attempt and not per URL. A first attempt that failed
                # partway still pulled bytes down, and they belong on its own
                # row: added to the retry instead, they would price a page that
                # loaded once as if it were the expensive one.
                spent_from = dict(counter)
                walked_from = hand.walk_ms if hand is not None else None
                paced_from = hand.stats() if hand is not None else None
                error = None
                try:
                    visit(page, url)
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                # Closed here rather than after the interaction, so the column
                # keeps meaning what it means on every warm row already on
                # disk: the navigation, and nothing else. What the interaction
                # cost is `interact_ms`, beside it and addable to it.
                nav_ms = round((time.perf_counter() - started) * 1000)

                # Only on a page that loaded. Driving a failed navigation would
                # aim a selector at whatever the browser is showing instead,
                # and the miss would be recorded against the target.
                acted = dict(warm.NOTHING)
                # `cell.kinds` and not `cell.interact == "on"`, which is what
                # this read until 2026-09-04 and which would have run the whole
                # scroll axis as the inert arm: `--warm-interact scroll` parsed,
                # printed, passed preflight, and never reached this line. The
                # rows would have carried `interact: scroll` and
                # `interact_delivered: 0` on every page, which is the shape of a
                # declared selector missing rather than of a flag that does
                # nothing - so the first reading would have been "the pages do
                # not declare scrolls" and the fix would have gone into the
                # target.
                #
                # The `is not None` is load-bearing rather than tidy. `off`
                # gives `kinds = None`, and `None` is exactly what
                # `warm.actions_for` reads as "every kind", so a truthiness test
                # that let `None` through would turn the control arm into the
                # full treatment.
                if error is None and cell.kinds is not None:
                    acted = warm.run(page, target, url, rng=rng, hand=hand,
                                     kinds=cell.kinds)
                # A bookkeeping row: no `query`, so every analysis in this
                # repository treats it as not an attempt and it can never reach
                # a pass rate. It is written anyway because a warm arm whose
                # warming silently failed would read as the warm-up not
                # working, and because warming costs traffic that belongs in
                # the price of the protocol.
                #
                # That last clause was true of the comment and false of the
                # row until 2026-09-01. The row carried `elapsed_ms` and no
                # byte column at all - 549 warm rows in
                # `probehold_20260831T222129Z` and not one of them priced - so
                # the price of a rung was the one thing the ladder could not
                # report. What the mistake looked like from the inside: `on_row`
                # attaches `bytes` from the relay to every row it is handed, and
                # that reads like the columns are uniform across phases. It only
                # runs when a relay is running, the ladder's engine does not
                # need one, and the probe rows that did carry bytes got them
                # somewhere else entirely - from `run_search`, which no warm
                # visit goes through.
                #
                # The dwell is the whole time on the page and the interaction
                # is spent out of it, not added to it. Otherwise the `on` arm
                # sits on every warm page for longer than the `off` arm and the
                # axis varies two things - what was done, and how long the
                # session lingered - with no way afterwards to say which moved.
                # `dwell_ms` is what was drawn and `dwell_slept_ms` is what was
                # left, so the run can be checked for having held it rather
                # than trusted to have.
                dwell_ms = rng.uniform(low_dwell, high_dwell) * 1000.0
                rest_ms = max(0.0, dwell_ms - acted["interact_ms"])
                interacted.append((acted["interact_planned"],
                                   acted["interact_done"]))
                on_row({"phase": "warm", "url": url, "verdict": "warm_visit",
                        "error": error, "position": None, "query": None,
                        "warm_attempt": attempt,
                        "elapsed_ms": nav_ms,
                        "dwell_ms": round(dwell_ms),
                        "dwell_slept_ms": round(rest_ms),
                        # Differenced the way `run_search` differences them, so
                        # a warm visit and a probe are priced by one instrument
                        # and the pointer columns add up across the phases. A
                        # session-level read here would charge the sixth warm
                        # page with the five walks before it.
                        **hand_delta(hand, walked_from, paced_from),
                        **acted,
                        **counter_delta(counter, spent_from)})
                time.sleep(rest_ms / 1000.0)
                if error is None:
                    delivered += 1
                    break

    low_gap, high_gap = args.gap_range
    probe_verdict = "error"
    # Not None: an identity whose first query never produced a row at all - the
    # session died between the warm-up and the probe - has failed for a reason
    # the caller cannot see, and a redraw on an unknown cause would loop.
    probe_error = "the probe produced no row"
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
                    "position": position,
                    # What this identity actually received, against the
                    # `warm_depth` it was labelled with. Written here rather
                    # than left to be reconstructed, because a rung name is an
                    # intention and this is the treatment.
                    #
                    # `probehold_20260826T152748Z` is why the column exists: 4
                    # of its 12 L2 identities were delivered 1 or 2 pages of
                    # the 4 their label claims, which is no deeper than L1, and
                    # nothing in the row said so. The ladder contrast in that
                    # run is not the contrast its labels name, and it took a
                    # re-read of the raw warm rows to find out.
                    "warm_delivered": delivered,
                    "warm_short": delivered < len(planned),
                    # The same distinction `warm_delivered` draws, for the same
                    # reason: `interact` is the label and these are the
                    # treatment. A probe row that says the arm was on and
                    # carries `interact_delivered: 0` was warmed exactly as the
                    # off arm was, and an analysis that groups on the label
                    # alone would count it as evidence about interaction.
                    #
                    # Deliberately not `interact_planned`: that name is taken on
                    # the warm rows, where it means the plan for one page, and
                    # one name meaning a page here and a session there is how a
                    # column gets read wrong by whoever writes the analysis six
                    # weeks from now. `interact_short` carries the shortfall
                    # and the plan is recoverable from the rung.
                    "interact_delivered": sum(d for _, d in interacted),
                    "interact_short": any(d < p for p, d in interacted)})
        on_row(row)
        if position == 0:
            probe_verdict = row["verdict"]
            probe_error = row.get("error")
        # Step 4, and the reason this is not the matrix runner: a refused exit
        # ends the identity instead of being asked again. It is spent either
        # way, and asking it again is what heats a shared production pool.
        if row["verdict"] != "ok":
            break
        time.sleep(rng.uniform(low_gap, high_gap))
    return probe_verdict, probe_error


def one_line(text: str, width: int) -> str:
    """Collapse to a single line and elide the middle, never the tail.

    Playwright and CDP errors put the call that failed at the front and the
    reason at the back - `net::ERR_TUNNEL_CONNECTION_FAILED`, a deadline, a
    host - so a plain `[:width]` keeps the half that is the same in every
    message and drops the half that identifies this one. At width 40 that turned
    every navigation failure of the 2026-08-27 obscura run into the identical
    string `Error: Page.goto: Protocol error (Page.n`, which names the call and
    says nothing about why it failed. Keeping both ends costs three characters.
    """
    flat = " ".join(text.split())
    if len(flat) <= width:
        return flat
    if width <= 3:
        return flat[:width]
    head = (width - 3) // 2
    return f"{flat[:head]}...{flat[head + 3 - width:]}"


def report(row: dict) -> None:
    """One line per row while the run works, warm-up visits included.

    The warm visits are printed because an arm whose warming was silently
    failing would look exactly like the warm-up not working, and that is the
    conclusion this run is most likely to reach on its own.
    """
    if not row.get("query"):
        # Same shape as a probe row below: a token in the column, the message on
        # its own line. Putting the message in the column meant it was cut to
        # fit, and what fitted was the part every message shares.
        error = row.get("error")
        print(f"      warm       {(row.get('url') or '')[:44]:<46}"
              f"{row.get('elapsed_ms', 0):>6} ms  {'error' if error else 'ok'}")
        if error:
            print(f"        -> {one_line(error, 160)}")
        return
    ready = {True: "ready", False: "no-markup", None: "-"}[row.get("ready")]
    print(f"      {row['phase']:<6}#{row['position'] + 1:<4}"
          f"{row['query'][:26]:<28}{row['status'] or '-'!s:<5}"
          f"{row['verdict']:<8}{ready:<10}{row['elapsed_ms']:>6} ms")
    if row.get("error"):
        print(f"        -> {one_line(row['error'], 160)}")


def smallest_separable(n_a: int, n_b: int, alpha: float = 0.05) -> str:
    """How large a difference this run could have detected at all.

    A flat ladder is only evidence of a flat ladder when the run could have
    seen a bump. `probehold_20260826T152748Z` printed four cells reading 3, 4,
    3 and 3 served and looked like a clean negative; at 11 against 9 judged
    attempts the smallest difference reaching p<0.05 is 0/11 against 4/9, so
    that run could not have distinguished a 40-point effect from nothing. The
    number belongs next to the table rather than in an analysis nobody runs.

    Returns a sentence, or None when either arm is too small to say anything.
    """
    if n_a < 2 or n_b < 2:
        return None
    from math import comb

    def fisher(a, b, c, d):
        total = a + b + c + d

        def point(x):
            return (comb(a + b, x) * comb(c + d, (a + c) - x)
                    / comb(total, a + c))

        observed = point(a)
        low = max(0, (a + c) - (c + d))
        high = min(a + b, a + c)
        return sum(point(x) for x in range(low, high + 1)
                   if point(x) <= observed * (1 + 1e-9))

    for k in range(n_b + 1):
        if fisher(0, n_a, k, n_b - k) < alpha:
            return (f"  smallest difference this run could have detected: "
                    f"0/{n_a} against {k}/{n_b}, about {100 * k / n_b:.0f} "
                    f"points. Anything smaller reads as flat whether it is "
                    f"there or not")
    return (f"  no difference at all is detectable at {n_a} against {n_b} "
            f"judged attempts, not even 0% against 100%. Read the table as "
            f"coverage and not as a result")


def warm_delivery(rows: list, attempts: list) -> None:
    """What the warm arms received, against what their labels claim.

    Printed because the alternative is what happened on 2026-08-26: a ladder
    whose four cells looked flat, where 30 of 144 warm visits had failed and 4
    of the 12 L2 identities had been warmed no deeper than an L1. The rung name
    is an intention. This block is the treatment.
    """
    warm_rows = [r for r in rows if r.get("verdict") == "warm_visit"]
    if not warm_rows:
        return

    print("\nWHAT THE WARM ARMS ACTUALLY RECEIVED")
    print("  a rung name is what was asked for. This is what arrived, and the "
          "two are not the same thing")
    by_level = defaultdict(list)
    for row in attempts:
        if row["phase"] == "probe" and row.get("warm_depth"):
            by_level[row["warm_level"]].append(row)
    if by_level:
        print(f"  {'rung':<6}{'planned':>9}{'identities':>12}{'short':>7}   "
              f"delivered")
        stale = False
        for level in sorted(by_level):
            bucket = by_level[level]
            planned = bucket[0].get("warm_depth")
            known = [r.get("warm_delivered") for r in bucket
                     if r.get("warm_delivered") is not None]
            if not known:
                # Rows written before 2026-08-26, when the column did not
                # exist. Saying "0 short" here would be a claim the data cannot
                # support, and it is the exact claim that made the first ladder
                # run look clean.
                stale = True
                print(f"  {level:<6}{planned:>9}{len(bucket):>12}"
                      f"{'?':>7}   not recorded in these rows")
                continue
            spread = Counter(known)
            short = sum(1 for r in bucket if r.get("warm_short"))
            shape = ", ".join(f"{k} pages x{v}"
                              for k, v in sorted(spread.items()))
            print(f"  {level:<6}{planned:>9}{len(bucket):>12}{short:>7}   "
                  f"{shape}")
        if stale:
            print("  `?` is a run taken before warm_delivered existed. Whether "
                  "those rungs were delivered has to be reconstructed from the "
                  "warm rows by hand, and in the one run where that was done "
                  "a third of them were short")
        if any(r.get("warm_short") for b in by_level.values() for r in b):
            print("  a short identity carries a rung label it did not receive. "
                  "Cut the analysis by warm_delivered, not by warm_level")

    print(f"  {'warm-up page':<46}{'delivered':>11}")
    per_host = defaultdict(lambda: [0, 0])
    for row in warm_rows:
        host = urlparse(row.get("url") or "").netloc or "?"
        per_host[host][0] += 1
        if row.get("error"):
            per_host[host][1] += 1
    for host, (seen, failed) in sorted(per_host.items(),
                                       key=lambda kv: -kv[1][1] / kv[1][0]):
        flag = "  <- drop it" if failed / seen > 0.25 else ""
        print(f"  {host:<46}{seen - failed:>6}/{seen:<4}{flag}")


def by_country(rows: list, attempts: list) -> None:
    """Where the exits were, and how each country was answered.

    Asked for on 2026-08-28: `country=any` draws from the whole pool, so a run
    is a sample of countries nobody chose, and the yield is an average over a
    mix that changes between runs. If it turns out that two countries are
    answered very differently, then a `country=any` figure is not a property of
    the provider at all - it is a property of that afternoon's draw - and every
    comparison in this repository taken at `any` inherits that.

    Two things it deliberately does not do.

    It does not rank. With 6 identities a cell the per-country counts are single
    digits, and a table sorted by rate would put a 1/1 country at the top of a
    list that included a 12/40 one. The rows print in draw order, most-drawn
    first, and every rate carries the interval that says how little it pins
    down.

    It does not pool across rungs. A country drawn mostly by one arm would
    otherwise carry that arm's warm-up into its rate, and with `--warm` the arms
    are the experiment. The column that says how many arms saw it is what makes
    that visible: a country seen by one arm is not evidence about the country.
    """
    known = [r for r in attempts
             if r["phase"] == "probe" and r.get("exit_country")
             and r["verdict"] in ("ok", "captcha", "block", "empty")]
    if not known:
        return
    print("\nWHERE THE EXITS WERE")
    coverage = sum(1 for r in attempts if r["phase"] == "probe")
    resolved = sum(1 for r in attempts
                   if r["phase"] == "probe" and r.get("exit_country"))
    print(f"  {resolved} of {coverage} probes resolved to a country, looked up "
          f"from this machine rather than through the exit")
    print(f"  {'country':<10}{'judged':>7}{'served':>7}{'rate':>9}{'':>13}"
          f"  {'arms':>5}  {'top network':<38}")
    buckets = defaultdict(list)
    for row in known:
        buckets[row["exit_country"]].append(row)
    for country in sorted(buckets, key=lambda c: -len(buckets[c])):
        bucket = buckets[country]
        good = sum(1 for r in bucket if r["verdict"] == "ok")
        arms = len({r.get("warm_level") for r in bucket})
        nets = Counter(r.get("exit_asn") or "-" for r in bucket)
        top, seen = nets.most_common(1)[0]
        print(f"  {country:<10}{len(bucket):>7}{good:>7}"
              f"{band(good, len(bucket))}  {arms:>5}  "
              f"{f'{top[:30]} {seen}/{len(bucket)}':<38}")
    if len(buckets) > 1:
        print("  Read the intervals, not the order. These are the countries "
              "the pool happened to hand out, not a chosen sample, and a "
              "country here is one afternoon's exits in it.")


def summarise(rows: list) -> None:
    """The three claims, each against the denominator that belongs to it."""
    attempts = [r for r in rows if r.get("query")]
    if not attempts:
        print("\nno attempt completed, so there is nothing to read")
        return

    print("\n" + "=" * 100)
    # `judged` and not `probes` is the denominator. An `error` is our client
    # failing to complete, so the target never judged anything and the row is
    # not evidence either way: counting it as a refusal reports a transport
    # problem as a target refusing us. `probehold_20260826T152748Z` is the
    # example - 10 of its 86 attempts were errors and they were not spread
    # evenly across the rungs, so the old column made the arm with the worst
    # local transport look like the arm the target liked least.
    print(f"{'cell':<48}{'probes':>8}{'judged':>8}{'served':>8}{'rate':>7}"
          f"{'held':>6}{'ok|held':>9}")
    print("-" * 100)
    by_cell = defaultdict(list)
    for row in attempts:
        by_cell[row["cell"]].append(row)
    judged_counts = []
    for key in sorted(by_cell):
        cell_rows = by_cell[key]
        probes = [r for r in cell_rows if r["phase"] == "probe"]
        judged = [r for r in probes
                  if r["verdict"] in ("ok", "captcha", "block", "empty")]
        good = sum(1 for r in judged if r["verdict"] == "ok")
        held = [r for r in cell_rows if r["phase"] == "hold"]
        held_ok = sum(1 for r in held if r["verdict"] == "ok")
        rate = f"{held_ok}/{len(held)}" if held else "-"
        served = f"{100 * good / len(judged):.0f}%" if judged else "-"
        judged_counts.append(len(judged))
        print(f"{key[:48]:<48}{len(probes):>8}{len(judged):>8}{good:>8}"
              f"{served:>7}{len(held):>6}{rate:>9}")
    if len(judged_counts) >= 2:
        # The two smallest arms, not the largest against the smallest. Every
        # pairwise comparison in this table involves at least one thin arm, so
        # the bound that matters is the one the thinnest pair sets.
        weakest = sorted(judged_counts)[:2]
        note = smallest_separable(weakest[1], weakest[0])
        if note:
            print(note)

    warm_delivery(rows, attempts)

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

    by_country(rows, attempts)

    print("\nwhy the non-ok verdicts happened")
    print("-" * 100)
    reasons = Counter((r["verdict"], r.get("verdict_reason"))
                      for r in attempts if r["verdict"] != "ok")
    for (verdict, reason), count in reasons.most_common():
        print(f"  {count:>4}  {verdict:<9}{(reason or '')[:70]}")

    # Separated from the verdict table above on purpose. A transport failure is
    # not a verdict about the target, and printing the two together is what let
    # a broken tunnel read as Google walling a rung.
    lost = [r for r in rows if r.get("transport_failure")]
    if lost:
        print(f"\n{len(lost)} of {len(rows)} rows failed below the "
              f"application layer, so the target judged nothing on them")
        for (marker, _), count in Counter(
                (one_line(r.get("error") or "", 62), None)
                for r in lost).most_common(8):
            print(f"  {count:>4}  {marker}")
        by_exit = Counter(r.get("exit_label") for r in lost if r.get("exit_label"))
        if by_exit:
            worst = by_exit.most_common(5)
            print("  by exit: " + ", ".join(f"{label} x{n}"
                                            for label, n in worst))
            print("  Concentrated on a few exits means it is the exit and not "
                  "the request; spread one-per-exit means it is not.")

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

    available = warm_levels()
    spellings = {text: level for text, level in WARM_LEVELS.items()
                 if level in available}
    levels = parse_flags(args.warm, "--warm", spellings, parser)
    levels.sort(key=list(LADDER).index)
    entries = parse_flags(args.entry, "--entry", {"home": "home", "url": "url"},
                          parser)
    geos = parse_flags(args.geo, "--geo", {"off": "off", "align": "align"},
                       parser)
    hands = parse_flags(args.humanize, "--humanize",
                        {"off": "off", "engine": "engine",
                         "trueman": "trueman"}, parser)
    interacts = parse_flags(args.warm_interact, "--warm-interact",
                            dict({"off": "off"},
                                 **{name: name for name in warm.SETS}), parser)
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

    cells = [Cell(engine, target, country, arm, level, entry, geo, hand,
                  interact, name_hand=len(hands) > 1,
                  name_interact=len(interacts) > 1)
             for engine in engine_names
             for target in target_names
             for country in (["direct"] if args.direct else countries)
             for arm in arms
             for level in levels
             for entry in entries
             for geo in geos
             for hand in hands
             for interact in interacts]

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

    # Checked before the plan is printed, and that order is the fix rather than
    # a preference. `describe` reads a target's warm-up surface to print it, so
    # a target with no `warm_ladder` and no `home_url` - every marketplace added
    # 2026-09-04 is one - killed it with `AttributeError: 'ShopeeSearch' object
    # has no attribute 'home_url'` **while `check_warm` was already holding the
    # sentence that says so in words**. The check could not speak because the
    # printer ran first and crashed on the same fact.
    #
    # Found 2026-09-04 by dry-running `--targets google_serp,shopee_search`
    # before an overnight run. The cost of leaving it is not the traceback: it
    # is that a typo in `--targets` at three in the morning reads as a broken
    # script rather than as an unwarmable target, and the night is gone.
    preflight(parser, args, cells)
    describe(args, cells, per_identity)
    if args.dry_run:
        print("\ndry run: nothing was sent")
        return 0

    rng = random.Random(args.seed)
    # A separate stream from `rng`, and separate on purpose: `rng` decides which
    # queries a session asks and in what order, so sharing it would make the
    # queries change when the cursor seed changed, and the humanized arm would
    # then differ from its control in two things at once. `--humanize-seed`
    # falls back to `--seed` so a run that says nothing still reproduces.
    hand_seed = args.seed if args.humanize_seed is None else args.humanize_seed
    local_ip = None if args.direct else gateway.identify_direct().get("exit_ip")
    # Named for the whole run and not for a cell, because a cell-scoped name
    # here is what let one iteration overwrite it - see the note beside
    # `options` below. Anything per-cell in this loop is `cell_*`.
    run_has_aligned_cell = any(c.geo == "align" for c in cells)

    sink = JsonlSink("probehold")
    store = ArtifactStore("probehold", run_id=sink.run_id,
                          sample_ok=args.sample_ok,
                          enabled=not args.no_bodies)
    registry = gateway.ExitRegistry()
    breakers = {c.key: CircuitBreaker(c.key, limit=args.breaker,
                                      base_pause=args.pause) for c in cells}
    watch = TransportWatch()
    inert = InteractWatch()
    rows = []
    print(f"\nraw rows -> {sink.path}\n")

    # Process-wide and therefore the runner's job rather than `PointerHand`'s:
    # without it `time.sleep` on Windows quantises to the 15.625 ms scheduler
    # tick, which is longer than most of the intervals the mover asks for.
    # Measured 2026-09-03 in the lab: 24% of a driven pointer's intervals landed
    # exactly on the tick against 4% of the human trace's, and that ratio is one
    # of the 19 metrics `lab/probes/trace_compare.py` scores. Raised here rather
    # than around each session because entering and leaving it per session is
    # 300-odd system-wide timer-resolution changes in a run. An `ExitStack`
    # rather than a `with` around the loop, because the loop is 300 lines and
    # reindenting it to add a timer would bury the change.
    # Raised for the whole run when any cell asks for it, not per cell, so on an
    # interleaved `off,trueman` run the control arm sits inside the fine timer
    # too. That is the wanted behaviour rather than a compromise: it holds the
    # timer fixed across the comparison, leaving the pointer as the only thing
    # that differs. It costs the `off` arm nothing measurable either way - what
    # the tick quantises is `time.sleep`, and the only sleeps on an `off` arm
    # are the gap and dwell ones, which are seconds.
    timer = ExitStack()
    if "trueman" in hands:
        timer.enter_context(fine_timer())

    try:
        # Round-robin at identity granularity, so every cell is spread over the
        # whole window. A cell run start to finish would be measuring its own
        # hour: Google's yield moved 17 points between two afternoons of the
        # same experiment.
        #
        # A queue and not two nested loops, because an identity whose probe
        # never reached the target has to be asked again with a fresh exit, and
        # a `for` cannot put one back. The redraw itself is at the bottom of
        # the body; `nmbench.breaker.is_transport_failure` decides what
        # qualifies. The queue is built in the same round-major, cell-minor
        # order the two loops produced, so a run with no redraws in it visits
        # identities in exactly the order every run on disk did.
        work = deque((index, cell, 0) for index in range(args.identities)
                     for cell in cells)
        # A redraw costs a fresh address and produces no measurement, so the
        # run is allowed to spend at most as many on transport as it planned to
        # spend on the experiment. Past that the gateway is the finding and
        # continuing only burns the pool - which is `TransportWatch`'s job, but
        # that watchdog needs 17 of 20 attempts to error and redrawing is
        # precisely what stops errors from accumulating in its window.
        redraw_budget = args.identities * len(cells)
        redraws_spent = 0
        while work:
            round_index, cell, redraw = work.popleft()
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
            elif run_has_aligned_cell:
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
            located = gateway.locate(identity.get("exit_ip"))

            cell_aligns = cell.geo == "align"
            # Whether this engine needs to be handed the zone at all. The
            # boolean engines resolve the exit against their own bundled
            # database, so our lookup failing says nothing about whether
            # they can align - see the skip below.
            needs_zone = not engines.REGISTRY[cell.engine].supports_geoip

            timezone_id = identity.get("timezone")
            if cell_aligns and needs_zone and not timezone_id:
                # The exit is live enough to have been asked and the answer
                # did not name a zone. Running anyway would write rows
                # claiming an alignment that never happened, which is worse
                # than the unaligned arm and unfixable afterwards. The
                # identity is dropped and said so; the breaker is not fed,
                # because this is our lookup falling short rather than the
                # target refusing anything.
                #
                # `needs_zone` was not in this condition until 2026-08-26,
                # so camoufox and cloak identities were dropped whenever our
                # echo did not name a zone - for engines that never receive
                # the zone and would have aligned from their own database.
                # Measured on the run that found it: 3 of 12 camoufox
                # identities discarded on `google_serp`, 1 of 2 on
                # `amazon_search`.
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

            # Two names for one axis, because the engines source the data
            # two ways: Camoufox and cloak look the exit up in a bundled
            # database and take a boolean, the Chromium-family engines have
            # to be handed the zone. `benchmark.py` has passed both since
            # 2026-08-14; this runner passed only the zone and pinned the
            # boolean to False until 2026-08-26, which is the same failure
            # mirrored - `--geo align` was accepted for camoufox and cloak,
            # since both declare the capability, and both then ran unaligned
            # while their rows would have said `geo=align`.
            #
            # No row on disk is wrong. Every `geo=align` row in `data/runs/`
            # is patchright or zendriver, 86 of them, all on 2026-08-14, and
            # those two take the zone - checked row by row rather than
            # assumed. The bug was latent and would have fired on the first
            # camoufox align arm anyone ran, which is exactly what was about
            # to happen.
            #
            # **The first fix of it, the same day, introduced a worse bug
            # and this is the note on what that looked like from the
            # inside.** The per-cell flag was written as `aligning`, which
            # is the name the run-level flag on line 805 already had, so
            # every iteration overwrote it. That flag decides at the top of
            # this loop whether the identity is sourced from `gateway.echo`
            # or from `gateway.identify`, and the comment there says in as
            # many words that every cell must ask the echo service so the
            # extra request through the exit does not land in one arm only.
            # After the clobber it landed in one arm only: a cell following
            # an unaligned one asked `identify`, which prefers the CONNECT
            # header, which carries an address and no zone - so aligned
            # identities were then dropped by the skip above for want of a
            # timezone the run had stopped asking for.
            #
            # It surfaced as roughly a quarter of aligned camoufox
            # identities being skipped, and the first reading of that - a
            # geoip database that does not know some exits - fitted the
            # symptom and was wrong. It fitted because `identify` falls
            # back to the echo service whenever the CONNECT header is
            # absent, which is about half the time, so the failure was
            # intermittent in exactly the way a patchy database would be.
            # A cause that fits is not a cause that was checked, and the
            # thing that separated them was reading the variable's other
            # assignment rather than reasoning about the symptom.
            # The redraw suffix is part of the name and not a separate column
            # to be joined on, because every analysis in this repository groups
            # by `identity` alone. Two draws of the same round and cell are two
            # different exits with two different warm-ups, and sharing a name
            # would silently merge them into one identity that visited twice as
            # many pages.
            identity_name = (f"{cell.key}#{round_index}"
                             + (f"r{redraw}" if redraw else ""))

            options = {"direct": args.direct, "params": params,
                       "preset": args.preset,
                       # `cell.hand` and not `args.humanize`: the cursor is an
                       # interleaved axis, so the flag holds every arm the run
                       # asked for and only the cell knows which one this
                       # identity is.
                       "headless": args.headless, "humanize": cell.hand,
                       "store": store, "geoip": cell_aligns,
                       "timezone_id": timezone_id if cell_aligns else None,
                       # Seeded from the identity name rather than drawn from
                       # the run's `rng`, so a session's walk is reproducible
                       # from the string in its own rows. Drawing sequentially
                       # would make every walk depend on how many redraws
                       # happened before it, and a redraw is decided by the
                       # transport - so re-running the same command with the
                       # same seed would produce different cursors whenever the
                       # network behaved differently, which is the one thing a
                       # seed exists to rule out.
                       #
                       # Unseeded means unseeded. This read
                       # `random.Random(f"{hand_seed}:{identity_name}")`
                       # unconditionally for about ten minutes on 2026-09-03,
                       # which with `hand_seed` None produced the perfectly
                       # deterministic string `"None:patchright/..."` - so a
                       # run that had asked for no seed would have drawn the
                       # same cursor for the same identity name every time.
                       # `--seed`'s own help says why that is wrong: unset, the
                       # run must record nothing a target could key on, and a
                       # walk that is a pure function of a cell key is exactly
                       # such a thing. It looked right because the seeded and
                       # the unseeded branch are the same expression, and
                       # `None` is a legal seed rather than an error.
                       "hand_rng": (None if cell.hand != "trueman"
                                    else random.Random()
                                    if hand_seed is None
                                    else random.Random(
                                        f"{hand_seed}:{identity_name}"))}

            wants_relay = (not args.direct
                           and engines.REGISTRY[cell.engine].needs_relay)
            extra_columns = {
                "identity": identity_name,
                # Which draw this is, 0 for the planned one. Lets a run be
                # re-read with the redrawn identities excluded, which is the
                # conservative reading: a redraw replaces an exit that failed
                # below the application layer, and if that classification is
                # ever shown to be wrong the run can still be analysed as
                # though no redraw had happened.
                "redraw": redraw,
                # Kept beside `warm_level` rather than replaced by it, so
                # that every analysis written against the rows already on
                # disk keeps working and a three-rung run still answers the
                # two-arm question the earlier ones asked.
                "warm": bool(cell.warm),
                # Which rung, by name. `warm: true` was never one treatment
                # and now openly is not: without this, two rungs are
                # distinguishable only by a depth, and two rungs of equal
                # depth would not be distinguishable at all.
                "warm_level": cell.level,
                # Pages the warm arm visited, on every row of every arm - 0
                # in the cold arm because that is a depth and not a missing
                # value.
                "warm_depth": len(warm_sequence(target, cell.level, args)),
                # On every row of every arm, whether or not the key names it.
                # That is what makes it safe for a single-arm run to keep the
                # key the inert runs already use: the arm is still readable off
                # the row. Same contract as `humanize_mode`.
                "interact": cell.interact,
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
                # Where the exit is, and on whose network, resolved from this
                # machine rather than through the session - see
                # `gateway.locate`. New columns rather than a backfill of
                # `exit_org` and `exit_timezone` beside them, which is
                # deliberate and is the point of the exercise.
                #
                # Those two are written from `identify`, which returns them
                # only when it fell back to the echo service, so they are on
                # roughly half the identities and *which* half is not random.
                # Pooling a complete source into the same column would make one
                # column mean "echo said so" on the rows already on disk and
                # "we looked it up" on the new ones, and every table drawn
                # across a run spanning 2026-08-28 would quietly mix them. The
                # existing columns keep their meaning and their gaps; these two
                # are the ones a per-country or per-network table can be built
                # on, because they were asked for unconditionally.
                #
                # It costs no request through the exit, so it is not warming
                # and it is identical in every arm. It costs this machine one
                # HTTPS request per distinct address, about 270 ms measured
                # 2026-08-28, before the browser starts.
                "exit_country": located.get("country"),
                "exit_asn": located.get("asn"),
            }

            print(f"  {cell.key}  #{round_index}"
                  f"{f' redraw {redraw}' if redraw else ''}  exit "
                  f"{identity.get('exit_ip') or '-'} "
                  f"{located.get('country') or '--'} "
                  f"{(located.get('asn') or identity.get('org') or '')[:36]}")

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
                            # Session-level, so it survives `meter["at"]` being
                            # advanced below - `since` passes the handshake
                            # through undifferenced for exactly that reason.
                            # Without it this probe's rows would carry the
                            # fingerprint on the first attempt of a session and
                            # None on every later one.
                            if spent["ja4"]:
                                row["tls_ja4"] = spent["ja4"][0]
                            for seen_exit in spent["exits"]:
                                row["session_exit_prefix"] = (
                                    registry.record(seen_exit)
                                    .get("exit_prefix"))
                            meter["at"] = hop.snapshot()
                        # On every row, warm visits included, so the column can
                        # be read the same way in both phases. False and not
                        # absent on a row that succeeded: absent would be
                        # indistinguishable from a row written before this
                        # column existed, and those are on disk.
                        row["transport_failure"] = is_transport_failure(
                            row.get("error"))
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
                        # Warm rows only, and gated on the phase rather than on
                        # the column being present: a probe row that happened
                        # to carry a zero plan would otherwise be counted as a
                        # page where nothing landed.
                        if row.get("phase") == "warm":
                            inert.record(row.get("interact_planned") or 0,
                                         row.get("interact_done") or 0,
                                         cell.interact)
                        if inert.tripped:
                            sink.write({"cell": cell.key,
                                        "target": cell.target,
                                        "verdict": "run_stopped",
                                        "verdict_reason": inert.reason})
                            raise ArmInert(inert.reason)

                    verdict, probe_error = run_identity(
                        active, page, counter, cell, target, queries,
                        rng=rng, args=args, on_row=on_row)

                    # A probe that never reached the target is not a refusal
                    # and must not be treated as one. Two things follow, and
                    # they are separate.
                    #
                    # It does not feed the breaker. `CircuitBreaker.record`
                    # counts anything that is not `ok`, so before this every
                    # broken tunnel was a tick toward "the target is walling
                    # this cell". In `probehold_20260827T201123Z` that ended
                    # three of the four rungs: transport errors were 18-33% of
                    # the warm arms' probes and `--breaker 12` stopped the
                    # cells that carried them, so the rungs did not stop
                    # because Google refused them.
                    #
                    # And the identity is asked again on a fresh exit. The
                    # attempt is otherwise in the denominator as a failure of a
                    # rung, which is the same error one level up.
                    #
                    # **Only the probe redraws, never a warm visit.** A warm
                    # failure is retried in place and the shortfall recorded,
                    # and it must stay that way: redrawing on a warm failure
                    # would pre-screen exits in the warm arms only - a bad exit
                    # would be found during warming and replaced, while the
                    # cold arm meets its first bad exit at the probe and keeps
                    # it. That hands the warm arms a cleaner pool than the
                    # control and the ladder would be measuring the screening.
                    if is_transport_failure(probe_error):
                        if (redraw < args.redraws
                                and redraws_spent < redraw_budget):
                            redraws_spent += 1
                            # To the front, so the replacement runs in the same
                            # minutes as the draw it replaces. At the back it
                            # would land after every planned identity, in a
                            # different hour, and hour is the confound this
                            # loop is interleaved to defeat in the first place.
                            work.appendleft((round_index, cell, redraw + 1))
                            note = ("the probe never reached the target, so a "
                                    "fresh exit is drawn for this identity "
                                    "rather than counting it against the cell")
                        else:
                            note = ("the probe never reached the target and "
                                    "the redraw budget is spent, so this "
                                    "identity is abandoned unmeasured")
                        print(f"    {cell.key}: {note}")
                        sink.write({"cell": cell.key, "target": cell.target,
                                    "verdict": "identity_redrawn",
                                    "verdict_reason": note,
                                    "identity": extra_columns["identity"],
                                    "error": probe_error,
                                    "exit_prefix": identity.get("exit_prefix"),
                                    "exit_label": identity.get("exit_label"),
                                    "exit_org": identity.get("org"),
                                    "exit_country": located.get("country"),
                                    "exit_asn": located.get("asn")})
                    else:
                        # The breaker is fed the probe only. A held query that
                        # fails is the exit burning out, which is the thing
                        # being measured and not a reason to stop asking; a
                        # probe that fails has spent a fresh address for
                        # nothing, and that is the cost worth limiting.
                        breaker.record(verdict)
                        if breaker.tripped:
                            print(f"    {cell.key}: stopped, "
                                  f"{breaker.reason}, each of them a fresh "
                                  f"exit spent on a probe that was refused")
                            sink.write({"cell": cell.key,
                                        "target": cell.target,
                                        "verdict": "cell_stopped",
                                        "verdict_reason": breaker.reason})
            except (TransportLost, ArmInert):
                # Not this identity. Both end the run and both must reach the
                # handlers below rather than the catch-all, which would record
                # them as a session that failed and carry on spending exits.
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
    except ArmInert as exc:
        print(f"\nrun stopped: {exc}")
        print("Nothing is wrong with the transport. The rows already written "
              "are usable as an inert-warm-up run; what is not available is "
              "the interaction arm, because it did not happen.")
    except TransportLost as exc:
        print(f"\nrun stopped: {exc}")
        print("Check the transport before restarting: "
              "python scripts/probes/gateway_health.py")
    except KeyboardInterrupt:
        print("\ninterrupted. The answered queries are recorded.")
    finally:
        timer.close()

    summarise(rows)
    print(f"raw rows: {sink.path}")
    print(store.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
