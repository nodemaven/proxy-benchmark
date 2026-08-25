"""Locate the layer at which Google SERP rejects a residential exit.

Background: Google returned 429 on 6 of 6 requests through Camoufox and a US
residential exit, with no warm-up. 429 is rate limiting, not a fingerprint
verdict, which points at the address rather than the browser - a hypothesis,
not a finding.

Each step moves exactly one variable:

    1  browser removed        camoufox -> http, same country, same sid strategy
    2  exit address varied    fresh sids in us, then five other countries
    3  target varied          same request shape against duckduckgo and bing
    4  network removed        no proxy at all, this machine's own address
    5  session shape varied   one query per session vs several in one session

Every attempt writes one JSONL row including the parameter set that produced it.
Requests are spaced by at least 5 seconds and each cell stops after 5 consecutive
failures. There is no sid rotation on failure anywhere in this file: rotating a
sid to escape a block heats the shared pool for every other customer.

Steps run independently so the proxy only has to be reachable while it is needed.
Each invocation writes its own file; scripts/analysis/analyze_429.py reads all of
them.

Usage:
    python scripts/probes/google_429.py --preflight     # is the proxy reachable at all
    python scripts/probes/google_429.py --steps 1
    python scripts/probes/google_429.py --steps 1,2,3
    python scripts/probes/google_429.py --steps 4       # no proxy, exposes local address
    python scripts/probes/google_429.py --steps 5       # camoufox, slowest
"""
import argparse
import sys
import time
import uuid
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nmbench.console import tolerate_unencodable_output

# Ahead of the engines: zendriver installs colorama's stdout wrapper on import,
# and the wrapper has no reconfigure of its own.
tolerate_unencodable_output()

from nmbench import engines, gateway, proxy
from nmbench.breaker import CircuitBreaker
from nmbench.sink import JsonlSink
from nmbench.targets import SEARCH_QUERIES, TARGETS

QUERIES = SEARCH_QUERIES[:5]
PAUSE = 5.0
OTHER_COUNTRIES = ["gb", "de", "fr", "ca", "jp"]

REGISTRY = gateway.ExitRegistry()
_identity_cache = {}


def sid() -> str:
    return f"g429{uuid.uuid4().hex[:8]}"


def session(held: str = None, **params) -> dict:
    """Gateway parameters for one sticky session, fresh unless an id is given.

    The session key is whatever the provider calls it rather than `sid`, which
    matters here more than the spelling suggests: every step of this probe is a
    statement about holding one exit or drawing a new one, so a key the gateway
    does not recognise turns both arms into the same experiment and neither row
    says so.
    """
    return proxy.session_params(held or sid(), **params)


_direct_identity = {}


def identify_direct() -> dict:
    """The local address, asked once and masked like every other exit."""
    if not _direct_identity:
        found = gateway.identify_direct()
        found.update(REGISTRY.record(found.get("exit_ip")))
        _direct_identity.update(found)
        print(f"    local network -> {found.get('exit_ip')} {found.get('org') or ''} "
              f"[{found.get('exit_label')}]")
    return _direct_identity


def identify(params: dict) -> dict:
    """Exit address for a parameter set. Cached: the sticky key is the whole
    recognised parameter set, so identical params mean the same session and
    asking twice would only spend traffic."""
    key = tuple(sorted(params.items()))
    if key not in _identity_cache:
        found = gateway.identify(**params)
        found.update(REGISTRY.record(found.get("exit_ip")))
        _identity_cache[key] = found
        if found.get("exit_ip"):
            print(f"    session -> {found['exit_ip']}  {found.get('org') or ''} "
                  f"[{found['exit_label']}, via {found['source']}]")
        else:
            print(f"    session -> exit address unavailable: {found.get('error')}")
    return _identity_cache[key]


def report(row: dict) -> None:
    marker = "" if row["verdict"] == "ok" else "  <-"
    print(f"    {row['query'][:32]:<32} "
          f"{row['status'] or '-'!s:<5} "
          f"{row['verdict']:<8} "
          f"{row.get('exit_label') or '-'!s:<8} "
          f"{row['elapsed_ms']:>6} ms{marker}")
    if row["error"]:
        print(f"      -> {row['error'][:110]}")


def run_cell(sink, cell: str, step: str, target, attempts, direct=False):
    """attempts: list of (query, params). One row per attempt, breaker per cell."""
    print(f"\n  [{cell}]")
    breaker = CircuitBreaker(cell)
    rows = []

    for query, params in attempts:
        if breaker.tripped:
            sink.write({"step": step, "cell": cell, "query": query,
                        "params": params, "verdict": "skipped",
                        "note": "cell stopped by circuit breaker, not attempted"})
            print(f"    {query[:32]:<32} skipped, cell stopped")
            continue

        if direct:
            identity = identify_direct()
        else:
            identity = identify(params)

        row = engines.fetch_http(target, query, direct=direct,
                                 headers=engines.BROWSER_HEADERS, **params)
        row["step"] = step
        row["cell"] = cell
        row["target"] = target.name
        row["exit_prefix"] = identity.get("exit_prefix")
        row["exit_label"] = identity.get("exit_label")
        row["exit_org"] = identity.get("org")
        row["exit_source"] = identity.get("source")

        rows.append(row)
        sink.write(row)
        report(row)

        wait = breaker.record(row["verdict"])
        if breaker.tripped:
            print(f"    cell stopped: {breaker.limit} consecutive failures")
            sink.write({"step": step, "cell": cell, "verdict": "cell_stopped",
                        "note": f"{breaker.limit} consecutive failures"})
            break
        time.sleep(wait)

    return rows


def step1(sink):
    print("\n=== step 1 - remove the browser ===")
    print("  same country, one sticky session, camoufox replaced by plain http")
    held = sid()
    attempts = [(q, session(held, country="us")) for q in QUERIES]
    return run_cell(sink, "step1/google/http/us/one-sid", "1",
                    TARGETS["google_serp"], attempts)


def step2(sink):
    print("\n=== step 2 - vary the exit address ===")
    rows = []

    attempts = [(q, session(country="us")) for q in QUERIES]
    rows += run_cell(sink, "step2/google/http/us/fresh-sid-each", "2",
                     TARGETS["google_serp"], attempts)

    time.sleep(PAUSE)
    # strict=False on purpose: the two lists are allowed to differ in length,
    # and the shorter one deciding how many attempts happen is the intended
    # behaviour. Pairing off whatever exists spends less traffic, never more.
    attempts = [(q, session(country=c))
                for q, c in zip(QUERIES, OTHER_COUNTRIES, strict=False)]
    rows += run_cell(sink, "step2/google/http/non-us/fresh-sid-each", "2",
                     TARGETS["google_serp"], attempts)
    return rows


def step3(sink):
    print("\n=== step 3 - control target ===")
    print("  same proxy, same engine, same queries, different search engine")
    rows = []
    for name in ("ddg_serp", "bing_serp"):
        held = sid()
        attempts = [(q, session(held, country="us")) for q in QUERIES]
        rows += run_cell(sink, f"step3/{name}/http/us/one-sid", "3",
                         TARGETS[name], attempts)
        time.sleep(PAUSE)
    return rows


def step4(sink):
    print("\n=== step 4 - control network ===")
    print("  no proxy. this exposes the local address to google, 3 requests only")
    attempts = [(q, {}) for q in QUERIES[:3]]
    return run_cell(sink, "step4/google/http/direct", "4",
                    TARGETS["google_serp"], attempts, direct=True)


def step5(sink):
    print("\n=== step 5 - session shape ===")
    print("  camoufox. does the rejection depend on requests per session")
    serp = TARGETS["google_serp"]
    rows = []

    cell = "step5/google/camoufox/one-session-five-queries"
    print(f"\n  [{cell}]")
    params = session(country="us")
    identity = identify(params)
    batch = engines.fetch_camoufox(serp, QUERIES, preset="light", **params)
    for row in batch:
        row.update({"step": "5", "cell": cell, "target": serp.name,
                    "exit_prefix": identity.get("exit_prefix"),
                    "exit_label": identity.get("exit_label"),
                    "exit_org": identity.get("org")})
        sink.write(row)
        report(row)
    rows += batch

    cell = "step5/google/camoufox/five-sessions-one-query"
    print(f"\n  [{cell}]")
    breaker = CircuitBreaker(cell)
    for query in QUERIES:
        if breaker.tripped:
            sink.write({"step": "5", "cell": cell, "query": query,
                        "verdict": "skipped",
                        "note": "cell stopped by circuit breaker, not attempted"})
            continue
        params = session(country="us")
        identity = identify(params)
        batch = engines.fetch_camoufox(serp, [query], preset="light", **params)
        for row in batch:
            row.update({"step": "5", "cell": cell, "target": serp.name,
                        "exit_prefix": identity.get("exit_prefix"),
                        "exit_label": identity.get("exit_label"),
                        "exit_org": identity.get("org")})
            sink.write(row)
            report(row)
        rows += batch

        wait = breaker.record(batch[-1]["verdict"])
        if breaker.tripped:
            print(f"    cell stopped: {breaker.limit} consecutive failures")
            sink.write({"step": "5", "cell": cell, "verdict": "cell_stopped",
                        "note": f"{breaker.limit} consecutive failures"})
            break
        time.sleep(wait)

    return rows


def preflight(attempts: int = 3) -> bool:
    """Is the tunnel usable at all. A dead tunnel records as a target failure,
    which is the most expensive kind of wrong answer this harness can produce.

    The gateway drops a share of connections on its own, so one refusal proves
    nothing. Retried against an echo service, not against the target, and on one
    unchanging session: nothing here rotates a sid to escape a rejection.
    """
    print(f"preflight: up to {attempts} requests through the proxy to an echo service")
    params = session(country="us")
    failures = []

    for attempt in range(1, attempts + 1):
        row = engines.fetch_http(TARGETS["ipinfo"], "json",
                                 headers=engines.BROWSER_HEADERS, **params)
        if row["verdict"] == "ok":
            found = gateway.identify(**params)
            note = f" after {len(failures)} dropped" if failures else ""
            print(f"  proxy OK, {row['elapsed_ms']} ms{note}, "
                  f"exit {found.get('exit_ip')} {found.get('org') or ''} "
                  f"(via {found.get('source')})")
            return True

        failures.append(row["error"] or row["verdict"])
        print(f"  attempt {attempt}/{attempts} failed: {str(failures[-1])[:90]}")
        if attempt < attempts:
            time.sleep(PAUSE)

    print(f"  proxy FAILED {len(failures)} times, the tunnel is not usable.")
    print("  Every step below this would record gateway failures as target blocks.")
    return False


STEPS = {"1": step1, "2": step2, "3": step3, "4": step4, "5": step5}


def summarise(all_rows) -> None:
    print("\n" + "=" * 92)
    print(f"{'cell':<50}{'n':>4}  verdicts")
    print("-" * 92)
    by_cell = {}
    for row in all_rows:
        by_cell.setdefault(row["cell"], []).append(row)
    for cell, rows in by_cell.items():
        verdicts = Counter(r["verdict"] for r in rows)
        print(f"{cell:<50}{len(rows):>4}  {dict(verdicts)}")

    total_bytes = sum(r.get("bytes", 0) for r in all_rows)
    print(f"\nattempts: {len(all_rows)}")
    print(f"locally counted bytes: {total_bytes / 1024:.1f} KB "
          f"(lower bound: excludes headers and TLS overhead)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--steps", default="",
                        help="comma separated, e.g. 1,2,3. Default: 1,2,3,4")
    parser.add_argument("--preflight", action="store_true",
                        help="check the proxy and exit, run nothing")
    parser.add_argument("--label", default="",
                        help="network condition to stamp on every row, "
                             "e.g. vpn-on. The direct control means something "
                             "different depending on what the local network is.")
    args = parser.parse_args()

    if args.preflight:
        sys.exit(0 if preflight() else 1)

    wanted = [s.strip() for s in (args.steps or "1,2,3,4").split(",") if s.strip()]
    unknown = [s for s in wanted if s not in STEPS]
    if unknown:
        parser.error(f"unknown steps {unknown}, known: {sorted(STEPS)}")

    needs_proxy = [s for s in wanted if s != "4"]
    if needs_proxy and not preflight():
        sys.exit(1)

    sink = JsonlSink("google_429")
    if args.label:
        sink_write = sink.write

        def write(row):
            row.setdefault("network_note", args.label)
            sink_write(row)

        sink.write = write

    print(f"\nsteps: {','.join(wanted)}")
    print(f"network: {args.label or 'not stated'}")
    print(f"raw rows -> {sink.path}")

    all_rows = []
    for name in wanted:
        all_rows += STEPS[name](sink)

    summarise(all_rows)
    print(f"\nraw rows: {sink.path}")


if __name__ == "__main__":
    main()
