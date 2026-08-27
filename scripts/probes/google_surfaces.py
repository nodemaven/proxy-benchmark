"""Which Google surfaces answer a pool exit, and which do not.

Written 2026-08-26, after the first warm-up ladder run found that one of its
warm-up pages was not arriving. `news.google.com` delivered 10 of 24 visits in
`probehold_20260826T152748Z` while `translate.google.com` delivered 24 of 24 on
the same exits in the same run, and nothing in the ladder's output said so - the
identities went on to be labelled L2 and L3 for a warm-up they had not received.
That is what this script exists to prevent happening again.

**It deliberately does not touch search.** `www.google.com/search` is the
target, and a run that warms an exit on the target and then measures the target
is measuring its own warm-up. Every URL below is a Google property that is not
the result page, which is the whole population a warm-up may be drawn from.

**What it measures is arrival, not refusal.** A warm-up page has one job: be
reached, cheaply, without the exit being spent. So the columns are whether
`goto` completed, what it cost in bytes, and how long it took. There is no
`judge` call and no verdict - Google does not serve a captcha to a browser
opening Translate, and a script that pretended to read one would be inventing a
result. The one refusal shape that is worth naming is a redirect to
`/sorry/`, so the final URL is recorded and checked for it.

**Cost is the second column and not an afterthought.** A warm-up page is paid
for on every identity of every warm rung, so a 3 MB page in L3 costs six times
what the same page costs in L1 and is charged against every row the ladder
produces. A surface that arrives reliably and costs 2 MB is a worse warm-up
than one that arrives reliably and costs 200 KB, and the ladder run cannot see
that difference because it charges the bytes to the run as a whole.

**One exit per pass, all surfaces on it, several passes.** The alternative -
a fresh exit per surface - would confound the surface with the exit, which is
exactly the mistake this script was written to catch. Sharing one exit across
the whole list means a surface that fails while its neighbours succeed has
failed on its own account. The passes are what give each surface an n.

What this cannot answer: whether visiting a surface helps the probe afterwards.
That is the ladder's question and it needs the ladder. This only says which
surfaces are eligible to be in one.
"""
import argparse
import random
import statistics
import sys
import time
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nmbench.console import tolerate_unencodable_output

tolerate_unencodable_output()

from nmbench import engines, gateway, proxy
from nmbench.sink import JsonlSink

# Marks this file as one that sends traffic, the same way the other probes do.
SENDS_REQUESTS = True

ROOT = Path(__file__).resolve().parents[2]

# The candidates, with `hl` pinned wherever the surface honours it for the same
# reason every other URL in this repository pins it: a localised page is a
# different page, and comparing a German Maps against an English Translate would
# be comparing two things at once.
#
# `www.google.com/imghp` and `translate.google.com` are here because they are
# already in the ladder and the survey has to be able to reproduce their known
# numbers before its verdict on anything else is worth reading. They are the
# positive controls. `news.google.com` is here for the same reason from the
# other side: it is the one already known to fail, and a survey that reports it
# healthy is a survey with a bug in it.
SURFACES = [
    ("images", "https://www.google.com/imghp?hl=en"),
    ("translate", "https://translate.google.com/?hl=en"),
    ("news", "https://news.google.com/?hl=en-US"),
    ("maps", "https://www.google.com/maps?hl=en"),
    ("books", "https://books.google.com/?hl=en"),
    ("scholar", "https://scholar.google.com/?hl=en"),
    ("shopping", "https://shopping.google.com/?hl=en"),
    ("flights", "https://www.google.com/travel/flights?hl=en"),
    ("finance", "https://www.google.com/finance/?hl=en"),
    ("trends", "https://trends.google.com/trends/?hl=en"),
    ("arts", "https://artsandculture.google.com/"),
    ("earth", "https://earth.google.com/web/"),
    ("photos", "https://www.google.com/photos/about/?hl=en"),
    ("store", "https://store.google.com/?hl=en"),
    ("about", "https://about.google/"),
    ("policies", "https://policies.google.com/?hl=en"),
    ("support", "https://support.google.com/?hl=en"),
    ("blog", "https://blog.google/"),
    ("youtube", "https://www.youtube.com/?hl=en"),
    ("gmail", "https://www.google.com/gmail/about/?hl=en"),
]


# The tunnel itself went away, which is not a fact about the surface that was
# being fetched when it happened.
#
# `surfaces_20260826T220435Z` is why this exists. Of 45 navigations in list
# positions 1-15 across three passes, none failed this way; of 15 navigations in
# positions 16-20, seven did, two-tailed Fisher p = 1.7e-5. In both affected
# passes every surface from the first such failure to the end of the list failed
# too, so it is one event and not seven. Counted per surface it made `youtube`
# and `gmail` read as 1 of 3 arrivals when what was measured was that they are
# last in the list, and it would have got them dropped from the ladder for it.
#
# Not a wall-clock session expiry: the pass that survived spent 100.5 s
# navigating, and the pass that died at position 16 had spent 34.1 s. Navigation
# count and accumulated bytes are both still live as explanations and this run
# cannot separate them, which is the other reason the order is now shuffled.
TRANSPORT_ERRORS = ("ERR_TUNNEL_CONNECTION_FAILED",
                    "ERR_PROXY_CONNECTION_FAILED")


def lost_the_tunnel(seen: dict) -> bool:
    error = seen.get("error") or ""
    return any(marker in error for marker in TRANSPORT_ERRORS)


def terse_error(error: str) -> str:
    """An error stripped of the parts that are identical on every row.

    `Page.goto:` is on every message this probe can produce, because every
    message comes from the same call, and the trailing ` at <url>` names the
    surface the row already names in its first column. Removing both is what
    lets the note be printed whole - see `report`.
    """
    return (error or "").replace("Page.goto: ", "").split(" at http", 1)[0]\
                        .strip()


def parse_args():
    parser = argparse.ArgumentParser(
        description=("Survey Google surfaces for use as warm-up pages. Search "
                     "is deliberately absent - see this file's docstring."))
    parser.add_argument("--engine", default="patchright",
                        help="the engine the ladder runs on, so the survey "
                             "measures what the ladder will meet")
    parser.add_argument("--passes", type=int, default=3,
                        help="exits. One exit visits the surfaces in a "
                             "shuffled order until it either finishes them or "
                             "loses the tunnel, whichever comes first")
    parser.add_argument("--country", default="any")
    parser.add_argument("--dwell", type=float, default=3.0,
                        help="seconds after each navigation. Doubles as the "
                             "gap between surfaces and as the window the "
                             "page's late subresources are counted in")
    parser.add_argument("--timeout", type=int, default=30000,
                        help="ms. 30s and not 60s on purpose: a warm-up page "
                             "that needs a minute is not a warm-up page, it is "
                             "an identity spent waiting")
    parser.add_argument("--only", default=None,
                        help="comma-separated names from SURFACES")
    parser.add_argument("--seed", type=int, default=None,
                        help="seeds the per-pass shuffle of the surface order. "
                             "Printed in the header either way, so a run can "
                             "always be repeated in the order it actually ran")
    parser.add_argument("--in-order", action="store_true",
                        help="visit the surfaces in the order SURFACES "
                             "declares them. Only for reproducing a run taken "
                             "before the shuffle existed: it puts the same "
                             "surfaces last every pass, and the last positions "
                             "are where the tunnel dies")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--direct", action="store_true",
                        help="no pool. Useful for checking the list itself, "
                             "useless for deciding a warm-up")
    parser.add_argument("--dry-run", action="store_true")
    return parser, parser.parse_args()


def chosen(args) -> list:
    if not args.only:
        return list(SURFACES)
    wanted = {n.strip() for n in args.only.split(",") if n.strip()}
    picked = [(n, u) for n, u in SURFACES if n in wanted]
    unknown = wanted - {n for n, _ in SURFACES}
    if unknown:
        raise SystemExit(f"unknown surfaces {sorted(unknown)}, "
                         f"known: {[n for n, _ in SURFACES]}")
    return picked


def visit(page, url: str, timeout_ms: int, counter: dict,
          settle_s: float) -> dict:
    """One navigation, with the bytes it cost attributed to it.

    `domcontentloaded` rather than `load` for the same reason the ladder uses
    it: a warm-up page is a visit, and waiting for every lazy image would both
    change what is being measured and spend bytes no session would spend.

    **Bytes are read as a delta on the session's own counter, not counted
    here.** The first version of this function attached a second `response`
    listener and summed `response.body()`. It reported 0.00 MB for every
    surface including the two that arrived, and the reason is worth keeping:
    `new_page` has already installed `blocking.install_counter` on the same
    event, listeners fire in registration order, and an exception in an earlier
    one propagates out of the emit instead of being contained - so the later
    listener is never reached. The 0.00 was not a page that cost nothing, it
    was a listener that never ran. Reusing the counter removes the second
    listener entirely and gives the same byte metric the rest of the harness
    quotes, with the same documented floor: `Content-Length` only, so a chunked
    response - which is how the large HTML document is usually served - is
    missed. Lower bound, and named as one in the table.

    The settle window is inside the count on purpose. `domcontentloaded`
    returns while subresources are still arriving, and reading the counter at
    that moment charges a page's tail to whichever page is visited next.
    """
    before = counter.get("bytes", 0)
    started = time.perf_counter()
    error = None
    status = None
    final = None
    try:
        response = page.goto(url, wait_until="domcontentloaded",
                             timeout=timeout_ms)
        status = response.status if response else None
        final = page.url
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}"
    elapsed = round((time.perf_counter() - started) * 1000)
    # Settled even after a failure: a navigation that timed out still spent
    # whatever it spent, and dropping those bytes would flatter the surface
    # that failed most expensively.
    time.sleep(settle_s)
    return {"elapsed_ms": elapsed, "status": status, "final_url": final,
            "error": error, "bytes": counter.get("bytes", 0) - before}


def report(results: dict, surfaces: list) -> None:
    """One row per surface, sorted by the thing that disqualifies a warm-up.

    Takes the surface list as well as the results so that a surface no pass
    ever reached is a visible `0/0` rather than a missing row. A pass now stops
    at the navigation that loses the tunnel, so an unlucky seed can leave a
    surface with no attempts at all, and a table that simply omitted it would
    be reporting silence as absence of a problem.
    """
    print("\n" + "=" * 96)
    print(f"{'surface':<12}{'host':<28}{'arrived':>9}{'median ms':>11}"
          f"{'median MB':>11}  {'note'}")
    print("-" * 96)
    rows = []
    for name, url in surfaces:
        seen = results.get((name, url), [])
        total = len(seen)
        good = [r for r in seen if r["error"] is None and "/sorry/"
                not in (r.get("final_url") or "")]
        sorry = sum(1 for r in seen
                    if "/sorry/" in (r.get("final_url") or ""))
        ms = statistics.median([r["elapsed_ms"] for r in good]) if good else 0
        mb = (statistics.median([r["bytes"] for r in good]) / 1024 / 1024
              if good else 0)
        note = ""
        if not total:
            note = "  never reached: every pass lost its tunnel first"
        elif sorry:
            note = f"  {sorry} redirected to /sorry/"
        elif len(good) < total:
            # Printed whole, and every distinct error rather than the first.
            #
            # This column used to be `first[:44]`. The note is the last thing
            # on the line, so nothing was bought by cutting it, and the 44th
            # character lands inside the part that identifies the failure:
            # 2026-08-27 it printed `net::ERR_SSL_PROTOCOL_ERRO`, and both
            # timeouts in the same run came out as the same stub, which reads
            # as one failure mode when it was two surfaces failing separately.
            # Showing only the first was the second half of the bug - a
            # surface that times out twice and drops TLS once is not the
            # surface `first` describes.
            kinds = Counter(terse_error(r["error"]) for r in seen if r["error"])
            note = "  " + "; ".join(f"{msg} x{n}" if n > 1 else msg
                                    for msg, n in kinds.most_common())
        rows.append((len(good) / total if total else 0, mb, name, url,
                     len(good), total, ms, note))
    # Sorted by arrival first and cost second, which is the order a warm-up
    # page is disqualified in: one that does not arrive is not cheap, it is
    # absent.
    for _share, mb, name, url, good, total, ms, note in sorted(
            rows, key=lambda r: (-r[0], r[1])):
        host = urlparse(url).netloc
        print(f"{name:<12}{host:<28}{good:>4}/{total:<4}{ms:>11.0f}"
              f"{mb:>11.2f}{note}")

    print("\nreading this table")
    print("  arrived  : navigations that completed and did not land on "
          "/sorry/, over the navigations that were attempted. Anything under "
          "all of them is a surface that will silently short the rung it is "
          "put in")
    print("  the denominator moves: a pass stops at the navigation that loses "
          "the tunnel, so surfaces it never reached are out of that pass "
          "rather than failing it. Order is shuffled per pass so that being "
          "last is not a property of a surface")
    print("  median MB: what one visit to this page costs, charged on every "
          "identity of every rung that contains it. Bytes counted at the "
          "response listener, so it is a lower bound")
    print("  a warm-up page needs both columns. Reliable and expensive is a "
          "tax on every row; cheap and unreliable is a mislabelled rung")
    print("\nsearch is not in this table on purpose. It is the target, and a "
          "warm-up drawn from it would be measuring itself")


def main() -> int:
    _parser, args = parse_args()
    surfaces = chosen(args)
    seed = random.randrange(2 ** 32) if args.seed is None else args.seed
    rng = random.Random(seed)

    print(f"engine    : {args.engine}")
    print(f"surfaces  : {len(surfaces)} - "
          f"{', '.join(n for n, _ in surfaces)}")
    print(f"passes    : {args.passes} exits, each visiting the surfaces until "
          f"it finishes them or loses the tunnel")
    print(f"arm       : {'direct' if args.direct else f'pool, country={args.country}'}")
    print(f"navigations: {len(surfaces) * args.passes}")
    print(f"order     : {'as declared' if args.in_order else 'shuffled'}, "
          f"seed {seed}")
    if args.dry_run:
        print("\ndry run: nothing was sent")
        return 0

    if not args.direct:
        try:
            seen = gateway.identify()
        except Exception as exc:
            print(f"\nrefused: the gateway could not be reached: "
                  f"{type(exc).__name__}: {exc}")
            return 2
        if not seen.get("exit_ip"):
            print(f"\nrefused: the gateway answered without an exit address, "
                  f"so the pool is not usable right now: {seen}")
            return 2
        print(f"preflight : pool answers, exit {seen['exit_ip']}")

    sink = JsonlSink("surfaces")
    results = defaultdict(list)
    try:
        for index in range(args.passes):
            # A fresh sticky identity per pass, the same way the ladder draws
            # one per identity. The whole list rides on it, which is why the
            # order is shuffled: in a fixed order the exit's own decay is
            # charged to whichever surfaces are declared last, every pass, and
            # it reads as those surfaces being unreliable. See TRANSPORT_ERRORS
            # for the run that showed it and by how much.
            order = list(surfaces)
            if not args.in_order:
                rng.shuffle(order)
            params = ({} if args.direct
                      else proxy.session_params(f"gs{uuid.uuid4().hex[:8]}",
                                                country=args.country))
            print(f"\n--- pass {index + 1} of {args.passes}")
            options = {"direct": args.direct, "params": params,
                       "preset": "none", "headless": args.headless,
                       "humanize": False}
            try:
                with engines.session(args.engine, **options) as active:
                    # `new_page` takes the byte counter it will write into, not
                    # a page index. Passing the pass number here is what broke
                    # the first run of this script: `blocking.install_counter`
                    # closed over an `int`, every response event raised
                    # `AttributeError` inside the emit, and the error surfaced
                    # on whatever navigation happened to be in flight - so the
                    # failures read as `Page.goto` errors against Google rather
                    # than as our own. Two surfaces reported ok before the
                    # context died and the rest reported `TargetClosedError`,
                    # which is a shape worth recognising: a crash in a listener
                    # is indistinguishable from a hostile target unless you
                    # read the exception type.
                    counter = {}
                    page = active.new_page(counter)
                    for position, (name, url) in enumerate(order):
                        seen = visit(page, url, args.timeout, counter,
                                     args.dwell)
                        # Written before the transport check, so the raw file
                        # keeps the failure that ends the pass. `position` is
                        # here because the confound this script had was only
                        # findable by reconstructing it from the console.
                        sink.write({"pass": index, "surface": name, "url": url,
                                    "position": position, "seed": seed,
                                    "engine": args.engine,
                                    "country": None if args.direct
                                    else args.country, **seen})
                        if lost_the_tunnel(seen):
                            unreached = len(order) - position - 1
                            print(f"    -- pass {index + 1} lost its tunnel at "
                                  f"position {position + 1} of {len(order)}, "
                                  f"on {name}. That surface and the "
                                  f"{unreached} after it are unreached rather "
                                  f"than refused, and are counted as neither")
                            break
                        results[(name, url)].append(seen)
                        # `/sorry/` is the one refusal this survey can see, and
                        # until 2026-08-27 it reached the table but not this
                        # line: the mark was read off `error` alone, so a
                        # blocked surface printed `ok` and only disagreed with
                        # itself in the summary. `shopping` did exactly that.
                        if seen["error"]:
                            mark, why = "FAIL", seen["error"]
                        elif "/sorry/" in (seen.get("final_url") or ""):
                            mark, why = "SORRY", "redirected to /sorry/"
                        else:
                            mark, why = "ok", ""
                        print(f"    {mark:<5} {name:<12}"
                              f"{seen['elapsed_ms']:>7} ms"
                              f"{seen['bytes'] / 1024 / 1024:>8.2f} MB  "
                              f"{why[:48]}")
            except Exception as exc:
                # A pass that dies takes its exit with it and the surfaces it
                # never reached record nothing, rather than recording a failure
                # they did not have.
                print(f"    pass {index + 1} ended early: "
                      f"{type(exc).__name__}: {exc}")
    except KeyboardInterrupt:
        # Caught rather than left to propagate so the interrupted run still
        # prints its table and names its file. The first run of this script was
        # interrupted and the traceback buried both.
        print("\ninterrupted - the table below covers only what was reached")
    finally:
        if results:
            report(results, surfaces)
        print(f"\nraw rows: {sink.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
