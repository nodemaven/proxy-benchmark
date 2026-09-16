"""Which of Shopee, Lazada and Google Maps should become the third target.

Sends requests. This answers a selection question, not a rate question: the
harness measures a proxy pool through `P(served)`, and a third target is only
worth its traffic if it discriminates where the two we have do not.

**The two we have bracket the range and leave a hole in the middle.** Google runs
1-22% depending on the window and Amazon 63-96%, both measured and both in
`NOTEBOOK.md`. A target that everything passes and a target that refuses
everything are equally uninformative; the useful third one sits between them
*and* is not driven by the same thing as either. That is the criterion this run
scores against, and it is worth stating because "what scrapes best" read
literally would pick the easiest site on the list, which is the one worth least.

**Google Maps is the same reputation surface as Google.** Same origin family,
same `/sorry/` diversion, same address history. Whatever it scores will
correlate with a column we already have, so it can only fail the independence
half of the criterion - which is a reason to measure it here rather than an
excuse to skip it, because the correlation is the finding and nobody has counted
it. `b2b_recon.py` left it out on 2026-08-28 on the different and still-valid
ground that it is an application rather than a document; the `/maps/search/` URL
below is a document with a result feed in it, which is the narrow shape that
makes it measurable at all.

**Shopee and Lazada have no global storefront and that is the whole difficulty.**
Both are per-country - `shopee.sg`, `shopee.co.id`, `lazada.com.my` and so on -
so "the target" is a country storefront, and reaching one from the wrong country
is refused for a reason that has nothing to do with bot defences. The VPS this
runs from egresses in Brazil (`201.51.6.239`, measured 2026-09-01), so **the
direct arm on a Southeast Asian storefront confounds geo with defence and cannot
be read on its own.** It is still run, as the control that makes the aligned arm
mean something: direct isolates the browser, a country-aligned exit isolates the
address, and the pair brackets what the storefront is actually objecting to.
Singapore is the default storefront because its pages are in English, and every
marker rule in `nmbench/targets.py` is an English string.

Order of operations, cheapest first, because the second step spends pool
reputation on a vertical nothing here can yet score:

    python -m nmbench gateway-health --provider nodemaven   # does sg exist
    python scripts/probes/marketplace_recon.py --arms http,chromium,patchright
    python scripts/probes/marketplace_recon.py --geo sg --sites shopee,lazada

A bad country answers 406 on the CONNECT, so the first command settles whether
an aligned arm is possible at all for a handful of handshakes and no target
traffic. Run it before the second.

No verdicts. Every row is `recon` and every body is kept, for the reason
CONTRIBUTING gives: marker rules are read off real archived bodies, never off a
vendor's description of its own defences, and rule order is load-bearing. The
output of this run is an archive to read and a table to argue from, not a number
to publish.
"""
import argparse
import sys
import time
from collections import Counter
from contextlib import ExitStack, contextmanager
from pathlib import Path
from urllib.parse import quote_plus, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
# Imported rather than copied. `b2b_recon` already solved the parts that are not
# specific to this vertical - the settle that stops a self-clearing interstitial
# being archived as the answer, the decoding check that read undecoded brotli as
# a block for a whole run on 2026-08-10, and the structural census. A second
# copy of those would drift from the first and the drift would look like a
# difference between verticals.
from b2b_recon import LADDER, browser_attempt, census, http_attempt

from nmbench import artifacts, engines, gateway
from nmbench.relay import Relay
from nmbench.sink import JsonlSink
from nmbench.targets import TARGETS, Judgement

SENDS_REQUESTS = True

# The table's columns, declared once because the header, the rows, the rules
# and the tests are four places that had already drifted apart twice.
#
# `statuses` was 18 wide and `{'200': 9, 'None': 1}` is 20, so one failed
# attempt in ten pushed the dict into the `n` column and the table printed
# `10{'200': 9,` as a single token. The tests read fields with `line.split()`
# and a negative index, which is correct only while every value is one token -
# and that same dict is three. So the overflow did not just look wrong, it
# would have moved every assertion one place while all of them still passed.
COLUMNS = (
    ("site", 13, "<"), ("arm", 11, "<"), ("doc", 7, "<"),
    ("entry", 6, "<"), ("n", 3, ">"), ("statuses", 22, ">"),
    ("median size", 13, ">"), ("links", 6, ">"),
    ("moved", 6, ">"), ("err", 5, ">"),
)
# The summary's rules and its table are one width, so a column added to one is
# added to the other. This was three separate literal 78s, then an 82, and is
# now derived - adding a column moves the rules without anyone remembering to.
WIDTH = sum(width for _, width, _ in COLUMNS)


def table_row(values) -> str:
    """One line of the table, laid out by `COLUMNS`.

    `strict=True` because a caller passing the wrong number of values is the
    failure this function exists to stop, and the default `zip` answers it by
    dropping the extras and printing a shorter row - which reads as a column
    that happened to be empty.
    """
    return "".join(f"{value!s:{align}{width}}"
                   for (_, width, align), value
                   in zip(COLUMNS, values, strict=True))


def table_fields(line: str) -> dict:
    """A printed row read back into its columns, sliced by the declared widths.

    Exported for the tests, and it exists so they stop splitting on whitespace.
    A value with a space in it - the statuses dict has two - makes `split()`
    return a different number of fields per row, so an index into it means a
    different column depending on the data. That is a test which passes and
    asserts on the wrong number, which is worse than one that fails.
    """
    out, at = {}, 0
    for name, width, _ in COLUMNS:
        out[name] = line[at:at + width].strip()
        at += width
    return out

# Product searches, not brand names. A marketplace answers a brand with a
# curated store page and a product with a result grid, and the grid is the shape
# a scraper is after. Fixed and in the repository so two runs send the same
# strings in the same order; literals rather than `data/queries/` because those
# files are inputs to runs whose numbers get published and nothing here produces
# a number.
PRODUCTS = (
    "wireless earbuds", "phone case", "laptop stand", "usb c cable",
    "air fryer", "running shoes", "coffee grinder", "backpack",
    "mechanical keyboard", "water bottle",
)
# Maps takes a place query, and sending it a product would measure how it
# answers a search with no places in it. That is a real page and not this one.
PLACES = (
    "coffee shop in Singapore", "dentist in Austin Texas",
    "plumber in Manchester", "hardware store in Warsaw",
    "law firm in Toronto", "gym in Berlin",
    "bakery in Lisbon", "car repair in Dublin",
    "pharmacy in Rotterdam", "bookshop in Edinburgh",
)


class Site:
    """A URL builder with no verdict rules, deliberately.

    `judge` returns a verdict outside `targets.VERDICTS`, copying `b2b_recon`:
    any code that later tries to count these rows as passes or refusals produces
    a visibly wrong category instead of a plausible number.
    """

    # No selector. We have never seen these pages, and inventing one makes every
    # attempt wait out its timeout and then record False for a page that was
    # fine. `--settle` does the waiting, unconditionally and identically for
    # every arm, so it cannot favour one.
    ready_selector = None
    # Conservative until measured: this refuses the script-blocking presets, so
    # a recon run cannot accidentally measure our own resource blocking rather
    # than the site's defences.
    needs_script = True

    def __init__(self, name, template, words, country, note, home,
                 warm_target=None):
        self.name = name
        self.template = template
        self.words = words
        # The key in `nmbench.targets.TARGETS` whose `warm_ladder` supplies
        # this site's warm-up URLs, or None if no rung has been declared for
        # it. Naming the target rather than copying the URLs keeps one
        # definition of a rung: a second copy here would drift from the
        # ladder's, and the drift would look like a difference between two
        # runs of the same rung.
        self.warm_target = warm_target
        # The country whose storefront this is, and the exit `--geo` will ask
        # for. None means the site is not partitioned and any exit is as
        # aligned as any other.
        self.country = country
        self.note = note
        # The site's own front page, for `--entry home`. Nothing about these is
        # measured: each is the origin of the search URL beside it, which is
        # structural rather than a guess at a page that exists. Whether the
        # front page serves at all through the pool is one of the two things
        # `--entry home` is for.
        self.home = home

    def url(self, query: str) -> str:
        return self.template.format(q=quote_plus(query))

    def judge(self, url: str, title: str, html: str) -> Judgement:
        return Judgement("recon", "no verdict rules exist for this target yet, "
                                  "the archived body is the output")


class SidePage:
    """A page visited in the same session as the search, a target in its own
    right.

    A wrapper rather than a branch inside `browser_attempt`, so each such page
    gets its own row, its own artifact and its own line in the table. The
    alternative - fetching it inside the search attempt and throwing the body
    away - would spend the request and keep nothing, and the body is often the
    point: a `search_box` selector taken out of an archived front page is
    derived the way Lazada's marker was, while one read off the live site is
    the `norotate` mistake with a CSS selector in it.

    The row is what makes a missing visit visible. A warm-up page that was
    itself refused leaves a session that is labelled warmed and is not, which
    is the failure `probe_and_hold` names in its own docstring - identities
    "labelled L2 and L3 for a warm-up they had not received".
    """

    ready_selector = None
    needs_script = True

    def __init__(self, site, url, kind, reason):
        self.site = site
        self.kind = kind
        self.name = f"{site.name}_{kind}"
        self.country = site.country
        self._url = url
        self._reason = reason

    def url(self, query: str) -> str:
        return self._url

    def judge(self, url: str, title: str, html: str) -> Judgement:
        return Judgement("recon", self._reason)


class HomePage(SidePage):
    """The site's own front page, visited immediately before the search.

    **What this does not carry is a referrer.** `page.goto` sends no `Referer`
    whether or not the same page fetched the front page first, so this arm
    varies the cookie jar and the session's history with the origin, and holds
    "arrived from nowhere" fixed. If the referrer is what a storefront reads,
    this arm cannot see it and a null result here does not clear the idea.

    On Shopee that null result arrived, and it is not the whole story: on
    `marketplace_recon_20260905T113703Z` the wall caught the front page too,
    2 of 3, so this arm's own first step is guarded by the thing it was meant
    to get around.
    """

    def __init__(self, site):
        super().__init__(site, site.home, "home",
                         "front page visited to warm the session, no verdict "
                         "rules exist for it either")


class WarmPage(SidePage):
    """One URL of a warm-up rung, visited once at the start of the session.

    Once per session and not once per query, which is what a rung means
    everywhere else in this repository: the identity is warmed, then it makes
    its requests. Per query would warm the session three times over and stop
    being the same axis `probe_and_hold` measures.

    The URLs are not written here. They are read from the target's own
    `warm_ladder` in `nmbench.targets`, so the rung has one definition and a
    run of this probe cannot drift from a run of the ladder.
    """

    def __init__(self, site, url, rung):
        super().__init__(site, url, "warm",
                         f"warm-up page for rung {rung}, visited before the "
                         f"queries; no verdict rules exist for it either")


# Every `note` said "unmeasured" until 2026-09-05, and the comment here promised
# that none of them had been fetched from this machine - it sits behind a Happ
# VPN gateway, so no reading taken here describes the network the harness runs
# on. Two of the three are now measurements and the promise has to go with them.
#
# What the gateway does and does not spoil is the part worth keeping. It can
# suppress or inflate a byte count and it can add latency, so nothing here
# quotes either. It cannot rewrite a response body or a redirect chain, which is
# what these notes are about, so a body shape taken from this workstation is
# usable and a millisecond taken beside it is not. Read the qualifier off the
# quantity, not off the host.
SITES = {s.name: s for s in (
    Site("shopee",
         "https://shopee.sg/search?keyword={q}",
         PRODUCTS, "sg",
         # `once in six` was this note until 2026-09-05 and it came off a
         # 6-attempt run. Four runs of that day - 113703Z, 154738Z, 164652Z,
         # 170518Z, 51 completed attempts - put it at 3 served of 27 front
         # pages and 0 served of 24 searches, all through an SG exit in one
         # afternoon. The front-page figure is inside what 1 in 6 predicts;
         # the search figure is the finding, and the old note could not have
         # carried it because it did not separate the two documents.
         "nginx 403 direct. Through an SG exit, 51 attempts on 2026-09-05: "
         "the front page served 3 of 27, the search 0 of 24, and everything "
         "else answered 200 with the storefront's title and 2 anchors, most "
         "of it redirected to /verify/traffic/error",
         "https://shopee.sg/",
         warm_target="shopee_search"),
    Site("lazada",
         "https://www.lazada.sg/catalog/?q={q}",
         PRODUCTS, "sg",
         "scoreable since 2026-09-05: the catalogue grid is archived and "
         "`targets.LazadaSearch` names it",
         "https://www.lazada.sg/"),
    Site("google_maps",
         "https://www.google.com/maps/search/{q}?hl=en",
         PLACES, None,
         "unmeasured. Not partitioned, and the same reputation surface as the "
         "google_serp target already in the registry",
         "https://www.google.com/maps?hl=en"),
)}


def warm_urls(site, rung):
    """The rung's URLs for one site, read from its target's `warm_ladder`.

    Returns `None` when the site does not offer the rung, and that is not the
    same as an empty tuple: the caller refuses the run rather than sending an
    unwarmed session labelled with a rung. `AmazonSearch.warm_ladder` states
    the policy this implements - "asking for L2 or L3 on this target is
    refused rather than silently answered with L1" - and the cost of the other
    behaviour is written in `probe_and_hold`, where identities were once
    "labelled L2 and L3 for a warm-up they had not received".

    **The front page is appended here because it is appended there.** This is
    the third caller of `warm_ladder`, and `probe_and_hold.warm_sequence` gives
    the reason for the other two: the front page is added by the function
    rather than by the caller so that no two callers can disagree about what
    the rung is. A rung that meant `[help, home]` in one script and `[help]` in
    another would put two different warm-ups on disk under one label, which is
    a wrong result and not an error. `test_the_two_scripts_deliver_one_rung`
    holds them together.
    """
    if rung == "off":
        return ()
    if not site.warm_target:
        return None
    target = TARGETS.get(site.warm_target)
    ladder = dict(getattr(target, "warm_ladder", ()) or ())
    pages = ladder.get(rung)
    return None if pages is None else (*pages, target.home_url)


def plan(wanted_sites, arms, args, availability) -> None:
    """What the run will send, printed before it sends it and by `--dry-run`.

    One function and two callers on purpose: a dry run that prints a plan the
    real run does not follow is worse than no dry run, because it is trusted.
    """
    runnable = [n for n, _ in arms if not availability.get(n)]
    total = len(wanted_sites) * args.queries * len(runnable)
    print(f"sites   : {len(wanted_sites)} x {args.queries} queries x "
          f"{len(runnable)} arms that can run here = {total} attempts")
    if len(runnable) < len(arms):
        for name, _ in arms:
            if availability.get(name):
                print(f"          {name}: cannot run here, {availability[name]}")
    if args.geo:
        print(f"mode    : through the pool, exit pinned to "
              f"{args.geo}, {'headless' if args.headless else 'headful'}")
        print("          this arm spends pool traffic and pool reputation on a "
              "vertical nothing here can score yet. That is the cost of the "
              "only reading a partitioned storefront has")
    else:
        print(f"mode    : direct, no gateway, no pool traffic, "
              f"{'headless' if args.headless else 'headful'}")
        partitioned = [s for s in wanted_sites if SITES[s].country]
        if partitioned:
            print(f"          {', '.join(partitioned)} is a country storefront "
                  f"and this arm reaches it from wherever this host egresses. "
                  f"A refusal here does not separate the defence from the "
                  f"mismatch - it is the control for the --geo run, not a "
                  f"reading on its own")
    if args.relay:
        print("relay   : through nmbench.relay, so each row records the exit "
              "of the tunnel the page used, its country and ASN, and the "
              "engine's JA4")
        print("          it adds a loopback hop. Bytes and verdicts stay "
              "comparable against an unrelayed run and elapsed_ms does not - "
              "`relayed` is on every row so a reader can tell")
    elif args.geo:
        print("mode    : the exit is asked for and never recorded. `geo` and "
              "`params` on these rows are the request, not the outcome - pass "
              "--relay to record what was actually given")
    if args.entry == "home":
        # Counted off `runnable` and off the sites that declare a front page,
        # not off everything asked for. A plan that promises attempts an
        # unavailable arm will never send is the failure `--dry-run` exists to
        # prevent, and it was in the first version of this line.
        browser_arms = [n for n in runnable if n != "http"]
        with_home = [s for s in wanted_sites if SITES[s].home]
        extra = len(with_home) * args.queries * len(browser_arms)
        print(f"entry   : through each site's own front page first, one visit "
              f"per query, on {len(browser_arms)} browser arm(s) over "
              f"{len(with_home)} site(s) that declare one: {total} + {extra} = "
              f"{total + extra} attempts")
        print("          it varies the cookie jar and the session's history "
              "with the origin. It does not vary the referrer - page.goto "
              "sends none either way - so a null result here does not clear "
              "the referrer as the thing being read")
    if args.warm != "off":
        # Counted the same way as `entry` above and off the same `runnable`,
        # but per session rather than per query: a rung warms the identity once
        # and then it makes its requests. The two lines differ by a factor of
        # `--queries` and printing both wrong ways round would be an easy way
        # to read a plan as promising three times the traffic it sends.
        browser_arms = [n for n in runnable if n != "http"]
        rungs = {s: warm_urls(SITES[s], args.warm) or () for s in wanted_sites}
        visits = sum(len(u) for u in rungs.values()) * len(browser_arms)
        print(f"warm    : rung {args.warm}, once per session before any query, "
              f"on {len(browser_arms)} browser arm(s): {visits} extra visit(s)")
        for site_name, urls in rungs.items():
            print(f"          {site_name}: "
                  f"{', '.join(urls) if urls else 'no URLs at this rung'}")
        print("          the rung is the axis, and it is not --entry. That one "
              "varies which URL the query is sent to; this one varies what the "
              "session had already done before it sent anything")
    print("verdicts: none. Every row is 'recon' and every body is kept")
    if args.dry_run:
        print("\ndry run, nothing sent")


class Wire:
    """The relay for one session, and the columns it lets a row carry.

    It exists because `--geo sg` records the *ask* and nothing else. Every one
    of the four Shopee runs of 2026-09-05 wrote `geo: 'sg'` and
    `params: {'country': 'sg'}` on every row, and not one of them says where the
    request actually left from - `patchright` hands its credentials to
    Playwright, so no CONNECT reply of its passes through this process and
    `session_exit_prefix` and `tls_ja4` are None on all 51 attempts.

    A side-channel `gateway.exit_ip()` would not have fixed it and would have
    looked like it did. `open_session` passes a country and no `sid`, so the
    session is not sticky, and `gateway.open_tunnel`'s own docstring says the
    probe reports "the exit IP that the sticky session resolved to at probe
    time, which is evidence about the session, not a guarantee about the
    connection the next request will use". Adding a `sid` to make it reliable
    changes the treatment - it stops the rotation these runs were measured with.
    The relay reads the reply the page's own tunnel got, so it instruments
    without changing what is instrumented.

    **The rotation is the run's largest uncontrolled variable, and this
    docstring called it a nuisance until it was measured.** Twelve short
    sessions on 2026-09-05 evening, `--geo sg --relay`, 22 attempts: the gateway
    named a new exit 7 to 11 times *inside a single page load* (per-attempt
    `exit_new` of 7,2,5,4,9,8,1,5,8,0,8,2,5,3,10,7,4,9,7,0,11,7). The addresses
    are real Singapore residential ISPs - Singtel, Starhub, M1, MyRepublic, NTU,
    Whiz - so one Shopee page was assembled from a handful of unrelated homes,
    which is not a thing a browser does. `ERR_NETWORK_CHANGED` three times in 22
    attempts is the visible cost. What the sentence above frames as "the
    treatment we chose" was never chosen: nothing here asked for rotation, it is
    simply what a `sid`-less username gets. A sticky arm is the experiment this
    now demands, and `marketplace_recon` has no flag to pass a `sid` yet.

    What it does change is one column and it is on the row: `relayed`. The hop
    is real, so `elapsed_ms` on a relayed row is not comparable against the four
    unrelayed runs. Bytes and verdicts are.

    **The vendor-fetch block is left at its default, and the claim that this
    costs nothing here was wrong.** It read: "measured rather than assumed: the
    idle table in the tree's notes puts patchright at 0.00 MB because
    Playwright's own default `--disable-features` already carries
    `OptimizationHints`, so there is no fetch for the block to refuse". The 11
    sessions of 2026-09-05 evening refused **350 requests over 22 attempts** -
    304 on the front page at 22-43 per single page load, 46 on the search page.
    The fetch is attempted, and often.

    What the mistake looked like from the inside: the 0.00 MB is a real
    measurement and it is quoted correctly. It was taken over 60 s parked on
    `about:blank` with nothing navigated. This probe navigates. An idle browser
    has no page asking for optimization hints and a loaded storefront does, so
    the arm never bore on the claim - the same failure this tree already records
    against the `OptimizationHints` crash question, where 12 clean starts on
    Google Chrome were offered against a report about unbranded Chromium. A
    negative result is only evidence if the arm matches the claim's conditions.
    `blocked` is counted on every row, which is how this was caught. The same
    correction is written against `VENDOR_FETCH` in `relay.py`, where the claim
    also stood, and it carries the caveat this one needs too: `blocked` counts
    refused requests and not the bytes they would have carried, so nothing here
    prices the block until a paired `block_hosts=()` arm is run.
    """

    def __init__(self, hop, registry):
        self.hop = hop
        self.registry = registry

    def mark(self):
        """The counters before an attempt, or None when nothing is relayed."""
        return self.hop.snapshot() if self.hop else None

    def stamp(self, row, before) -> dict:
        """Write what the tunnel said onto the row the attempt produced.

        `exit_prefix` is the last address the gateway named at or before this
        attempt, which is not the same as the address this attempt used. The
        header is on some CONNECT replies and not others - six replies on
        2026-08-13, all 200, three different reason phrases, the header on two -
        so a row can inherit the previous attempt's address. `exit_new` is what
        separates the two: 0 means no new address was named during this attempt,
        and that covers both "the tunnel was reused" and "the gateway did not
        say". Never read an absent header as a reused exit.

        `exit_new` is a count of *changes* and not of distinct addresses -
        `relay.py` appends only when the header differs from the last one it
        stored, so an A-B-A rotation counts three. Nor is 1 the normal reading:
        measured over the 22 attempts of 2026-09-05 evening it runs 0 to 11 per
        attempt, so a page load is many exits by default and `exit_prefix` names
        the last of them. `tunnels` is written beside it to give the count a
        denominator: 7 changes over 9 tunnels and 7 over 200 are different
        findings, and a bare change count cannot tell them apart.

        The full address is never written. `data/runs/` is committed and these
        are real people's home addresses; the /24 and an ordered label keep what
        the analysis needs, and `exit_label` is what tells two exits inside one
        /24 apart.
        """
        row["relayed"] = self.hop is not None
        if self.hop is None:
            return row
        now = self.hop.snapshot()
        seen = now["exits"]
        row["exit_new"] = len(seen) - len(before["exits"])
        # The session's handshake and not the attempt's: a browser sends one
        # ClientHello and then reuses the tunnel, so differencing this would
        # fill it on the first row of a session and leave it empty on the rest,
        # which reads as an engine that stopped having a TLS fingerprint.
        row["tls_ja4"] = now["ja4"][0] if now["ja4"] else None
        row["tunnels"] = now["tunnels"] - before["tunnels"]
        row["tunnel_failures"] = (now["tunnel_failures"]
                                  - before["tunnel_failures"])
        if not seen:
            return row
        latest = seen[-1]
        row.update(self.registry.record(latest))
        # Asked about the address rather than through it, which is the whole
        # point of `gateway.locate`: a request through the exit would warm the
        # exit, and this run is measuring what an unwarmed one meets. It is
        # cached per address and latches off after three failures, so a run with
        # two exits pays two lookups.
        where = gateway.locate(latest)
        row["exit_country"] = where["country"]
        row["exit_asn"] = where["asn"]
        row["exit_timezone"] = where["timezone"]
        return row


@contextmanager
def open_session(name, site, args, store, registry):
    """A session for one arm, aligned to the storefront when asked.

    The country reaches the gateway through `params`, which is the same path
    every other run uses, so the username is built by the provider's own dialect
    rather than spelled here. A site with no country gets the proxied arm with
    no country pinned, and the plan output says which it was.

    Yields `(session, wire)`. The relay is built here rather than around the
    loop for the reason `benchmark.py` gives: the username carries the gateway
    parameters and the parameter set is the sticky session key, so two sessions
    sharing one relay would share an exit address without either row saying so.

    `params` goes to the relay and the address goes to the engine, and the
    engine builds no username at all on this path. One dict, one username; see
    `ChromiumEngine.proxy_config`.

    `registry` belongs to the run and not to the session, so `exit_2` means one
    address across the whole file. One per session would restart the numbering
    and put two different addresses under one label in two arms of the same
    run - which is the shape of mistake that reads as an exit being reused.
    """
    params = {}
    if args.geo and site.country:
        params["country"] = site.country
    options = {"direct": not args.geo, "params": params, "preset": "light",
               "headless": args.headless, "channel": args.channel,
               "store": store}
    with ExitStack() as stack:
        hop = None
        if args.relay:
            hop = stack.enter_context(Relay(params=params))
            options["relay_address"] = hop.address
        session = stack.enter_context(engines.get(name).open(**options))
        yield session, Wire(hop, registry)


def record(row, entry, arm, site_name, args, sink, rows, bodies) -> None:
    """Label one attempt, write it, and keep its body for the census.

    `entry` is written on every row and not only on the arm that varies it, so
    the default arm's rows say `url` rather than saying nothing. A column that
    is absent on the old rows and present on the new ones cannot be grouped by,
    and the whole point of the axis is the comparison across it.
    """
    html = row.pop("_html", "")
    if html:
        bodies.append(html)
    # Anchors in the body, and this is the only thing measured on 2026-09-05
    # that separated a served storefront from the wall. Across the four Shopee
    # runs of that day - `113703Z`, `154738Z`, `164652Z`, `170518Z`, 51
    # completed attempts - the three served front pages carry 451, 423 and 426
    # `<a `, and all 24 walled ones carry exactly 2. Nothing else separates
    # them: the status is 200 on both, the title is the storefront's own on
    # both, the median sizes overlap once a body is truncated, wall-clock is
    # 26.4 / 27.4 / 37.2 s served against a walled median of 27.6 s, and
    # `b2b_recon`'s own `blocked` marker count scored the served pages 71, 22
    # and 46 against a walled range of 60 to 95 - higher than 15 of the 24
    # walls, so it is not a weak signal but an inverted one.
    #
    # It is recorded as a count and never as a verdict. This probe emits
    # `recon` on purpose, and a threshold picked off one site's four runs is
    # exactly the kind of number that would be carried to a second vertical
    # and be wrong there. `None` and `0` are kept apart: an attempt with no
    # body did not arrive, an arrival with no anchors did.
    row["links"] = html.lower().count("<a ") if html else None
    row["arm"] = arm
    row["site"] = site_name
    row["geo"] = args.geo or "direct"
    row["entry"] = entry
    # Written on every row for the reason `entry` is: the comparison is across
    # the axis, and a column that is absent on the `off` rows cannot be
    # grouped by.
    row["warm"] = args.warm
    sink.write(row)
    rows.append(row)


def main() -> int:
    known_arms = [name for name, _ in LADDER]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sites", default=",".join(SITES),
                        help=f"comma separated, from {list(SITES)}")
    parser.add_argument("--arms", default=",".join(known_arms),
                        help=f"comma separated, from {known_arms}")
    parser.add_argument("--queries", type=int, default=4,
                        help="queries per site per arm. The default over three "
                             "sites and three arms is 36 attempts, which is a "
                             "first look and not a rate")
    parser.add_argument("--geo", default=None, metavar="CC",
                        help="run through the pool with the exit pinned to each "
                             "site's own country instead of direct. This is the "
                             "only arm a Southeast Asian storefront can be read "
                             "from, and it is the one that spends pool traffic. "
                             "The value is a confirmation, not the country: each "
                             "site carries its own, and it must match")
    parser.add_argument("--settle", type=float, default=8.0,
                        help="seconds to let the page finish before the body is "
                             "read. A marketplace grid and a Maps feed both "
                             "arrive after the document does")
    parser.add_argument("--entry", choices=("url", "home"), default="url",
                        help="how the search page is reached. `url` navigates "
                             "straight to it. `home` visits the site's own "
                             "front page first, in the same session, and "
                             "archives that body too - so it answers both "
                             "whether arriving cold is what a storefront "
                             "refuses, and where a search-box selector could "
                             "later be taken from. Browser arms only; the "
                             "scriptless arm records entry=url and says so")
    parser.add_argument("--warm", default="off", metavar="RUNG",
                        help="warm-up rung, `off` or a rung name the site's "
                             "target declares in its `warm_ladder`. The URLs "
                             "come from `nmbench.targets`, are visited once "
                             "at the start of the session and each gets its "
                             "own row. A rung the target does not declare is "
                             "refused, never quietly downgraded. This is a "
                             "different axis from `--entry`: that one varies "
                             "which URL is requested, this one varies what "
                             "the session did before requesting anything")
    parser.add_argument("--relay", action="store_true",
                        help="route the browser arms through nmbench.relay "
                             "instead of handing the credentials to the "
                             "browser, so every row records the exit of the "
                             "tunnel the page actually used, its country and "
                             "its ASN, plus the engine's JA4. Without this the "
                             "geo column records what was asked for and "
                             "nothing records what was given. It costs a "
                             "loopback hop, so `elapsed_ms` on these rows is "
                             "not comparable against a run made without it - "
                             "the `relayed` column says which")
    parser.add_argument("--pause", type=float, default=6.0,
                        help="seconds between attempts")
    parser.add_argument("--headless", action="store_true",
                        help="not the default: the recipe under test is headful "
                             "and a headless refusal would not tell us about "
                             "the site")
    parser.add_argument("--channel", default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan and send nothing. Added after this "
                             "script was run once to look at its plan output "
                             "and sent 8 requests and 1.5 MB instead - a "
                             "sending probe with no way to see its plan is a "
                             "trap, and `benchmark.py` has had one all along")
    args = parser.parse_args()

    wanted_sites = [s.strip() for s in args.sites.split(",") if s.strip()]
    unknown = [s for s in wanted_sites if s not in SITES]
    if unknown:
        parser.error(f"unknown sites {unknown}, known: {list(SITES)}")

    # Refused rather than truncated, for the reason a rung is. `words` is
    # `site.words[:args.queries]`, so asking for more queries than a site's
    # list holds used to send the list and print the request: `plan()` promised
    # `1 x 20 queries x 1 arms = 20 attempts` and the run made 10, which is the
    # plan-does-not-match-the-run failure `plan()`'s own docstring exists to
    # prevent. Checked per site because the lists are per site and a run over
    # two of them would otherwise be truncated on one and whole on the other.
    short = {s: len(SITES[s].words) for s in wanted_sites
             if len(SITES[s].words) < args.queries}
    if short:
        parser.error(f"--queries {args.queries} is more than these sites have "
                     f"queries: {short}. The list is not repeated and the "
                     f"extra attempts are not sent, so the number is lowered "
                     f"here rather than in the plan output")

    # Refused before anything is launched, and refused for the whole run
    # rather than per site, because a run where one site is warmed and another
    # silently is not produces two arms under one label.
    if args.warm != "off":
        missing = [s for s in wanted_sites if warm_urls(SITES[s],
                                                        args.warm) is None]
        if missing:
            offered = {s: [r for r, _ in getattr(
                TARGETS.get(SITES[s].warm_target), "warm_ladder", ()) or ()]
                for s in missing}
            parser.error(
                f"--warm {args.warm} is not declared for {missing}. Offered: "
                f"{offered}. A rung is refused rather than answered with a "
                f"shorter one: a row labelled with a warm-up it did not "
                f"receive is a wrong result, not a missing one")

    wanted_arms = [n.strip() for n in args.arms.split(",") if n.strip()]
    unknown = [n for n in wanted_arms if n not in known_arms]
    if unknown:
        parser.error(f"unknown arms {unknown}, known: {known_arms}")
    arms = [(n, why) for n, why in LADDER if n in wanted_arms]

    if args.relay:
        # There is nothing to authenticate to on the direct arm, so a relay
        # there would be a loopback hop in front of a connection that has no
        # gateway behind it. Refused rather than quietly skipped, because the
        # `relayed` column would then say False on a run the operator asked to
        # be relayed and the exit columns would be empty for a reason nobody
        # would look for.
        if not args.geo:
            parser.error(
                "--relay without --geo. The relay authenticates to the gateway "
                "and the direct arm reaches no gateway, so there would be "
                "nothing to record an exit for. Add --geo, or drop --relay")
        # Asked of the engine's own declaration rather than of its name. Every
        # `open` in the package ends in `**ignored`, so an arm that does not
        # handle a relay address would swallow it, dial the gateway itself, and
        # write `relayed=True` beside an empty exit column - a run that looks
        # instrumented and is not.
        cannot = [n for n, _ in arms if not engines.REGISTRY[n].accepts_relay]
        if cannot:
            parser.error(
                f"--relay with {cannot}, which cannot be pointed at one. Those "
                f"arms would reach the gateway directly and their rows would "
                f"claim otherwise. Run them in their own invocation without "
                f"--relay, where the missing exit column is at least honest")

    # Refused rather than silently ignored, the way a mixed capability matrix is.
    # `--geo sg` against a site whose storefront is Indonesian would pin an exit
    # that does not match the URL and record it as though it did, which is the
    # one error this run exists to avoid.
    if args.geo:
        wrong = sorted({s for s in wanted_sites
                        if SITES[s].country and SITES[s].country != args.geo})
        if wrong:
            pairs = ", ".join(f"{s} is {SITES[s].country}" for s in wrong)
            parser.error(
                f"--geo {args.geo} does not match {pairs}. The exit would be "
                f"pinned to one country and the storefront would be another, "
                f"and the row would record an aligned run that never happened. "
                f"Run those sites in their own invocation")
        # Refused rather than dropped, the way a mixed capability matrix is.
        # This fired for real: `--geo sg --sites shopee,google_maps` ran both,
        # pinning an exit for one and leaving the other on whatever the pool
        # gave, and wrote every row with `geo=sg`. The column then says the two
        # sites were reached the same way and they were not, which is the one
        # error in a comparison that cannot be seen afterwards in the rows.
        unpinned = sorted({s for s in wanted_sites if not SITES[s].country})
        if unpinned and len(unpinned) < len(wanted_sites):
            parser.error(
                f"--geo {args.geo} with {unpinned}, which is not a country "
                f"storefront and would run unpinned beside sites that are. "
                f"Every row would record geo={args.geo} and one of them would "
                f"be wrong. Run the unpartitioned sites in their own "
                f"invocation, where direct is the honest arm anyway")

    # Only the arms this invocation will use. `report_availability` asks every
    # engine in the registry, and a Chromium-family `check` starts a driver
    # process to read a path off it, so the default answer costs several
    # launches and prints their teardown to stderr. Under `--arms patchright`
    # that is a wall of asyncio tracebacks about engines the run was told not to
    # touch, and a dry run that looks like a crash is a dry run nobody reads.
    availability = {name: engines.get(name).check() for name, _ in arms}
    if args.dry_run:
        plan(wanted_sites, arms, args, availability)
        return 0

    sink = JsonlSink("marketplace_recon")
    # Every body, not a sample. The whole purpose of this run is the archive,
    # and a sampled recon means going back to the site for a page already
    # fetched.
    store = artifacts.ArtifactStore("marketplace_recon", run_id=sink.run_id,
                                    sample_ok=10_000)
    # One per run and not one per session. `exit_2` has to mean one address for
    # the whole file: a registry rebuilt per (site, arm) restarts the numbering,
    # so two different addresses would carry the same label in two arms and the
    # analysis would read it as one exit reused - the shape of mistake that
    # cannot be seen afterwards in the rows. Costs nothing when `--relay` is off,
    # because nothing calls `record`.
    registry = gateway.ExitRegistry()

    plan(wanted_sites, arms, args, availability)
    print(f"raw rows -> {sink.path}\n")

    collected = {}
    for site_name in wanted_sites:
        site = SITES[site_name]
        words = list(site.words[:args.queries])
        print(f"{site_name}  ({urlparse(site.url('x')).netloc})")
        print(f"  {site.note}")
        for name, why in arms:
            if availability.get(name):
                print(f"  {name}: SKIPPED, {availability[name]}")
                continue
            print(f"  {name}: {why}")
            # Two body lists, and the split is not tidiness. `census` counts
            # markers across the list it is given, so a front page in with the
            # search pages turns "3 of 3 carry this marker" into "3 of 6", and
            # the structural ids it lists become the ones two different page
            # types happen to share. Both halves of this run need the opposite:
            # a served-grid marker is looked for in the search bodies, and the
            # `search_box` selector this arm exists to find is looked for in
            # the front pages.
            rows, bodies, home_bodies = [], [], []
            try:
                with open_session(name, site, args, store,
                                  registry) as (session, wire):
                    browser = hasattr(session, "new_page")
                    settle_ms = int(args.settle * 1000)
                    # Once, before the queries, which is what a rung means:
                    # the identity is warmed and then makes its requests. The
                    # bodies go with the front pages rather than the search
                    # pages - a warm-up page is not the document the census's
                    # search markers are calibrated against either.
                    for warm_url in (warm_urls(site, args.warm) or ()):
                        if not browser:
                            break
                        # Every rung ends on the front page, and that visit is
                        # recorded as the front page. The two columns answer
                        # different questions: `target` says which document was
                        # fetched, `warm` says what the session had done first,
                        # and every row of the session carries the rung anyway.
                        # Filing it as a third document would split the one
                        # number this arm is for - the wall rate on `shopee.sg/`
                        # - across two lines of the table.
                        page = (HomePage(site) if warm_url == site.home
                                else WarmPage(site, warm_url, args.warm))
                        # The counters are read either side of the attempt, so
                        # `exit_new` counts what this attempt caused and not
                        # what the session has seen. `record` is left alone: it
                        # takes the row it is given, and growing its signature
                        # to carry the relay would put the instrument inside the
                        # thing that writes rows for every run, relayed or not.
                        before = wire.mark()
                        got = browser_attempt(session, page, words[0],
                                              settle_ms, store)
                        wire.stamp(got, before)
                        record(got, args.entry, name, site_name, args, sink,
                               rows, home_bodies)
                        print(f"    {f'({page.kind} {args.warm})':<24}"
                              f"{got['status'] or '-'!s:<6}"
                              f"{got['html_len']:>9}  "
                              f"{(got['title'] or '')[:36]}")
                        time.sleep(args.pause)
                    # The front page is fetched per query rather than once per
                    # session, so every search attempt has one directly behind
                    # it. Once per session would be cheaper and would measure
                    # something else: by the third query the warm-up would be
                    # two searches old, and this arm exists to ask what a
                    # storefront does with a request that just arrived from its
                    # own front page.
                    entry_home = args.entry == "home" and browser and site.home
                    for query in words:
                        if entry_home:
                            before = wire.mark()
                            home = browser_attempt(session, HomePage(site),
                                                   query, settle_ms, store)
                            wire.stamp(home, before)
                            record(home, "home", name, site_name, args, sink,
                                   rows, home_bodies)
                            print(f"    {'(front page)':<24}"
                                  f"{home['status'] or '-'!s:<6}"
                                  f"{home['html_len']:>9}  "
                                  f"{(home['title'] or '')[:36]}")
                            time.sleep(args.pause)
                        # Branch on the capability, never on the engine name. A
                        # session that can hand out a page gets the settle; one
                        # that cannot has nothing to settle.
                        before = wire.mark()
                        if browser:
                            row = browser_attempt(session, site, query,
                                                  settle_ms, store)
                        else:
                            row = http_attempt(session, site, query)
                        wire.stamp(row, before)
                        record(row, "home" if entry_home else "url", name,
                               site_name, args, sink, rows, bodies)
                        print(f"    {query[:22]:<24}"
                              f"{row['status'] or '-'!s:<6}"
                              f"{row['html_len']:>9}  {(row['title'] or '')[:36]}")
                        time.sleep(args.pause)
            except Exception as exc:
                print(f"  {name}: FAILED, {type(exc).__name__}: {exc}")
            if rows:
                collected[(site_name, name)] = {
                    "rows": rows,
                    "census": census(bodies),
                    "home": census(home_bodies) if home_bodies else None,
                }
        print()

    if not collected:
        print("nothing captured")
        return 1

    report(collected, wanted_sites, arms)
    print(f"\nraw rows: {sink.path}")
    print(f"bodies:   {store.written} saved -> {store.dir}")
    print("\nNext step is by hand and it is the one that decides: open one body "
          "per site per arm, and ask whether a served grid and a refusal are "
          "distinguishable by a structural marker. A site whose refusal cannot "
          "be told from its result page by reading the markup is not a target "
          "this harness can score, whatever its pass rate looks like.")
    return 0


def moved(row) -> bool:
    """Did the response end somewhere other than where it was sent?

    Compared against `url[:200]` because `fingerprint` truncates `final_url`
    there, so a long request URL would otherwise read as a redirect on every
    row. A row with no `final_url` at all - an error row - is not counted as
    moved: it did not arrive anywhere to be counted.
    """
    final = row.get("final_url")
    return bool(final) and final != (row.get("url") or "")[:200]


def report(collected, site_names, arms) -> None:
    arm_names = [n for n, _ in arms]

    print("=" * WIDTH)
    print(table_row(name for name, _, _ in COLUMNS))
    print("-" * WIDTH)
    # One line per document, not one per arm. A front page and a result page
    # are different documents, and a median taken across both is a median of
    # neither - on a site that answers the front page and refuses the search,
    # the mixed figure sits between the two and resembles a half-served page
    # that was never fetched.
    #
    # The split is on the target and not on `entry`, because on this arm both
    # rows carry `entry="home"`: the column says how the search was reached,
    # and it says the same thing on the front page that got it there. Grouping
    # by it would have collapsed the two back into one line, which is the bug
    # this comment exists because I wrote.
    for site_name in site_names:
        for arm in arm_names:
            info = collected.get((site_name, arm))
            if not info:
                continue
            by_doc = {}
            for row in info["rows"]:
                # Split off the site's own name rather than on a suffix. A
                # bare `endswith("_home")` was the first version and it breaks
                # on `google_maps`, whose search target already ends in a
                # word after an underscore.
                target = row["target"]
                doc = ("search" if target == site_name
                       else target[len(site_name) + 1:])
                by_doc.setdefault(doc, []).append(row)
            for doc, rows in by_doc.items():
                got = [r for r in rows if r["verdict"] != "error"]
                lens = sorted(r["html_len"] for r in got)
                median = lens[len(lens) // 2] if lens else 0
                # The median is taken over rows that have a count, not over
                # rows that arrived: an older run file has no `links` key at
                # all, and `r.get("links") or 0` would report a body full of
                # anchors as a body with none. The two are the same word in
                # the output and opposite findings.
                anchors = sorted(r["links"] for r in got
                                 if r.get("links") is not None)
                counts = Counter(str(r["status"]) for r in rows)
                print(table_row((
                    site_name, arm, doc, rows[0]["entry"], len(rows),
                    dict(counts), f"{median:,}",
                    anchors[len(anchors) // 2] if anchors else "-",
                    sum(1 for r in rows if moved(r)),
                    len(rows) - len(got),
                )))

    # Where they went, under the table rather than in it, because a URL does
    # not fit in a column and the query string is where the answer is:
    # `next=https://shopee.sg/` on a walled front page is what refuted the
    # request-path hypothesis on 2026-09-05.
    #
    # This block exists because the table was read wrong twice in one day.
    # `marketplace_recon_20260905T113703Z` and `154738Z` both printed nothing
    # but HTTP 200 with the storefront's own title while 5 of 6 and 6 of 8
    # responses had been redirected to `/verify/traffic/error`. The instruction
    # was already written down - `b2b_recon`'s docstring says "the first thing
    # to read off a run is `final_url`" - and the summary this probe prints did
    # not implement it, so following the instruction meant not trusting the
    # output. A summary that has to be distrusted is worse than no summary.
    #
    # **Grouped on the path and not on the whole URL, and a synthetic fixture
    # could not have shown why.** The first version counted whole URLs and read
    # correctly against hand-built rows; replayed against `154738Z` it printed
    # six lines of "moved 1x", because Shopee's wall carries a per-request
    # `tracking_id`. Six near-identical 200-character lines is the same failure
    # as the clean table - technically complete and not readable - so the count
    # goes on the destination and one full URL is shown beneath it as evidence.
    #
    # That URL may already be short of its tail: `fingerprint` truncates
    # `final_url` at 200 characters when the row is written, so the `next=` of
    # a long search URL is cut before this ever sees it. Nothing here can undo
    # that, and this comment is the warning not to read an absent parameter as
    # a parameter the site did not send.
    for site_name in site_names:
        for arm in arm_names:
            info = collected.get((site_name, arm))
            if not info:
                continue
            gone, example = Counter(), {}
            for row in info["rows"]:
                if not moved(row):
                    continue
                went = urlparse(row["final_url"])
                where = f"{went.netloc}{went.path}"
                gone[where] += 1
                example.setdefault(where, row["final_url"])
            for where, n in gone.most_common():
                print(f"\n  {site_name}/{arm} moved {n}x -> {where}")
                # Only when the query string carries something the key drops.
                # The first guard here compared the full URL against the key
                # and so could never be false, which put an `e.g.` line under
                # `help.shopee.sg/portal/4` that repeated it with a scheme in
                # front. A condition that cannot fail is not a condition.
                if "?" in example[where]:
                    print(f"      e.g. {example[where]}")

    # Where in the session each attempt sat, which the table cannot show because
    # it groups. `open_session` is entered once per site and arm and every
    # attempt goes through it, so this index is the request's place in one
    # session and not a counter over the run - the engine's own
    # `session_index`, written on every row, agrees with it for that reason.
    #
    # It is here because a run on 2026-09-05 could not be read without it. Two
    # arms 20x apart in request spacing - `164652Z` at `--pause 3` and
    # `170518Z` at `--pause 60` - both walled 19 of 19 completed attempts, and
    # the question that decides what that means is whether the wall is earned
    # during the session or is there before it starts. The sequence answers it
    # in one line and the table cannot answer it at all: across the four Shopee
    # runs of that day the very first request of the session was already walled
    # in three of four, and the three attempts that were served sat at
    # positions 2, 1 and 4 with walls on both sides of them.
    #
    # `arrived` is the honest word for `.` and `served` is not. On 2026-09-05
    # `164652Z` position 13 answered its own search URL with no redirect, and
    # its body carries 0 anchors and no `<title>` - it is the wall that failed
    # to redirect, not a page. That is why the anchor count is printed on the
    # same line as the exception rather than left to the median in the table.
    print("\n" + "=" * WIDTH)
    print("POSITION IN SESSION. One session per site and arm; the index is the "
          "request's\nplace in it. `W` moved, `.` arrived where it was sent, "
          "`!` never answered.")
    for site_name in site_names:
        for arm in arm_names:
            info = collected.get((site_name, arm))
            if not info:
                continue
            rows = info["rows"]
            glyphs = "".join("!" if r["verdict"] == "error"
                             else ("W" if moved(r) else ".") for r in rows)
            print(f"\n  {site_name}/{arm}, {len(rows)} request(s)")
            for start in range(0, len(glyphs), 50):
                print(f"    {start:>4} {glyphs[start:start + 50]}")
            for i, row in enumerate(rows):
                if moved(row) or row["verdict"] == "error":
                    continue
                links = row.get("links")
                print(f"    arrived at #{i}: {row['target']}, "
                      f"{row['html_len']:,} b, "
                      f"{'no link count' if links is None else f'{links} anchors'}")

    # Where the requests left from, which is the one thing `--geo` does not
    # record. `geo` and `params` on a row are the request; this block is the
    # outcome, and it is filled only when the run was made with `--relay`. The
    # four Shopee runs of 2026-09-05 wrote `geo: 'sg'` and
    # `params: {'country': 'sg'}` on all 51 attempts and not one of them says
    # where the exit was, so the question those runs raised - whether the wall
    # is on the exit or on the browser build - cannot be put to those files at
    # all. That is not a gap in the analysis, it is a column that was never
    # written.
    #
    # Every read is a `.get`, because those four files are replayed through this
    # same function and predate every key below. A `KeyError` here would make
    # the archive unreadable in order to report a column the archive cannot
    # have.
    print("\n" + "=" * WIDTH)
    print("WHERE IT LEFT FROM. Filled only under --relay. Without it `geo` "
          "records what was\nasked for and nothing records what was given.")
    for site_name in site_names:
        for arm in arm_names:
            info = collected.get((site_name, arm))
            if not info:
                continue
            rows = info["rows"]
            by_exit = {}
            for row in rows:
                label = row.get("exit_label")
                if label:
                    by_exit.setdefault(label, []).append(row)
            if not by_exit:
                # Two different silences and the row can tell them apart, so the
                # summary does too. `relayed=False` means nobody was watching;
                # `relayed=True` with no label means the relay was watching and
                # the gateway never named an address, which is a finding about
                # the gateway rather than about the run.
                why = ("not relayed" if not rows[0].get("relayed")
                       else "relayed, and the gateway named no exit")
                print(f"\n  {site_name}/{arm}: no exit recorded, {why}")
                continue
            # One JA4 per session and not per row: a browser sends one
            # ClientHello and then reuses the tunnel, so the first row that
            # carries one carries the session's.
            ja4 = next((r.get("tls_ja4") for r in rows if r.get("tls_ja4")),
                       None)
            print(f"\n  {site_name}/{arm}, JA4 {ja4 or 'not recorded'}")
            # Printed before the table, because the table reads as "this exit
            # served these requests" and that is only true when the number
            # below is 0. Measured 2026-09-05 evening it was 7 to 11 per
            # attempt: the gateway rotated mid-page-load, so a row's
            # `exit_prefix` is the last of many and not the one that served it.
            changed = [r.get("exit_new") for r in rows
                       if r.get("exit_new") is not None]
            if changed:
                # "not recorded" and not 0. The 22 rows of 2026-09-05 evening
                # carry `exit_new` and predate `tunnels`, so summing with a
                # `or 0` prints "over 0 tunnels" - which reads as a measured
                # zero and is the one number a denominator can never be. The
                # rows can tell absent from zero, so the line does too.
                counted = [r.get("tunnels") for r in rows
                           if r.get("tunnels") is not None]
                over = (f"{sum(counted)} tunnels" if counted
                        else "tunnels not recorded")
                print(f"    exit changed {sum(changed)}x over {len(changed)} "
                      f"attempts and {over}, "
                      f"{min(changed)}-{max(changed)} per attempt")
                if max(changed) > 1:
                    print("    so a row below names the LAST exit of its "
                          "attempt, not the exit that served it")
            for label, got in by_exit.items():
                first = got[0]
                moved_n = sum(1 for r in got if moved(r))
                errors = sum(1 for r in got if r["verdict"] == "error")
                print(f"    {label:<9}"
                      f"{first.get('exit_prefix') or '?':<18}"
                      f"{first.get('exit_country') or '?':<4}"
                      f"{(first.get('exit_asn') or '?')[:26]:<28}"
                      f"{len(got):>3} req, {moved_n} moved, "
                      f"{len(got) - moved_n - errors} arrived")

    distinct = {r.get("exit_label") for info in collected.values()
                for r in info["rows"] if r.get("exit_label")}
    if distinct:
        if len(distinct) == 1:
            # Said plainly rather than left for the reader to notice, because
            # the table above looks like an answer either way. One address is
            # one address, however many requests went through it.
            print("\nOne exit for the whole run, so this run cannot separate "
                  "the exit from the\nbrowser build - everything it saw, it saw "
                  "from one address. Separating them\nneeds several "
                  "invocations, each opening its own session and drawing its "
                  "own\nexit, and the labels above are what makes them "
                  "comparable afterwards.")
        print("\n`exit_new` is 0 on a row when no new address was named during "
              "that attempt.\nThat covers a reused tunnel and a gateway that "
              "sent no header, which are not the\nsame thing: six CONNECT "
              "replies on 2026-08-13 were all 200 with three different\nreason "
              "phrases and the header on two. An absent header is never "
              "evidence of a\nreused exit.")
        print("\nIt counts changes, not distinct addresses: the relay appends "
              "only when the\nheader differs from the last one stored, so an "
              "A-B-A rotation counts three.\nNo `sid` is passed on this path, "
              "so rotation is the default rather than a\nsetting - measured "
              "2026-09-05 evening at 7-11 changes inside one page load,\nacross "
              "unrelated Singapore ISPs, with ERR_NETWORK_CHANGED three times "
              "in 22\nattempts as the visible cost. Read any per-exit outcome "
              "above with that in mind.")

    for site_name in site_names:
        for arm in arm_names:
            info = collected.get((site_name, arm))
            if not info:
                continue
            hits = info["census"]["scan"]
            if hits:
                print(f"\n{site_name}/{arm} scan: {hits}")
    # The line above is occurrences of a substring across the joined bodies. It
    # is not a count of bodies, and it is not a count of rendered elements, and
    # on this vertical it has already been read as both. `164652Z` on
    # 2026-09-05 printed `{'search-result': 9}` beside a table in which every
    # search attempt had been walled; the string is in every Shopee body,
    # walled or not, inside the asset manifest as the JSON key
    # `"pcmall-search-result-page"`. Nine bodies each carrying it once is what
    # a nine means there, and a served grid is not what it means.
    print("\nThe scan counts substrings in the joined bodies, so it says a "
          "string was present\nand nothing about what carried it. Shopee's "
          "asset manifest ships `search-result`\nin every body, wall included, "
          "measured 2026-09-05 on `164652Z`.")

    print("\n" + "=" * WIDTH)
    print("STRUCTURE, for choosing a ready_selector and a marker")
    for site_name in site_names:
        for arm in arm_names:
            info = collected.get((site_name, arm))
            if not info:
                continue
            for kind, common in info["census"]["attrs"].items():
                if common:
                    print(f"  {site_name}/{arm} {kind}: "
                          f"{[c for c, _ in common[:6]]}")
            # The front page's own listing, and on this arm it is the
            # deliverable rather than a by-product: `entry=home` in
            # `base.py::ensure_entry` needs a `search_box` selector, and the
            # only honest way to get one is out of an archived body. Reading
            # it off the live site is the `norotate` mistake with a CSS
            # selector in it.
            if info["home"]:
                for kind, common in info["home"]["attrs"].items():
                    if common:
                        print(f"  {site_name}_home/{arm} {kind}: "
                              f"{[c for c, _ in common[:6]]}")

    print("\n" + "=" * WIDTH)
    print("A handful of attempts per site from one address in one window. "
          "Enough to choose markers and to see whether a site is reachable at "
          "all. Not a pass rate, and nothing here belongs in the report.")
    # This line said until 2026-09-05 that the redirect is "the only thing in
    # the summary that shows it". That was true when the redirect column was
    # the only one, and `164652Z` refuted it the same day: position 13 answered
    # its own search URL with no redirect at all, and its body carries 0
    # anchors and no `<title>`. Read the redirect alone and that row counts as
    # served. The two columns miss different things, so they are read together.
    print("Read `moved` and `links` before the status column. On this vertical "
          "a refusal is an HTTP 200 carrying the storefront's own title. The "
          "redirect catches most of them; the anchor count catches the wall "
          "that answered in place, and 2026-09-05 produced one of those in 51 "
          "attempts.")


if __name__ == "__main__":
    raise SystemExit(main())
