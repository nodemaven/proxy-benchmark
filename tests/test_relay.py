"""What the relay refuses, and what it must not.

The relay blocks one hostname, `optimizationguide-pa.googleapis.com`, because
that is where a fresh-profile Chrome spends 43.2 MB of a 43.4 MB idle window
fetching an on-device model - measured 2026-08-19 on the server with bytes
attributed per CONNECT authority. At `--batch 1` every attempt pays it again, so
on a thousand attempts it is about 43 GB of a 100 GB shared quota, billed as
residential traffic for something no target ever sees.

Refusing traffic in a measurement harness is a serious thing to do, so the two
failure modes get a test each. Blocking too little means the four-day run is
priced at three times what it should cost and may not finish. Blocking too much
is worse and is silent: a suffix rule would take the whole of `googleapis.com`,
and a page failing because this file refused one of its requests would be
recorded as a verdict about the target.

Nothing here opens a socket. `_open_upstream` is replaced with a call that
fails the test if it is reached, which is also the assertion that the refusal
happens before any upstream connection exists rather than after one.
"""
import pytest

from nmbench import providers, relay


class FakeClient:
    """Enough socket for the refusal path: it is written to and closed."""

    def __init__(self):
        self.written = b""

    def sendall(self, data):
        self.written += data


class FakeUpstream:
    """A gateway answering one CONNECT with a canned reply and no payload."""

    def __init__(self, reply: bytes):
        self._reply = reply

    def sendall(self, data):
        pass

    def recv(self, _size):
        reply, self._reply = self._reply, b""
        return reply

    def close(self):
        pass


def make(fake_credentials, **kwargs):
    """A relay whose upstream is unreachable by construction.

    The host in `fake_credentials` is `.invalid`, so a test that got as far as
    opening a connection would fail on DNS rather than reach anything - but the
    patched `_open_upstream` names the mistake instead of waiting for a resolver
    to time out.
    """
    hop = relay.Relay(**kwargs)

    def refuse_to_dial():
        raise AssertionError("the relay opened an upstream socket for a host "
                             "it was supposed to refuse, so the block is "
                             "happening after the connection rather than "
                             "instead of it")

    hop._open_upstream = refuse_to_dial
    return hop


class TestTheVendorFetchIsRefused:

    def test_the_tunnel_is_answered_here_and_never_opened(self,
                                                          fake_credentials):
        hop = make(fake_credentials)
        client = FakeClient()
        hop._tunnel(client, "optimizationguide-pa.googleapis.com:443", "")
        assert client.written.startswith(b"HTTP/1.1 403")
        assert hop.snapshot()["blocked"] == 1
        # Not a tunnel and not a failure: the gateway was never asked, so
        # neither counter may move. `tunnel_failures` is read as trouble with
        # the gateway and this is the opposite of that.
        assert hop.snapshot()["tunnels"] == 0
        assert hop.snapshot()["tunnel_failures"] == 0

    def test_the_port_is_not_part_of_the_match(self, fake_credentials):
        hop = make(fake_credentials)
        for authority in ("optimizationguide-pa.googleapis.com:443",
                          "optimizationguide-pa.googleapis.com:80"):
            hop._tunnel(FakeClient(), authority, "")
        assert hop.snapshot()["blocked"] == 2

    def test_the_plain_http_path_blocks_the_same_host(self, fake_credentials):
        # HTTPS is the only way this host is ever contacted, so this path will
        # not fire in practice. It is here because a block holding on one path
        # and not the other is a hole that the byte column cannot show: the
        # traffic would simply be present.
        hop = make(fake_credentials)
        client = FakeClient()
        hop._forward(client, "GET http://optimizationguide-pa.googleapis.com/v1"
                             " HTTP/1.1\r\nHost: x\r\n\r\n")
        assert client.written.startswith(b"HTTP/1.1 403")
        assert hop.snapshot()["blocked"] == 1


class TestNothingElseIsRefused:

    @pytest.mark.parametrize("authority", [
        "www.google.com:443",
        "www.amazon.com:443",
        # The name appears and the host is a different one. A suffix or
        # substring rule passes this and takes a target's own traffic with it
        # the first time a vendor reorganises its hostnames.
        "sub.optimizationguide-pa.googleapis.com:443",
        "notoptimizationguide-pa.googleapis.com:443",
        "googleapis.com:443",
    ])
    def test_the_relay_tries_to_dial(self, fake_credentials, authority):
        hop = make(fake_credentials)
        with pytest.raises(AssertionError):
            hop._tunnel(FakeClient(), authority, "")
        assert hop.snapshot()["blocked"] == 0

    def test_an_empty_list_prices_the_fetch_again(self, fake_credentials):
        # The measurement has to remain repeatable by whoever reads the claim,
        # so the block is a constructor argument and not a fact about the file.
        hop = make(fake_credentials, block_hosts=())
        with pytest.raises(AssertionError):
            hop._tunnel(FakeClient(), "optimizationguide-pa.googleapis.com:443",
                        "")
        assert hop.snapshot()["blocked"] == 0


class TestTheCounterReachesTheRow:

    def test_it_is_differenced_per_attempt(self, fake_credentials):
        hop = make(fake_credentials)
        hop._tunnel(FakeClient(), "optimizationguide-pa.googleapis.com:443", "")
        before = hop.snapshot()
        hop._tunnel(FakeClient(), "optimizationguide-pa.googleapis.com:443", "")
        hop._tunnel(FakeClient(), "optimizationguide-pa.googleapis.com:443", "")
        assert hop.since(before)["blocked"] == 2

    def test_a_snapshot_without_the_key_is_still_a_reading(self,
                                                           fake_credentials):
        # `probe_and_hold.py` keeps snapshots taken before this counter existed
        # in its own structures; a KeyError there would end a run over a
        # counter nothing depends on.
        hop = make(fake_credentials)
        hop._tunnel(FakeClient(), "optimizationguide-pa.googleapis.com:443", "")
        assert hop.since({"bytes_up": 0, "bytes_down": 0, "bytes": 0,
                          "tunnels": 0, "tunnel_failures": 0,
                          "exits": []})["blocked"] == 1


class TestTheExitHeaderIsTheProvidersOwn:
    """The header carrying the exit address is a gateway dialect, not a constant.

    `gateway.identify` read it off the definition and this file spelled
    NodeMaven's name in, which is the shape of mistake the provider layer exists
    to prevent and the hardest kind to see: against another gateway nothing
    raises, the relay simply never records an exit, and the column reads exactly
    like a gateway that did not send one. It falls on zendriver, seleniumbase and
    botasaurus, for which this list is the only per-tunnel exit evidence there is.
    """

    def dial(self, monkeypatch, vendor, reply: bytes):
        """One CONNECT through a relay on `vendor`, answered with `reply`.

        The credentials are set here rather than taken from `fake_credentials`
        because their variable names are derived from the provider id, which is
        the same mechanism being tested one layer down.
        """
        for field in ("LOGIN", "PASSWORD"):
            monkeypatch.setenv(f"{vendor.id.upper()}_{field}", "x")
        monkeypatch.setenv(f"{vendor.id.upper()}_HOST", "gw.example.invalid")
        monkeypatch.setenv(f"{vendor.id.upper()}_PORT", "8080")

        hop = relay.Relay(provider=vendor)
        hop._open_upstream = lambda: FakeUpstream(reply)
        hop._pump = lambda client, upstream: None
        hop._tunnel(FakeClient(), "www.example.com:443", "")
        return hop

    def test_a_gateway_with_another_header_name_is_still_read(self, monkeypatch):
        vendor = providers.Provider(id="other", label="Other",
                                    known_params=frozenset({"country"}),
                                    exit_ip_header="X-Egress-Address")
        hop = self.dial(monkeypatch, vendor,
                        b"HTTP/1.1 200 Connection established\r\n"
                        b"X-Egress-Address: 1.2.3.4\r\n\r\n")
        assert hop.snapshot()["exits"] == ["1.2.3.4"]

    def test_a_gateway_that_declares_no_header_records_no_exit(self, monkeypatch):
        """Not a defect: the definition says the address costs an echo request
        instead, and reading a header this gateway never documented would
        attribute another vendor's value to it."""
        vendor = providers.Provider(id="quiet", label="Quiet",
                                    known_params=frozenset({"country"}))
        hop = self.dial(monkeypatch, vendor,
                        b"HTTP/1.1 200 Connection established\r\n"
                        b"X-Proxy-Exit-IP: 1.2.3.4\r\n\r\n")
        assert hop.snapshot()["exits"] == []
        assert hop.snapshot()["tunnels"] == 1


class TestHostParsing:

    @pytest.mark.parametrize("authority,host", [
        ("example.com:443", "example.com"),
        ("EXAMPLE.com:443", "example.com"),
        ("example.com", "example.com"),
        ("[2606:4700::1111]:443", "2606:4700::1111"),
    ])
    def test_host_of(self, authority, host):
        assert relay._host_of(authority) == host

    @pytest.mark.parametrize("line,host", [
        ("GET http://example.com/x HTTP/1.1\r\n\r\n", "example.com"),
        ("GET http://example.com:8080/x HTTP/1.1\r\n\r\n", "example.com"),
        ("POST https://example.com/x HTTP/1.1\r\n\r\n", "example.com"),
        # Origin form: there is no host in the line at all, and inventing one
        # from the `Host` header would let the block fire on a request shape it
        # was never written for.
        ("GET /x HTTP/1.1\r\nHost: example.com\r\n\r\n", ""),
        ("", ""),
    ])
    def test_absolute_uri_host(self, line, host):
        assert relay._absolute_uri_host(line) == host
