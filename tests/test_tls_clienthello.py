"""The listener and the attribution rule in `scripts/probes/tls_clienthello.py`.

The probe answers one question - which handshake did this engine send - and it
got that question wrong once already. On 2026-09-02 it recorded
`t13d1812h1_85036bcba153_b26ce05bbdd6` as `seleniumbase`'s: the OpenSSL
fingerprint of `requests`, which SeleniumBase's UC mode calls before the browser
navigates. Nineteen hellos arrived, two of them distinct, and the probe printed
the earliest with no warning that it had discarded anything. The row looked
exactly like a correct one.

That is the failure this file exists to keep out. A fingerprint column is a
claim about an engine; a wrong value in it is worse than a missing one, because
every reader downstream takes it at face value and nothing in the row says it
should not.

Nothing here starts an engine and nothing leaves this machine. The listener is
loopback, the clients are Python's own `ssl` and a subprocess of it, and the
attribution rule is fed hand-built triples so that each half can fail on its
own.
"""
import importlib.util
import socket
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from nmbench import tlsfp

ROOT = Path(__file__).resolve().parent.parent


def load():
    path = ROOT / "scripts" / "probes" / "tls_clienthello.py"
    spec = importlib.util.spec_from_file_location("tls_clienthello", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = load()


def speak_tls(port: int) -> None:
    """One handshake attempt at the listener, from this process.

    Fails, and is meant to: the listener answers nothing. The ClientHello is
    sent before it can fail, which is the whole subject.
    """
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((probe.TARGET_HOST, port), timeout=5) as raw:
            with context.wrap_socket(
                    raw, server_hostname=probe.TARGET_HOST) as tls:
                tls.recv(1)
    except OSError:
        pass


def wait_for_hellos(listener, count: int = 1, timeout: float = 10.0) -> list:
    deadline = time.time() + timeout
    while len(listener.collected()) < count and time.time() < deadline:
        time.sleep(0.02)
    return listener.collected()


@pytest.fixture
def listener():
    made = probe.HelloListener()
    yield made
    made.close()


class TestTheListenerReadsAHandshakeAndAnswersNothing:
    """The `SENDS_REQUESTS = False` claim, and the record it keeps."""

    def test_the_probe_declares_that_it_sends_nothing(self):
        # `python -m nmbench` reads this attribute to mark the command
        # `[offline]`, and this probe is the one that has to be runnable from
        # behind the gateway. If it ever grows a live host, this is where the
        # contradiction should surface.
        assert probe.SENDS_REQUESTS is False

    def test_a_handshake_is_captured_whole(self, listener):
        speak_tls(listener.port)
        hellos = wait_for_hellos(listener)
        assert len(hellos) == 1
        _arrived, _own, record = hellos[0]
        assert tlsfp.hello_is_complete(record)
        value, _readable = tlsfp.ja4(record)
        assert value and value.startswith("t13d")

    def test_the_target_is_a_name_so_that_sni_is_sent(self, listener):
        # Not cosmetic. A client sends no `server_name` to a bare address, which
        # flips the JA4 SNI character to `i` and drops the extension count by
        # one, and a fingerprint taken that way cannot be compared with one
        # taken against a real host. This is what makes the loopback listener a
        # replacement for `tls_echo.py` rather than a different measurement.
        assert probe.TARGET_HOST == "localhost"
        assert listener.url == f"https://localhost:{listener.port}/"
        speak_tls(listener.port)
        _arrived, _own, record = wait_for_hellos(listener)[0]
        assert tlsfp.parse_client_hello(record)["sni"] is True
        # `t` `13` `d` ... - the version is two characters, so the SNI flag is
        # at index 3. Written as 4 first, which is the same slip already made
        # and corrected once in `test_tlsfp.py`; it fails loudly, which is the
        # only reason it is cheap.
        assert tlsfp.ja4(record)[0][3] == "d"

    def test_a_peer_that_speaks_no_tls_is_not_recorded(self, listener):
        with socket.create_connection(("127.0.0.1", listener.port),
                                      timeout=5) as raw:
            raw.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        time.sleep(0.5)
        assert listener.collected() == []

    def test_nothing_is_written_back_to_the_client(self, listener):
        # The listener holds no certificate and implements no TLS. If it ever
        # answered, the fingerprint would stop being the first thing on the
        # wire and would start depending on what the answer was.
        received = []

        def talk():
            with socket.create_connection(("127.0.0.1", listener.port),
                                          timeout=5) as raw:
                raw.sendall(bytes.fromhex("160301"))
                raw.settimeout(1.0)
                try:
                    received.append(raw.recv(4096))
                except OSError:
                    received.append(b"")

        thread = threading.Thread(target=talk)
        thread.start()
        thread.join(10)
        assert received == [b""]


class TestEachEngineGetsItsOwnPort:
    """Why a straggler cannot be recorded under the next engine's name."""

    def test_two_listeners_do_not_share_a_port(self):
        first = probe.HelloListener()
        second = probe.HelloListener()
        try:
            assert first.port != second.port
        finally:
            first.close()
            second.close()

    def test_a_closed_listener_stops_accepting(self):
        # The anti-bleed property stated as behaviour rather than as intent. A
        # browser that is still retrying the previous engine's handshake meets a
        # closed port and is refused by the kernel, so its hellos cannot land in
        # the window that is open for somebody else.
        stale = probe.HelloListener()
        port = stale.port
        stale.close()
        fresh = probe.HelloListener()
        try:
            assert fresh.port != port
            speak_tls(port)
            time.sleep(0.5)
            assert fresh.collected() == []
        finally:
            fresh.close()


class TestOwnershipIsReadFromTheSocketAndNotGuessed:
    """`_is_our_own_socket`, the only thing that separates the two clients."""

    def test_a_connection_from_this_process_is_recognised(self, listener):
        speak_tls(listener.port)
        _arrived, own, _record = wait_for_hellos(listener)[0]
        assert own is True

    def test_a_connection_from_a_child_process_is_not(self, listener):
        # The case that matters. `seleniumbase`'s browser is a child process and
        # its `requests` call is not, and the recorded value has to come from
        # the first and not the second.
        script = (
            "import socket, ssl\n"
            "c = ssl.create_default_context()\n"
            "c.check_hostname = False\n"
            "c.verify_mode = ssl.CERT_NONE\n"
            "try:\n"
            f"    s = socket.create_connection(('localhost', {listener.port}),"
            " timeout=5)\n"
            "    c.wrap_socket(s, server_hostname='localhost').recv(1)\n"
            "except OSError:\n"
            "    pass\n"
        )
        subprocess.run([sys.executable, "-c", script], timeout=60,
                       capture_output=True, check=False)
        _arrived, own, _record = wait_for_hellos(listener)[0]
        assert own is False


class TestTheRule:
    """`choose`, fed by hand so the parse and the rule fail separately."""

    @staticmethod
    def hello(alpn=("h2",), host="localhost"):
        """A real ClientHello, produced by OpenSSL rather than assembled."""
        made = probe.HelloListener()
        try:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            context.set_alpn_protocols(list(alpn))
            try:
                with socket.create_connection((host, made.port),
                                              timeout=5) as raw:
                    context.wrap_socket(raw, server_hostname=host).recv(1)
            except OSError:
                pass
            return wait_for_hellos(made)[0][2]
        finally:
            made.close()

    def test_the_first_is_taken_and_the_rest_are_kept_visible(self):
        # camoufox, 2026-09-02: the first hello carried `compress_certificate`
        # and the nine that followed dropped it, because nothing answered. A
        # majority rule records the fallback; this one records the handshake a
        # working host would have seen, and still reports what it discarded.
        first = self.hello(alpn=("h2",))
        later = self.hello(alpn=("http/1.1",))
        chosen = probe.choose(
            [(1.0, False, first)] + [(1.1 + i, False, later) for i in range(9)],
            browser=True)
        assert chosen["tls_ja4"] == tlsfp.ja4(first)[0]
        assert chosen["distinct_ja4"] == 2
        assert chosen["hellos"] == 10
        assert [c["hellos"] for c in chosen["candidates"]] == [1, 9]

    def test_this_process_is_dropped_for_a_browser_engine(self):
        mine = self.hello(alpn=("http/1.1",))
        theirs = self.hello(alpn=("h2",))
        chosen = probe.choose([(1.0, True, mine), (1.1, False, theirs)],
                              browser=True)
        assert chosen["tls_ja4"] == tlsfp.ja4(theirs)[0]
        assert chosen["hellos_from_this_process"] == 1
        assert chosen["hellos"] == 1
        # One fingerprint survived, so there is nothing to warn about.
        assert chosen["distinct_ja4"] == 1
        assert chosen["candidates"] is None

    def test_this_process_is_kept_for_an_in_process_engine(self):
        # `http` and `curl_cffi` run in this process, so their handshake is the
        # answer rather than the confound. Without this scoping the exclusion
        # would delete the very row it is supposed to protect.
        mine = self.hello()
        chosen = probe.choose([(1.0, True, mine)], browser=False)
        assert chosen["tls_ja4"] == tlsfp.ja4(mine)[0]
        assert chosen["hellos_from_this_process"] == 0
        assert chosen["hellos"] == 1

    def test_an_unattributable_second_client_gets_no_value(self):
        # `psutil` absent, so ownership is None. One fingerprint is still safe;
        # two are not, because the first could be either client's.
        one = self.hello(alpn=("h2",))
        other = self.hello(alpn=("http/1.1",))
        assert probe.choose([(1.0, None, one)],
                            browser=True)["tls_ja4"] == tlsfp.ja4(one)[0]
        chosen = probe.choose([(1.0, None, one), (1.1, None, other)],
                              browser=True)
        assert chosen["tls_ja4"] is None
        assert chosen["ja4_r"] is None
        assert chosen["distinct_ja4"] == 2
        assert chosen["candidates"] is not None

    def test_the_readable_form_belongs_to_the_value_reported(self):
        one = self.hello(alpn=("h2",))
        chosen = probe.choose([(1.0, False, one)], browser=True)
        value, readable = tlsfp.ja4(one)
        assert (chosen["tls_ja4"], chosen["ja4_r"]) == (value, readable)

    def test_nothing_arriving_is_reported_as_nothing(self):
        chosen = probe.choose([], browser=True)
        assert chosen["tls_ja4"] is None
        assert chosen["hellos"] == 0
        assert chosen["distinct_ja4"] == 0

    def test_records_that_do_not_parse_are_not_counted_as_fingerprints(self):
        chosen = probe.choose([(1.0, False, b"\x16\x03\x01\x00\x02\x01\x00")],
                              browser=True)
        assert chosen["distinct_ja4"] == 0
        assert chosen["tls_ja4"] is None


class TestTheComparisonAgainstTheStockBrowser:
    """`compare_to_reference`, which is what turns the sweep into an answer.

    The split is the part a reader acts on, so it has to be wrong loudly rather
    than quietly. The case that would be quiet is a missing reference: a machine
    with no Chrome would otherwise compare every engine against None, put all of
    them in one bucket and print a table that looks exactly like a measurement.
    """

    CHROME = "t13d1516h2_8daaf6152771_d8a2da3f94cd"
    OTHER = "t13d1516h2_8daaf6152771_806a8c22fdea"

    def test_engines_are_split_by_whether_they_match(self):
        against = probe.compare_to_reference(
            {"cloak": (self.CHROME, "149.0.7827.201"),
             "chromium": (self.OTHER, "151.0.7922.34"),
             "http": ("t13d1812h1_85036bcba153_b26ce05bbdd6", "2.34.2")},
            (self.CHROME, "149.0.7827.201"))
        assert [n for n, _v, _e in against["same"]] == ["cloak"]
        assert [n for n, _v, _e in against["different"]] == ["chromium", "http"]

    def test_the_engine_version_travels_with_the_verdict(self):
        # Not decoration. A non-match is ambiguous on its own - the same engine
        # on a different Chrome build lands in a different group, measured
        # 2026-09-02 - so the build has to be on the same line as the split or
        # the reader cannot tell a property of the engine from a property of the
        # browser it launched.
        against = probe.compare_to_reference(
            {"chromium": (self.OTHER, "151.0.7922.34")},
            (self.CHROME, "149.0.7827.201"))
        assert against["different"] == [
            ("chromium", self.OTHER, "151.0.7922.34")]

    def test_no_reference_is_reported_as_no_comparison(self):
        # The quiet failure this guards. Without it every engine would be
        # compared against None, land in `different` together, and print as a
        # finding.
        results = {"chromium": (self.OTHER, "151.0.7922.34")}
        assert probe.compare_to_reference(results, None) is None
        assert probe.compare_to_reference(results, (None, None)) is None

    def test_the_reference_is_the_installed_chrome_and_not_the_bundled_one(self):
        # `chromium` with no channel launches Playwright's bundled Chromium,
        # which is not the browser anybody is being compared against. The
        # channel is what makes this the Chrome on the machine.
        assert probe.REFERENCE == ("chromium", "chrome")
