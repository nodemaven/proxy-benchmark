"""How many unauthenticated CONNECTs a browser sends before it uses its credential.

The gateway's unanswered-CONNECT rate fell from 18.3% to 0.1% at 21:00 UTC on
2026-08-19, inside one uninterrupted run, and the drop is not spread evenly over
the engines. Counting every error rather than one error string, the split is:

    via the local authenticating relay    10/421 = 2.4%  ->   7/322 = 2.2%
    browser opens its own CONNECTs       185/568 = 32.6% ->  14/432 = 3.2%

The relay is the only thing those three engines have that the other five do not,
and what it does at the wire is put `Proxy-Authorization` on the *first* CONNECT
it sends upstream (`relay._tunnel`). A browser handed proxy credentials does not
have to behave that way: HTTP proxy authentication is a challenge-response, so
the ordinary shape is CONNECT without a credential, 407 back, CONNECT again with
one. If that is what the engines do, then every tunnel they open registers at
the gateway as an unauthenticated attempt, which is the input to the very
counter whose behaviour changed at 21:00.

That is a hypothesis about a browser, so it is answerable without a gateway.
This probe is a proxy on loopback that demands authentication like the real one
and then joins the tunnel straight to the target, so it measures the client and
spends no pool traffic at all.

Two numbers come out of it and they are worth different amounts:

- **whether the first CONNECT carries a credential.** This is the claim above,
  and either answer settles it.
- **whether the next tunnel to a different host carries one.** This is the size
  of the effect and it is the part nobody can predict from the protocol. If the
  browser caches the challenge per profile, a page load costs one unauthenticated
  CONNECT however many hosts it touches, and at `--batch 1` that is one per
  attempt. If it does not, a page costs one per third-party host, which is tens.
  Those two readings differ by more than an order of magnitude and only one of
  them makes an IP-level counter plausible as a cause.

The credentials handed to the browser are dummies and the probe records only
whether the header was present, never its value: it is base64 and printing one
is how a credential ends up in a terminal capture.

Chromium is launched twice, once the way `ChromiumEngine.open` does it and once
the way `PatchrightEngine.open` does, because those differ in a way that could
matter here - a persistent profile has somewhere to cache a challenge and a
fresh context may not. Camoufox is the third arm because it is the one engine on
the losing side of the table that is not Chromium, and if the shape is a
property of proxy authentication rather than of Chrome it has to appear there
too.

Usage:
    python scripts/probes/proxy_auth_shape.py
    python scripts/probes/proxy_auth_shape.py --engines patchright
    python scripts/probes/proxy_auth_shape.py --headful
"""
import argparse
import socket
import sys
import tempfile
import threading
from importlib import import_module
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nmbench.console import tolerate_unencodable_output

SENDS_REQUESTS = True

CHUNK = 65536
HEAD_TIMEOUT = 30.0
IDLE_TIMEOUT = 30.0

# Three hosts rather than one, and deliberately small ones. The question needs
# more than a single authority to see whether a credential is reused, and a page
# heavy enough to pull in third-party hosts would answer that too while making
# the tally unreadable.
HOSTS = ("https://example.com/", "https://api.ipify.org/", "https://www.iana.org/")

CHALLENGE = (b"HTTP/1.1 407 Proxy Authentication Required\r\n"
             b'Proxy-Authenticate: Basic realm="probe"\r\n'
             b"Proxy-Connection: keep-alive\r\n"
             b"Content-Length: 0\r\n\r\n")


def _read_head(sock):
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(CHUNK)
        if not chunk:
            return ""
        buf += chunk
        if len(buf) > 65536:
            return ""
    return buf.decode("latin-1")


def _has_auth(head):
    # Header names are case-insensitive and a dict over a parsed message is not.
    # The value is never read: presence is the whole measurement.
    return any(line.lower().startswith("proxy-authorization:")
               for line in head.split("\r\n"))


class CountingProxy:
    """Loopback proxy that challenges unauthenticated CONNECTs and records both.

    It does not forward to a gateway. Once authenticated it opens the tunnel to
    the target directly, so the browser gets a working connection and no proxy
    quota is spent.
    """

    def __init__(self):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(128)
        self.port = self.sock.getsockname()[1]
        self.events = []
        self._lock = threading.Lock()
        self._stop = False

    def start(self):
        threading.Thread(target=self._serve, daemon=True).start()
        return self

    def close(self):
        self._stop = True
        self.sock.close()

    def _serve(self):
        self.sock.settimeout(0.5)
        while not self._stop:
            try:
                client, _ = self.sock.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _record(self, method, target, had_auth):
        with self._lock:
            self.events.append((method, target, had_auth))

    def _handle(self, client):
        try:
            client.settimeout(HEAD_TIMEOUT)
            while True:
                head = _read_head(client)
                if not head:
                    return
                line = head.split("\r\n", 1)[0].split(" ")
                method = line[0].upper()
                target = line[1] if len(line) > 1 else "?"
                had_auth = _has_auth(head)
                self._record(method, target, had_auth)
                if method != "CONNECT":
                    client.sendall(b"HTTP/1.1 501 Not Implemented\r\n"
                                   b"Content-Length: 0\r\n\r\n")
                    return
                if not had_auth:
                    client.sendall(CHALLENGE)
                    continue
                self._tunnel(client, target)
                return
        except (OSError, ValueError):
            pass
        finally:
            client.close()

    def _tunnel(self, client, authority):
        host, _, port = authority.rpartition(":")
        try:
            upstream = socket.create_connection((host, int(port or 443)), timeout=15)
        except (OSError, ValueError):
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            return
        try:
            client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            self._pump(client, upstream)
        finally:
            upstream.close()

    @staticmethod
    def _pump(a, b):
        import select
        a.settimeout(None)
        b.settimeout(None)
        while True:
            ready, _, _ = select.select([a, b], [], [], IDLE_TIMEOUT)
            if not ready:
                return
            for src in ready:
                dst = b if src is a else a
                try:
                    data = src.recv(CHUNK)
                except OSError:
                    return
                if not data:
                    return
                try:
                    dst.sendall(data)
                except OSError:
                    return


def _visit(page, hosts):
    for url in hosts:
        try:
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
        except Exception as exc:
            print(f"    goto {url}: {type(exc).__name__}")


def _chromium(driver, port, hosts, headless, persistent):
    api = import_module(f"{driver}.sync_api")
    cfg = {"server": f"http://127.0.0.1:{port}",
           "username": "probe", "password": "probe"}
    with api.sync_playwright() as pw:
        if persistent:
            profile = tempfile.mkdtemp(prefix="authshape-")
            context = pw.chromium.launch_persistent_context(
                user_data_dir=profile, headless=headless, proxy=cfg,
                no_viewport=True)
            browser = None
        else:
            browser = pw.chromium.launch(headless=headless, proxy=cfg)
            context = browser.new_context()
        try:
            _visit(context.new_page(), hosts)
        finally:
            context.close()
            if browser is not None:
                browser.close()


def _camoufox(port, hosts, headless):
    from camoufox.sync_api import Camoufox
    cfg = {"server": f"http://127.0.0.1:{port}",
           "username": "probe", "password": "probe"}
    with Camoufox(headless=headless, humanize=False, geoip=False,
                  block_webrtc=True, proxy=cfg) as browser:
        context = browser.new_context()
        try:
            _visit(context.new_page(), hosts)
        finally:
            context.close()


ARMS = {
    "chromium": lambda p, h, hl: _chromium("playwright", p, h, hl, persistent=False),
    "patchright": lambda p, h, hl: _chromium("patchright", p, h, hl, persistent=True),
    "camoufox": lambda p, h, hl: _camoufox(p, h, hl),
}


def main():
    tolerate_unencodable_output()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--engines", default="chromium,patchright,camoufox")
    ap.add_argument("--hosts", default=",".join(HOSTS))
    ap.add_argument("--headful", action="store_true")
    args = ap.parse_args()

    hosts = [h for h in args.hosts.split(",") if h]
    for name in args.engines.split(","):
        if name not in ARMS:
            print(f"unknown engine: {name}")
            return 2
        proxy = CountingProxy().start()
        print(f"\n=== {name}, proxy on 127.0.0.1:{proxy.port}")
        try:
            ARMS[name](proxy.port, hosts, not args.headful)
        except Exception as exc:
            print(f"    launch failed: {type(exc).__name__}: {exc}")
        finally:
            proxy.close()

        events = proxy.events
        connects = [e for e in events if e[0] == "CONNECT"]
        unauth = [e for e in connects if not e[2]]
        authed = [e for e in connects if e[2]]
        print(f"    CONNECT {len(connects)}, without credential {len(unauth)}, "
              f"with credential {len(authed)}")
        seen = [f"{method} {target} {'auth' if had else 'NO-AUTH'}"
                for method, target, had in events]
        for line in seen[:40]:
            print("      " + line)
        if len(seen) > 40:
            print(f"      ... {len(seen) - 40} more")
        first_per_host = {}
        for _, target, had in connects:
            first_per_host.setdefault(target.rpartition(":")[0], had)
        cold = sum(1 for v in first_per_host.values() if not v)
        print(f"    distinct hosts {len(first_per_host)}, of which the first "
              f"CONNECT carried no credential: {cold}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
