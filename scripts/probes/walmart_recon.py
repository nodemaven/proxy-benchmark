"""Walmart: capture bodies first, write verdict rules afterwards.

This is the reconnaissance step for a third target, and it deliberately produces
no verdicts at all.

The reason is a mistake this repository has already made twice. `AmazonSearch`
carries interstitial rules written from public descriptions rather than from a
captured body, and not one of them has fired in any run since. Worse, on
2026-08-12 a claim left this repository saying Amazon refuses pool exits with a
bare 503; re-reading the archived bodies showed the identical refusal markup
arriving as both 200 and 503, so the status was never the tell and the published
sentence was wrong. Rules invented ahead of evidence do not fail loudly, they sit
there looking plausible.

So this probe writes `recon` into the verdict column, which is not a member of
`targets.VERDICTS`, and writes to `walmart_recon_*.jsonl`, which no script in
`scripts/analysis/` globs. Neither the rows nor the file can be folded into a
pass rate by accident. The output that matters is on disk: every body is kept, and
the census below is only a reading aid for choosing markers by hand.

Why Walmart is the candidate. It is the same vertical as Amazon, so it takes the
same committed query list and the comparison holds every variable except the
defence in front of it. Amazon runs its own stack; Walmart is fronted by a
commercial bot-management product. That makes the pair a vendor comparison rather
than a second copy of a number we already have.

The three arms are a ladder, not a competition, and they are ordered by how much
of the stack they present:

    http        plain requests, browser headers, a Python TLS handshake
    chromium    the unmodified control, headful, a real browser making a genuine
                handshake and announcing navigator.webdriver
    patchright  the recipe that passes Google 30/30 from this line

Reading the ladder is the whole point. If `http` is served, the target is soft
like Amazon and the game is address reputation. If `http` is refused where the
browsers are served, the refusal is above the address, on the handshake or the
headers, and that is the layer this harness claims to measure and currently has
no target for. If the control is refused where Patchright is served, it is the
browser signals. If everything is refused from this line, the target is harder
than both current ones and that is a finding on its own.

Direct only, from the operator's own address. No gateway, no pool traffic, no
pool reputation. This address is known good for a shop: it serves Amazon a
919 KB result list to a plain client. Putting the pool in as well would move two
variables and the answer would name no layer.

Usage:
    python scripts/probes/walmart_recon.py
    python scripts/probes/walmart_recon.py --queries 8
    python scripts/probes/walmart_recon.py --arms http,patchright
"""
import argparse
import re
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import quote_plus

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nmbench import artifacts, engines, queries
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

# Structural attributes, counted rather than matched. A result list and a
# challenge page are both built out of these, and reading the two censuses side
# by side is how a marker gets chosen from evidence instead of from memory.
ATTRS = {
    "data-testid": re.compile(r'data-testid="([^"]{1,48})"'),
    "data-automation-id": re.compile(r'data-automation-id="([^"]{1,48})"'),
    "id": re.compile(r'\bid="([^"]{1,48})"'),
}

# Substrings worth knowing the count of. This is a scan, not a rule set: a hit
# here is a place to look in the archived body, and nothing in this file turns
# one into a verdict. Several are deliberately over-broad - "bot" matches
# robots.txt and "captcha" already appeared in the telemetry of a perfectly good
# Bing page - which is exactly why the number is printed and not acted on.
SCAN = (
    "px-captcha", "perimeterx", "_px", "recaptcha", "turnstile", "cf-challenge",
    "captcha", "robot", "are you a human", "verify you are", "access denied",
    "blocked", "unusual", "__NEXT_DATA__", "add to cart", "no results",
)


class WalmartRecon:
    """A URL builder with no verdict rules, on purpose.

    `judge` returns a verdict that is not in `targets.VERDICTS`. That is not
    sloppiness: it means any code that later tries to count these rows as
    successes or refusals produces a visibly wrong category instead of a
    plausible number.
    """

    name = "walmart_search"
    # The same list Amazon uses, from the same generator and the same seed.
    # Asking a shop a search-engine query measures how it answers a query with
    # no products, which is not the question here.
    query_list = "amazon_1000"
    # No selector, because we have never seen this page and inventing one would
    # make every attempt wait 8 seconds and then record False for a page that
    # was fine. `--settle` is used instead: unconditional, and the same for
    # every arm.
    ready_selector = None
    # Conservative until measured: this refuses the script-blocking presets, so
    # a recon run cannot accidentally measure our own resource blocking.
    needs_script = True

    def url(self, query: str) -> str:
        return f"https://www.walmart.com/search?q={quote_plus(query)}"

    def judge(self, url: str, title: str, html: str) -> Judgement:
        return Judgement("recon", "no verdict rules exist for this target yet, "
                                  "the archived body is the output")


def browser_attempt(session, target, query, settle_ms, store) -> dict:
    """One query through a browser, with an unconditional settle.

    This mirrors `ChromiumSession.fetch` rather than calling it, for one reason:
    `fetch` snapshots at `domcontentloaded` and waits only for a selector the
    target declares. This target declares none because none is known, and a page
    that builds its list after hydration would be archived half-empty and read
    as "no results" by whoever opens the body later. The settle is the thing this
    probe cannot do without.
    """
    url = target.url(query)
    row = blank_row(f"{session.label}-direct/{session.preset}",
                    session.version, query, url,
                    target=target.name, direct=True, preset=session.preset,
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
        row["verdict_reason"] = target.judge(page.url, "", html).reason
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


def http_attempt(session, target, query) -> dict:
    """One query through the plain client.

    Routed through `HttpSession.fetch` rather than a bare `requests.get`, so the
    headers and, more importantly, the decoding check are the same ones the
    benchmark uses. Undecoded brotli read as a block for a whole run on
    2026-08-10 and that is not a mistake worth making twice on a new target.

    `fetch` archives through the store the session was opened with, so the body
    is read back off disk for the census rather than kept in a second place. If
    that read fails the census loses one body and the run does not notice, which
    is the right way round.
    """
    row = session.fetch(target, query)
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
        "attrs": {name: Counter(rx.findall(joined)).most_common(10)
                  for name, rx in ATTRS.items()},
        "scan": {s: low.count(s.lower()) for s in SCAN if low.count(s.lower())},
    }


def main() -> None:
    known = [name for name, _ in LADDER]
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", type=int, default=5,
                        help="queries per arm. The default times three arms is "
                             "the 15 attempt budget this probe was asked for")
    parser.add_argument("--arms", default=",".join(known),
                        help=f"comma separated, from {known}")
    parser.add_argument("--settle", type=float, default=6.0,
                        help="seconds to let the page finish building before "
                             "the body is read. Applied to every browser arm "
                             "equally, so it cannot favour one")
    parser.add_argument("--pause", type=float, default=5.0,
                        help="seconds between attempts")
    parser.add_argument("--headless", action="store_true",
                        help="run the browser arms headless. Not the default: "
                             "the recipe under test is headful and a headless "
                             "refusal would not tell us about the target")
    parser.add_argument("--channel", default=None)
    parser.add_argument("--query", action="append", default=None, metavar="TEXT",
                        help="ask this exact string instead of the committed "
                             "list, repeatable. The one case that needs it is "
                             "capturing the page a shop returns when nothing "
                             "matches: on Amazon that page is the search "
                             "working, and a rule that had never seen it would "
                             "have scored it a refusal")
    args = parser.parse_args()

    wanted = [n.strip() for n in args.arms.split(",") if n.strip()]
    unknown = [n for n in wanted if n not in known]
    if unknown:
        parser.error(f"unknown arms {unknown}, known: {known}")
    arms = [(n, why) for n, why in LADDER if n in wanted]

    target = WalmartRecon()
    words = args.query or queries.load(target.query_list, limit=args.queries)
    availability = engines.report_availability()

    sink = JsonlSink("walmart_recon")
    # Every body, not a sample. The entire purpose of this run is the archive,
    # and a sampled recon would mean going back to the target to see the page we
    # already fetched.
    store = artifacts.ArtifactStore("walmart_recon", run_id=sink.run_id,
                                    sample_ok=10_000)

    print(f"target  : {target.name}, {target.url(words[0])}")
    print(f"queries : {len(words)} per arm, from {target.query_list}, "
          f"the list Amazon uses")
    print(f"mode    : direct, no gateway, no pool traffic, "
          f"{'headless' if args.headless else 'headful'}")
    print("verdicts: none. Every row is 'recon' and every body is kept")
    print(f"raw rows -> {sink.path}\n")

    collected = {}
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
                    # session that can hand out a page gets the settle; one that
                    # cannot has nothing to settle. Written this way so adding a
                    # fourth arm needs no edit here.
                    if hasattr(session, "new_page"):
                        row = browser_attempt(session, target, query,
                                              int(args.settle * 1000), store)
                    else:
                        row = http_attempt(session, target, query)
                    html = row.pop("_html", "")
                    if html:
                        bodies.append(html)
                    row["arm"] = name
                    sink.write(row)
                    rows.append(row)
                    print(f"    {query[:24]:<26}{row['status'] or '-'!s:<6}"
                          f"{row['html_len']:>9}  {(row['title'] or '')[:38]}")
                    time.sleep(args.pause)
        except Exception as exc:
            print(f"  {name}: FAILED, {type(exc).__name__}: {exc}")
        if rows:
            collected[name] = {"rows": rows, "census": census(bodies)}

    if not collected:
        print("\nnothing captured")
        return

    report(collected, arms)
    print(f"\nraw rows: {sink.path}")
    print(f"bodies:   {store.written} saved -> {store.dir}")
    print("\nNext step is by hand: open two bodies from different arms, pick the "
          "markers off them, and only then write the target class. Nothing in "
          "this file is a verdict rule and nothing here should be quoted as a "
          "refusal rate.")


def report(collected, arms) -> None:
    order = [n for n, _ in arms if n in collected]

    print("\n" + "=" * 78)
    print(f"{'arm':<14}{'n':>3}{'statuses':>26}{'median size':>14}"
          f"{'errors':>8}")
    print("-" * 78)
    sizes = {}
    for name in order:
        rows = collected[name]["rows"]
        got = [r for r in rows if r["verdict"] != "error"]
        lens = sorted(r["html_len"] for r in got)
        median = lens[len(lens) // 2] if lens else 0
        sizes[name] = median
        counts = Counter(str(r["status"]) for r in rows)
        print(f"{name:<14}{len(rows):>3}{dict(counts)!s:>26}"
              f"{median:>14,}{len(rows) - len(got):>8}")

    for name in order:
        info = collected[name]["census"]
        print("\n" + "-" * 78)
        print(f"{name}: structure across {len(collected[name]['rows'])} bodies")
        for attr, common in info["attrs"].items():
            if not common:
                continue
            shown = ", ".join(f"{v}x{c}" for v, c in common[:6])
            print(f"  {attr:<20}{shown}")
        if info["scan"]:
            print(f"  {'scan hits':<20}{info['scan']}")
        else:
            print(f"  {'scan hits':<20}none of the candidate strings appeared")

    print("\n" + "=" * 78)
    read(order, sizes, collected)


def read(order, sizes, collected) -> None:
    """Name the layer, or say that the ladder did not separate one."""
    if len(order) < 2:
        print("one arm ran, so the ladder cannot separate a layer. What this "
              "bought is a body to read, which is still the point.")
        return

    # A refusal page is small and a result list is large, in every target this
    # harness has measured. That is a size heuristic and it is stated as one:
    # it decides which sentence gets printed, never what a row is worth.
    biggest = max(sizes.values()) or 1
    served = [n for n in order if sizes[n] >= biggest * 0.5]
    short = [n for n in order if n not in served]

    if len(served) == len(order):
        print("Every arm came back with a body of comparable size, including "
              "the plain client. If those bodies turn out to be result lists, "
              "Walmart is soft from this address the way Amazon is, the game is "
              "address reputation, and the third target adds a vendor rather "
              "than a new layer.")
    elif "http" in short and "http" not in served:
        print("The plain client came back short where a browser did not. That "
              "is the handshake or the header layer refusing before any page "
              "logic runs, which is the third layer in the README table and the "
              "one this harness has claimed to measure without owning a target "
              "for it. If it holds up, Obscura finally has something to prove.")
    elif "chromium" in short and "patchright" in served:
        print("The unmodified control came back short where the patched driver "
              "did not, with the same address and the same handshake. That is "
              "the browser signal layer, the same place Google catches us.")
    else:
        print(f"The arms split in a way the size heuristic does not name: "
              f"{served} came back full and {short} short. Read the bodies "
              f"before drawing the ladder.")

    print("Fifteen attempts from one address in one window. This is enough to "
          "choose markers and to point at a layer. It is not a pass rate, and "
          "no number from this run belongs in the report.")


if __name__ == "__main__":
    main()
