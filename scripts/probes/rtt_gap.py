"""Where the TCP handshake ends and where the TLS handshake ends.

A backconnect residential proxy terminates TCP at its own gateway and carries
TLS end to end, to the exit device and on to the target. The two handshakes
therefore travel different distances, and the difference is a property of the
architecture that no configuration hides.

BADPASS (Chiapponi et al., ISPEC 2022, reproduced by Stanford CS244C) measures
this from the *server* side: the server's TCP handshake ends at the relay while
its TLS handshake reaches the real client, so a residential proxy shows a gap of
roughly 245-265 ms against roughly 4 ms for a direct visitor, and 50 ms
separates them cleanly. We cannot stand on the server side, so this script does
not reproduce that measurement and its numbers are not what a target sees.

What it measures is the same asymmetry from the other end. Our TCP handshake
ends at the gateway, which is usually near; our TLS handshake goes gateway ->
exit device -> target and back. The difference between the two is the round trip
we are paying for the chain, measured against a direct connection to the same
host in the same minutes as a control.

That makes it a benchmark column rather than a detection test: it separates
providers by how they are built instead of by what they claim. A provider whose
gap is close to the direct control is doing something structurally different
from one whose gap is a quarter of a second.

Cost: a TLS handshake per attempt and no HTTP request at all, so a few KB. The
target is contacted, so use a host you are willing to be seen contacting.

Usage:
    python scripts/probes/rtt_gap.py
    python scripts/probes/rtt_gap.py --host www.google.com --n 8
    python scripts/probes/rtt_gap.py --mode proxy --country gb
"""
import argparse
import socket
import ssl
import statistics
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nmbench import gateway, proxy
from nmbench.sink import JsonlSink

CONTEXT = ssl.create_default_context()


def handshake(sock, host: str) -> dict:
    """Negotiate TLS on an already connected socket and time it."""
    started = time.perf_counter()
    tls = CONTEXT.wrap_socket(sock, server_hostname=host)
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    result = {"tls_ms": elapsed, "tls_version": tls.version(),
              "cipher": tls.cipher()[0] if tls.cipher() else None}
    tls.close()
    return result


def measure_direct(host: str, port: int, timeout: int) -> dict:
    """DNS, TCP and TLS straight from this machine. The control."""
    row = {"path": "direct", "host": host, "dns_ms": None, "tcp_ms": None,
           "tls_ms": None, "gap_ms": None, "error": None}
    try:
        started = time.perf_counter()
        address = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)[0][4]
        row["dns_ms"] = round((time.perf_counter() - started) * 1000, 1)

        started = time.perf_counter()
        sock = socket.create_connection(address, timeout=timeout)
        row["tcp_ms"] = round((time.perf_counter() - started) * 1000, 1)
        sock.settimeout(timeout)

        row.update(handshake(sock, host))
        row["gap_ms"] = round(row["tls_ms"] - row["tcp_ms"], 1)
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
    return row


def measure_proxy(host: str, port: int, timeout: int, **params) -> dict:
    """TCP to the gateway, CONNECT, then TLS the whole way to the target.

    `tcp_ms` here is the distance to the gateway and `tls_ms` the distance
    through the exit device, which is the point of the comparison.
    """
    row = {"path": "proxy", "host": host, "params": dict(params),
           "dns_ms": None, "tcp_ms": None, "connect_ms": None,
           "tls_ms": None, "gap_ms": None, "exit_ip": None,
           "connect_status": None, "error": None}

    sock, info = gateway.open_tunnel(host, port, timeout=timeout, **params)
    row["tcp_ms"] = info["tcp_ms"]
    row["connect_ms"] = info["connect_ms"]
    row["connect_status"] = info["status"]
    row["exit_ip"] = info["exit_ip"]

    if sock is None:
        row["error"] = info["error"] or f"CONNECT returned {info['status']}"
        return row
    try:
        row.update(handshake(sock, host))
        row["gap_ms"] = round(row["tls_ms"] - row["tcp_ms"], 1)
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
        sock.close()
    return row


def summarise(label: str, rows: list) -> None:
    ok = [r for r in rows if r["gap_ms"] is not None]
    if not ok:
        print(f"{label:<10}no successful handshakes")
        return
    tcp = statistics.median(r["tcp_ms"] for r in ok)
    tls = statistics.median(r["tls_ms"] for r in ok)
    gap = statistics.median(r["gap_ms"] for r in ok)
    print(f"{label:<10}{len(ok):>3}/{len(rows):<5}"
          f"tcp {tcp:>7.1f}   tls {tls:>7.1f}   gap {gap:>7.1f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="www.google.com")
    parser.add_argument("--port", type=int, default=443)
    parser.add_argument("--n", type=int, default=6, help="repetitions per path")
    parser.add_argument("--mode", default="both",
                        choices=["both", "direct", "proxy"])
    parser.add_argument("--country", default="us")
    parser.add_argument("--pause", type=float, default=3.0)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--fresh-exit", action="store_true",
                        help="a new session per repetition, so the medians "
                             "describe the pool rather than one device")
    args = parser.parse_args()

    sink = JsonlSink("rtt_gap")
    registry = gateway.ExitRegistry()
    direct_rows, proxy_rows = [], []

    print(f"host: {args.host}:{args.port}   repetitions: {args.n}   "
          f"mode: {args.mode}")
    print("the gap is tls minus tcp: how much further the TLS handshake "
          "travelled than the TCP one")
    print(f"raw rows -> {sink.path}\n")
    print(f"{'#':<4}{'path':<9}{'tcp ms':>8}{'connect':>9}{'tls ms':>9}"
          f"{'gap ms':>9}  exit")
    print("-" * 78)

    # One sid for the whole run unless asked otherwise: a median over six
    # different devices measures the pool, a median over one measures that
    # device. Both are useful and they are not the same number.
    session = f"rtt{uuid.uuid4().hex[:8]}"

    for index in range(args.n):
        # Direct and proxy alternate inside the same minutes. Running all the
        # direct attempts first would let the network's own weather show up as
        # a difference between the paths.
        if args.mode in ("both", "direct"):
            row = measure_direct(args.host, args.port, args.timeout)
            row["index"] = index
            sink.write(row)
            direct_rows.append(row)
            print(f"{index:<4}{'direct':<9}"
                  f"{_ms(row['tcp_ms']):>8}{'-':>9}{_ms(row['tls_ms']):>9}"
                  f"{_ms(row['gap_ms']):>9}  this machine")
            if row["error"]:
                print(f"      -> {row['error'][:90]}")

        if args.mode in ("both", "proxy"):
            sid = f"{session}{index}" if args.fresh_exit else session
            params = proxy.session_params(sid, country=args.country)
            row = measure_proxy(args.host, args.port, args.timeout, **params)
            row["index"] = index
            row.update(registry.record(row.get("exit_ip")))
            sink.write(row)
            proxy_rows.append(row)
            print(f"{index:<4}{'proxy':<9}"
                  f"{_ms(row['tcp_ms']):>8}{_ms(row['connect_ms']):>9}"
                  f"{_ms(row['tls_ms']):>9}{_ms(row['gap_ms']):>9}  "
                  f"{row['exit_ip'] or 'not reported'}")
            if row["error"]:
                print(f"      -> {row['error'][:90]}")

        time.sleep(args.pause)

    print("\n" + "=" * 78)
    print(f"{'path':<10}{'ok':>3}/{'n':<5}medians")
    print("-" * 78)
    summarise("direct", direct_rows)
    summarise("proxy", proxy_rows)

    both = [r for r in direct_rows if r["gap_ms"] is not None], \
           [r for r in proxy_rows if r["gap_ms"] is not None]
    if both[0] and both[1]:
        d = statistics.median(r["gap_ms"] for r in both[0])
        p = statistics.median(r["gap_ms"] for r in both[1])
        print(f"\nthe proxied TLS handshake travelled {p - d:.1f} ms further "
              f"than the direct one ({p:.1f} against {d:.1f}).")
        print("This is the client side of the chain, not the target's. A target "
              "measuring the same asymmetry sees its own numbers, and this "
              "one cannot be read as 'what Google sees'.")

    print(f"\nraw rows: {sink.path}")


def _ms(value) -> str:
    return "-" if value is None else f"{value:.1f}"


if __name__ == "__main__":
    main()
