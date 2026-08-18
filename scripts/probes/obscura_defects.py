"""The defects that keep Obscura out of an engine comparison.

This engine has been excluded from the tables for reasons recorded in prose and
re-checked by nothing until this file existed. One turned out to have been fixed
two days after it was written down; one turned out never to have been measured at
all; and one - the third below - turned out to be a defect this repository had
explicitly written off as harmless. That is the argument for the file: a defect
that is only described is one nobody notices the end of, a capability that is
only declared is one nobody notices the absence of, and a defect that has been
argued away is one nobody re-measures.

    STATUS  can this engine's rows be split into served and refused
    TYPING  can the target's own search box be entered
    AGENT   does the browser tell the network what it tells the page

All three are asked of a server on this machine, so the file costs no traffic and
can be pointed at cases a real target will not produce on demand. That needs
`--allow-private-network`, which the engine keeps off everywhere else - see
`ObscuraEngine.open`.

Sends nothing to any target, needs no credentials, touches no gateway. Run it
after any Obscura upgrade.

STATUS
------

This engine spent most of its history excluded from the served-versus-refused
tables because `status` was null on every row it produced, which made
`report.was_served` structurally false for it: not a result about Obscura, an
instrument that could not see. The adapter has recorded the status since
2026-08-12 and the fix has never been re-checked by anything, so it is one
upgrade away from silently reverting to the state that cost the engine its
column.

Silently is the word that matters. A build that stopped reporting the status
would not fail a run, raise an error or change a verdict - every row would keep
its verdict, its bytes and its timing, and only the one column that decides
whether a refusal belongs to the address or to the browser would go quiet.

The check is three responses from a server on this machine: a plain 200, a plain
503, and a **redirect** to that 503. The redirect is the case worth the whole
probe. Google refuses an exit by sending it to `/sorry/`, so on the target this
repository cares most about, the refusal *is* a redirect, and it is exactly the
case where this browser hands `page.goto` back a None. Measured 2026-08-14 on
obscura 0.2.0: goto returns 200 and 503 correctly for the two plain responses
and None for the redirect, where the response event reports the post-redirect
503. A check that only asked for a plain 200 would pass on a build that had lost
every Google refusal.

TYPING
------

`supports_typing` was False for this engine on the grounds that the status was
missing, so when the status was fixed the flag looked free to flip. It is not,
and the reason was found by turning it on and running one query: **this renderer
lays out a `<textarea>` with height 0**. Measured 2026-08-14 on one local page -
every other element reports a correct box and the textarea reports 1264x0,
against 168x36 on Chromium for identical markup. Playwright resolves it to
`hidden`, `wait_for_selector(state="visible")` waits its full 60 s, and every
typed attempt on every target is recorded as an `error`.

It lands where it costs the most: Google's search box is a textarea, which this
repository already knew from the zendriver clearing bug. So the check below
compares an `<input>` against a `<textarea>` on one page, because a probe that
only looked at the input would find nothing wrong.

AGENT
-----

`engine_fingerprint.py` reads `navigator.userAgent` in the page, and on that
reading this browser is clean: it reports the host OS correctly and carries no
`HeadlessChrome`. NOTEBOOK.md and `engines/obscura.py` both then say the
`X11; Linux x86_64` string this build is known to carry is CDP banner metadata,
"not the identity a target sees", and that it must not be quoted as a fingerprint
defect. That was reasoned from where the string was found rather than measured,
and it is the kind of claim this file exists to refuse.

The question a page cannot answer is what went out on the wire. A target reads
the `User-Agent` **request header** before any script runs, and a browser whose
header and whose JavaScript disagree about the operating system is not merely
odd - it is a contradiction no real browser produces, available to the cheapest
possible detector, on the first byte of the first request. So the server here
records the header it was sent and the check compares it against what the page
says about itself. One string, two sources, and they have to agree.

`obscura serve --user-agent` exists, so if they disagree this is a defect with a
switch beside it rather than a property of the engine.
"""
import argparse
import http.server
import socket
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nmbench.console import tolerate_unencodable_output

tolerate_unencodable_output()

from nmbench import engines
from nmbench.targets import Judgement

SENDS_REQUESTS = False

# Enough markup to have a title and a body the judge can be handed. The verdict
# is not what is under test and the target below returns a fixed one; what is
# under test is the column beside it.
PAGE = (b"<html><head><title>local</title></head>"
        b"<body><p id='marker'>local</p>"
        # The two form controls a search box is ever built from, laid out
        # normally and side by side. Chromium gives both a box; the typing
        # check below is only interesting because one engine does not.
        b"<input type='text' id='an-input'>"
        b"<textarea id='a-textarea'></textarea>"
        b"</body></html>")

# Elements the typed entry shape has to be able to see, and what a browser that
# lays out its page has to say about them. `search_box` on Google resolves to a
# textarea and on Amazon to an input, so both are load-bearing here.
LAID_OUT = ("#an-input", "#a-textarea")

# path, what the server answers, what the row must end up recording
CASES = (
    ("/ok", 200, 200, "a plain success"),
    ("/fail", 503, 503, "a plain refusal"),
    ("/redirect", 302, 503, "a redirect to a refusal, which is how Google "
                            "serves /sorry/"),
)

# Request headers as the far end received them, newest last, keyed in lower
# case. This is the only thing in the probe that a page cannot be asked for:
# everything else here is read back through CDP, and the whole point of the AGENT
# check is that CDP and the socket can disagree.
#
# Lower-cased deliberately. Header names are case-insensitive and `dict()` over
# the parsed message throws that away, so a client sending `user-agent` would be
# recorded under a key nothing looks up and read as having sent no agent at all.
# That is a false positive on the one check here whose failure mode is an absent
# string, which is to say it would have manufactured the defect it exists to
# find.
RECEIVED = []


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        # The default handler writes every request to stderr, which would put
        # the probe's own plumbing in the middle of its output.
        pass

    def do_GET(self):
        RECEIVED.append({k.lower(): v for k, v in self.headers.items()})
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/fail")
            self.end_headers()
            return
        self.send_response(503 if self.path == "/fail" else 200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)


class _LocalTarget:
    """A target the probe can point at itself.

    Deliberately a real target object rather than a mock: the point is to drive
    `ObscuraSession.fetch`, which is the code a matrix run uses, so that the
    check covers the path that writes rows rather than a copy of it kept in
    step by hand.
    """

    name = "local_status_probe"
    needs_script = False
    ready_selector = "#marker"

    def __init__(self, base: str, path: str):
        self._url = f"{base}{path}"

    def url(self, query: str) -> str:
        return self._url

    def judge(self, url: str, title: str, html: str) -> Judgement:
        # Fixed, because a verdict here would be a claim about our own server.
        return Judgement("ok", "served by the probe's own local server")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _serve():
    port = _free_port()
    server = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{port}"


def check_status(session, base: str) -> bool:
    """Does a row this engine writes carry the HTTP status it was answered with.

    Driven through `session.fetch`, which is the call a matrix run makes, so the
    three cases below exercise the code that writes rows rather than a copy of
    it.
    """
    results = []
    for path, served, expected, why in CASES:
        row = session.fetch(_LocalTarget(base, path), "probe")
        got = row.get("status")
        ok = got == expected
        results.append(ok)
        print(f"  {path:<10} server answered {served:<4} "
              f"row recorded {got!s:<6} "
              f"{'OK' if ok else 'WRONG, expected ' + str(expected)}")
        print(f"  {'':<10} {why}")
        if row.get("error"):
            print(f"  {'':<10} the attempt also threw: {row['error']}")
    return all(results) and len(results) == len(CASES)


def check_typing(session, base: str) -> bool:
    """Does this browser lay out the elements a query has to be typed into.

    Read through `bounding_box` and `is_visible` rather than by typing, because
    the failure this is written for costs 60 s per attempt and says nothing
    about its cause: `submit_query` waits on `state="visible"`, Playwright
    decides that from the box, and a box of zero height is `hidden` however
    correct the rest of the element is. Asking for the box directly is the same
    question one layer down, it answers in milliseconds, and it names the number
    that is wrong.
    """
    page = session.new_page()
    results = []
    try:
        page.goto(f"{base}/ok", wait_until="domcontentloaded", timeout=30000)
        for selector in LAID_OUT:
            box = page.locator(selector).bounding_box()
            visible = page.locator(selector).is_visible()
            # Height is what fails and width is printed beside it because the
            # measured defect gets the width right - 1264x0 on this renderer
            # against 168x36 on Chromium. A summary that only said "hidden"
            # would read as the element being missing, which it is not.
            laid_out = bool(box) and box["height"] > 0 and box["width"] > 0
            ok = laid_out and visible
            results.append(ok)
            shape = (f"{box['width']:.0f}x{box['height']:.0f}" if box
                     else "no box at all")
            print(f"  {selector:<12} {shape:<14} "
                  f"visible={visible!s:<6} "
                  f"{'OK' if ok else 'NOT LAID OUT'}")
    except Exception as exc:
        print(f"  the page did not load: {type(exc).__name__}: {exc}")
        return False
    finally:
        page.close()
    return all(results) and len(results) == len(LAID_OUT)


def _platform(agent: str) -> str:
    """The operating system a User-Agent claims, or the string itself.

    Compared on the platform rather than on the whole agent because the two
    sources legitimately differ in ways that are nobody's defect: a build may
    report a different Chrome version to the network than the one V8 answers
    with, and a comparison on the full string would fail on that and read as this
    defect. The operating system is the part no real browser can disagree with
    itself about.
    """
    for token in ("Windows NT", "X11; Linux", "Macintosh", "Android", "iPhone"):
        if token in agent:
            return token
    return agent


def check_user_agent(session, base: str) -> bool:
    """Does this browser send the network the same identity it shows the page.

    Read from both ends of one request rather than from the browser twice. The
    header comes off the socket, which is where a target reads it, and the page
    value comes from CDP, which is where `engine_fingerprint.py` reads it - so a
    disagreement between them is exactly the thing neither of those two readings
    alone can see.
    """
    del RECEIVED[:]
    page = session.new_page()
    try:
        page.goto(f"{base}/ok", wait_until="domcontentloaded", timeout=30000)
        in_page = page.evaluate("() => navigator.userAgent")
    except Exception as exc:
        print(f"  the page did not load: {type(exc).__name__}: {exc}")
        return False
    finally:
        page.close()

    headers = [h for h in RECEIVED if h.get("user-agent")]
    if not headers:
        # Not a pass. The comparison did not happen, and reporting that as
        # agreement is how a broken check goes quiet. Sending no agent at all is
        # also a finding in its own right and a louder one than a mismatch, so
        # the names of what did arrive are printed rather than summarised.
        print("  the server received no User-Agent header at all, so the two "
              "identities could not be compared")
        print(f"  what did arrive: "
              f"{sorted(RECEIVED[-1]) if RECEIVED else 'no request at all'}")
        return False
    on_wire = headers[-1]["user-agent"]

    print(f"  header   {on_wire}")
    print(f"  in page  {in_page}")
    agree = _platform(on_wire) == _platform(in_page)
    print(f"  platform {_platform(on_wire)} on the wire against "
          f"{_platform(in_page)} in the page - "
          f"{'OK' if agree else 'CONTRADICTION'}")
    return agree


def check(engine_name: str) -> bool:
    engine = engines.REGISTRY[engine_name]
    unavailable = engine.check()
    if unavailable:
        print(f"{engine_name} cannot run here: {unavailable}")
        return False

    server, base = _serve()
    try:
        # One server and one browser for both checks. Not to be quick: a second
        # launch would let the two answers come from two processes, and the
        # whole point of asking them together is that they describe one build.
        with engines.session(engine_name, direct=True, params={},
                             preset="none",
                             allow_private_network=True) as session:
            print("STATUS - a plain 200, a plain 503, and a redirect to that "
                  "503, all from a server on this machine")
            status_ok = check_status(session, base)
            print("\nTYPING - the two controls a search box is built from, on "
                  "one ordinary page")
            typing_ok = check_typing(session, base)
            print("\nAGENT - the User-Agent off the socket against the one the "
                  "page reports")
            agent_ok = check_user_agent(session, base)
    except Exception as exc:
        print(f"  the session did not run: {type(exc).__name__}: {exc}")
        return False
    finally:
        server.shutdown()

    print()
    if status_ok:
        print("STATUS OK. The status column is filled and correct, including "
              "through a redirect, so this engine's rows can be split into "
              "served and refused.")
    else:
        print("STATUS BROKEN. Every row this engine writes will be dropped by "
              "anything grouping on status, and its refusals cannot be "
              "attributed to the address rather than to the browser. Do not "
              "put it in a served-versus-refused table until this passes.")
    if typing_ok:
        print("TYPING OK. Both controls are laid out and visible, so this "
              "engine can be entered through a target's own search box. If "
              "`supports_typing` is still False, that flag is now the only "
              "thing keeping it out of an entry-shape comparison.")
    else:
        print("TYPING BROKEN. A control this browser does not lay out cannot "
              "be waited on: `wait_for_selector(state=\"visible\")` spends its "
              "full 60 s and every typed attempt is recorded as an `error`, on "
              "every target. Google's search box is a textarea, so this costs "
              "the target it costs the most. `supports_typing` must stay "
              "False.")
    if agent_ok:
        print("AGENT OK. The header and the page agree on the operating "
              "system, so this build presents one identity and rows it wrote "
              "are not carrying a contradiction a target could read for free.")
    else:
        print("AGENT BROKEN. This build tells the network one operating system "
              "and the page another, which no real browser does and which any "
              "target sees on the first request, before a script runs. Every "
              "row this engine has written was measured on a client giving "
              "itself away for free, so its refusals cannot be read as the "
              "engine's anti-detection failing. Pass --user-agent and re-run "
              "before quoting a number from it.")
    return status_ok and typing_ok and agent_ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", default="obscura",
                        help="any engine whose session takes "
                             "allow_private_network; only obscura does")
    args = parser.parse_args()

    if args.engine not in engines.REGISTRY:
        parser.error(f"unknown engine {args.engine!r}, "
                     f"known: {engines.names()}")

    print(f"asking {args.engine} the three questions that keep it out of an "
          f"engine comparison\n")
    return 0 if check(args.engine) else 1


if __name__ == "__main__":
    raise SystemExit(main())
