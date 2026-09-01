"""B2B enrichment sites: capture bodies first, write verdict rules afterwards.

Reconnaissance for a vertical this harness has never touched, and like
`walmart_recon.py` it produces no verdicts on purpose. The reason is the same
mistake, twice made: `AmazonSearch` carries interstitial rules written from
public descriptions rather than from a captured body and not one of them has
ever fired, and on 2026-08-12 a claim left this repository saying Amazon refuses
pool exits with a bare 503 when the identical markup was arriving as both 200
and 503. Rules invented ahead of evidence do not fail loudly.

So every row here says `recon`, which is not a member of `targets.VERDICTS`, and
the file is `b2b_recon_*.jsonl`, which no script in `scripts/analysis/` globs.
Neither can be folded into a pass rate by accident.

Why this vertical is worth a target at all. The three sites the harness measures
now are two search engines and two shops, and all four defend themselves.
Nothing here has a target where the defence belongs to a *named third-party
vendor*, so the harness cannot say anything about the products a customer
actually buys against. The five sites below were probed from this machine on
2026-08-28, plain `requests` with `engines.http.BROWSER_HEADERS`, and they are
not one wall:

    opencorporates.com    403, title "Forbidden", 15.7 KB, sets an `htch`
                          cookie. No vendor string in the body. Bespoke or
                          white-labelled; unidentified, and that is written
                          down as unidentified.
    www.sec.gov           403, Server `AkamaiNetStorage`, sets `ak_bmsc` -
                          Akamai Bot Manager. Note that `efts.sec.gov`, the
                          full-text search backend, answered 200 with 64 KB in
                          the same minute, so the refusal belongs to one host's
                          configuration and not to the SEC.
    www.capterra.com      403, "Just a moment...", Server `cloudflare`,
                          `__cf_bm`. Cloudflare's JS interstitial.
    clutch.co             403, "Just a moment...", byte-identical length to
                          Capterra's. Cloudflare.
    www.producthunt.com   403, the same Cloudflare interstitial.

    (www.crunchbase.com, not in the asked-for list, was probed in the same
    minute: 403, "Attention Required! | Cloudflare". Recorded here because it
    is free information about the vertical, not because it is scheduled.)

**The first version of this docstring gave ProductHunt as "200, 207 KB, served"
and sec.gov as a 503, and both were wrong for the client this file actually
uses.** What the mistake looked like from the inside: the survey that chose
these five sites was an ad-hoc `requests.get` with a hand-written header dict
carrying `Chrome/149`, and it was written up as "plain requests, browser
headers" - the same phrase this file uses for the `http` arm, which sends
`BROWSER_HEADERS` and its pinned `Chrome/127`. Two clients, one label. The
discrepancy only surfaced because the smoke run disagreed with the note sitting
above it.

Chasing it produced the finding that is worth more than this file: **on those
two sites the refusal is a User-Agent version floor and nothing else.**
ProductHunt serves at Chrome/131 and refuses at 130, 16 of 16 either way;
sec.gov serves at 146 and refuses at 145. Different vendors, different floors.
The other three refuse at 149, at 160 and at an impossible 999, so they are
refusing something else. The full table and what it costs the `http` arm's
readings are in `nmbench/engines/http.py` above `BROWSER_HEADERS`.

That is also why the URL templates below carry a caveat: on Capterra both
`?query=` and `?q=` returned the same Cloudflare interstitial, so **which query
parameter these sites take has not been verified** - the challenge answers
before the request is routed. The first thing to read off a run is `final_url`
and whether a served body mentions the query at all.

The three arms are the same ladder as `walmart_recon.py`, ordered by how much of
the stack they present, and reading the split is the point:

    http        plain requests, browser headers, a Python TLS handshake
    chromium    the unmodified control, headful, a real browser announcing
                navigator.webdriver
    patchright  the recipe that passes Google from this line

If a site refuses `http` and serves the browsers, the refusal is on the
handshake or the headers. If it refuses the control and serves Patchright, it is
the browser signals. If it refuses all three from a clean residential address,
it is harder than anything currently in the registry and that is a finding on
its own.

Direct only, from the operator's own address. No gateway, no pool traffic, no
pool reputation spent on a vertical we cannot yet score. The addresses in the
pool are worth more than this run.

Google Maps is part of the same backlog item and is deliberately not here: it is
a map application rather than a document, so it needs its own capture strategy
and it would have made this file measure two things badly.

Usage:
    python scripts/probes/b2b_recon.py
    python scripts/probes/b2b_recon.py --sites capterra,clutch --queries 6
    python scripts/probes/b2b_recon.py --arms http
"""
import argparse
import re
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import quote_plus, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nmbench import artifacts, engines
from nmbench.engines.base import blank_row, record_error
from nmbench.sink import JsonlSink
from nmbench.targets import Judgement, fingerprint

# Ordered by how much of the stack each one presents. The order is the argument,
# so the report prints them in it whatever order the flag asked for.
LADDER = (
    ("http", "plain requests, browser headers, Python TLS handshake"),
    ("chromium", "unmodified control, headful, real handshake"),
    ("patchright", "patched driver, headful, the Google recipe"),
)

# Two lists, because the sites ask two different kinds of question. A registry
# wants a company name and a software directory wants a category, and sending a
# category to a registry measures how it answers a query with no rows - which is
# a real page worth having, but not the one this run is for.
#
# Literals rather than a file in `data/queries/`, and the distinction matters:
# those files are the committed inputs for runs whose numbers get published, and
# nothing here produces a number. They are still fixed and in the repository, so
# two runs of this command send the same strings in the same order.
COMPANIES = (
    "Stripe", "Databricks", "Snowflake", "Cloudflare", "Datadog",
    "Twilio", "Atlassian", "Palantir", "Shopify", "MongoDB",
)
CATEGORIES = (
    "crm software", "help desk", "project management", "payroll",
    "email marketing", "seo agency", "web development", "video production",
    "data analytics", "cyber security",
)


class Site:
    """A URL builder with no verdict rules, on purpose.

    `judge` returns a verdict that is not in `targets.VERDICTS`. That is not
    sloppiness: any code that later tries to count these rows as successes or
    refusals produces a visibly wrong category instead of a plausible number.
    """

    # No selector, for the same reason `walmart_recon` has none: we have never
    # seen these pages, and inventing one makes every attempt wait out its
    # timeout and then record False for a page that was fine. `--settle` does
    # the waiting instead - unconditional, and identical for every arm.
    ready_selector = None
    # Conservative until measured: this refuses the script-blocking presets, so
    # a recon run cannot accidentally measure our own resource blocking. Two of
    # the five are known to serve a JS interstitial, where blocking script would
    # measure the block.
    needs_script = True

    def __init__(self, name, template, words, note):
        self.name = name
        self.template = template
        self.words = words
        self.note = note

    def url(self, query: str) -> str:
        return self.template.format(q=quote_plus(query))

    def judge(self, url: str, title: str, html: str) -> Judgement:
        return Judgement("recon", "no verdict rules exist for this target yet, "
                                  "the archived body is the output")


# Every template below was requested from this machine on 2026-08-28 with
# `BROWSER_HEADERS`, which is what the `http` arm sends, and the answer is in
# `note`. A 403 there is a measurement and not a broken URL; what is genuinely
# unverified is the query parameter on the Cloudflare sites, because the
# interstitial answers before the parameter is read.
SITES = {s.name: s for s in (
    Site("opencorporates",
         "https://opencorporates.com/companies?q={q}",
         COMPANIES,
         "403 Forbidden, 15.7 KB, `htch` cookie, no vendor string. Still 403 "
         "at Chrome/999, so not a version floor"),
    Site("sec_edgar",
         "https://www.sec.gov/cgi-bin/browse-edgar?company={q}&CIK=&type=10-K"
         "&dateb=&owner=include&count=40&action=getcompany",
         COMPANIES,
         "403, AkamaiNetStorage, `ak_bmsc`. Serves at Chrome/146, refuses at "
         "145, so the http arm's pin is below the floor"),
    Site("producthunt",
         "https://www.producthunt.com/search?q={q}",
         CATEGORIES,
         "403 cloudflare. Serves at Chrome/131 with 205 KB, refuses at 130, "
         "so the http arm's pin is below the floor"),
    Site("capterra",
         "https://www.capterra.com/search/?query={q}",
         CATEGORIES,
         "403 Just a moment, cloudflare, still 403 at Chrome/999; ?query= and "
         "?q= gave the same page"),
    Site("clutch",
         "https://clutch.co/search?q={q}",
         CATEGORIES,
         "403 Just a moment, cloudflare, still 403 at Chrome/999, same length "
         "as Capterra's"),
)}

# Structural attributes, counted rather than matched. A result list and a
# challenge page are both built out of these, and reading the two censuses side
# by side is how a marker gets chosen from evidence instead of from memory.
ATTRS = {
    "data-testid": re.compile(r'data-testid="([^"]{1,48})"'),
    "data-test": re.compile(r'data-test="([^"]{1,48})"'),
    "id": re.compile(r'\bid="([^"]{1,48})"'),
}

# Substrings worth knowing the count of. This is a scan, not a rule set: a hit
# here is a place to look in the archived body, and nothing in this file turns
# one into a verdict. Several are deliberately over-broad - "bot" matches
# robots.txt and "captcha" already appeared in the telemetry of a perfectly good
# Bing page - which is exactly why the number is printed and not acted on.
#
# The vendor names are first because this vertical's whole interest is which
# product is in front of which site.
SCAN = (
    "cloudflare", "cf-chl", "turnstile", "just a moment", "attention required",
    "ak_bmsc", "akamai", "_abck", "datadome", "perimeterx", "px-captcha",
    "incapsula", "imperva", "recaptcha", "hcaptcha",
    "enable javascript", "access denied", "forbidden", "unusual", "automated",
    "rate limit", "no results", "__NEXT_DATA__", "search-result",
)


def browser_attempt(session, site, query, settle_ms, store) -> dict:
    """One query through a browser, with an unconditional settle.

    This mirrors `ChromiumSession.fetch` rather than calling it, for one reason:
    `fetch` snapshots at `domcontentloaded` and waits only for a selector the
    target declares. These targets declare none because none is known, and a
    Cloudflare interstitial that clears itself after a few seconds would be
    archived as the challenge and read as a refusal that was not one. The settle
    is the thing this probe cannot do without, and it is the same for every arm
    so it cannot favour one.
    """
    url = site.url(query)
    row = blank_row(f"{session.label}-direct/{session.preset}",
                    session.version, query, url,
                    target=site.name, direct=True, preset=session.preset,
                    params={}, headless=bool(session.headless),
                    session_index=session.index)
    session.index += 1

    counter = {}
    page = session.new_page(counter)
    started = time.perf_counter()
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=60000)
        if response:
            row["status"] = response.status
        page.wait_for_timeout(settle_ms)
        html = page.content()
        row["html_len"] = len(html)
        row["verdict"] = "recon"
        row["verdict_reason"] = site.judge(page.url, "", html).reason
        row.update(fingerprint(page.url, html))
        row["title"] = page.title()[:120]
        row["artifact"] = store.save(row, html)
        row["_html"] = html
    except Exception as exc:
        record_error(row, exc)
    finally:
        row["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        row["bytes"] = counter.get("bytes", 0)
        row["blocked"] = counter.get("blocked", 0)
        row["allowed"] = counter.get("allowed", 0)
        page.close()
    return row


def http_attempt(session, site, query) -> dict:
    """One query through the plain client.

    Routed through `HttpSession.fetch` rather than a bare `requests.get`, so the
    headers and, more importantly, the decoding check are the same ones the
    benchmark uses. Undecoded brotli read as a block for a whole run on
    2026-08-10 and that is not a mistake worth making twice on a new vertical.
    """
    row = session.fetch(site, query)
    if row.get("artifact"):
        try:
            row["_html"] = artifacts.read(row["artifact"])
        except OSError:
            pass
    return row


def census(bodies: list) -> dict:
    """Count structure and scan strings over every body one arm collected."""
    joined = "\n".join(bodies)
    low = joined.lower()
    return {
        "attrs": {name: Counter(rx.findall(joined)).most_common(8)
                  for name, rx in ATTRS.items()},
        "scan": {s: low.count(s.lower()) for s in SCAN if low.count(s.lower())},
    }


def main() -> None:
    known_arms = [name for name, _ in LADDER]
    parser = argparse.ArgumentParser()
    parser.add_argument("--sites", default=",".join(SITES),
                        help=f"comma separated, from {list(SITES)}")
    parser.add_argument("--arms", default=",".join(known_arms),
                        help=f"comma separated, from {known_arms}")
    parser.add_argument("--queries", type=int, default=4,
                        help="queries per site per arm. The default over five "
                             "sites and three arms is 60 attempts, which is a "
                             "first look and not a rate")
    parser.add_argument("--settle", type=float, default=8.0,
                        help="seconds to let the page finish before the body is "
                             "read. Higher than walmart_recon's 6 because a "
                             "Cloudflare interstitial can clear itself, and a "
                             "challenge archived as the answer is a wrong body")
    parser.add_argument("--pause", type=float, default=6.0,
                        help="seconds between attempts")
    parser.add_argument("--headless", action="store_true",
                        help="run the browser arms headless. Not the default: "
                             "the recipe under test is headful and a headless "
                             "refusal would not tell us about the site")
    parser.add_argument("--channel", default=None)
    args = parser.parse_args()

    wanted_sites = [s.strip() for s in args.sites.split(",") if s.strip()]
    unknown = [s for s in wanted_sites if s not in SITES]
    if unknown:
        parser.error(f"unknown sites {unknown}, known: {list(SITES)}")

    wanted_arms = [n.strip() for n in args.arms.split(",") if n.strip()]
    unknown = [n for n in wanted_arms if n not in known_arms]
    if unknown:
        parser.error(f"unknown arms {unknown}, known: {known_arms}")
    arms = [(n, why) for n, why in LADDER if n in wanted_arms]

    availability = engines.report_availability()
    sink = JsonlSink("b2b_recon")
    # Every body, not a sample. The entire purpose of this run is the archive,
    # and a sampled recon would mean going back to the site to see the page we
    # already fetched.
    store = artifacts.ArtifactStore("b2b_recon", run_id=sink.run_id,
                                    sample_ok=10_000)

    print(f"sites   : {len(wanted_sites)} x {args.queries} queries x "
          f"{len(arms)} arms = "
          f"{len(wanted_sites) * args.queries * len(arms)} attempts")
    print(f"mode    : direct, no gateway, no pool traffic, "
          f"{'headless' if args.headless else 'headful'}")
    print("verdicts: none. Every row is 'recon' and every body is kept")
    print(f"raw rows -> {sink.path}\n")

    collected = {}
    for site_name in wanted_sites:
        site = SITES[site_name]
        words = list(site.words[:args.queries])
        print(f"{site_name}  ({urlparse(site.url('x')).netloc})")
        print(f"  2026-08-28, plain client, direct: {site.note}")
        for name, why in arms:
            if availability.get(name):
                print(f"  {name}: SKIPPED, {availability[name]}")
                continue
            print(f"  {name}: {why}")
            rows, bodies = [], []
            try:
                with engines.get(name).open(direct=True, preset="light",
                                            headless=args.headless,
                                            channel=args.channel,
                                            store=store) as session:
                    for query in words:
                        # Branch on the capability, never on the engine name. A
                        # session that can hand out a page gets the settle; one
                        # that cannot has nothing to settle.
                        if hasattr(session, "new_page"):
                            row = browser_attempt(session, site, query,
                                                  int(args.settle * 1000), store)
                        else:
                            row = http_attempt(session, site, query)
                        html = row.pop("_html", "")
                        if html:
                            bodies.append(html)
                        row["arm"] = name
                        row["site"] = site_name
                        sink.write(row)
                        rows.append(row)
                        print(f"    {query[:22]:<24}"
                              f"{row['status'] or '-'!s:<6}"
                              f"{row['html_len']:>9}  {(row['title'] or '')[:36]}")
                        time.sleep(args.pause)
            except Exception as exc:
                print(f"  {name}: FAILED, {type(exc).__name__}: {exc}")
            if rows:
                collected[(site_name, name)] = {"rows": rows,
                                                "census": census(bodies)}
        print()

    if not collected:
        print("nothing captured")
        return

    report(collected, wanted_sites, arms)
    print(f"\nraw rows: {sink.path}")
    print(f"bodies:   {store.written} saved -> {store.dir}")
    print("\nNext step is by hand: open one body per site per arm, decide what "
          "a served page and a refusal look like there, and only then write a "
          "target class. Nothing in this file is a verdict rule and no number "
          "from this run is a refusal rate.")


def report(collected, site_names, arms) -> None:
    arm_names = [n for n, _ in arms]

    print("=" * 78)
    print(f"{'site':<17}{'arm':<12}{'n':>3}{'statuses':>22}"
          f"{'median size':>14}{'err':>6}")
    print("-" * 78)
    sizes = {}
    for site_name in site_names:
        for arm in arm_names:
            info = collected.get((site_name, arm))
            if not info:
                continue
            rows = info["rows"]
            got = [r for r in rows if r["verdict"] != "error"]
            lens = sorted(r["html_len"] for r in got)
            median = lens[len(lens) // 2] if lens else 0
            sizes[(site_name, arm)] = median
            counts = Counter(str(r["status"]) for r in rows)
            print(f"{site_name:<17}{arm:<12}{len(rows):>3}{dict(counts)!s:>22}"
                  f"{median:>14,}{len(rows) - len(got):>6}")

    for site_name in site_names:
        for arm in arm_names:
            info = collected.get((site_name, arm))
            if not info:
                continue
            hits = info["census"]["scan"]
            if hits:
                print(f"\n{site_name}/{arm} scan: {hits}")

    print("\n" + "=" * 78)
    read(collected, site_names, arm_names, sizes)


def read(collected, site_names, arm_names, sizes) -> None:
    """Name the layer per site, or say the ladder did not separate one."""
    for site_name in site_names:
        present = [a for a in arm_names if (site_name, a) in sizes]
        if len(present) < 2:
            print(f"{site_name}: fewer than two arms ran, so the ladder cannot "
                  f"separate a layer. What it bought is a body to read.")
            continue
        # A refusal page is small and a result list is large, on every target
        # this harness has measured. That is a size heuristic and it is stated
        # as one: it decides which sentence gets printed, never what a row is
        # worth. It is weaker here than on a shop, because a Cloudflare
        # interstitial and a thin result page are not far apart in bytes.
        biggest = max(sizes[(site_name, a)] for a in present) or 1
        served = [a for a in present if sizes[(site_name, a)] >= biggest * 0.5]
        short = [a for a in present if a not in served]

        if not short:
            print(f"{site_name}: every arm came back comparable in size, "
                  f"including the plain client if it ran. If those are result "
                  f"pages, the site is soft from this address and the game "
                  f"there is address reputation.")
        elif "http" in short and "http" not in served:
            print(f"{site_name}: the plain client came back short where a "
                  f"browser did not. That is the handshake or the header layer, "
                  f"which is the layer the README claims and has no target for.")
        elif "chromium" in short and "patchright" in served:
            print(f"{site_name}: the unmodified control came back short where "
                  f"the patched driver did not, same address, same handshake. "
                  f"That is the browser signal layer.")
        else:
            print(f"{site_name}: the arms split in a way the size heuristic "
                  f"does not name - {served} full, {short} short. Read the "
                  f"bodies.")

    print("\nA handful of attempts per site from one address in one window. "
          "Enough to choose markers and to point at a layer. Not a pass rate, "
          "and nothing here belongs in the report.")


if __name__ == "__main__":
    main()
