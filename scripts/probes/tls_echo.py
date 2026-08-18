"""What each engine's TLS handshake looks like, read from an echo service.

This is the layer `engine_fingerprint.py` explicitly cannot see. Everything that
probe reads is JavaScript, negotiated after the connection exists; the ClientHello
is sent before a single byte of script runs, and no amount of property patching
reaches it.

The question it exists to settle: Amazon serves Camoufox and throttles the
Playwright-driven Chromium engines, and the standing explanation is that the
handshake sorts them. That explanation was written from the pass rates alone and
has never been checked against an actual handshake. Two rows on disk already
argue with it - a plain `requests` client passes Amazon five times more often
than Patchright, and Obscura is Chromium and passes DuckDuckGo 44 of 44 - so the
handshakes have to be read rather than assumed.

Cost: one request per engine to an echo service. No target is asked anything, no
gateway is involved, no pool reputation is spent, and the run is `direct` on
purpose. That is also why `direct` costs nothing in accuracy here: a CONNECT
proxy tunnels TLS end to end, so the ClientHello that reaches the far side is
byte for byte the one the engine emitted either way. What the proxy would change
is the exit address, and this probe is not about the address.

Read it as evidence about the layer, not as a ranking. Two engines with the same
JA4 cannot be told apart by a detector at this layer, whatever their pass rates
say - which means a pass-rate gap between them has to be explained somewhere
else. That is the inference this probe supports, and it is the only one it does.

Usage:
    python scripts/probes/tls_echo.py
    python scripts/probes/tls_echo.py --engines chromium,obscura
    python scripts/probes/tls_echo.py --echo https://tls.peet.ws/api/all
"""
import argparse
import json
import socket
import ssl
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import requests

from nmbench import engines
from nmbench.engines.http import BROWSER_HEADERS
from nmbench.sink import JsonlSink

# It opens a connection, so the command list must say so, even though the
# connection is to an echo and not to a target.
SENDS_REQUESTS = True

ECHO = "https://tls.peet.ws/api/all"

# GREASE values, RFC 8701. A real browser sends them and most tooling does not,
# so their presence is the single cheapest tell in the ClientHello.
GREASE = {0x0a0a, 0x1a1a, 0x2a2a, 0x3a3a, 0x4a4a, 0x5a5a, 0x6a6a, 0x7a7a,
          0x8a8a, 0x9a9a, 0xaaaa, 0xbaba, 0xcaca, 0xdada, 0xeaea, 0xfafa}


def is_grease(name: str) -> bool:
    """The echo returns names like `TLS_GREASE (0x1a1a)`."""
    text = str(name).lower()
    if "grease" in text:
        return True
    start = text.find("0x")
    if start == -1:
        return False
    try:
        return int(text[start:start + 6], 16) in GREASE
    except ValueError:
        return False


def summarise(payload: dict) -> dict:
    """The handshake reduced to the fields a detector would key on.

    The exit address is dropped rather than masked. It is in the echo's reply,
    these rows are committed, and an address in a public repository is the leak
    this repository has a rule about.
    """
    tls = payload.get("tls") or {}
    ciphers = tls.get("ciphers") or []
    extensions = [e.get("name", "") for e in (tls.get("extensions") or [])
                  if isinstance(e, dict)]
    http2 = payload.get("http2") or {}
    return {
        "ja3_hash": tls.get("ja3_hash"),
        "ja4": tls.get("ja4"),
        "peetprint_hash": tls.get("peetprint_hash"),
        "ciphers": len(ciphers),
        "extensions": len(extensions),
        "grease_ciphers": sum(1 for c in ciphers if is_grease(c)),
        "grease_extensions": sum(1 for e in extensions if is_grease(e)),
        "tls_version": tls.get("tls_version_negotiated"),
        "http_version": payload.get("http_version"),
        "akamai_hash": http2.get("akamai_fingerprint_hash"),
        "first_cipher": ciphers[0] if ciphers else None,
        "user_agent": payload.get("user_agent"),
    }


def echo_certificate_problem(url: str) -> str:
    """Why the echo cannot be reached, checked before any engine is launched.

    This exists because the failure it catches has already been misread once.
    Measured 2026-08-18: `tls.peet.ws` serves OpenSSL's default self-signed
    placeholder - subject and issuer both `Internet Widgits Pty Ltd`, no SAN,
    valid until 2023-08-29 and therefore two years expired. Every engine
    refuses it, and each refuses it in its own words. Obscura's is a
    `CERTIFICATE_VERIFY_FAILED` out of a BoringSSL path naming a build
    directory on somebody else's CI machine, which reads exactly like a defect
    in the engine under test, and was read that way - the previous attempt at
    this probe attributed it to Obscura and then to the VPN tunnel that was
    being unwound the same week. Neither was involved: `www.google.com`,
    `example.com`, `api.ipify.org` and `ipinfo.io` all verify from this machine
    on the same run.

    So the check is asked from Python, where the answer arrives as one sentence
    naming the certificate rather than as a stack trace naming a browser. A
    verification failure here is the echo's, and this probe cannot run against
    it at all - not for Obscura, and not for the four engines that would have
    failed a few lines later in four other dialects.

    It cannot be worked around by not verifying. The value of this endpoint is
    that it reports the handshake it received, and a handshake to a host that
    is not who it says it is measures the interceptor. `--echo` takes another
    service, which is the only real answer while this one is broken.
    """
    host = urlsplit(url).hostname
    port = urlsplit(url).port or 443
    if not host:
        return f"{url!r} names no host"
    try:
        with socket.create_connection((host, port), timeout=20) as raw:
            with ssl.create_default_context().wrap_socket(
                    raw, server_hostname=host):
                return None
    except ssl.SSLCertVerificationError as exc:
        return (f"{host} is not presenting a certificate this machine will "
                f"accept ({exc.verify_message or exc.reason}), so no engine "
                f"can reach it and nothing here is a statement about any "
                f"engine. Pass --echo with another service")
    except OSError as exc:
        return (f"{host}:{port} is unreachable ({exc}), so nothing here is a "
                f"statement about any engine")


def read_with_browser(engine_name: str, url: str, headful: bool) -> dict:
    """Navigate and take the body back, however this engine hands it over.

    `response.text()` is the direct route and Obscura returns no response object
    at all - the same defect that leaves its rows without an HTTP status - so the
    rendered body is the fallback. Both are the same bytes: Chromium shows JSON
    in a `<pre>`.
    """
    engine = engines.get(engine_name)
    # preset "none": a blocking rule cannot change a handshake, but it can drop
    # the request, and a preset difference between engines would be a difference
    # in what was measured.
    with engine.open(direct=True, preset="none", headless=not headful) as session:
        page = session.new_page()
        try:
            response = page.goto(url, wait_until="load")
            text = None
            if response is not None:
                try:
                    text = response.text()
                except Exception:
                    text = None
            if not text:
                text = page.evaluate("() => document.body.innerText")
            payload = json.loads(text)
        finally:
            page.close()
        payload["_engine_version"] = session.version
    return payload


def read_with_requests(url: str) -> dict:
    """The plain client, sending exactly the headers the http engine sends."""
    response = requests.get(url, headers=BROWSER_HEADERS, timeout=45)
    payload = response.json()
    payload["_engine_version"] = requests.__version__
    return payload


ROWS = ["ja4", "ja3_hash", "peetprint_hash", "akamai_hash", "ciphers",
        "extensions", "grease_ciphers", "grease_extensions", "first_cipher",
        "tls_version", "http_version", "user_agent"]


def main() -> None:
    known = ["http"] + [n for n, e in engines.REGISTRY.items()
                        if getattr(e, "runs_script", False)]

    parser = argparse.ArgumentParser()
    parser.add_argument("--engines", default=",".join(known),
                        help=f"comma separated, from {known}")
    parser.add_argument("--echo", default=ECHO,
                        help="TLS echo endpoint returning JA3/JA4")
    parser.add_argument("--headful", action="store_true",
                        help="open real windows. The handshake should not care, "
                             "which is itself worth confirming once")
    args = parser.parse_args()

    names = [n.strip() for n in args.engines.split(",") if n.strip()]
    unknown = [n for n in names if n not in known]
    if unknown:
        parser.error(f"unknown or scriptless engines {unknown}, known: {known}")

    availability = engines.report_availability()
    print(f"reading handshakes from {args.echo}")
    print("direct, one request each: no target, no gateway, no quota\n")

    # Before a browser is launched, so the operator is told about the echo in
    # one sentence instead of about five engines in five dialects.
    broken = echo_certificate_problem(args.echo)
    if broken:
        print(f"  the echo is unusable: {broken}")
        raise SystemExit(2)

    sink = JsonlSink("tls_echo")
    results = {}
    for name in names:
        if name != "http" and availability.get(name):
            print(f"  {name}: SKIPPED, {availability[name]}")
            continue
        engine = engines.REGISTRY[name]
        if args.headful and not getattr(engine, "supports_headful", False):
            print(f"  {name}: SKIPPED, this engine is headless only")
            continue
        try:
            if name == "http":
                payload = read_with_requests(args.echo)
            else:
                payload = read_with_browser(name, args.echo, args.headful)
        except Exception as exc:
            print(f"  {name}: FAILED, {type(exc).__name__}: {exc}")
            continue

        info = summarise(payload)
        info["engine_version"] = payload.get("_engine_version")
        results[name] = info
        sink.write({"engine": name, "headful": args.headful,
                    "echo": args.echo, **info})
        print(f"  {name}: {info['ja4']}")

    if not results:
        print("\nnothing to compare")
        return

    shown = list(results)
    width = max(20, *(len(n) + 2 for n in shown))
    print("\n" + "=" * (20 + width * len(shown)))
    print(f"{'field':<20}" + "".join(f"{n:<{width}}" for n in shown))
    print("-" * (20 + width * len(shown)))
    for key in ROWS:
        cells = []
        for name in shown:
            value = results[name].get(key)
            cells.append(("-" if value is None else str(value))[:width - 2])
        same = len({str(results[n].get(key)) for n in shown}) == 1
        label = key if not same or len(shown) == 1 else f"{key} ="
        print(f"{label:<20}" + "".join(f"{c:<{width}}" for c in cells))

    groups = {}
    for name in shown:
        groups.setdefault(results[name]["ja4"], []).append(name)
    print("\nengines sharing a JA4:")
    for ja4, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        print(f"  {ja4}  {', '.join(members)}")
    if any(len(m) > 1 for m in groups.values()):
        print("\nEngines inside one group are identical to a detector reading "
              "this layer.\nA difference in their pass rates was decided "
              "somewhere above the handshake.")

    print(f"\nraw rows: {sink.path}")


if __name__ == "__main__":
    main()
