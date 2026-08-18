"""Save one response body per target so a verdict can be checked against reality.

A verdict function is a claim about markup, and markup changes without notice.
When a run reports a 43 KB "block", the only way to tell a real block from a
misread is to look at the bytes. Bodies land in data/samples/, which is not
committed: they are large, they carry the exit address in embedded links, and
they are a debugging aid rather than evidence.

Usage:
    python scripts/probes/capture_sample.py google_serp
    python scripts/probes/capture_sample.py google_serp bing_serp ddg_serp
    python scripts/probes/capture_sample.py google_serp --direct
"""
import argparse
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import requests

from nmbench import engines, proxy
from nmbench.targets import FINGERPRINT_MARKERS, TARGETS

SAMPLES = Path(__file__).resolve().parents[2] / "data" / "samples"


def capture(name: str, direct: bool, query: str = None) -> None:
    target = TARGETS[name]
    # The target's own first query by default. A shop asked "tls fingerprint
    # ja4" answers with an empty shelf, and this probe exists to look at the
    # page a working request returns.
    query = query or target.queries[0]
    url = target.url(query)
    params = {} if direct else proxy.session_params(
        f"smpl{uuid.uuid4().hex[:8]}", country="us")

    if direct:
        proxies = None
    else:
        purl = proxy.proxy_url(**params)
        proxies = {"http": purl, "https": purl}

    resp = requests.get(url, proxies=proxies, headers=engines.BROWSER_HEADERS,
                        timeout=45)
    html = resp.text

    SAMPLES.mkdir(parents=True, exist_ok=True)
    tag = "direct" if direct else "proxy"
    path = SAMPLES / f"{name}_{tag}.html"
    path.write_text(html, encoding="utf-8")

    print(f"\n=== {name} ({tag}) ===")
    print(f"  status {resp.status_code}   final url {resp.url[:90]}")
    judgement = target.judge(resp.url, "", html)
    print(f"  length {len(html)}   verdict now: {judgement.verdict} "
          f"({judgement.reason})")
    print(f"  saved  {path}")

    title = ""
    if "<title>" in html:
        title = html.split("<title>", 1)[1].split("</title>", 1)[0][:120]
    print(f"  title  {title!r}")

    # The same list the rows are fingerprinted with, so what is printed here is
    # what a verdict would have had to work from. A second copy of the markers
    # went stale the moment a target was added.
    print("  markers present:")
    for marker in FINGERPRINT_MARKERS:
        count = html.lower().count(marker.lower())
        if count:
            print(f"    {marker:<20} x{count}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # Required, with no default. This probe sends a real request to a real
    # target, and a bare invocation that quietly picks one is how `--help` came
    # to spend traffic against Google.
    parser.add_argument("targets", nargs="+", choices=sorted(TARGETS),
                        help="which targets to capture, one request each")
    parser.add_argument("--direct", action="store_true",
                        help="bypass the gateway, leaving from this machine's "
                             "own address")
    parser.add_argument("--query", default=None,
                        help="override the query. The default is the target's "
                             "own first query, which is a product for a shop "
                             "and a search phrase for a search engine")
    parser.add_argument("--pause", type=float, default=5.0,
                        help="seconds between targets")
    args = parser.parse_args()

    for index, name in enumerate(args.targets):
        if index:
            time.sleep(args.pause)
        capture(name, args.direct, args.query)
    return 0


if __name__ == "__main__":
    sys.exit(main())
