"""Does the timezone the harness asks for actually reach the page.

`supports_geo_align` is a promise made by an engine and a column written on
every row of an aligned run. Nothing else here can check that the promise was
kept: a signature that accepted `timezone_id` and dropped it would produce rows
saying `geo-align` from browsers running on the host's own zone, and the mistake
would be unfalsifiable once the exits were gone.

So this reads the zone back from inside the page, the same way a detector does -
`Intl.DateTimeFormat().resolvedOptions().timeZone`, plus the UTC offset, plus the
same two values from a Web Worker. The worker is the part worth having: a
JavaScript property patch does not survive it, and the whole reason both engines
use browser-level emulation (`Emulation.setTimezoneOverride`) rather than
patching is that it does.

Sends nothing to any target, needs no credentials, touches no gateway. Run it
after any Playwright, Patchright or zendriver upgrade.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nmbench.console import tolerate_unencodable_output

# Ahead of the engines: zendriver installs colorama's stdout wrapper on import,
# and the wrapper has no reconfigure of its own.
tolerate_unencodable_output()

from nmbench import engines

SENDS_REQUESTS = False

# Deliberately not a zone this machine could be in, and deliberately one with a
# half-hour offset: a browser that ignored the override entirely and one that
# applied some default would both be caught, where a swap between two whole-hour
# European zones might not be.
ZONE = "Asia/Kolkata"
EXPECTED_OFFSET_MINUTES = -330

READ_BACK = """
() => new Promise(resolve => {
  const here = {
    zone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    offset: new Date().getTimezoneOffset(),
  };
  const src = `onmessage = () => postMessage({
    zone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    offset: new Date().getTimezoneOffset(),
  })`;
  const worker = new Worker(URL.createObjectURL(
    new Blob([src], {type: 'application/javascript'})));
  const done = w => resolve({page: here, worker: w});
  worker.onmessage = e => done(e.data);
  worker.onerror = () => done(null);
  worker.postMessage(1);
  setTimeout(() => done(null), 5000);
})
"""


def read_back(engine_name: str, zone: str, headless: bool) -> dict:
    with engines.session(engine_name, direct=True, params={}, preset="none",
                         headless=headless, timezone_id=zone) as session:
        page = session.new_page({})
        page.goto("about:blank")
        return page.evaluate(READ_BACK)


def check(engine_name: str, zone: str, headless: bool) -> bool:
    engine = engines.REGISTRY[engine_name]
    if not engine.supports_geo_align:
        print(f"{engine_name:<12} declares supports_geo_align = False, skipped")
        return True

    try:
        seen = read_back(engine_name, zone, headless)
    except Exception as exc:
        print(f"{engine_name:<12} FAILED TO RUN  {type(exc).__name__}: {exc}")
        return False

    if not seen or not seen.get("page"):
        print(f"{engine_name:<12} read nothing back")
        return False

    page = seen["page"]
    worker = seen.get("worker")
    # The offset decides it and the name does not, because the browser answers
    # with ICU's canonical spelling rather than the one it was handed: ask for
    # `Asia/Kolkata` and Chromium reports `Asia/Calcutta`. Reading that as a
    # failure would have condemned an override that had in fact been applied
    # correctly, and it is why the constant above is a zone whose offset no
    # machine running this repository is likely to already have.
    ok = page.get("offset") == EXPECTED_OFFSET_MINUTES
    print(f"{engine_name:<12} page   {page.get('zone')!s:<20}"
          f"offset {page.get('offset')}")
    if worker:
        # A patch that stopped at the main context would show up here and
        # nowhere else, which is exactly the failure mode this repository keeps
        # documenting for JS property patching.
        agrees = worker.get("offset") == page.get("offset")
        print(f"{'':<12} worker {worker.get('zone')!s:<20}"
              f"offset {worker.get('offset')}"
              f"{'' if agrees else '   DISAGREES WITH THE PAGE'}")
        ok = ok and agrees
    else:
        print(f"{'':<12} worker did not answer, so the strongest half of this "
              f"check is missing")
    # The control, and it costs one more launch. Everything above is also
    # satisfied by an engine that reports this zone whatever it is handed, and
    # the unaligned arm of a geo run is exactly that case: half the rows in the
    # comparison are the ones where nothing was set, so an override that could
    # not be turned off would collapse the axis into a single arm and every
    # difference in it would read as zero.
    try:
        loose = read_back(engine_name, None, headless)
        host = loose["page"]
    except Exception as exc:
        print(f"{'':<12} the unaligned control did not run: "
              f"{type(exc).__name__}: {exc}")
        return False
    unset = host.get("offset") != page.get("offset")
    print(f"{'':<12} unset  {host.get('zone')!s:<20}"
          f"offset {host.get('offset')}"
          f"{'' if unset else '   SAME AS THE ALIGNED ARM'}")
    ok = ok and unset

    note = "OK" if ok else ("WRONG, the row would claim an alignment that did "
                            "not happen")
    print(f"{'':<12} {note}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engines", default="patchright,zendriver")
    parser.add_argument("--zone", default=ZONE)
    parser.add_argument("--headful", action="store_true",
                        help="the override is browser level and mode should "
                             "not matter, which is itself worth confirming once")
    args = parser.parse_args()

    names = [n.strip() for n in args.engines.split(",") if n.strip()]
    unknown = [n for n in names if n not in engines.REGISTRY]
    if unknown:
        parser.error(f"unknown engines {unknown}, known: {engines.names()}")

    print(f"asking every engine for {args.zone}, reading it back from the page "
          f"and from a Web Worker\n")
    results = [check(n, args.zone, not args.headful) for n in names]
    print()
    if all(results):
        print("every engine that claims the feature applied it")
        return 0
    print("at least one engine did not apply the zone it was handed. An "
          "aligned run would write rows that cannot be corrected afterwards, "
          "so do not start one until this passes.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
