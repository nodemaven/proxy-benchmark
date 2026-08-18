"""What each engine tells a page about itself, measured offline.

Every marker here is read on `about:blank`. Nothing is requested, no gateway is
touched, no target sees anything: this probe costs zero traffic and zero pool
reputation, so it can be run as often as you like and it is the right thing to
run first when an engine is added or upgraded.

It answers the question the pass rates cannot. A pass rate says an engine got
through; it does not say what the engine was hiding, or whether the next target
would read the same value and refuse. Two engines with the same pass rate today
and different `navigator.webdriver` are not equally safe tomorrow.

The control earns its place here. Stock Chromium should light up this table -
`webdriver: True`, no plugins, SwiftShader instead of a GPU. If it does not,
the probe is broken, not the browser. And a marker where the control and an
anti-detect engine agree is a marker that engine is not addressing.

What this cannot see: the TLS handshake, which is negotiated below JavaScript,
and the CDP artefacts a detector finds by timing `Runtime.enable` or reading a
stack trace. An engine that looks clean here can still be caught on either.
`tls_echo.py` reads the layer below this one; the layer model itself is in the
README.

Usage:
    python scripts/probes/engine_fingerprint.py
    python scripts/probes/engine_fingerprint.py --engines chromium,camoufox
    python scripts/probes/engine_fingerprint.py --headful
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nmbench import engines
from nmbench.sink import JsonlSink

# Read by `python -m nmbench` when it lists what a command costs. This probe
# lives in probes/ because it is one question about the engines, but it opens no
# connection, and a command list that told an operator otherwise would make them
# hesitate over the one measurement that is free.
SENDS_REQUESTS = False

# Read in one evaluate so every value comes from the same page in the same
# state. Wrapped individually because a single throwing getter would otherwise
# take the whole row with it, and "this engine threw" is worth less than "this
# engine hides WebGL".
PROBE = """() => {
  const safe = (fn) => { try { const v = fn(); return v === undefined ? null : v; }
                         catch (e) { return "threw: " + e.name; } };
  const gl = safe(() => {
    const c = document.createElement("canvas");
    const ctx = c.getContext("webgl") || c.getContext("experimental-webgl");
    if (!ctx) return null;
    const dbg = ctx.getExtension("WEBGL_debug_renderer_info");
    return dbg ? ctx.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : "no-debug-ext";
  });
  return {
    webdriver: safe(() => navigator.webdriver),
    user_agent: safe(() => navigator.userAgent),
    platform: safe(() => navigator.platform),
    languages: safe(() => (navigator.languages || []).join(",")),
    plugins: safe(() => navigator.plugins.length),
    mime_types: safe(() => navigator.mimeTypes.length),
    hardware_concurrency: safe(() => navigator.hardwareConcurrency),
    device_memory: safe(() => navigator.deviceMemory),
    max_touch_points: safe(() => navigator.maxTouchPoints),
    pdf_viewer: safe(() => navigator.pdfViewerEnabled),
    webgl_renderer: gl,
    window_chrome: safe(() => typeof window.chrome),
    permissions_api: safe(() => typeof navigator.permissions),
    screen: safe(() => screen.width + "x" + screen.height),
    inner: safe(() => window.innerWidth + "x" + window.innerHeight),
    color_depth: safe(() => screen.colorDepth),
    timezone: safe(() => Intl.DateTimeFormat().resolvedOptions().timeZone),
    // A headless Chromium historically had no outer window at all. Zero here
    // is itself the tell, which is why it is recorded rather than skipped.
    outer_delta: safe(() => window.outerWidth - window.innerWidth),
    // Set by Playwright's own bindings on the page. Their presence means the
    // driver is visible to any script that enumerates the window object.
    binding_leaks: safe(() => Object.getOwnPropertyNames(window)
        .filter((k) => /^(__playwright|__pw|cdc_|__driver|__selenium|__nightmare)/.test(k))
        .join(",") || null),
    stack_leak: safe(() => {
      try { null.f(); } catch (e) {
        return /puppeteer|playwright|devtools/i.test(e.stack || "") ? "yes" : "no";
      }
      return "no";
    }),
  };
}"""

# Printed in this order: the markers that decide a refusal first.
ROWS = ["webdriver", "webgl_renderer", "plugins", "mime_types", "window_chrome",
        "binding_leaks", "stack_leak", "platform", "languages", "timezone",
        "hardware_concurrency", "device_memory", "max_touch_points",
        "pdf_viewer", "screen", "inner", "outer_delta", "color_depth",
        "permissions_api", "user_agent"]


def main() -> None:
    browsers = [name for name, engine in engines.REGISTRY.items()
                if getattr(engine, "runs_script", False)]

    parser = argparse.ArgumentParser()
    parser.add_argument("--engines", default=",".join(browsers),
                        help=f"comma separated, from {browsers}")
    parser.add_argument("--headful", action="store_true",
                        help="open real windows: a real compositor and a real "
                             "GPU, which changes webgl_renderer and the window "
                             "geometry. Engines that cannot do it are skipped")
    parser.add_argument("--channel", default=None,
                        help="browser build for the Chromium engines, for "
                             "example chrome or msedge. Patchright's own docs "
                             "ask for chrome; on the bundled build it still "
                             "sends HeadlessChrome in the User-Agent. Engines "
                             "that ship their own binary ignore this")
    args = parser.parse_args()

    names = [n.strip() for n in args.engines.split(",") if n.strip()]
    unknown = [n for n in names if n not in engines.REGISTRY]
    if unknown:
        parser.error(f"unknown engines {unknown}, known: {engines.names()}")
    not_browsers = [n for n in names if n not in browsers]
    if not_browsers:
        parser.error(f"{not_browsers} do not run JavaScript, so there is "
                     f"nothing for a page to report about them")

    availability = engines.report_availability()
    print("reading navigator on about:blank - no request leaves this machine\n")

    sink = JsonlSink("engine_fingerprint")
    results = {}
    for name in names:
        if availability.get(name):
            print(f"  {name}: SKIPPED, {availability[name]}")
            continue
        engine = engines.REGISTRY[name]
        if args.headful and not engine.supports_headful:
            print(f"  {name}: SKIPPED, this engine is headless only")
            continue
        try:
            # preset "none" on purpose: a blocking rule that never fires on
            # about:blank would still be a difference between the engines.
            with engines.get(name).open(direct=True, preset="none",
                                        headless=not args.headful,
                                        channel=args.channel) as session:
                page = session.new_page()
                try:
                    page.goto("about:blank")
                    info = page.evaluate(PROBE)
                finally:
                    page.close()
                info["engine_version"] = session.version
        except Exception as exc:
            print(f"  {name}: FAILED, {type(exc).__name__}: {exc}")
            continue

        results[name] = info
        sink.write({"engine": name, "headful": args.headful,
                    "channel": args.channel, **info})
        print(f"  {name}: read {len(info)} markers")

    if not results:
        print("\nnothing to compare")
        return

    shown = list(results)
    width = max(24, *(len(n) + 2 for n in shown))
    print("\n" + "=" * (26 + width * len(shown)))
    print(f"{'marker':<26}" + "".join(f"{n:<{width}}" for n in shown))
    print("-" * (26 + width * len(shown)))
    for key in ROWS:
        cells = []
        for name in shown:
            value = results[name].get(key)
            text = "-" if value is None else str(value)
            cells.append(text[:width - 2])
        # A marker every engine agrees on is not evidence about any of them.
        same = len({str(results[n].get(key)) for n in shown}) == 1
        marker = key if not same or len(shown) == 1 else f"{key} ="
        print(f"{marker:<26}" + "".join(f"{c:<{width}}" for c in cells))

    print("\n'=' marks a row where every engine reported the same value: either "
          "nobody addresses it,\nor it does not vary on this machine. The "
          "differences are the rows without it.")
    if "chromium" in results:
        print("\nchromium is the unmodified control. A row where an engine "
              "matches it is a row\nthat engine is not hiding.")
    print(f"\nraw rows: {sink.path}")
    print(json.dumps({n: results[n].get("user_agent") for n in shown}, indent=2))


if __name__ == "__main__":
    main()
