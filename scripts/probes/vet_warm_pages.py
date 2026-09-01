"""Which third-party pages report the exit to Google without a Google navigation.

2026-09-01. `GoogleSerp.warm_ladder`'s L3 opens two non-Google pages before its
four Google ones, and both were vetted on 2026-08-26 by the same criterion this
script automates: the page must carry a Google-owned tag in its body, so that
loading it tells Google's infrastructure the exit exists without the browser
ever navigating to a Google host.

The rung this is being run for is N3 - L3's depth with none of L3's Google
pages - which needs six such pages and currently has two. N3 is what decides
whether L3's yield comes from Google having seen the exit or merely from the
browser having lived through six navigations, and those two are not
distinguishable in anything measured so far.

Two controls, and the run refuses to report without them:

  positive  theverge and wikihow passed this check on 2026-08-26. If they fail
            here the check is broken, not the pages.
  negative  en.wikipedia.org runs no third-party analytics. If it passes, the
            marker list is matching something other than a Google tag - a
            mention in prose, a CSP header echoed into the body, an
            interstitial - and every other row is then unreadable.

A candidate that 403s is not a candidate that lacks the tag. The two are
reported separately because they are different facts: one says the page will
not serve a plain client, the other says it serves and carries nothing.

This touches live hosts, so it does not run from the workstation behind the
Happ gateway - a 403 there could be the gateway's exit rather than the site.
Run it on the VPS.
"""
import argparse
import json
import pathlib
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# Substrings that mean a Google property is told about this load. Kept as the
# 2026-08-26 check used them, plus the two tag-manager spellings, which are the
# usual way a site loads the others.
MARKERS = (
    "google-analytics.com",
    "googletagmanager.com",
    "doubleclick.net",
    "googlesyndication.com",
    "googletagservices.com",
    "gstatic.com/",
)

# A browser-ish header set. Not an impersonation - the point is to be served the
# ordinary page rather than a bot interstitial, and the warm-up will fetch these
# with a real browser anyway.
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/151.0.0.0 Safari/537.36"),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
}

POSITIVE = (
    "https://www.theverge.com/",
    "https://www.wikihow.com/Main-Page",
)
NEGATIVE = ("https://en.wikipedia.org/wiki/Main_Page",)

CANDIDATES = (
    "https://arstechnica.com/",
    "https://www.howtogeek.com/",
    "https://www.wired.com/",
    "https://www.theguardian.com/international",
    "https://www.instructables.com/",
    "https://edition.cnn.com/",
    "https://www.imdb.com/",
    "https://www.weather.com/",
    "https://www.rottentomatoes.com/",
    "https://www.cnet.com/",
    "https://www.techradar.com/",
    "https://www.makeuseof.com/",
)


def fetch(url, timeout):
    """Return (status, body, error). Never raises - a dead host is a row."""
    import requests
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout,
                         allow_redirects=True)
        return r.status_code, r.text, None
    except Exception as exc:
        return None, "", f"{type(exc).__name__}: {exc}"


def hits(body):
    found = {}
    for m in MARKERS:
        n = len(re.findall(re.escape(m), body))
        if n:
            found[m] = n
    return found


def classify(status, body, error):
    if error:
        return "unreachable", {}
    if status != 200:
        return f"http {status}", {}
    found = hits(body)
    return ("tagged" if found else "no tag"), found


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--pause", type=float, default=1.5,
                    help="between fetches, so twelve hosts are not hit at once")
    ap.add_argument("--extra", default="",
                    help="comma separated additional candidate URLs")
    args = ap.parse_args()

    extra = [u.strip() for u in args.extra.split(",") if u.strip()]
    plan = ([(u, "positive control") for u in POSITIVE]
            + [(u, "negative control") for u in NEGATIVE]
            + [(u, "candidate") for u in CANDIDATES + tuple(extra)])

    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out = ROOT / "data" / "runs" / f"warmvet_{run_id}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)

    print(f"{'url':<46}{'role':<18}{'verdict':<12}markers")
    print("-" * 100)
    rows = []
    for url, role in plan:
        status, body, error = fetch(url, args.timeout)
        verdict, found = classify(status, body, error)
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "run_id": run_id, "url": url, "role": role, "status": status,
               "bytes": len(body), "verdict": verdict, "markers": found,
               "error": error}
        rows.append(row)
        names = ", ".join(f"{k.split('.')[0]}x{v}" for k, v in found.items())
        print(f"{url[:44]:<46}{role:<18}{verdict:<12}{names or '-'}")
        time.sleep(args.pause)

    with open(out, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    by_role = {}
    for r in rows:
        by_role.setdefault(r["role"], []).append(r)

    pos_ok = all(r["verdict"] == "tagged" for r in by_role["positive control"])
    neg_ok = all(r["verdict"] != "tagged" for r in by_role["negative control"])

    print()
    print(f"positive controls tagged : {pos_ok}")
    print(f"negative control clean   : {neg_ok}")
    if not (pos_ok and neg_ok):
        print()
        print("INCONCLUSIVE. The controls disagree with what they were chosen "
              "to show, so this run reports on the check and not on the pages. "
              "Nothing here may be added to a warm ladder.")
        print(f"raw rows: {out}")
        return 1

    passed = [r["url"] for r in by_role["candidate"] if r["verdict"] == "tagged"]
    print()
    print(f"usable candidates: {len(passed)} (N3 needs 4 beyond the two "
          f"already in L3)")
    for u in passed:
        print(f"  {u}")
    refused = [(r["url"], r["verdict"]) for r in by_role["candidate"]
               if r["verdict"] != "tagged"]
    if refused:
        print("not usable, and the two reasons are different facts:")
        for u, v in refused:
            print(f"  {v:<14} {u}")
    print(f"\nraw rows: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
