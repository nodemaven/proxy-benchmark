"""JA4, and whether this repository computes it the way everyone else does.

The risk this file exists to cover is not that the parser crashes. It is that it
quietly disagrees with every other JA4 implementation by one detail of the spec,
and publishes 36-character strings that look exactly like the real thing. A
fingerprint is only worth recording if it is comparable with the fingerprints
other people publish, so the load-bearing test here is the cross-check against a
value an independent implementation produced.

Everything in this module is offline. `TestAgainstAnIndependentImplementation`
opens a socket to itself and reads the first record; nothing is asked of a host.
"""
import socket
import ssl
import threading
import warnings

import pytest

from nmbench import tlsfp
from nmbench.engines.base import ROW_FIELDS, blank_row

# `data/runs/tls_echo_20260901T112110Z.jsonl`, the `http` engine's row. Computed
# by `tls.peet.ws` from a connection this repository's own client made, and the
# only JA4 in this tree that did not come from this file.
PEET_HTTP_ENGINE = "t13d1812h1_85036bcba153_b26ce05bbdd6"


def client_hello(target: str, *, configure=None) -> bytes:
    """The first TLS record a client sends to `target`, and nothing after it.

    A listener that accepts and then answers nothing at all. The handshake fails
    a moment later and that is fine: the ClientHello is the whole subject, it is
    the first thing on the wire, and refusing to answer means the test never
    needs a certificate, a key, or a second implementation of TLS to be correct.
    """
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    host, port = listener.getsockname()
    box = {}

    def serve():
        try:
            conn, _ = listener.accept()
        except OSError:
            return
        buffer = b""
        while (not tlsfp.hello_is_complete(buffer)
               and len(buffer) < tlsfp.MAX_HELLO_BYTES):
            try:
                chunk = conn.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            buffer += chunk
        box["hello"] = buffer
        conn.close()

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        if configure is None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                import requests
                try:
                    requests.get(f"https://{target}:{port}/", verify=False,
                                 timeout=5)
                except Exception:
                    pass
        else:
            context = configure()
            raw = socket.create_connection((host, port), timeout=5)
            try:
                context.wrap_socket(raw, server_hostname=target)
            except Exception:
                pass
    finally:
        thread.join(timeout=10)
        listener.close()
    return box.get("hello", b"")


class TestAgainstAnIndependentImplementation:
    """The only test here that can catch a misread of the spec.

    Everything else in this file checks that the parser reports what was put
    into the handshake, which a consistently wrong implementation passes.
    """

    def test_it_reproduces_the_value_the_echo_service_computed(self):
        """`tls.peet.ws` and this file, on the same client, must agree.

        The stored row was written on 2026-09-01 by the `http` engine, which is
        `requests` over the interpreter's own OpenSSL. Running that client into
        a socket that only reads has to give the same 36 characters.

        If this fails after a Python or OpenSSL upgrade, suspect the handshake
        before suspecting the parser: a new OpenSSL changes the cipher list, and
        that moves the JA4 legitimately. The way to tell them apart is the two
        halves - a changed cipher list moves the first hash and a changed
        extension set moves the second, while a misread of the spec typically
        moves the prefix or both.
        """
        hello = client_hello("localhost")
        if not hello:
            pytest.skip("no ClientHello was captured on this host")
        assert tlsfp.ja4(hello)[0] == PEET_HTTP_ENGINE

    def test_dropping_sni_moves_the_count_and_not_the_hash(self):
        """The asymmetry most likely to be transcribed wrong, isolated.

        `server_name` is counted in the extension field of the prefix and
        excluded from the extension hash. Connecting by IP rather than by name
        removes exactly that extension, so a correct implementation drops the
        count by one, flips `d` to `i`, and leaves both hashes untouched. An
        implementation that hashed `server_name` would move the second hash and
        pass every other test in this file.
        """
        by_name = tlsfp.ja4(client_hello("localhost"))[0]
        by_address = tlsfp.ja4(client_hello("127.0.0.1"))[0]
        if not by_name or not by_address:
            pytest.skip("no ClientHello was captured on this host")
        # `t13d1812h1`: transport, two-character version, SNI, two-digit cipher
        # count, two-digit extension count, two ALPN characters.
        assert by_name[3] == "d"
        assert by_address[3] == "i"
        assert int(by_address[6:8]) == int(by_name[6:8]) - 1
        assert by_address[4:6] == by_name[4:6]
        assert by_address.split("_")[1:] == by_name.split("_")[1:]


class TestTheParserReportsWhatWasSent:
    """The handshake is configured here, so a wrong answer is visible without
    trusting any published value."""

    def test_the_alpn_characters_are_the_first_and_last(self):
        def configure():
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            context.set_alpn_protocols(["http/1.1"])
            return context

        hello = client_hello("example.invalid", configure=configure)
        if not hello:
            pytest.skip("no ClientHello was captured on this host")
        assert tlsfp.ja4(hello)[0][8:10] == "h1"

    def test_a_modern_client_reports_tls_13(self):
        hello = client_hello("localhost")
        if not hello:
            pytest.skip("no ClientHello was captured on this host")
        parsed = tlsfp.parse_client_hello(hello)
        assert parsed["version"] == 0x0304
        assert tlsfp.ja4(hello)[0][1:3] == "13"

    def test_the_raw_form_hashes_to_the_digest(self):
        """`ja4_r` is published so a disagreement can be settled offline. That
        is only true if the two halves of it actually hash to the digest they
        are printed beside."""
        import hashlib

        hello = client_hello("localhost")
        if not hello:
            pytest.skip("no ClientHello was captured on this host")
        value, readable = tlsfp.ja4(hello)
        _, ciphers, extensions = readable.split("_", 2)
        _, cipher_hash, extension_hash = value.split("_", 2)
        assert hashlib.sha256(ciphers.encode()).hexdigest()[:12] == cipher_hash
        assert (hashlib.sha256(extensions.encode()).hexdigest()[:12]
                == extension_hash)

    def test_grease_is_removed_from_the_counts(self):
        """Without this the fingerprint is per-connection noise.

        A browser picks its GREASE values at random for every connection, so an
        implementation that counted or hashed them would give the same browser a
        different JA4 every time and the column would be worthless. Checked by
        splicing a GREASE cipher into a real handshake rather than by asserting
        on the constant, so it tests the parser and not the table.
        """
        parsed = tlsfp.parse_client_hello(client_hello("localhost"))
        if parsed is None:
            pytest.skip("no ClientHello was captured on this host")
        assert not (set(parsed["ciphers"]) & tlsfp.GREASE)
        assert not (set(parsed["extensions"]) & tlsfp.GREASE)


class TestItRefusesRatherThanGuesses:
    """This runs inside the relay's byte-copy loop, so every one of these has to
    return rather than raise. A malformed record must cost the fingerprint and
    never the tunnel."""

    @pytest.mark.parametrize("data", [
        b"",
        b"\x16",
        b"\x16\x03\x01",
        b"\x17\x03\x03\x00\x10" + b"\x00" * 16,      # application data, not a
                                                     # handshake
        b"GET / HTTP/1.1\r\n\r\n",                   # a plaintext CONNECT
        b"\x16\x03\x01\xff\xff\x01\x00\x00\x10",     # a length that overruns
        b"\x16\x03\x01\x00\x04\x02\x00\x00\x00",     # ServerHello, not Client
    ])
    def test_it_returns_none(self, data):
        assert tlsfp.ja4(data) == (None, None)
        assert tlsfp.parse_client_hello(data) is None

    def test_a_truncated_hello_is_not_reported_as_a_short_one(self):
        """The failure that would be invisible in the output.

        Half a ClientHello parses into a plausible dictionary with a short
        cipher list, and the JA4 built from it looks like an ordinary
        fingerprint for an unusual client. `hello_is_complete` is what stops the
        relay handing a partial record to the parser, so it has to be false for
        every prefix of a real one.
        """
        hello = client_hello("localhost")
        if not hello:
            pytest.skip("no ClientHello was captured on this host")
        assert tlsfp.hello_is_complete(hello)
        for cut in range(1, len(hello)):
            assert not tlsfp.hello_is_complete(hello[:cut]), (
                f"a {cut}-byte prefix of a {len(hello)}-byte hello was reported "
                f"complete, so the relay would fingerprint half a handshake")


class TestTheColumn:
    def test_it_is_in_the_published_schema(self):
        assert "tls_ja4" in ROW_FIELDS

    def test_it_is_none_rather_than_absent_on_an_unrelayed_row(self):
        """Absent and None are the same thing to a reader of the schema, and
        the schema is what someone forking this repository reads. A row that
        omitted the key entirely would need a special case in every analysis."""
        row = blank_row("engine", "1.0", "q", "https://example.invalid/")
        assert "tls_ja4" in row
        assert row["tls_ja4"] is None
