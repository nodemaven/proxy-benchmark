"""Test setup.

The repository root goes on the path so the tests import the working tree rather
than an installed copy, and the gateway credentials are removed from the
environment for every test. A test that quietly picked up a real `.env` could
open a real connection, and a suite that can spend the traffic budget is a suite
nobody will run.

The variable names are derived from the provider definitions on disk rather than
listed here. A hardcoded list covers the providers that existed when it was
written, so adding a definition would carry four variables the suite does not
strip - and on the one machine that has that account, the offline suite quietly
stops being offline. Deriving it means a definition cannot escape the guard.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nmbench import config, providers  # imported after ROOT is on the path

CREDENTIAL_VARS = tuple(sorted(
    name
    for provider in providers.load_all().values()
    for name in config.variables(provider).values()
))


@pytest.fixture(autouse=True)
def no_real_credentials(monkeypatch):
    for name in CREDENTIAL_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(providers.SELECT_VAR, raising=False)


class _FakePage:
    """Enough of a Playwright page to replay response events, and no more.

    Deliberately minimal: a response here has a resource type and a frame and
    nothing else, so a filter that reaches for another attribute fails the test
    rather than being exercised against a fake that agreed to invent it.
    """

    def __init__(self):
        self.main_frame = "main"
        self._handlers = []

    def on(self, event, handler):
        assert event == "response", f"unexpected event {event!r}"
        self._handlers.append(handler)

    def emit_response(self, status, *, resource_type="document", frame=None):
        response = type("Response", (), {
            "status": status,
            "frame": self.main_frame if frame is None else frame,
            "request": type("Request", (), {"resource_type": resource_type})(),
        })()
        for handler in self._handlers:
            handler(response)


@pytest.fixture
def fake_page():
    return _FakePage()


@pytest.fixture
def fake_credentials(monkeypatch):
    """Syntactically valid credentials that reach nothing."""
    monkeypatch.setenv("NODEMAVEN_LOGIN", "testlogin")
    monkeypatch.setenv("NODEMAVEN_PASSWORD", "p@ss:word/1")
    monkeypatch.setenv("NODEMAVEN_HOST", "gate.example.invalid")
    monkeypatch.setenv("NODEMAVEN_PORT", "8080")
