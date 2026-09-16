"""The hand-built row in `scripts/probes/b2b_recon.py::browser_attempt`.

Every other engine builds its row inside a session class, from the session's own
attributes. This probe builds one by hand because it needs a settle that
`fetch` does not do, and the hand-built row is where two bugs have now lived.

Both were invisible to the suite before this file existed, and both were
invisible for the same reason: the probe's browser arm is only ever exercised
against a live site, so nothing here ever constructed one of its rows. The fake
session below is the whole point - it is what makes the row inspectable without
a browser.

The first bug: `-direct`, `direct=True` and `params={}` were literals, so every
proxied browser row claimed to be direct
(`marketplace_recon_20260905T095839Z`, 3 of 3). The second: the fix passed the
`Provider` object into the row, and the sink refused it with `TypeError: Object
of type Provider is not JSON serializable`, which cost three Shopee runs of
2026-09-05 - `101920Z`, `102005Z`, `102050Z` - all of which captured nothing.

The second bug is why the assertions here are about JSON and not about
equality. A row that holds the right value in an unwritable type is worse than
a row that holds the wrong value, because it takes the run down with it.
"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load():
    path = ROOT / "scripts" / "probes" / "b2b_recon.py"
    spec = importlib.util.spec_from_file_location("b2b_recon", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


b2b_recon = load()


class Provider:
    """Stands in for `nmbench.providers.Provider`: has an `id`, is not JSON."""

    id = "nodemaven"


class Page:
    """Fails the navigation, so the test reaches `finally` and returns a row."""

    def __init__(self):
        self.url = "about:blank"

    def goto(self, *args, **kwargs):
        raise RuntimeError("net::ERR_TIMED_OUT")

    def close(self):
        pass


class Session:
    def __init__(self, direct, params, provider):
        self.label = "patchright"
        self.preset = "light"
        self.direct = direct
        self.params = params
        self.provider = provider
        self.headless = False
        self.index = 0
        # An attribute, not a method: `browser_attempt` passes `session.version`
        # uncalled. Written as a method here first, and the row then carried a
        # bound method into the sink and failed with the same `TypeError` shape
        # the real bug had - which is a fair warning that this test can pass for
        # the wrong reason if the fake drifts from `ChromiumSession`.
        self.version = "151.0.7922.34"

    def new_page(self, counter):
        return Page()


class Site:
    name = "shopee"

    def url(self, query):
        return f"https://shopee.sg/search?keyword={query}"

    def judge(self, url, text, html):
        raise AssertionError("not reached: the navigation fails first")


def attempt(session):
    return b2b_recon.browser_attempt(session, Site(), "usb hub", None, 0)


class TestTheRowIsWritable:
    def test_a_proxied_row_survives_the_sink(self):
        row = attempt(Session(False, {"country": "sg"}, Provider()))
        assert json.loads(json.dumps(row))["provider"] == "nodemaven"

    def test_a_direct_row_survives_the_sink(self):
        row = attempt(Session(True, {}, None))
        assert json.loads(json.dumps(row))["provider"] is None


class TestTheRowSaysWhatTheSessionIs:
    def test_a_proxied_row_is_not_labelled_direct(self):
        row = attempt(Session(False, {"country": "sg"}, Provider()))
        assert row["engine"] == "patchright/light"
        assert row["direct"] is False
        assert row["params"] == {"country": "sg"}

    def test_a_direct_row_still_says_direct(self):
        row = attempt(Session(True, {}, None))
        assert row["engine"] == "patchright-direct/light"
        assert row["direct"] is True
        assert row["params"] == {}

    def test_the_params_are_copied_and_not_aliased(self):
        params = {"country": "sg"}
        row = attempt(Session(False, params, Provider()))
        params["country"] = "my"
        assert row["params"] == {"country": "sg"}
