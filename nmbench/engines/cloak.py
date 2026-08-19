"""CloakBrowser: a patched Chromium that hands back a Playwright browser.

This engine is cheap to add and that is itself the measurement. `cb.launch()`
returns a `playwright.sync_api.Browser`, so the session class, the resource
blocking and the byte counters are the ones the other Chromium engines already
use. Nothing here re-implements a request path, which means a difference in its
numbers cannot come from a second adapter written on a different day - the same
confound this file's Chromium/Patchright pairing exists to avoid.

Three things it brings that the matrix could not measure before.

**A headless Chromium that does not announce itself.** Read 2026-08-13 on
about:blank: `Chrome/146.0.0.0`, `navigator.webdriver` false, 5 plugins. The
Playwright-family engines send `HeadlessChrome` in the same mode because
Playwright downloads a separate "Chromium Headless Shell" build, and that
substring is the only property that sorts the DuckDuckGo table (95 of 95 against
0 of 50). So this engine reaches, headless, what previously cost a headful run -
and headful is the setting Obscura cannot do at all.

**Geo alignment on a Chromium.** Until now `supports_geo_align` was true for
Camoufox alone, which made `--geo align` unusable as an axis: the runner refuses
a mixed matrix, correctly, so the flag could only ever produce a run of one
engine. Turning it on for Camoufox and nothing else would have measured
Camoufox's geoip feature and called it anti-detect patching. With a second
aligning engine the flag becomes a comparison. `geoip` is wired to the same
keyword the runner already passes, and it defaults to False here rather than to
Camoufox's True: every row on disk was written with alignment off, so an engine
that aligned unless told not to would make its own column incomparable with all
1991 of them.

**Humanized input on a Chromium**, for the same reason and with the same
consequence. `--humanize` had a live failure in this repository until
2026-08-11, when it was accepted for any matrix and implemented by one engine.
The flag is now refused on a mixed matrix, which left it measurable only within
Camoufox. A second engine with the feature is what turns it into an axis.

What is deliberately not done here: no `stealth_args=False` escape hatch is
exposed. The stealth arguments are the product under test, the same way
Obscura's `--stealth` build is, and an engine that could quietly run without
them would produce rows indistinguishable from ones that ran with them.

**Headless and headful are not the same client on the free binary, and no flag
here says so.** Read 2026-08-18 in the installed package: `browser.py` installs
its `no_viewport` default on `new_context` only `if not headless or
binary_supports_headless_no_viewport(...)`, and that floor is 148.0.7778.215.4
against the free tier's 146.0.7680.177.5. So headful yields a context with no
device-metrics override and a real window, while headless falls through to
Playwright's default 1280x720 - which on a 1920x1080 machine reports a monitor
the machine does not have. Passing `no_viewport=True` from here would not fix it:
below the floor the package drops it, which is the silent-drop failure this
repository refuses to build on.

Read as a caveat and not as a result. `probes/screen_override.py` changed exactly
this one thing on Patchright and measured 10 of 10 both ways at Google, so the
override is not known to cost anything at the target it would matter most for.
What is not acceptable is running the two modes as if they were one engine: this
is a second axis riding on `--headful` for this engine alone, so a cloak column
has to name its mode, and a headful run is the one to prefer while the binary is
below the floor.

The license tier is recorded in the version string. The free tier is what was
screened, and a paid tier changing the patches without changing the package
version is exactly the silent build swap that the Obscura handshake check exists
to catch.
"""
from contextlib import contextmanager

from .. import providers, proxy
from .base import EngineUnavailable
from .chromium import ChromiumSession, _package_version


class CloakEngine:
    """CloakBrowser over its bundled patched Chromium."""

    name = "cloak"
    supports_blocking = True
    supports_headful = True
    supports_geo_align = True
    supports_humanize = True
    runs_script = True
    # Yields a `ChromiumSession`, so `search` comes with it. See
    # `ChromiumEngine` for why this is declared rather than discovered at the
    # call site.
    supports_typing = True
    # Hands back a Playwright browser, which takes proxy credentials directly,
    # so a relay would add a loopback hop and buy nothing. See `nmbench.relay`.
    needs_relay = False

    @classmethod
    def check(cls) -> str:
        try:
            import cloakbrowser  # noqa: F401
        except ImportError:
            return ("cloakbrowser is not installed, so this engine cannot run: "
                    "pip install cloakbrowser")
        # The patched binary is downloaded on first use, not at install, so an
        # importable package is not a runnable engine. Checked here rather than
        # at launch because a 90 MB download starting in the middle of a matrix
        # lands in the elapsed time of whichever cell happened to be first.
        try:
            import cloakbrowser as cb
            info = cb.binary_info()
        except Exception as exc:
            return f"cloakbrowser could not report its binary: {exc}"
        if not info.get("installed"):
            # `ensure_binary` and not `download`: `cloakbrowser.download` is a
            # module, so the call this line printed until 2026-08-19 raised
            # `TypeError: 'module' object is not callable`. The setup notes and
            # requirements-dev.txt were corrected on 2026-08-18 and this copy was
            # missed, which is the copy that matters - it is what `--dry-run`
            # prints on a fresh clone, so the only reader who ever saw it was the
            # one who did not already have the binary.
            return ("cloakbrowser has no browser binary: run "
                    "python -c \"import cloakbrowser; cloakbrowser.ensure_binary()\"")
        return None

    @classmethod
    def version(cls) -> str:
        detail = ""
        try:
            import cloakbrowser as cb
            info = cb.binary_info()
            detail = f" / chromium {info.get('version')} / {info.get('tier')}"
        except Exception:
            pass
        return f"cloakbrowser {_package_version('cloakbrowser')}{detail}"

    @contextmanager
    def open(self, *, direct: bool = False, params: dict = None,
             preset: str = "light", headless: bool = True,
             humanize: bool = False, geoip: bool = False,
             ready_timeout_ms: int = 8000, store=None, provider=None,
             **ignored):
        unavailable = self.check()
        if unavailable:
            raise EngineUnavailable(unavailable)

        import cloakbrowser as cb

        params = params or {}
        provider = None if direct else (provider or providers.load())
        # proxy_dict already returns exactly cloakbrowser's ProxySettings shape
        # (server, username, password), so the username DSL and its client-side
        # validation apply here unchanged.
        proxy_cfg = None if direct else proxy.proxy_dict(provider=provider,
                                                        **params)

        # Alignment derives from the exit address, so asking for it on the
        # direct arm would align against the operator's own line and label the
        # row as aligned. Refused rather than ignored.
        if geoip and direct:
            raise EngineUnavailable(
                "geo alignment reads the exit address, and the direct arm has "
                "no exit: the row would claim an alignment taken from this "
                "machine's own address. Run the aligned arm through the pool.")

        browser = cb.launch(headless=headless, proxy=proxy_cfg, geoip=geoip,
                            humanize=humanize)
        try:
            context = browser.new_context()
            yield ChromiumSession(
                context, label=self.name, preset=preset, direct=direct,
                params=params, headless=headless, humanize=humanize,
                version=f"{browser.version} / {self.version()}",
                ready_timeout_ms=ready_timeout_ms, store=store,
                provider=provider)
        finally:
            browser.close()
