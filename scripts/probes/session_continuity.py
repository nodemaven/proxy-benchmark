"""Does a batch actually behave as one identity, measured offline.

`--batch 10` is documented as ten queries through one browser: one identity
doing ten searches, which is what a scraper looks like and is the unit every
pass rate is quoted in. That claim is about state, not about process lifetime. A
browser that opens a fresh context per query keeps its fingerprint and its exit
address across the batch and throws away everything a site set on it, so the
tenth query arrives from an identity that has been searching for five minutes
and has never accepted a cookie. That is not the naive scraper we meant to
measure and it is not a human either.

Measured 2026-08-11: Camoufox was doing exactly that, alone among the four, so
its column was answering a slightly different question from the others. This
probe is what caught it and is what stops it coming back, because the test suite
is browser-free by design and cannot see this.

Everything happens on `about:blank` and a cookie set through the driver. No
target is contacted, no gateway is used, no credentials are needed.

Usage:
    python scripts/probes/session_continuity.py
    python scripts/probes/session_continuity.py --engines chromium,camoufox
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nmbench import engines

# Read by `python -m nmbench` when it lists what a command costs.
SENDS_REQUESTS = False

COOKIE = {"name": "nmbench_continuity", "value": "1",
          "url": "https://example.com/"}


def inspect(name: str) -> dict:
    """Two pages from one session, and whether state crosses between them."""
    result = {"engine": name, "shared_context": None, "cookie_carries": None,
              "note": None}
    problem = engines.REGISTRY[name].check()
    if problem:
        result["note"] = problem
        return result

    try:
        # Direct: this opens no connection, and a proxy config would demand
        # credentials for a probe that is supposed to need none.
        with engines.session(name, direct=True, preset="none") as active:
            # By capability, never by engine name. The no-browser control holds
            # its jar in a requests.Session instead of a browser context, and
            # the question asked of it is the same one.
            if not hasattr(active, "new_page"):
                jar = getattr(getattr(active, "client", None), "cookies", None)
                result["note"] = ("not a browser: one requests.Session holds "
                                  "the jar for the batch")
                result["shared_context"] = jar is not None
                result["cookie_carries"] = jar is not None
                return result

            first = active.new_page()
            second = active.new_page()
            try:
                result["shared_context"] = first.context is second.context
                first.context.add_cookies([COOKIE])
                result["cookie_carries"] = any(
                    c["name"] == COOKIE["name"] for c in second.context.cookies())
            finally:
                first.close()
                second.close()
    except Exception as exc:
        result["note"] = f"{type(exc).__name__}: {exc}"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engines", default=",".join(engines.names()),
                        help=f"comma separated, known: {engines.names()}")
    args = parser.parse_args()

    names = [n.strip() for n in args.engines.split(",") if n.strip()]
    unknown = [n for n in names if n not in engines.REGISTRY]
    if unknown:
        parser.error(f"unknown engines {unknown}, known: {engines.names()}")

    rows = [inspect(name) for name in names]

    print(f"\n{'engine':<14}{'one context':<14}{'cookie carries':<16}note")
    for row in rows:
        note = row["note"] or ""
        print(f"{row['engine']:<14}{row['shared_context']!s:<14}"
              f"{row['cookie_carries']!s:<16}{note[:60]}")

    # An engine that could not start is not a failure of this probe: the point
    # is disagreement between engines that did run, because that is what makes
    # their columns incomparable.
    ran = [r for r in rows if r["cookie_carries"] is not None]
    broken = [r["engine"] for r in ran if not r["cookie_carries"]]
    print()
    if broken:
        print(f"{broken} drop their cookie jar between queries while the rest "
              f"keep one. Their batches are not the same experiment as the "
              f"others', and any comparison across that line is unattributable.")
        return 1
    if ran:
        print(f"{len(ran)} engines checked, all carry state across a batch, so "
              f"a batch is one identity for every column.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
