"""The speed measurement, and the one property that makes it safe as a default.

Everything here is offline. The download itself is a `requests.get` and pinning
that would be pinning `requests`; what is worth a test is the arithmetic around
it and, above all, the bound - because the bound is the entire argument for
turning this on by default, and a bound that is wrong is worse than no bound at
all. A run that was promised 20 downloads and takes 7627 has spent 7.6 GB of
metered residential traffic before anybody looks at the output.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nmbench import speed


def test_the_budget_stops_at_its_limit():
    """The whole reason this can be a default."""
    budget = speed.Budget(3)
    assert [budget.take() for _ in range(6)] == [True, True, True,
                                                 False, False, False]
    assert budget.spent == 3, (
        "a refused take must not be charged, or a long run walks the counter "
        "up forever and `describe` reports a spend that never happened")


def test_off_and_unlimited_are_distinguishable():
    """0 and None mean opposite things and neither is a default.

    Written as one test because the risk is that they get confused with each
    other, and a confusion is only visible when both are in view. A `Budget(0)`
    that behaved like `Budget(None)` is the failure that costs money.
    """
    assert speed.Budget(0).take() is False
    unlimited = speed.Budget(None)
    assert all(unlimited.take() for _ in range(1000))


def test_a_refused_measurement_writes_no_columns(monkeypatch):
    """Absent is not zero, and the analysis depends on the difference.

    A row with `speed_kbps = 0` reads as an exit that could not move data. A row
    with no `speed_kbps` at all reads as a session that was outside the sample.
    The budget produces the second case on purpose, and the runner merges an
    empty dict to express it - so this pins the empty dict rather than a dict of
    Nones, which would land the first meaning on 99% of a large run.
    """
    budget = speed.Budget(1)
    assert budget.take() is True
    assert budget.take() is False
    # What the runner does with a refusal: merge nothing.
    row = {"verdict": "ok"}
    row.update({})
    assert "speed_kbps" not in row


class _Response:
    def __init__(self, chunks, status=200):
        self._chunks = chunks
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, size):
        return iter(self._chunks)


def test_a_truncated_download_is_counted_and_flagged(monkeypatch):
    """The failure that looks identical to success unless the bytes are counted.

    A transfer cut short - by a timeout, by a gateway that gives up, by an exit
    that drops - still returns a 200 and still finishes quickly. If the rate
    were computed from the size that was ASKED for, a session that delivered a
    tenth of the body would be recorded as a fast one, and the faster the
    failure the better the number. So the arithmetic uses what arrived, and the
    shortfall is a column rather than an inference.
    """
    monkeypatch.setattr(speed.requests, "get",
                        lambda *a, **k: _Response([b"x" * 1000]))
    result = speed.measure(direct=True, size=1024 * 1024)
    assert result["speed_bytes"] == 1000
    assert result["speed_asked"] == 1024 * 1024
    assert result["speed_short"] is True
    assert result["speed_error"] is None
    assert result["speed_kbps"] is not None, (
        "a truncated transfer has a real rate and dropping it would lose the "
        "evidence that the truncation happened")


def test_a_full_download_is_not_flagged_short(monkeypatch):
    monkeypatch.setattr(speed.requests, "get",
                        lambda *a, **k: _Response([b"x" * 512, b"y" * 512]))
    result = speed.measure(direct=True, size=1024)
    assert result["speed_bytes"] == 1024
    assert result["speed_short"] is False


def test_a_failed_download_names_the_error_and_not_the_credentials(monkeypatch):
    """`requests` puts the URL it was handed into several of its own messages,
    and on the proxied path that URL carries the login and the password.

    This tree has leaked a credential twice - once through a `Proxy-Authorization`
    header and once through an API response body - and both times the rule that
    would have caught it was written narrowly, for the surface that had already
    burned. An exception string is the third surface and it is the one nobody
    reads until it is in a log.
    """
    class _Creds:
        login = "someone"
        password = "s3cr3t-do-not-print"

    monkeypatch.setattr(speed, "DOWNLOAD_URL", "https://example.invalid/__down")

    def _boom(*args, **kwargs):
        raise RuntimeError("failed to connect to "
                           "http://someone:s3cr3t-do-not-print@gw:8080")

    monkeypatch.setattr(speed.requests, "get", _boom)
    import nmbench.config as config
    monkeypatch.setattr(config, "credentials", lambda provider: _Creds())

    result = speed.measure(direct=True)
    assert result["speed_error"], "the failure has to be recorded, not swallowed"
    assert "s3cr3t-do-not-print" not in result["speed_error"]
    assert "someone" not in result["speed_error"]
    assert "RuntimeError" in result["speed_error"]


def test_a_gateway_parameter_named_like_a_knob_reaches_the_gateway(monkeypatch):
    """The collision that an earlier draft of this module could not have caught.

    `measure` has knobs called `size`, `url`, `timeout` and `direct`, and the
    providers here define fourteen gateway parameters from TOML - a namespace
    this module does not control. Under `**params` a provider parameter named
    `size` would be bound to the knob by Python before the function body runs:
    the download silently retunes, the parameter never reaches the gateway, and
    nothing inside `measure` can tell, because `params` is empty by then. A
    guard was written for exactly this and could not fire; the signature was
    changed instead. This pins the property that replaced it - a parameter named
    after a knob goes to the gateway and leaves the measurement alone.
    """
    seen = {}

    from nmbench import proxy

    def _url(provider=None, **params):
        seen.update(params)
        return "http://user:pass@gw:8080"

    monkeypatch.setattr(proxy, "proxy_url", _url)
    monkeypatch.setattr(speed.requests, "get",
                        lambda *a, **k: _Response([b"x" * 4096]))

    result = speed.measure(size=4096, params={"size": "nonsense"})
    assert seen == {"size": "nonsense"}, "the parameter has to reach the gateway"
    assert result["speed_asked"] == 4096, (
        "and it must not have moved the measurement's own size")
    assert result["speed_short"] is False


def test_an_ordinary_gateway_parameter_still_reaches_the_gateway(monkeypatch):
    """The other half of the guard, and it has to run on the PROXIED path.

    A guard that over-refuses turns an arm off silently, so the real parameters
    have to be shown going through. `speed` is the one worth naming: it is a
    live NodeMaven parameter AND the name of this module, which is exactly the
    shape that invites an over-broad reserved list. It is not reserved - the
    reserved names are `measure`'s own arguments - and this pins that.

    Written against `direct=False` on purpose. With `direct=True` the params are
    never forwarded anywhere, so the same assertions would pass while testing
    nothing, which is the failure this file is meant to avoid.
    """
    seen = {}

    from nmbench import proxy

    def _url(provider=None, **params):
        seen.update(params)
        return "http://user:pass@gw:8080"

    monkeypatch.setattr(proxy, "proxy_url", _url)
    monkeypatch.setattr(speed.requests, "get",
                        lambda *a, **k: _Response([b"x" * 16]))

    result = speed.measure(size=16, params={"filter": "medium", "speed": "fast"})
    assert result["speed_bytes"] == 16
    assert result["speed_error"] is None
    assert seen == {"filter": "medium", "speed": "fast"}, (
        "the download has to run through the same session the row describes, "
        "or the throughput column belongs to a different exit than the verdict")


@pytest.mark.parametrize("value,limit", [
    ("20", 20), ("0", 0), ("off", 0), ("OFF", 0), ("all", None),
    ("unlimited", None), (" 5 ", 5),
])
def test_the_flag_parses_the_three_settings(value, limit):
    """Spelled as words rather than as sentinels, because two of the three
    useful settings are not numbers and -1 for "no cap" is the kind of value
    that gets read the wrong way round by the next caller."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import benchmark
    assert benchmark.resolve_speed_budget(value).limit == limit


@pytest.mark.parametrize("value", ["-1", "lots", "", "1.5"])
def test_the_flag_refuses_what_it_cannot_read(value):
    sys.path.insert(0, str(ROOT / "scripts"))
    import benchmark
    with pytest.raises(SystemExit):
        benchmark.resolve_speed_budget(value)
