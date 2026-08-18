"""The verdict in `scripts/probes/connect_integrity.py`.

This probe gates every proxied run and its two failure modes are not
symmetrical. A missed interception costs an afternoon and a support ticket
blaming a gateway nobody asked. A false alarm costs more than it looks: it says
"do not read a failure rate off this network" about a network that was fine, and
the second time it cries wolf nobody stops.

It cried wolf on 2026-08-18, which is why this file exists. Nothing here sends
anything - the rows are the ones two real lines produced, and the intercepted
line existed for a few hours in August and cannot be summoned back to re-measure.
"""
import importlib.util
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parent.parent


def load():
    path = ROOT / "scripts" / "probes" / "connect_integrity.py"
    spec = importlib.util.spec_from_file_location("connect_integrity", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


connect_integrity = load()


def row(host, connect, get):
    """One host's pair of answers, shaped as `probe` returns them."""
    def answer(pair):
        if pair is None:
            return {"status": None, "server": "", "error": "TimeoutError"}
        status, server = pair
        return {"status": status, "server": server, "error": None}

    return {"host": host, "connect": answer(connect), "get": answer(get)}


class TestACleanLineIsNotReportedAsIntercepted:
    """Measured 2026-08-18 on a server with no tunnel in front of it."""

    ROWS: ClassVar = [
        # Google refuses the method in its own voice, and its own voice here
        # carries no `Server` header at all.
        row("www.google.com:80", (404, ""), (200, "gws")),
        # These two really are operated by Cloudflare, on CONNECT and on GET
        # alike, so `cloudflare` on a CONNECT reply is not evidence of anything.
        row("example.com:80", (400, "cloudflare"), (200, "cloudflare")),
        row("1.1.1.1:80", (400, "cloudflare"), (301, "cloudflare")),
    ]

    def test_the_hosts_differ_so_nothing_is_in_the_middle(self):
        code, message = connect_integrity.verdict(self.ROWS)
        assert code == 0
        assert "no interception found" in message

    def test_the_headerless_answer_is_the_whole_of_the_difference(self):
        """The bug, pinned in the direction it actually failed.

        Two of these three hosts really are Cloudflare and really do answer
        CONNECT identically, so Google's reply is the only thing separating
        this line from an intercepted one. Building the set of voices from the
        `Server` header alone dropped exactly that reply - it carries no
        `Server` at all - which left one voice and produced the false alarm.

        Both halves are asserted, because the second is what makes the fix
        load-bearing rather than cosmetic: on these rows the old rule really
        did see a single voice.
        """
        voices = {connect_integrity.voice(r["connect"]) for r in self.ROWS}
        assert voices == {(404, ""), (400, "cloudflare")}

        headers_only = {r["connect"]["server"] for r in self.ROWS
                        if r["connect"]["server"]}
        assert headers_only == {"cloudflare"}


class TestTheInterceptedLineIsStillCaught:
    """Measured 2026-08-13, the hours this probe was written for.

    Three unrelated hosts, one voice on CONNECT, their own voices on GET.
    """

    ROWS: ClassVar = [
        row("www.google.com:80", (400, "cloudflare"), (200, "gws")),
        row("example.com:80", (400, "cloudflare"), (200, "cloudflare")),
        row("1.1.1.1:80", (400, "cloudflare"), (301, "cloudflare")),
    ]

    def test_it_is_reported_and_the_exit_code_is_a_failure(self):
        code, message = connect_integrity.verdict(self.ROWS)
        assert code == 1
        assert "INTERCEPTED" in message

    def test_a_box_choosing_another_disguise_is_caught_too(self):
        """The evidence is agreement between hosts, never a vendor name."""
        rows = [row(r["host"], (503, "nginx"), connect_integrity.voice(r["get"]))
                for r in self.ROWS]
        code, message = connect_integrity.verdict(rows)
        assert code == 1
        assert "nginx" in message

    def test_agreement_with_no_server_header_at_all_is_still_agreement(self):
        rows = [row(r["host"], (400, ""), connect_integrity.voice(r["get"]))
                for r in self.ROWS]
        code, _ = connect_integrity.verdict(rows)
        assert code == 1


class TestTheClaimNeedsEnoughAnswersToMakeIt:
    def test_no_answer_at_all_is_inconclusive_rather_than_clean(self):
        rows = [row(f"h{i}:80", None, (200, f"s{i}")) for i in range(3)]
        code, message = connect_integrity.verdict(rows)
        assert code == 0
        assert "Inconclusive" in message

    def test_one_host_agreeing_with_itself_is_not_a_chorus(self):
        """Two answers are the fewest that can be the same.

        A single reply satisfies "every CONNECT sounded alike" trivially, and
        the sentence this probe prints is a claim about hosts sounding like
        each other.
        """
        rows = [row("www.google.com:80", (400, "cloudflare"), (200, "gws")),
                row("example.com:80", None, (200, "cloudflare")),
                row("1.1.1.1:80", None, (301, "cloudflare"))]
        code, _ = connect_integrity.verdict(rows)
        assert code == 0

    def test_hosts_that_sound_alike_on_get_too_are_not_evidence(self):
        """One operator answering for all three is a fact about the hosts.

        The GET column is the control: it is what shows the hosts are really
        different when nothing is intercepting them. Without that difference,
        agreement on CONNECT says nothing.
        """
        rows = [row(f"h{i}:80", (400, "cloudflare"), (200, "cloudflare"))
                for i in range(3)]
        code, _ = connect_integrity.verdict(rows)
        assert code == 0
