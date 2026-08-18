"""Is this line answering CONNECT itself?

Run before any proxied work. It needs no credentials, touches no gateway and no
benchmark target, and it costs four small requests to servers that are not
proxies.

The check exists because of 2026-08-13, when every proxied attempt from this
machine began failing and the obvious reading was that the provider was down.
It was not. Something between this machine and the internet answered every
outbound CONNECT itself with `400 Bad Request` and `Server: cloudflare`,
including CONNECTs addressed to Google - which does not answer as Cloudflare. An
hour could have gone into debugging the username DSL, and a support ticket could
have gone out blaming a gateway that was never asked.

The culprit turned out to be a VPN client running on this machine, holding the
default route through a Cloudflare-fronted remote, and that is the reason the
docstring says "something" rather than naming a layer. The probe cannot tell an
ISP middlebox from a local tunnel and must not pretend to: what it establishes
is that CONNECT is not reaching the internet unmodified, and where the box sits
is the next question rather than this one's answer.

The method is a control, not a signature. Each host is sent a CONNECT and an
ordinary GET on the same port. A healthy line produces different `Server`
headers for different hosts, because the hosts really are different. An
intercepted line produces one voice for every CONNECT and the hosts' own voices
for every GET, and that difference is what names the interception rather than
the outage.

Do not tighten this into a test for the string `cloudflare`. The forged identity
is whatever the box in front of you chose; the evidence is that Google stopped
sounding like Google, and that survives a box that picks a different disguise.
"""
import argparse
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

SENDS_REQUESTS = True

# Deliberately not proxies, and deliberately run by different operators. If one
# of them is intercepted the answer is ambiguous; if all of them speak with one
# voice on CONNECT and their own on GET, nothing but a box in the middle
# explains it.
HOSTS = [("www.google.com", 80), ("example.com", 80), ("1.1.1.1", 80)]
TIMEOUT = 8


def read_head(sock) -> str:
    buffer = b""
    while b"\r\n\r\n" not in buffer:
        try:
            chunk = sock.recv(65536)
        except OSError:
            return ""
        if not chunk:
            return ""
        buffer += chunk
        if len(buffer) > 65536:
            break
    return buffer.decode("latin-1")


def ask(host: str, port: int, request: bytes) -> dict:
    result = {"status": None, "server": "", "error": None}
    try:
        sock = socket.create_connection((host, port), timeout=TIMEOUT)
        sock.settimeout(TIMEOUT)
        sock.sendall(request)
        head = read_head(sock)
        sock.close()
    except OSError as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    if not head:
        return result
    lines = head.split("\r\n")
    bits = lines[0].split(" ")
    if len(bits) > 1 and bits[1].isdigit():
        result["status"] = int(bits[1])
    for line in lines[1:]:
        if line.lower().startswith("server:"):
            result["server"] = line.split(":", 1)[1].strip()
    return result


def probe(host: str, port: int) -> dict:
    connect = ask(host, port,
                  b"CONNECT example.org:443 HTTP/1.1\r\n"
                  b"Host: example.org:443\r\n\r\n")
    plain = ask(host, port,
                f"GET / HTTP/1.1\r\nHost: {host}\r\n"
                f"Connection: close\r\n\r\n".encode())
    return {"host": f"{host}:{port}", "connect": connect, "get": plain}


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()

    rows = [probe(host, port) for host, port in HOSTS]

    print(f"{'host':22} {'CONNECT':28} {'GET':28}")
    for row in rows:
        con, get = row["connect"], row["get"]
        print(f"{row['host']:22} "
              f"{str(con['status']) + ' ' + (con['server'] or con['error'] or '-'):28} "
              f"{str(get['status']) + ' ' + (get['server'] or get['error'] or '-'):28}")

    voices = {r["connect"]["server"] for r in rows if r["connect"]["server"]}
    answered = [r for r in rows if r["connect"]["status"] is not None]
    own = {r["get"]["server"] for r in rows if r["get"]["server"]}

    print()
    if not answered:
        print("no host answered a CONNECT at all. That is what a healthy line "
              "looks like when nothing is listening for one, and it is also "
              "what a line that drops CONNECT looks like. Inconclusive: try "
              "again, or from another network.")
        return 0
    if len(voices) == 1 and len(own) > 1:
        voice = next(iter(voices))
        print(f"INTERCEPTED. Every CONNECT was answered by {voice!r} while the "
              f"same hosts answered GET as {sorted(own)}. These servers are not "
              f"proxies and do not share an operator, so one voice on CONNECT "
              f"and several on GET is a box in the middle.")
        print("Proxied runs from this line will fail for reasons that have "
              "nothing to do with the provider. Do not open a ticket and do "
              "not read a failure rate off this network.")
        return 1
    print("no interception found: CONNECT was not answered in one voice. "
          "Proxied runs can be read normally.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
