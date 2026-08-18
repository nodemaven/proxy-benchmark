"""Engine registry.

Adding a framework is one import and one entry. Nothing downstream branches on
an engine name, which is the only way a comparison stays honest: a runner that
knows which engine it is driving is a runner that can treat one of them
differently without anyone noticing.
"""
from contextlib import contextmanager

from .base import (
    ROW_FIELDS,
    EngineUnavailable,
    blank_row,
    record_error,
    record_judgement,
    validate_preset,
)
from .botasaurus import BotasaurusEngine
from .camoufox import CamoufoxEngine
from .chromium import ChromiumEngine, PatchrightEngine
from .cloak import CloakEngine
from .curlcffi import CurlCffiEngine
from .http import BROWSER_HEADERS, HttpEngine, looks_undecoded, supported_encodings
from .obscura import ObscuraEngine
from .rebrowser import RebrowserEngine
from .seleniumbase import SeleniumBaseEngine
from .zendriver import ZendriverEngine

REGISTRY = {
    engine.name: engine
    for engine in (HttpEngine, ChromiumEngine, CamoufoxEngine, PatchrightEngine,
                   ObscuraEngine, CloakEngine, CurlCffiEngine,
                   SeleniumBaseEngine, ZendriverEngine, RebrowserEngine,
                   BotasaurusEngine)
}

__all__ = [
    "BROWSER_HEADERS",
    "REGISTRY",
    "ROW_FIELDS",
    "EngineUnavailable",
    "blank_row",
    "fetch_camoufox",
    "fetch_http",
    "get",
    "looks_undecoded",
    "names",
    "record_error",
    "record_judgement",
    "report_availability",
    "session",
    "supported_encodings",
    "validate_preset",
]


def names() -> list:
    return list(REGISTRY)


def get(name: str):
    """An engine instance by name, or a message naming the known ones."""
    if name not in REGISTRY:
        raise KeyError(f"unknown engine {name!r}, known: {names()}")
    return REGISTRY[name]()


def report_availability() -> dict:
    """Which engines can run here, and why the others cannot.

    Checked before a matrix starts rather than when the first cell reaches an
    engine, so a missing binary costs a message instead of half a run.
    """
    return {name: engine.check() for name, engine in REGISTRY.items()}


@contextmanager
def session(name: str, **options):
    """Open a session on a named engine."""
    with get(name).open(**options) as active:
        yield active


def fetch_http(target, query: str, direct: bool = False, headers: dict = None,
               provider=None, **params) -> dict:
    """One request, one row. For probes that genuinely want a single attempt.

    `provider` is named rather than left to `**params`, which would send it to
    the gateway as a username parameter and have the DSL reject it.
    """
    with HttpEngine().open(direct=direct, params=params, headers=headers,
                           provider=provider) as s:
        return s.fetch(target, query)


def fetch_camoufox(target, queries, preset: str = "none", **options) -> list:
    """One browser session, several queries inside it.

    Kept as a function because the probe scripts ask exactly this question. The
    parameters that are not engine options are gateway parameters, which is why
    they arrive as keywords and are passed on whole. `provider` is an engine
    option and belongs in `known` for that reason: left out, it would be sent to
    the gateway as a username parameter and rejected by the DSL.
    """
    known = {"geoip", "ready_timeout_ms", "headless", "humanize", "direct",
             "provider"}
    settings = {k: options.pop(k) for k in list(options) if k in known}
    with CamoufoxEngine().open(preset=preset, params=options, **settings) as s:
        return [s.fetch(target, query) for query in queries]
