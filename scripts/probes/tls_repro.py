"""What the gateway actually puts in the tunnel when a handshake fails.

`probehold_20260827T201123Z` produced 36 `net::ERR_SSL_PROTOCOL_ERROR` rows out
of 409 requests, and Chrome's message is the same string for every reason a
handshake can fail, so the log cannot say which one it was. That run also showed
the failure is a property of the *exit* and not of the request: 6 of 111 exits
carried 3 or more failures where a per-request independent model predicts 0.6 of
them, and one exit failed 5 of its 9 requests. So the question is what those
exits do differently, and it is answerable only by holding the bytes.

This script does the handshake by hand through `ssl.MemoryBIO`, which is the
whole point: every byte the gateway sends arrives in a buffer we own before
OpenSSL is allowed to reject it. When the handshake fails we can print what came
back. The three answers it can give, and they need opposite fixes:

- **Plaintext in the tunnel.** The gateway answered CONNECT with 200 and then
  wrote an HTTP error, or a captive-portal page, or its own diagnostic, instead
  of relaying. The client sends a ClientHello, gets `HTTP/1.1 502 ...` back,
  and reports a TLS protocol error because that is what a non-TLS first byte
  is. This is a gateway bug and the byte dump proves it outright.
- **A TLS alert.** Byte 0 is 0x15 and the tunnel really did reach a TLS peer
  that refused us. Then the alert code says whether it was the handshake being
  rejected - `handshake_failure`, `protocol_version`, `inappropriate_fallback` -
  which is a fingerprint or version question and not a plumbing one.
- **Nothing at all, then EOF.** The upstream hung up without speaking. That is
  the exit's own network, and the only fix available to us is to stop drawing
  that exit.

Run it with the VPN off. The operator's own transport sits under the gateway
here, and a VPN that reconnects mid-run produces exactly the symptom being
investigated, from our side rather than theirs.

Nothing here prints the `Proxy-Authorization` header or the password. The header
is base64 and not encryption, and this script's output is meant to be pasteable
into an issue.
"""

from __future__ import annotations

import argparse
import base64
import collections
import socket
import ssl
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nmbench import config, providers, proxy

# How much of the tunnel to keep when the handshake fails. A gateway error page
# is small and an alert is 7 bytes, so this is generous on purpose - the value
# of the dump is that it is complete for the cases that matter.
KEEP = 512


def connect_tunnel(gateway: str, host: str, port: int, username: str,
                   password: str, timeout: float):
    """Open a CONNECT tunnel and return the socket plus the reply line.

    The reply is read a byte at a time up to the header terminator rather than
    with a fixed `recv`, because a single `recv` can return the CONNECT reply
    *and* whatever the gateway wrote after it, and separating those two is the
    entire question. Reading exactly to `\\r\\n\\r\\n` leaves anything that
    followed in the kernel buffer, where the handshake will find it and where it
    is evidence rather than a parsing accident.
    """
    ghost, gport = gateway.rsplit(":", 1)
    sock = socket.create_connection((ghost, int(gport)), timeout=timeout)
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    request = (f"CONNECT {host}:{port} HTTP/1.1\r\n"
               f"Host: {host}:{port}\r\n"
               f"Proxy-Authorization: Basic {token}\r\n"
               f"Proxy-Connection: keep-alive\r\n\r\n")
    sock.sendall(request.encode())

    head = b""
    while b"\r\n\r\n" not in head:
        chunk = sock.recv(1)
        if not chunk:
            break
        head += chunk
        if len(head) > 8192:
            break
    return sock, head


def handshake(sock, host: str, timeout: float) -> dict:
    """TLS by hand, so the bytes survive the failure.

    `wrap_socket` would read the tunnel itself and raise with the content gone.
    A `MemoryBIO` pair inverts that: OpenSSL asks us for bytes, we do the
    `recv`, and we keep a copy. The cost is having to drive the handshake loop
    by hand, which is the loop below.
    """
    context = ssl.create_default_context()
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    tls = context.wrap_bio(incoming, outgoing, server_hostname=host)
    received = b""
    sock.settimeout(timeout)
    started = time.perf_counter()
    while True:
        try:
            tls.do_handshake()
        except ssl.SSLWantReadError:
            pending = outgoing.read()
            if pending:
                sock.sendall(pending)
            try:
                chunk = sock.recv(4096)
            except TimeoutError:
                return {"ok": False, "why": "timeout waiting for the peer",
                        "bytes": received,
                        "ms": round((time.perf_counter() - started) * 1000)}
            if not chunk:
                # An orderly close with nothing said. Distinguished from a
                # refusal on purpose: this one carries no message to read.
                return {"ok": False, "why": "peer closed without sending",
                        "bytes": received,
                        "ms": round((time.perf_counter() - started) * 1000)}
            received += chunk
            incoming.write(chunk)
        except ssl.SSLError as exc:
            return {"ok": False, "why": f"{type(exc).__name__}: {exc}",
                    "bytes": received,
                    "ms": round((time.perf_counter() - started) * 1000)}
        except OSError as exc:
            return {"ok": False, "why": f"{type(exc).__name__}: {exc}",
                    "bytes": received,
                    "ms": round((time.perf_counter() - started) * 1000)}
        else:
            pending = outgoing.read()
            if pending:
                sock.sendall(pending)
            return {"ok": True, "why": None, "bytes": received,
                    "cipher": tls.cipher(), "version": tls.version(),
                    "ms": round((time.perf_counter() - started) * 1000)}


def classify(head: bytes, result: dict) -> str:
    """Name the failure from the bytes, not from the exception text.

    The ordering matters. A plaintext body and a TLS alert both make OpenSSL say
    roughly the same thing, so the first byte is checked before the message is
    trusted at all.
    """
    if result["ok"]:
        return "ok"
    body = result["bytes"]
    if not head.startswith(b"HTTP/1.1 200") and not head.startswith(
            b"HTTP/1.0 200"):
        return "connect refused"
    if not body:
        return "silence" if "closed" in result["why"] else "no bytes"
    if body[0:1] == b"\x15":
        # A TLS alert record: type 21, then version, then length, then level
        # and description. The description is what names the refusal.
        code = body[6] if len(body) > 6 else None
        return f"tls alert {code}"
    if body[0:1] == b"\x16":
        return "tls handshake, failed later"
    if body[:5].isascii() and body[:4] in (b"HTTP", b"<htm", b"<HTM"):
        return "plaintext in the tunnel"
    return f"unknown first byte 0x{body[0]:02x}"


def show(label: str, blob: bytes) -> None:
    if not blob:
        print(f"    {label}: nothing")
        return
    head = blob[:KEEP]
    printable = "".join(chr(b) if 32 <= b < 127 else "." for b in head[:200])
    print(f"    {label}: {len(blob)} bytes, first {min(len(head), 48)} hex")
    print(f"      {head[:48].hex(' ')}")
    print(f"      as text: {printable}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reproduce ERR_SSL_PROTOCOL_ERROR through the gateway and "
                    "print what the tunnel actually carried")
    parser.add_argument("--host", default="www.google.com",
                        help="the host to handshake with, default the one the "
                             "ladder failed on")
    parser.add_argument("--attempts", type=int, default=8,
                        help="handshakes per session")
    parser.add_argument("--sessions", type=int, default=6,
                        help="how many distinct sticky sessions to draw")
    parser.add_argument("--country", default="any")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--gap", type=float, default=2.0,
                        help="seconds between handshakes on one session")
    args = parser.parse_args()

    provider = providers.load()
    creds = config.credentials(provider)
    print(f"gateway   : {creds.gateway}")
    print(f"provider  : {provider.id}")
    print(f"target    : {args.host}:443")
    print(f"plan      : {args.sessions} sessions x {args.attempts} handshakes, "
          f"{args.gap}s apart")
    print("credentials are not printed, and neither is the auth header\n")

    tally = collections.Counter()
    per_session = []
    for index in range(args.sessions):
        sid = f"tls{uuid.uuid4().hex[:8]}"
        params = proxy.session_params(sid, provider=provider,
                                      country=args.country)
        username = proxy.build_username(creds.login, provider=provider,
                                        **params)
        print(f"session {index + 1}/{args.sessions}  sid={sid}")
        outcomes = []
        for attempt in range(1, args.attempts + 1):
            sock = None
            try:
                sock, head = connect_tunnel(creds.gateway, args.host, 443,
                                            username, creds.password,
                                            args.timeout)
                first = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
                result = handshake(sock, args.host, args.timeout)
                verdict = classify(head, result)
                tally[verdict] += 1
                outcomes.append(verdict)
                mark = "ok " if result["ok"] else "FAIL"
                print(f"  #{attempt} {mark} {verdict}  ({result['ms']} ms)  "
                      f"connect: {first}")
                if not result["ok"]:
                    print(f"    why: {result['why']}")
                    show("tunnel", result["bytes"])
            except Exception as exc:
                tally[f"{type(exc).__name__}"] += 1
                outcomes.append(type(exc).__name__)
                print(f"  #{attempt} FAIL {type(exc).__name__}: {exc}")
            finally:
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
            if attempt < args.attempts:
                time.sleep(args.gap)
        per_session.append((sid, outcomes))
        print()

    print("=" * 68)
    total = sum(tally.values())
    for name, count in tally.most_common():
        print(f"  {count:4d}/{total}  {name}")
    print()
    # The question the run exists to answer: is a failure a property of the
    # session, or does it strike anywhere. A session that fails every attempt
    # and five that fail none is a pool problem; failures sprinkled evenly
    # across all of them is a gateway or a client problem.
    print("per session, in order:")
    for sid, outcomes in per_session:
        bad = sum(1 for o in outcomes if o != "ok")
        print(f"  {sid}  {bad}/{len(outcomes)} failed  "
              f"{' '.join('.' if o == 'ok' else 'X' for o in outcomes)}")
    spread = [sum(1 for o in outs if o != "ok") for _, outs in per_session]
    if total and any(spread):
        print()
        print("  Failures concentrated in some sessions and absent in others "
              "means the exit is the variable, which is what the ladder run "
              "showed. Evenly spread means it is not.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
