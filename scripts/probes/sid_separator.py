"""Does a value carrying the separator reach the gateway whole, or get cut?

For this dialect `separator` and `pair_separator` are both `-`, so the username
`acct-country-us-sid-order-4417` is ambiguous by construction. It can be read as
`sid=order-4417`, or as `sid=order` followed by a parameter named `4417`. The
SDK refuses the input on the second reading. Nobody has ever asked the gateway
which reading is its own, and `order-4417` is the obvious thing for a caller to
use as an order id, so the refusal is either a correct guard or a library that
is stricter than the gateway it wraps.

Nothing here fetches a target. The whole measurement is the CONNECT reply, plus
an echo request on the replies that arrive without an exit address, so it costs
a few kilobytes and no pool reputation.

**The discriminator is the sticky session, and the obvious control does not
work.** The first design of this probe compared `sid-order-4417` against
`sid-order4417` and asked whether the gateway held one exit or two. It always
holds two: under either reading those are different session keys - `order-4417`
against `order4417` on one, `order` against `order4417` on the other - so the
answer is the same whichever is true and the experiment is empty. It is written
down because it looked like a control and was not one.

What separates the readings is `sid-order` on its own:

    arm       username tail        if the value is carried   if it is cut
    hyphen    sid-order-4417       session `order-4417`      session `order`
    head      sid-order            session `order`           session `order`
    joined    sid-order4417        session `order4417`       session `order4417`

So `hyphen` and `head` land on the same exit if and only if the tail was cut.
`joined` is not part of the discrimination; it is there because two arms that
agree prove nothing unless a third arm is seen to disagree in the same window -
without it, "every arm gave the same exit" reads as a result when it is a pool
that stopped rotating.

The arms are interleaved rather than run in blocks, for the reason every run in
this repository is: a sticky session has a lifetime, and three blocks measure
the minutes as well as the parameters.

Two ways this comes back inconclusive rather than wrong, and both are reported
as such:

- an arm draws more than one exit across its rounds, so the session did not hold
  and nothing can be read off two exits differing
- the gateway answers the `hyphen` arm with a status other than 200, which is
  its own answer and a better one - a refusal names the parse without any of
  this arithmetic

Usage:
    python scripts/probes/sid_separator.py
    python scripts/probes/sid_separator.py --rounds 5 --country de
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

from nmbench import config, gateway, providers, proxy
from nmbench.sink import JsonlSink

# The value under test, the value it collapses to if the gateway cuts on the
# separator, and a value that is a distinct session under either reading.
STEM = "order"
TAIL = "4417"


def attempt(arm: str, session_id: str, base: dict, timeout: int, provider,
            registry) -> dict:
    """One CONNECT, and an echo request only when the reply named no exit.

    Roughly half the replies carry `X-Proxy-Exit-IP` and the rest do not - which
    backend answered decides it, and the reason phrase is the label. This probe
    needs the address on every attempt, because a missing one is a lost round
    rather than a slower one, so the paid fallback is worth its 330 bytes here.
    """
    params = proxy.session_params(session_id, provider=provider, **base)
    info = gateway.exit_ip(timeout=timeout, provider=provider, **params)

    row = dict(info)
    row["source"] = "connect-header" if info["exit_ip"] else None
    if info["status"] == 200 and not info["exit_ip"]:
        seen = gateway.echo(provider=provider, **params)
        row["exit_ip"] = seen.get("exit_ip")
        row["source"] = "echo" if seen.get("exit_ip") else None
        row["error"] = row["error"] or seen.get("error")

    row.update({
        "arm": arm,
        "session": session_id,
        "opened": info["status"] == 200,
        "params": {k: v for k, v in params.items() if k != provider.session_param},
        "provider": provider.id,
        "provider_status": provider.status,
        "cell": f"sid_separator/{arm}",
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
    parser.add_argument("--pause", type=float, default=1.5)
    parser.add_argument("--provider", default=None)
    args = parser.parse_args()

    try:
        provider = providers.load(args.provider)
        creds = config.credentials(provider)
    except (providers.ProviderError, config.MissingCredentials) as exc:
        parser.error(str(exc))

    if not provider.session_param:
        parser.error(f"{provider.label} declares no session parameter, so there "
                     f"is no sticky session to read this off")

    # The mark goes on the stem rather than on each arm, which is what keeps the
    # collapse exact: if the gateway cuts at the separator, `hyphen` becomes
    # byte-identical to `head`. It also makes all three sessions fresh, so a run
    # cannot inherit an exit the gateway assigned to this probe on another day.
    stem = f"{STEM}{uuid.uuid4().hex[:6]}"
    arms = {
        "hyphen": f"{stem}-{TAIL}",
        "head": stem,
        "joined": f"{stem}{TAIL}",
    }

    base = {"country": args.country}

    sink = JsonlSink("sid_separator")
    registry = gateway.ExitRegistry()

    line = gateway.identify_direct()
    if line["error"]:
        print(f"this machine cannot reach the internet directly: {line['error']}")
        raise SystemExit(1)
    print(f"own line: {line['exit_ip']}  {line.get('org') or '-'}")
    print(f"provider: {provider.label} ({provider.status}), {creds.gateway}")
    print(f"held:     country={args.country}, {provider.session_param} per arm")
    for arm, value in arms.items():
        print(f"  {arm:<8} {provider.session_param}-{value}")
    print(f"raw rows -> {sink.path}\n")

    rows = []
    for round_index in range(args.rounds):
        for arm, value in arms.items():
            row = attempt(arm, value, base, args.timeout, provider, registry)
            sink.write(row)
            rows.append(row)
            print(f"  {round_index + 1}.{arm:<8} "
                  f"{row['status'] or '-'!s:<5}{(row['reason'] or '-')[:22]:<24}"
                  f"{row['exit_label'] or '-':<9}"
                  f"{row['exit_prefix'] or (row['error'] or '')[:44]}")
            time.sleep(args.pause)

    print("\n" + "=" * 78)

    by_arm = defaultdict(list)
    for row in rows:
        by_arm[row["arm"]].append(row)

    exits = {}
    for arm in arms:
        named = [r for r in by_arm[arm] if r["exit_ip"]]
        exits[arm] = {r["exit_ip"] for r in named}
        statuses = Counter(f"{r['status']} {r['reason']}" if r["status"]
                           else (r["error"] or "no reply") for r in by_arm[arm])
        print(f"{arm:<8} {len(named)}/{len(by_arm[arm])} exits named, "
              f"{len(exits[arm])} distinct")
        for text, count in statuses.most_common():
            print(f"         {count:>3}  {text[:60]}")

    print()
    refused = [r for r in by_arm["hyphen"] if r["status"] not in (200, None)]
    if refused:
        print("the gateway answered the hyphen arm with a status of its own, "
              "which settles it without the exit comparison: it is not treating "
              "the value as an ordinary one.")
        return

    unstable = [arm for arm in arms if len(exits[arm]) > 1]
    missing = [arm for arm in arms if not exits[arm]]
    if missing:
        print(f"no exit address for {missing}, so nothing can be compared. "
              f"Re-run; the header is not guaranteed and the echo fallback can "
              f"fail on its own.")
        return
    if unstable:
        print(f"{unstable} drew more than one exit, so the session did not hold "
              f"in this window and two arms differing proves nothing. Re-run, "
              f"and if it repeats the sticky session is the finding rather than "
              f"the separator.")
        return
    if exits["hyphen"] == exits["joined"]:
        print("the hyphen arm and the joined arm landed on one exit, which "
              "neither reading predicts. Something other than the session key "
              "is deciding the exit here, so this run cannot be read.")
        return

    if exits["hyphen"] == exits["head"]:
        print("MEASURED: the gateway CUTS the value at the separator.")
        print(f"  {provider.session_param}-{arms['hyphen']} and "
              f"{provider.session_param}-{arms['head']} are one session on one "
              f"exit, so the {TAIL!r} tail was parsed as something else and "
              f"dropped.")
        print("  The SDK's refusal is correct and can stop being called "
              "inference. A caller passing an order id with a hyphen would "
              "otherwise share one session with every other order beginning "
              f"{STEM!r}.")
    else:
        print("MEASURED: the gateway CARRIES the separator inside the value.")
        print(f"  {provider.session_param}-{arms['hyphen']} and "
              f"{provider.session_param}-{arms['head']} are two sessions on two "
              f"exits, so the value arrived whole.")
        print("  The SDK is stricter than the gateway: it refuses a session id "
              "that works. That is a decision to revisit, not a bug to leave - "
              "and the refusal still holds for parameters other than the "
              "session, where a cut value selects a different setting silently.")

    print(f"\nraw rows: {sink.path}")


if __name__ == "__main__":
    main()
