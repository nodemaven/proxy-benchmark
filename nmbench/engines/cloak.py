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

**It did not happen, and what happened instead is worse: the provenance string
started lying while the browser stayed put.** Installing the standalone
CloakBrowser Manager - a separate GUI distribution, not a pip upgrade - put a
`chromium-151.0.7922.108.3-pro` build and a `latest_pro_version_windows-x64`
marker into the cache at `~/.cloakbrowser/`, which this package shares. From
that moment `binary_info()` reports `version 151.0.7922.108.3, tier "pro"`, and
`version()` below interpolates it, so a cloak row claims a pro 151.

The browser that actually starts is still the free 146. Measured 2026-09-03 by
three readings that agree: the running image is
`~/.cloakbrowser/chromium-146.0.7680.177.5/chrome.exe`, `chrome://version`
inside it says `146.0.7680.177 (Official Build, ungoogled-chromium)`, and
`navigator.userAgent` says `Chrome/146.0.0.0`.

The mechanism is in the library and is not a bug in it. `binary_info()` sets its
tier from `_pro_binary_ready()` - whether a pro binary exists on disk - and its
own docstring says so: "tier reflects what is actually installed on disk, not
merely whether a license is cached". `launch()` decides from the license key,
and this engine passes none. The two answer different questions, and they only
diverge once something else on the machine downloads a pro build.

What the mistake looked like from the inside: `binary_info()` is the function
whose whole job is to describe the binary, so its answer was written down as the
description of the binary. It was never asked what `launch()` would do with no
key. The disagreement only surfaced because `chrome://version` was read for an
unrelated reason and said 146. The check that settles it is one command - list
the running `chrome.exe` image - and it is the same failure as `norotate` and
`OptimizationHints` before it: a source that looks authoritative standing in for
a measurement.

The consequence for the rows: no published number is affected, because no cloak
run happened between the Manager's install and this note. Going forward a cloak
row on this host is **self-contradictory rather than simply wrong** - `open()`
builds `engine_version` as `f"{browser.version} / {self.version()}"`, so the
first field is the truth from Playwright and the tail is the claim from
`binary_info()`. Read the first field.

The headless caveat above therefore still applies exactly as written: the binary
in play is 146, which is below the 148.0.7778.215.4 floor.
"""
from contextlib import contextmanager

from .. import providers, proxy
from .base import EngineUnavailable
from .base import humanize_mode as _humanize_mode
from .chromium import ChromiumSession, _package_version


class CloakEngine:
    """CloakBrowser over its bundled patched Chromium."""

    name = "cloak"
    supports_blocking = True
    supports_headful = True
    supports_geo_align = True
    supports_geoip = True
    # Both kinds, the same way Camoufox has both: "engine" is the option
    # `cb.launch` takes, "trueman" is the model in `nmbench.pointer`, and this
    # engine yields a `ChromiumSession` so the second comes with it. They are
    # alternatives - see `CamoufoxSession.__init__` for why stacking them would
    # produce a path neither model describes.
    humanize_modes = frozenset({"off", "engine", "trueman"})
    runs_script = True
    # Yields a `ChromiumSession`, so `search` comes with it. See
    # `ChromiumEngine` for why this is declared rather than discovered at the
    # call site.
    supports_typing = True
    # Hands back a Playwright browser, which takes proxy credentials directly,
    # so this engine does not need a relay to reach the pool. See
    # `nmbench.relay`.
    needs_relay = False
    # Not taught to take one either, and there is no obstacle to it: `cb.launch`
    # below takes the same Playwright-shaped proxy dict `ChromiumEngine` passes,
    # so the change is the same three lines. It was not made on 2026-09-06
    # because the run that needed the exit column was patchright's, and an
    # option added in a commit that never exercises it is an untested path that
    # reads as a tested one. This flag is the honest record of that, and it is
    # what stops a caller labelling the arm relayed and finding no exit on it.
    #
    # Worth doing when there is a reason: the relay would also read this
    # binary's ClientHello, and `tls_clienthello_20260902T180555Z` says eight
    # engines share one fingerprint decided by the Chrome build, which is a
    # claim about a build this engine resolves at launch time.
    accepts_relay = False
    # The patched binary is the engine. Pointing it at a stock Chrome would take
    # the patches out and leave the launcher, which is not this engine running on
    # a fixed browser but a different engine wearing its name.
    supports_chrome_binary = False

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
        """The build a keyless launch resolves, not the best build on disk.

        `binary_info()` was what this read until 2026-09-03, and it answers a
        different question: it sets its tier from whether a pro binary exists in
        the shared cache, whoever put it there. Once the standalone Manager put
        one in, every cloak row on this host claimed a pro 151 while `launch()`
        went on starting the free 146 - see the correction in this module's
        docstring. `get_effective_version()` with no `pro` is the free path, and
        the free path is what this engine takes because it passes no
        `license_key`.

        If that ever stops being true - if a key is threaded through `open` -
        this has to move with it, so the tier is derived here rather than
        written as a constant.
        """
        detail = ""
        try:
            import cloakbrowser as cb
            from cloakbrowser.config import get_effective_version
            resolved = get_effective_version()
            detail = f" / chromium {resolved} / free"
            info = cb.binary_info()
            # Named on the row rather than logged, because a row is the only
            # thing left to read a year from now. This fires whenever something
            # else on the machine has a newer or paid build cached.
            if info.get("version") != resolved:
                detail += f" (cache also holds {info.get('version')} "
                detail += f"{info.get('tier')}, not used)"
        except Exception:
            pass
        return f"cloakbrowser {_package_version('cloakbrowser')}{detail}"

    @contextmanager
    def open(self, *, direct: bool = False, params: dict = None,
             preset: str = "light", headless: bool = True,
             humanize="off", hand_rng=None, geoip: bool = False,
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

        # `== "engine"`, not `bool(...)`: under `--humanize trueman` the binary
        # must launch with its own cursor off, or two hands drive one path. See
        # `CamoufoxSession.__init__`.
        humanize = _humanize_mode(humanize)
        browser = cb.launch(headless=headless, proxy=proxy_cfg, geoip=geoip,
                            humanize=humanize == "engine")
        try:
            context = browser.new_context()
            yield ChromiumSession(
                context, label=self.name, preset=preset, direct=direct,
                params=params, headless=headless, humanize=humanize,
                version=f"{browser.version} / {self.version()}",
                ready_timeout_ms=ready_timeout_ms, store=store,
                hand_rng=hand_rng, provider=provider)
        finally:
            browser.close()
