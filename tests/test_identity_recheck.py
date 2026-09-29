"""The three-point identity check: `gateway.recheck_exit` and `summarise_identity`.

Two properties are worth more than the rest of this file and both are about what
the check refuses to do.

The first is that a re-check never sends a request through the session. The whole
harness measures what a *warmed* exit does, and `gateway.locate`'s docstring
states the consequence: a request through the exit is warming. `identify` may
fall back to `echo`, which fetches a page, and it is allowed to because it runs
once at the top of every session and is therefore part of the treatment in every
arm. A mid-batch echo would not be - it would land on the sessions whose gateway
did not offer the CONNECT header, which is not a random half. `identify` measured
that itself: `exit_timezone` is present on 56 of 117 probes of
`probehold_20260827T201123Z`, and those identities were served 14% against 28%.
So the re-check is header-only and returns "not measured" rather than paying.

The second is that "not measured" and "did not rotate" stay apart. Three separate
things produce a None address - a provider with no header, a backend that answered
without one, a refused handshake - and none of them is evidence the session held.
A boolean `identity_stable` would have to call one of them stable, and that would
put the strongest-looking claim on the population this harness can say the least
about.

Nothing here opens a socket.
"""
import pytest

from nmbench import gateway


class FakeProvider:
    def __init__(self, provider_id="fake", header="x-proxy-exit-ip"):
        self.id = provider_id
        self.exit_ip_header = header


@pytest.fixture(autouse=True)
def clean_latch():
    """The miss counter is module state shared with `identify`.

    Saved and restored rather than cleared, because a test that leaves it dirty
    would change how a later test's `recheck_exit` behaves and the failure would
    land somewhere else entirely.
    """
    saved = dict(gateway._header_misses)
    gateway._header_misses.clear()
    yield
    gateway._header_misses.clear()
    gateway._header_misses.update(saved)


class TestTheRecheckNeverWarmsTheExit:

    def test_it_does_not_fall_back_to_the_echo_service(self, monkeypatch):
        """The property the whole design rests on. `identify` falls back to
        `echo` when the header is absent; this must not, or the instrument
        fetches a page in the middle of the batch it is measuring and does so
        only on the sessions whose gateway is quiet about the exit."""
        monkeypatch.setattr(gateway, "exit_ip",
                            lambda **kwargs: {"exit_ip": None, "error": None})

        def refuse(*args, **kwargs):
            raise AssertionError(
                "the re-check sent a request through the session, which warms "
                "the exit it is supposed to be observing")

        monkeypatch.setattr(gateway, "echo", refuse)
        result = gateway.recheck_exit(provider=FakeProvider())
        assert result["exit_ip"] is None
        assert result["recheck_reason"] == gateway.RECHECK_NOT_OFFERED

    def test_a_provider_with_no_header_is_not_probed_at_all(self, monkeypatch):
        """Not merely "does not echo": it does not open a tunnel either. There
        is nothing to read on such a provider, so a handshake per check would be
        cost with no column behind it."""
        def refuse(*args, **kwargs):
            raise AssertionError("a tunnel was opened with nothing to read")

        monkeypatch.setattr(gateway, "exit_ip", refuse)
        result = gateway.recheck_exit(provider=FakeProvider(header=""))
        assert result["exit_ip"] is None
        assert result["recheck_reason"] == gateway.RECHECK_NO_HEADER
        assert result["recheck_source"] == "none"


class TestTheRecheckCannotChangeHowIdentifyBehaves:
    """The asymmetry with `_header_misses`, which is deliberate.

    It reads the latch, so a provider whose header has been given up on is not
    charged a handshake per check. It never writes it, because `identify`'s
    fallback costs traffic: a re-check that could trip the latch would make the
    billed cost of a run depend on how many re-checks it happened to run, and
    the run would not say so anywhere.
    """

    def test_a_missed_header_does_not_advance_the_latch(self, monkeypatch):
        monkeypatch.setattr(gateway, "exit_ip",
                            lambda **kwargs: {"exit_ip": None, "error": None})
        provider = FakeProvider()
        for _ in range(gateway._HEADER_GIVE_UP * 3):
            gateway.recheck_exit(provider=provider)
        assert gateway._header_misses.get(provider.id, 0) == 0, (
            "re-checks pushed the provider towards the paid echo fallback")

    def test_a_latched_provider_is_not_probed(self, monkeypatch):
        def refuse(*args, **kwargs):
            raise AssertionError("a tunnel was opened on a latched provider")

        monkeypatch.setattr(gateway, "exit_ip", refuse)
        provider = FakeProvider()
        gateway._header_misses[provider.id] = gateway._HEADER_GIVE_UP
        result = gateway.recheck_exit(provider=provider)
        assert result["recheck_reason"] == gateway.RECHECK_GAVE_UP

    def test_a_transport_error_is_reported_rather_than_raised(self, monkeypatch):
        """A failed re-check must not end a session. The batch is the expensive
        thing here and the check is a side channel; losing the run to protect a
        column is the wrong way round."""
        monkeypatch.setattr(
            gateway, "exit_ip",
            lambda **kwargs: {"exit_ip": None, "error": "TimeoutError: timed out"})
        result = gateway.recheck_exit(provider=FakeProvider())
        assert result["exit_ip"] is None
        assert "TimeoutError" in result["recheck_reason"]


class TestUnreadIsNotUnchanged:

    def test_two_agreeing_reads_are_stable(self):
        summary = gateway.summarise_identity(
            [("start", "1.2.3.4"), ("middle", "1.2.3.4"), ("end", "1.2.3.4")])
        assert summary["identity_stable"] is True
        assert summary["identity_read"] == 3
        assert summary["identity_changed_at"] is None

    def test_one_disagreeing_read_is_not_stable(self):
        summary = gateway.summarise_identity(
            [("start", "1.2.3.4"), ("middle", "1.2.3.4"), ("end", "9.9.9.9")])
        assert summary["identity_stable"] is False
        assert summary["identity_changed_at"] == "end"

    def test_the_first_divergence_is_named_not_the_last(self):
        """With three points this only shows up on a session that moved twice,
        which is exactly the session a sticky-session claim is about."""
        summary = gateway.summarise_identity(
            [("start", "1.2.3.4"), ("middle", "5.5.5.5"), ("end", "9.9.9.9")])
        assert summary["identity_changed_at"] == "middle"

    @pytest.mark.parametrize("points", [
        [],
        [("start", "1.2.3.4")],
        [("start", "1.2.3.4"), ("middle", None), ("end", None)],
        [("start", None), ("middle", None), ("end", None)],
    ])
    def test_fewer_than_two_reads_answers_nothing(self, points):
        """None, and specifically not True. A session read once and then not
        read again has not been shown to hold - and folding that into True would
        report the strongest stability on providers whose gateway never names an
        exit, which is the population there is least evidence about."""
        summary = gateway.summarise_identity(points)
        assert summary["identity_stable"] is None
        assert summary["identity_changed_at"] is None

    def test_the_denominator_is_on_the_row(self):
        """`identity_read` beside `identity_points`, so a stable=True that rests
        on two readings out of three is distinguishable from one that rests on
        three. Without it every True looks equally well supported."""
        summary = gateway.summarise_identity(
            [("start", "1.2.3.4"), ("middle", None), ("end", "1.2.3.4")])
        assert summary == {"identity_points": 3, "identity_read": 2,
                           "identity_stable": True, "identity_changed_at": None}

    def test_a_gap_between_two_agreeing_reads_is_still_stable(self):
        """Stated as its own case because it is the one that could reasonably
        have gone the other way. The middle reading failed, so the session is
        unobserved across that stretch and could in principle have rotated and
        come back. It is called stable anyway: the alternative is to let a
        failed handshake in our own instrument count as a rotation on the
        provider's side, and a column that charges instrument gaps to the
        subject is worse than one that under-reports them."""
        summary = gateway.summarise_identity(
            [("start", "1.2.3.4"), ("middle", None), ("end", "1.2.3.4")])
        assert summary["identity_stable"] is True
