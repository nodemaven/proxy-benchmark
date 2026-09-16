"""Does the sticky session key on the parsed parameter set, or on the username?

`data/providers/nodemaven.toml` records that the session key is "the whole
recognised parameter set, not `sid` alone". If that is literally a *set*, then
reordering the parameters changes the username string and not the session, and
two callers who asked for the same thing land on one exit. If the gateway
instead keys on the raw username it was handed, they land on two, and neither
caller has done anything wrong.

Nobody has asked. The separator probe of 2026-08-20 does not answer it - all
three of its arms held the same parameter order - so the sentence in the TOML
is carrying more weight than the measurement behind it.

It is being asked now because of what is about to be frozen. The Python SDK
emits parameters in the order the caller wrote them, and the vendor's own proxy
generator emits a canonical order instead (country, region, city, isp, sid,
filter, speed, norotate, read 2026-08-21). Those agree only while the caller
happens to type the canonical order. The golden vectors will pin one of the two
behaviours for four language SDKs, and if the gateway keys on the string then
pinning call order means a Go developer and a Python developer with identical
configuration draw different exits.

    arm         username tail                        keyed on the set   on the string
    canonical   country-any-sid-S-filter-medium      session S/set      session A
    shuffled    filter-medium-sid-S-country-any      session S/set      session B
    control     country-any-sid-T-filter-medium      session T/set      session C

So `canonical` and `shuffled` land on one exit if and only if the gateway
parsed them into the same key. `control` differs from both under either
reading and is not part of the discrimination: it is there because two arms
agreeing proves nothing unless a third is seen to disagree in the same window.
Without it, "both arms gave one exit" reads as a result when it is a pool that
stopped rotating.

A non-200 on `shuffled` settles the question without any of this arithmetic and
is reported as its own answer: it would mean the gateway parses positionally
and refuses an order it did not expect, which is a stronger statement than
either branch below.

The exit address is read by one instrument on every arm, and by default that is
an echo request through the tunnel - around 330 bytes each, so a default run of
3 arms by 4 rounds costs 12 CONNECTs and about 4 kB, and no pool reputation.

Reading it off the CONNECT header instead would be cheaper and is not sound
here: only one of the back ends behind this name sends the header, which one
answers is decided by the username, and the arms differ by construction in
exactly that. `--source` picks the instrument and `attempt` states the whole
argument.

Two ways this comes back inconclusive rather than wrong, both reported as such:

- an arm draws more than one exit across its rounds, so the session did not
  hold and nothing can be read off two arms differing
- `control` lands on the same exit as `canonical`, so something other than the
  session key is choosing the exit and the run cannot be read

Usage:
    python scripts/probes/param_order.py
    python scripts/probes/param_order.py --rounds 5 --country de
"""
import argparse
import sys
import time
import uuid
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nmbench.console import tolerate_unencodable_output

tolerate_unencodable_output()

from nmbench import config, gateway, providers
from nmbench.sink import JsonlSink

# Held constant across the arms. `filter` is here rather than `ttl` because the
# TOML records that `filter` participates in the session key and `ttl` does
# not, so `ttl` would add a parameter to reorder that the gateway may not be
# reading at all.
FILTER = "medium"


def attempt(arm: str, params: dict, timeout: int, provider, registry,
            source: str = "echo") -> dict:
    """One CONNECT, and the exit address read by one named instrument.

    **The instrument is chosen and not taken as it comes, and that is the whole
    of this function.** Roughly half the replies carry `X-Proxy-Exit-IP` and the
    rest do not, and which half a reply lands in is not noise: measured
    2026-08-12 over 17 opened tunnels, every reply reading `Connection
    established` carried the header and every reply reading `OK` carried none,
    with no exception either way. So the source is decided by which back end
    answered, the back end is decided by the username, and the arms of this
    probe differ by construction in exactly that.

    Falling back per reply - CONNECT header when there is one, echo when there
    is not - therefore reads `canonical` off one instrument and `shuffled` off
    another whenever they land on different back ends. Two arms then differ
    because two instruments differ, which is what "the session key is the
    username string" is supposed to look like, and the run cannot tell the two
    apart. That was this function's behaviour until 2026-09-08.

    `echo` is the default because it is the only source available on every
    reply; the header is available on some. Both are recorded when both exist,
    so their agreement is measured for free rather than assumed - see the
    instrument check in `main`.
    """
    info = gateway.exit_ip(timeout=timeout, provider=provider, **params)

    row = dict(info)
    header_ip = info.get("exit_ip")
    row["header_ip"] = header_ip
    row["source"] = "connect-header" if header_ip else None

    # `echo` asks on every opened tunnel, so every arm is read the same way.
    # `auto` asks only when the header named nothing, which is the pre-2026-09-08
    # behaviour and is kept solely so the two can be compared on one account.
    wants_echo = info["status"] == 200 and (
        source == "echo" or (source == "auto" and not header_ip)
    )
    if wants_echo:
        seen = gateway.echo(provider=provider, **params)
        row["echo_ip"] = seen.get("exit_ip")
        row["exit_ip"] = seen.get("exit_ip")
        row["source"] = "echo" if seen.get("exit_ip") else None
        row["error"] = row["error"] or seen.get("error")

    row.update({
        "arm": arm,
        "opened": info["status"] == 200,
        # The order is the experiment, so the row records the sequence rather
        # than a mapping. A dict here would be read back by whatever JSON
        # loader the analysis uses, and not all of them preserve key order.
        "param_order": list(params.keys()),
        "params": {k: v for k, v in params.items()
                   if k != provider.session_param},
        "session": params.get(provider.session_param),
        "provider": provider.id,
        "provider_status": provider.status,
        "cell": f"param_order/{arm}",
    })
    # The full address stays in `row` for the comparison and the console;
    # JsonlSink reduces it to a /24 on the way to the committed file.
    row.update(registry.record(row.get("exit_ip")))
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=4,
                        help="times to visit each of the three arms")
    parser.add_argument("--country", default="de",
                        help="held constant across the arms. Any country does, "
                             "because no target is fetched and exit yield is "
                             "not what is being read")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--pause", type=float, default=3.0,
                        help="seconds between attempts. The repository's rule "
                             "for exploration is 3-5 s on a shared production "
                             "pool")
    parser.add_argument("--provider", default=None)
    parser.add_argument("--source", default="echo",
                        choices=("echo", "header", "auto"),
                        help="which instrument reads the exit address. `echo` "
                             "asks the target on every arm, which is the only "
                             "source available on every reply. `header` uses "
                             "the CONNECT header and loses the arms that do "
                             "not carry one. `auto` mixes them per reply and "
                             "is unsound here - see attempt()")
    args = parser.parse_args()

    try:
        provider = providers.load(args.provider)
        creds = config.credentials(provider)
    except (providers.ProviderError, config.MissingCredentials) as exc:
        parser.error(str(exc))

    session_param = provider.session_param
    if not session_param:
        parser.error(f"{provider.label} declares no session parameter, so there "
                     f"is no sticky session to read this off")
    for name in ("country", "filter"):
        if name not in provider.known_params:
            parser.error(f"{provider.label} does not recognise {name!r}, so this "
                         f"probe cannot build the arms it compares")

    # Fresh session ids, so a run cannot inherit an exit the gateway assigned to
    # this probe on another day. `held` and `other` differ only in the id.
    held = f"order{uuid.uuid4().hex[:8]}"
    other = f"order{uuid.uuid4().hex[:8]}"

    # Dict literals preserve insertion order and `**` unpacking carries it into
    # the username builder, which emits parameters in the order it receives
    # them. That is the entire mechanism of this probe, and it is the reason
    # `proxy.session_params` is not used here: it appends the session id last,
    # which is exactly one of the orders under test.
    arms = {
        "canonical": {"country": args.country, session_param: held,
                      "filter": FILTER},
        "shuffled": {"filter": FILTER, session_param: held,
                     "country": args.country},
        "control": {"country": args.country, session_param: other,
                    "filter": FILTER},
    }

    sink = JsonlSink("param_order")
    registry = gateway.ExitRegistry()

    line = gateway.identify_direct()
    if line["error"]:
        print(f"this machine cannot reach the internet directly: {line['error']}")
        raise SystemExit(1)
    print(f"own line: {line['exit_ip']}  {line.get('org') or '-'}")
    print(f"provider: {provider.label} ({provider.status}), {creds.gateway}")
    print(f"held:     country={args.country}, filter={FILTER}, "
          f"{session_param}={held} on two arms")
    for arm, params in arms.items():
        print(f"  {arm:<10} "
              + "-".join(f"{k}-{v}" for k, v in params.items()))
    print(f"raw rows -> {sink.path}\n")

    rows = []
    for round_index in range(args.rounds):
        for arm, params in arms.items():
            row = attempt(arm, params, args.timeout, provider, registry,
                          source=args.source)
            sink.write(row)
            rows.append(row)
            print(f"  {round_index + 1}.{arm:<10} "
                  f"{row['status'] or '-'!s:<5}{(row['reason'] or '-')[:22]:<24}"
                  f"{row['exit_label'] or '-':<9}"
                  f"{row['exit_prefix'] or (row['error'] or '')[:44]}")
            time.sleep(args.pause)

    print("\n" + "=" * 78)

    by_arm = defaultdict(list)
    for row in rows:
        by_arm[row["arm"]].append(row)

    # Which instrument read each arm. Printed before the verdict and not after,
    # because it decides whether the verdict may be read at all: an arm read by
    # a different instrument from the arm it is compared against can differ for
    # a reason that has nothing to do with parameter order.
    sources = {arm: Counter(r["source"] or "none" for r in by_arm[arm])
               for arm in arms}
    print("instrument per arm")
    for arm in arms:
        shown = ", ".join(f"{name}={n}"
                          for name, n in sources[arm].most_common())
        print(f"  {arm:<10} {shown}")
    named = {name for arm in arms for name in sources[arm] if name != "none"}
    if len(named) > 1:
        print("\nINCONCLUSIVE: the arms were not all read by the same "
              "instrument.")
        print("  Which source answers is decided by the back end and the back "
              "end by the username, so a difference between arms here is a "
              "difference between instruments and cannot be attributed to "
              "parameter order. Re-run with --source echo, which is available "
              "on every reply.")
        print(f"\nraw rows: {sink.path}")
        return

    # Free, and it is the one thing that says whether the two instruments are
    # interchangeable at all. Only rows where both were read can contribute, so
    # this is silent under --source header and under an account whose back end
    # never sends the header.
    both = [r for r in rows if r.get("header_ip") and r.get("echo_ip")]
    if both:
        agree = sum(1 for r in both if r["header_ip"] == r["echo_ip"])
        print(f"\ninstrument agreement: {agree}/{len(both)} replies where the "
              f"CONNECT header and the echo were both read named the same "
              f"address")
        if agree != len(both):
            print("  They disagree, so the header does not name the address "
                  "the target sees. That is a finding in its own right and it "
                  "invalidates every past run that mixed the two.")

    exits = {}
    for arm in arms:
        named = [r for r in by_arm[arm] if r["exit_ip"]]
        exits[arm] = {r["exit_ip"] for r in named}
        statuses = Counter(f"{r['status']} {r['reason']}" if r["status"]
                           else (r["error"] or "no reply") for r in by_arm[arm])
        print(f"{arm:<10} {len(named)}/{len(by_arm[arm])} exits named, "
              f"{len(exits[arm])} distinct")
        for text, count in statuses.most_common():
            print(f"           {count:>3}  {text[:60]}")

    print()
    refused = [r for r in by_arm["shuffled"] if r["status"] not in (200, None)]
    if refused:
        print("MEASURED: the gateway REFUSED the reordered arm outright, which "
              "settles this without the exit comparison.")
        print("  It is reading the username positionally rather than as a set "
              "of pairs, so parameter order is part of the request and not a "
              "presentation detail. The SDKs must emit the generator's "
              "canonical order, and the golden vectors have to carry it.")
        print(f"\nraw rows: {sink.path}")
        return

    missing = [arm for arm in arms if not exits[arm]]
    if missing:
        print(f"no exit address for {missing}, so nothing can be compared. "
              f"Re-run; the header is not guaranteed and the echo fallback can "
              f"fail on its own.")
        return
    unstable = [arm for arm in arms if len(exits[arm]) > 1]
    if unstable:
        print(f"{unstable} drew more than one exit, so the session did not hold "
              f"in this window and two arms differing proves nothing. Re-run, "
              f"and if it repeats the sticky session is the finding rather than "
              f"the parameter order.")
        return
    if exits["control"] == exits["canonical"]:
        print("the control landed on the same exit as the canonical arm, "
              "although it carries a different session id. Something other "
              "than the session key is choosing the exit here, so this run "
              "cannot be read either way.")
        return

    if exits["canonical"] == exits["shuffled"]:
        print("MEASURED: the session key is the parsed SET. Parameter order "
              "does not reach it.")
        print("  The same parameters in two orders are two usernames and one "
              "session on one exit, while a third id held its own. So the "
              "sentence in the TOML is now measured rather than assumed, and "
              "pinning call order in the golden vectors is safe: a Go caller "
              "and a Python caller writing the same configuration in different "
              "orders reach the same exit.")
    else:
        print("MEASURED: the session key is the USERNAME STRING. Parameter "
              "order is part of the identity.")
        print("  The same parameters in two orders are two sessions on two "
              "exits. The consequence is not a documentation note: the SDKs "
              "must sort into the generator's canonical order before building "
              "the username, and the golden vectors must pin the sorted form. "
              "Pinning call order instead would put two developers with "
              "identical configuration on different exits, silently, in every "
              "language.")
        print("  The TOML sentence 'the session key is the whole recognised "
              "parameter set' is wrong as written and has to be corrected: it "
              "is the whole recognised parameter *sequence*.")

    print(f"\nraw rows: {sink.path}")


if __name__ == "__main__":
    main()
