"""Rebrowser: Playwright with the `Runtime.enable` leak patched out.

This engine was added to put a number on one patch. `Runtime.enable` is what a
CDP client sends to be told about execution contexts, and enabling it makes the
browser announce the main world to anything watching - the artefact the
undetected-driver family exists to remove. The patch is on by default: read
2026-08-18 in the installed driver, `lib/server/chromium/crConnection.js`,
`const fixMode = process.env["REBROWSER_PATCHES_RUNTIME_FIX_MODE"] ||
"addBinding"`, where `"0"` is the value that disables it. No escape hatch for
turning it off is exposed here, on the argument `cloak.py` makes about its
stealth arguments: an engine that could quietly run unpatched would write rows
indistinguishable from patched ones.

**Screened on 2026-08-18 before its first run, and the screening inverted the
premise. Read the two findings below before reading this column.**

**It is the only engine in the registry that a `window` enumeration identifies
outright.** `probes/engine_fingerprint.py` returned `binding_leaks =
__pwInitScripts,__playwright_builtins__` where the unmodified control returned
null, on the same machine minutes apart. Both are plain enumerable properties of
`globalThis`, so three lines of JavaScript on any page name the driver. The cause
is the fork's base version rather than the patch: `__pwInitScripts` appears in
`lib/server/page.js` of this driver and **nowhere in stock Playwright 1.60's
`lib/` at all**, so upstream stopped using it somewhere between 1.52 and 1.60 and
the fork is pinned behind that. `stack_leak` is `no` on both, and
`navigator.webdriver` is **True on this fork exactly as it is on the control** -
the fork does not touch it. So of the 21 markers the probe reads, this engine
differs from the unmodified control in one, and in the wrong direction.

That is a reason to run it and not a reason to drop it: a popular fork sold as
undetectable, carrying a driver global the stock driver has already removed, is
the kind of thing this repository exists to measure. It is a reason not to read a
refusal here as the `Runtime.enable` patch failing.

**It ships Chromium 136.0.7103.25, against Playwright's 148 and Patchright's
149.** Twelve major versions is a fingerprint of its own and it is the reason the
one-variable claim below has to be qualified. It is a Headless Shell like the
rest of the Playwright family, so headless it announces
`HeadlessChrome/136.0.7103.25` on the wire - measured in the same run - which
puts it in the 0 of 50 half of the DuckDuckGo table and means a headless cell
here measures the User-Agent rather than the fork.

**The launch path is the control's and not Patchright's.** `ChromiumEngine.open`
opens a plain `launch()` plus `new_context()`, and so does this; Patchright opens
a persistent context with `no_viewport=True`, because its documentation asks for
that and its patches depend on which domains a fresh context turns on. Driving
this fork that way would put the context mode in the column beside the fork.
Against `chromium` the driver API, the launch call and the context are identical,
so **a matrix holding this engine and not `chromium` cannot read it** - a
property of the engine rather than of the run, which is why it is recorded here.

What that pairing does not hold constant is the browser build, and the way to
close it is a matrix flag rather than a change here: `--channel chrome` puts both
engines on the host's Chrome and leaves the driver as the only difference.
Hardcoding it would be this engine deciding a question that belongs to the run.

It ships its own Chromium download, separate from Playwright's and Patchright's,
so `python -m rebrowser_playwright install chromium` is a third install and not a
shared one. `check` reports a missing binary rather than letting the failure
arrive at launch, for the reason the other Chromium engines do.
"""
from contextlib import contextmanager

from .. import providers, proxy
from .base import EngineUnavailable
from .chromium import ChromiumEngine, ChromiumSession, label_for


class RebrowserEngine(ChromiumEngine):
    """rebrowser-playwright over its own Chromium build."""

    name = "rebrowser"
    driver = "rebrowser_playwright"
    supports_headful = True
    # It is Playwright, so `timezone_id` on the context reaches Chromium's own
    # emulation, and the zone is in place before any target exists. That timing
    # is the reason this flag is worth having rather than a detail: the geo axis
    # measured patchright flat and zendriver down six sevenths, and the only
    # thing that differed between them was pre-context against post-tab
    # installation. This engine is a second pre-context installer, so it is one
    # half of the pair that can test that.
    supports_geo_align = True
    supports_geoip = False

    @contextmanager
    def open(self, *, direct: bool = False, params: dict = None,
             preset: str = "light", headless: bool = True,
             channel: str = None, ready_timeout_ms: int = 8000, store=None,
             timezone_id: str = None, provider=None, **ignored):
        unavailable = self.check()
        if unavailable:
            raise EngineUnavailable(unavailable)

        from importlib import import_module
        api = import_module(f"{self.driver}.sync_api")

        params = params or {}
        provider = None if direct else (provider or providers.load())
        proxy_cfg = None if direct else proxy.proxy_dict(provider=provider,
                                                        **params)

        with api.sync_playwright() as pw:
            # No `args` and no `user_agent`, exactly as the control launches:
            # the fork is the variable, and an argument here would make it two.
            # `locale` is deliberately not set beside the timezone. See
            # `base.ROW_FIELDS`.
            browser = pw.chromium.launch(headless=headless, proxy=proxy_cfg,
                                         channel=channel)
            try:
                context = browser.new_context(timezone_id=timezone_id)
                yield ChromiumSession(
                    context, label=label_for(self.name, channel), preset=preset,
                    direct=direct, params=params, headless=headless,
                    version=f"{browser.version} / {self.version()}"
                            f"{' / ' + channel if channel else ''}",
                    ready_timeout_ms=ready_timeout_ms, store=store,
                    provider=provider)
            finally:
                browser.close()
