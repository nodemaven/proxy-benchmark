"""Does the browser's identity match the address it is speaking from?

Camoufox with `geoip=True` looks the exit address up in a MaxMind database and
sets timezone, locale and coordinates from it. That only helps if the lookup
resolves the address the requests actually leave from. Two ways it can silently
fail: the database is missing, in which case the browser keeps the host machine's
timezone, or the sticky session hands out a different exit than the one the
lookup saw, in which case the browser confidently announces the wrong place.

A US exit announcing Europe/Moscow is a contradiction any target can read for
free, and it would sit inside every pass rate this harness produces without ever
appearing as an error.

This costs almost no traffic: two IP lookups per session and a browser that never
leaves about:blank.

Usage:
    python scripts/probes/check_geoip.py
    python scripts/probes/check_geoip.py --n 3 --country gb
"""
import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nmbench.console import tolerate_unencodable_output

tolerate_unencodable_output()

from nmbench import gateway, proxy
from nmbench.sink import JsonlSink

PROBE = """() => {
    const opts = Intl.DateTimeFormat().resolvedOptions();
    let webgl = {};
    try {
        const gl = document.createElement('canvas').getContext('webgl');
        const ext = gl.getExtension('WEBGL_debug_renderer_info');
        webgl = {vendor: gl.getParameter(ext.UNMASKED_VENDOR_WEBGL),
                 renderer: gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)};
    } catch (e) { webgl = {error: String(e)}; }
    return {
        timezone: opts.timeZone,
        locale: opts.locale,
        language: navigator.language,
        languages: navigator.languages,
        platform: navigator.platform,
        webdriver: navigator.webdriver,
        user_agent: navigator.userAgent,
        screen: [screen.width, screen.height],
        offset_minutes: new Date().getTimezoneOffset(),
        webgl: webgl,
    };
}"""


def camoufox_config(options: dict) -> dict:
    """What Camoufox resolved, before a browser is started.

    The settings are handed to the browser as JSON in the environment, split
    across `CAMOU_CONFIG_1..N` because a single variable would be too long.
    Reading them back is the only way to see the geoip result without launching:
    `launch_options` returns just args, env and paths.
    """
    env = options.get("env", {})
    keys = sorted((k for k in env if k.startswith("CAMOU_CONFIG_")),
                  key=lambda k: int(k.rsplit("_", 1)[-1]))
    if not keys:
        return {}
    try:
        return json.loads("".join(env[k] for k in keys))
    except ValueError:
        return {}


def check_database() -> bool:
    """Report whether the geoip database is actually on disk."""
    from camoufox.geolocation import ALLOW_GEOIP, MMDB_DIR, load_geoip_config

    if not ALLOW_GEOIP:
        print("geoip2 is not installed, so geoip=True cannot resolve anything.")
        print("  pip install camoufox[geoip]")
        return False
    files = sorted(MMDB_DIR.glob("*.mmdb")) if MMDB_DIR.exists() else []
    if not files:
        print(f"no mmdb database in {MMDB_DIR}: every launch would fall back to "
              f"this machine's timezone and locale.")
        print("  python -m camoufox fetch")
        return False
    print(f"geoip database: {load_geoip_config().get('name')}")
    for path in files:
        print(f"  {path.name}  {path.stat().st_size / 1024 / 1024:.0f} MB")
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=2, help="sessions to check")
    parser.add_argument("--country", default="us")
    args = parser.parse_args()

    if not check_database():
        sys.exit(1)

    from camoufox.sync_api import Camoufox
    from camoufox.utils import launch_options

    sink = JsonlSink("geoip_check")
    print(f"\nraw rows -> {sink.path}\n")

    for index in range(args.n):
        params = proxy.session_params(f"geo{uuid.uuid4().hex[:8]}",
                                      country=args.country)

        # The echo service directly, not `identify`: the CONNECT header is
        # cheaper but carries only the address, and the comparison needs the
        # timezone that address belongs to.
        seen = gateway.echo(**params)
        expected_tz = seen.get("timezone")

        # Camoufox runs its own public-IP lookup through the same proxy, so a
        # mismatch between this and `seen` is the sticky session moving under us.
        proxy_cfg = proxy.proxy_dict(**params)
        resolved = camoufox_config(launch_options(geoip=True, proxy=proxy_cfg,
                                                  headless=True))
        camoufox_tz = resolved.get("timezone")
        camoufox_locale = "-".join(
            v for v in (resolved.get("locale:language"),
                        resolved.get("locale:region")) if v)
        camoufox_coords = (resolved.get("geolocation:latitude"),
                           resolved.get("geolocation:longitude"))

        with Camoufox(headless=True, geoip=True, block_webrtc=True,
                      proxy=proxy_cfg) as browser:
            page = browser.new_page()
            reported = page.evaluate(PROBE)
            page.close()

        row = {"params": params, "exit_ip": seen.get("exit_ip"),
               "exit_org": seen.get("org"), "exit_country": seen.get("country"),
               "exit_region": seen.get("region"), "exit_timezone": expected_tz,
               "camoufox_timezone": camoufox_tz, "camoufox_locale": camoufox_locale,
               "camoufox_coords": camoufox_coords, "browser": reported}

        agrees = expected_tz and reported.get("timezone") == expected_tz
        row["timezone_matches_exit"] = bool(agrees) if expected_tz else None
        sink.write(row)

        print(f"session {index + 1}")
        print(f"  exit            {seen.get('exit_ip')}  {seen.get('country')}"
              f"/{seen.get('region')}  {(seen.get('org') or '')[:40]}")
        print(f"  exit timezone   {expected_tz or 'not reported'}")
        print(f"  camoufox set    {camoufox_tz}   locale {camoufox_locale}   "
              f"coords {camoufox_coords[0]},{camoufox_coords[1]}")
        print(f"  browser reports {reported['timezone']}   "
              f"{reported['language']}  offset {reported['offset_minutes']}")
        print(f"  device          {reported['platform']}  "
              f"screen {reported['screen'][0]}x{reported['screen'][1]}")
        print(f"  user agent      {reported['user_agent']}")
        print(f"  webgl           {reported['webgl'].get('vendor')} / "
              f"{reported['webgl'].get('renderer')}")
        print(f"  webdriver       {reported['webdriver']}")
        if expected_tz and not agrees:
            print(f"  MISMATCH: the address belongs to {expected_tz}, the browser "
                  f"announces {reported['timezone']}. Either the lookup missed "
                  f"this address or the session moved between the lookup and the "
                  f"launch.")
        elif agrees:
            print("  ok: browser timezone matches the exit address")
        print()

    print(f"raw rows: {sink.path}")


if __name__ == "__main__":
    main()
