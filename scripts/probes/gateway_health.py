"""Is the gateway usable right now, asked before a matrix is allowed to spend a night.

CONNECT and nothing else: no target, no browser, no body. The handshake with the
gateway is the whole measurement, so this costs a few hundred bytes and answers
the one question that invalidates every other number when the answer is no.

It exists because the 2026-08-11 matrix spent 4.5 hours and 547 attempts
producing pass rates that were survivorship over a 46% transport failure, while
the same fault was visible from here in thirty seconds: TCP to the gateway came
up in 3 ms and the CONNECT reply never arrived at all.

Two timings, and the split between them is the diagnosis:

    tcp_ms      distance to the front door. Healthy means the host is up, the
                port listens and the route is fine, which is what separates
                "the gateway is refusing us" from "this machine is offline".
    connect_ms  what the gateway took to answer, including whatever it did to
                reach an exit. A silent close after a fixed interval with no
                status line is not a parameter fault: a bad country answers 406,
                a bad filter 407 and a bad city 500. Silence points at the
                account or at the gateway itself, and that is a conversation
                with the provider rather than a change to the request.

The direct line is checked first, once. Without it a run of failures is
ambiguous, because a machine with no internet at all produces exactly the same
table as a gateway that has stopped answering.

`--bare` repeats the run with no parameters whatsoever. Identical failures with
and without parameters mean nothing in the username DSL is responsible, which is
the difference between a bug here and an outage there.

`--provider` is what a newly written definition should be pointed at first. A
definition is a username format transcribed from a vendor's documentation, and a
mistake in one is invisible by construction: at least one gateway answers an
unrecognised parameter name with 200 and the setting quietly dropped. Ten
CONNECTs here cost a few hundred bytes and are the cheapest thing that can tell
a wrong dialect from a wrong password - and the alternative is finding out from a
matrix whose rows all claim settings that were never applied.

Usage:
    python scripts/probes/gateway_health.py
    python scripts/probes/gateway_health.py --n 30 --bare
    python scripts/probes/gateway_health.py --param filter=medium
    python scripts/probes/gateway_health.py --provider oxylabs --n 5
"""
import argparse
import statistics
import sys
import time
import uuid
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nmbench.console import tolerate_unencodable_output

tolerate_unencodable_output()

from nmbench import config, gateway, providers, proxy
from nmbench.sink import JsonlSink


def median(values: list):
    clean = [v for v in values if v is not None]
    return round(statistics.median(clean), 1) if clean else None


def attempt(index: int, params: dict, timeout: int, registry, provider,
            identify: bool = False) -> dict:
    """One CONNECT, recorded whether it opened or not.

    With `identify`, an exit that the CONNECT reply did not name is looked up
    through the echo service, which costs about 330 bytes and is the only source
    that also gives the operator. That is what turns "the tunnel opened" into
    "the tunnel opened onto a residential line", and the two are not the same
    measurement: a run on 2026-08-11 drew a datacenter ASN out of a pool sold as
    residential, and nothing in the CONNECT reply would ever have said so.
    """
    sock, info = gateway.open_tunnel(timeout=timeout, strict=False,
                                     provider=provider, **params)
    if sock is not None:
        sock.close()
    row = dict(info)
    row["org"] = None
    if identify and info["status"] == 200:
        seen = gateway.echo(provider=provider, **params)
        row["exit_ip"] = seen.get("exit_ip") or info["exit_ip"]
        row["org"] = seen.get("org")
        row["exit_country"] = seen.get("country")
        row["exit_city"] = seen.get("city")
        info = dict(info, exit_ip=row["exit_ip"])
    row.update({
        "index": index,
        "opened": info["status"] == 200,
        # The parameter set is on the row because it is the sticky session key:
        # a result that cannot say which session produced it is not evidence.
        "params": {k: v for k, v in params.items()
                   if k != provider.session_param},
        "bare": not params,
        # Which gateway answered, and whether its dialect had ever been sent
        # before this run. A failure row from a `documented` definition is
        # ambiguous in a way one from a measured definition is not: it may be
        # the gateway and it may be the transcription.
        "provider": provider.id,
        "provider_status": provider.status,
    })
    # `row` still names the exit in full, which is what the console below prints.
    # `JsonlSink.write` is what keeps it out of the committed file.
    row.update(registry.record(info.get("exit_ip")))
    return row


def report(rows: list, label: str) -> None:
    opened = [r for r in rows if r["opened"]]
    failed = [r for r in rows if not r["opened"]]
    print(f"\n{label}: {len(opened)}/{len(rows)} tunnels opened")

    statuses = Counter(f"{r['status']} {r['reason']}" if r["status"] else
                       (r["error"] or "no reply") for r in rows)
    for text, count in statuses.most_common():
        print(f"  {count:>3}  {text[:70]}")

    print(f"  tcp_ms     median {median([r['tcp_ms'] for r in rows])}")
    print(f"  connect_ms median {median([r['connect_ms'] for r in opened])} opened, "
          f"{median([r['connect_ms'] for r in failed])} failed")

    named = [r for r in opened if r["exit_prefix"]]
    prefixes = {r["exit_prefix"] for r in named}
    if named:
        print(f"  distinct /24 prefixes: {len(prefixes)} of {len(named)} exits named")
    elif opened:
        print("  no exit address on any reply, so the header source is "
              "unavailable and the matrix will pay an echo request per session")

    orgs = Counter(r["org"] for r in opened if r.get("org"))
    if orgs:
        print("  exit operators")
        for org, count in orgs.most_common():
            print(f"    {count:>3}  {org[:64]}")
        # Legal forms are not evidence: Charter Communications LLC is an access
        # provider and the first version of this list called it hosting. A name
        # check is a hint at best, so it only fires on words that describe the
        # business rather than the incorporation.
        datacenter = [o for o in orgs if any(
            word in o.lower() for word in
            ("datacamp", "hosting", "data center", "datacenter", "vps",
             "digitalocean", "ovh", "linode", "hetzner", "colo"))]
        if datacenter:
            print("  at least one exit is on a network whose name reads as "
                  "hosting rather than an access provider. A residential pool "
                  "is what the targets are being asked to believe, so this is "
                  "a finding about the pool and not about the run")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=10,
                        help="tunnels to open, each on a fresh session")
    parser.add_argument("--country", default="us")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--pause", type=float, default=1.0,
                        help="seconds between attempts. This opens no target "
                             "traffic, so it does not need the 3-5 s an "
                             "exploration against a real site does")
    parser.add_argument("--reuse-session", action="store_true",
                        help="one session for the whole round instead of a "
                             "fresh one per tunnel. This is the discriminator "
                             "for a 529: if reuse opens every tunnel and fresh "
                             "sessions do not, the account is limited in how "
                             "many sessions it may hold, not in how fast it may "
                             "ask. The two need opposite fixes, and a matrix "
                             "takes one session per batch")
    parser.add_argument("--identify", action="store_true",
                        help="name the operator behind every exit, through the "
                             "echo service. Costs about 330 bytes per opened "
                             "tunnel and is the only way to see whether a "
                             "residential pool is returning hosting ranges")
    parser.add_argument("--bare", action="store_true",
                        help="repeat the run with no parameters at all. "
                             "Identical failures both ways clear the username "
                             "DSL of responsibility")
    parser.add_argument("--param", action="append", default=[], metavar="KEY=VALUE",
                        help="extra gateway parameter, repeatable. Every "
                             "recognised parameter belongs to the sticky session "
                             "key, so adding one selects a different slice of "
                             "the pool")
    parser.add_argument("--provider", default=None,
                        help=f"which gateway to open tunnels to. Defined: "
                             f"{providers.names()}, and adding one is a file in "
                             f"data/providers/. This is the cheapest check a new "
                             f"definition can be given, because a wrong username "
                             f"format is otherwise invisible. Defaults to "
                             f"{providers.default_name()!r}")
    args = parser.parse_args()

    try:
        provider = providers.load(args.provider)
        # Against the provider that was named, so a parameter one gateway
        # recognises and another does not is refused here rather than dropped
        # silently by the gateway that does not.
        extra = proxy.parse_params(args.param, provider=provider)
        # Read before the first tunnel rather than inside it. Unset credentials
        # would otherwise surface as an exception per attempt, and this probe is
        # the one an operator runs when they already suspect the gateway - a
        # traceback there reads as the outage they came to confirm.
        creds = config.credentials(provider)
    except (providers.ProviderError, proxy.ParamError,
            config.MissingCredentials) as exc:
        parser.error(str(exc))

    sink = JsonlSink("gateway_health")
    registry = gateway.ExitRegistry()

    line = gateway.identify_direct()
    if line["error"]:
        print(f"this machine cannot reach the internet directly: {line['error']}")
        print("every gateway result below would be unattributable, so nothing "
              "was sent")
        raise SystemExit(1)
    print(f"own line: {line['exit_ip']}  {line.get('org') or '-'}  "
          f"{line.get('country') or '-'}")
    print(f"provider: {provider.label} ({provider.status}), {creds.gateway}")
    # The point of this line is to hand a refusal its most likely cause before
    # the operator starts debugging their account. For a transcribed DSL that
    # cause is the transcription; a definition carrying no DSL at all has
    # nothing to have transcribed, so saying it anyway would send them looking
    # through a vendor page that does not exist.
    if not provider.measured and provider.known_params:
        print("          the username format below was read off the vendor's "
              "documentation and has never been sent from here, so a refusal "
              "may be the transcription rather than the account")
    elif not provider.measured:
        print("          nothing here has ever been sent through this gateway, "
              "and it takes no settings in the username - so a refusal is the "
              "address, the port or the password, and nothing else")
    # The country is asked for only where the definition sells one. A gateway
    # that recognises no parameters is the ordinary shape of a proxy somebody
    # already owns, and putting `country-us` into its username would come back
    # as an authentication failure - which is exactly the reading this probe
    # exists to rule out. This is the first thing a new definition is pointed at,
    # so it must not be the thing that manufactures the failure.
    asks_country = "country" in provider.known_params
    base = ({"country": args.country, **extra} if asks_country else dict(extra))
    # The label is a cell key as well as a heading, so it stays short and the
    # explanation goes on its own line.
    label = f"country={args.country}" if asks_country else "no parameters"
    if not provider.known_params:
        print("          this definition recognises no parameters, so nothing "
              "is asked for and every tunnel opens onto the same exit")
    elif not asks_country:
        # Distinguished from the line above because the two are different
        # findings. A gateway with no parameters at all is a proxy somebody
        # owns; a gateway with parameters and no `country` sells something, just
        # not geography, and --country silently doing nothing there would be the
        # dropped-setting failure this repository is built around.
        print(f"          this definition sells no country, so --country "
              f"{args.country} was not sent. It knows "
              f"{sorted(provider.known_params)}")
    print(f"gateway:  {' '.join(f'{k}={v}' for k, v in sorted(base.items())) or 'none'}")
    print(f"raw rows -> {sink.path}\n")

    rounds = [(label, base)]
    # Nothing to strip when the first round already sends nothing, and a second
    # identical round would read as a control against itself.
    if args.bare and base:
        rounds.append(("no parameters", {}))

    results = {}
    for label, base in rounds:
        rows = []
        held = f"health{uuid.uuid4().hex[:8]}"
        print(f"{label}" + ("  one held session" if args.reuse_session else ""))
        for index in range(args.n):
            params = dict(base)
            if params:
                params = proxy.session_params(
                    held if args.reuse_session
                    else f"health{uuid.uuid4().hex[:8]}",
                    provider=provider, **params)
            row = attempt(index, params, args.timeout, registry, provider,
                          args.identify)
            # Named only when it is not the default, the rule the matrix cell
            # key follows: the 221 gateway_health rows already on disk were all
            # taken through the default, and a key that renamed itself now would
            # leave them uncomparable against anything measured after.
            suffix = "" if provider.id == providers.default_name() \
                else f"/provider-{provider.id}"
            row["cell"] = f"gateway_health/{label.replace(' ', '-')}{suffix}"
            sink.write(row)
            rows.append(row)
            mark = "ok " if row["opened"] else "   "
            print(f"  {mark}{index + 1:>3}  "
                  f"{row['status'] or '-'!s:<5}{(row['reason'] or '-')[:24]:<26}"
                  f"tcp {row['tcp_ms']!s:<8}connect {row['connect_ms']!s:<10}"
                  f"{row['exit_ip'] or (row['error'] or '')[:44]!s:<18}"
                  f"{(row.get('org') or '')[:38]}")
            time.sleep(args.pause)
        results[label] = rows
        report(rows, label)

    print("\n" + "=" * 84)
    first = results[rounds[0][0]]
    opened = sum(1 for r in first if r["opened"])
    if opened == len(first):
        print("the gateway answered every CONNECT. Transport is not a reason to "
              "hold a matrix.")
    elif opened == 0:
        print("not one tunnel opened. Every attempt a matrix makes would be "
              "recorded as an error, and any pass rate computed over what "
              "survived would be survivorship over the failure, not a "
              "measurement of the target.")
        if median([r["tcp_ms"] for r in first]) and median(
                [r["tcp_ms"] for r in first]) < 100:
            print("TCP reached the gateway quickly, so the host is up and the "
                  "port listens. The refusal is above the socket: the account, "
                  "this machine's address, or the gateway itself.")
    else:
        share = round(100 * (len(first) - opened) / len(first))
        print(f"{share}% of tunnels failed. A matrix inherits that share as "
              f"errors, spread evenly across every engine, which reads in the "
              f"report as five frameworks failing at once. Fix the transport "
              f"before spending a night on it.")

    if args.bare and results.get("no parameters"):
        bare_opened = sum(1 for r in results["no parameters"] if r["opened"])
        if bare_opened == opened == 0:
            print("The bare round failed identically, so no parameter is "
                  "responsible and the username DSL is not the fault.")
        elif bare_opened and not opened:
            print("Bare tunnels open and parameterised ones do not, so the "
                  "fault is in the parameter set, not in the account.")

    print(f"\nraw rows: {sink.path}")


if __name__ == "__main__":
    main()
