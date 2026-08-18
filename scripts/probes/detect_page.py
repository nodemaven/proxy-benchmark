"""What a page that runs its own checks says about each engine.

`engine_fingerprint.py` reads 21 markers we chose, on `about:blank`, and costs
nothing. That is the right first probe and it has one blind spot it cannot fix:
it can only report the markers someone thought to write down. On 2026-08-12
Camoufox was clean on every one of them - `navigator.webdriver` false, plugins
and mimeTypes present, a real renderer string - and Google refused it 10 times
out of 10 from the operator's own line with a real window and no transport loss
at all. Something the list does not contain was doing the work.

So this probe gives up the zero-traffic property on purpose and loads a page
whose own job is to catch automation. It is a hint, not a verdict: the page
implements its checks and Google implements different ones, and an engine that
passes everything here can still be refused. What it buys is a list of named
failures to look at, which beats guessing at what an opaque `/sorry/` meant.

The page is a parameter rather than a constant so the probe outlives any single
vendor, and rows are read generically - name, value, and whatever class the page
put on the cell - so a page that adds a check does not need a change here.

This sends real requests to a third party. It touches no gateway and spends no
pool reputation, but it is not free and it is not silent.

Usage:
    python scripts/probes/detect_page.py
    python scripts/probes/detect_page.py --headful
    python scripts/probes/detect_page.py --engines camoufox,patchright --headful
    python scripts/probes/detect_page.py --url https://example.org/detector
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nmbench import engines
from nmbench.sink import JsonlSink

DEFAULT_URL = "https://bot.sannysoft.com/"

# Rows are read by shape rather than by name: any table row carrying at least
# two cells is a check, the first cell names it and the second holds the result.
# A page that renames a check or adds one keeps working; a page that changes its
# markup entirely returns nothing, which is visible as an empty table rather
# than as a silently short list.
EXTRACT = """
() => {
  const out = [];
  document.querySelectorAll('tr').forEach(tr => {
    const cells = tr.querySelectorAll('td, th');
    if (cells.length < 2) return;
    const name = (cells[0].innerText || '').trim();
    if (!name) return;
    out.push({
      name: name.slice(0, 40),
      value: (cells[1].innerText || '').trim().slice(0, 48),
      cls: (cells[1].className || '').trim().slice(0, 24),
    });
  });
  return out;
}
"""


def failed(row: dict) -> bool:
    """Whether the page itself called this check a failure.

    The class the page puts on the cell is the page's own judgement, so it is
    read rather than re-derived. A value is only inspected when no class was
    offered at all: inventing a rule here would mean this probe disagreeing with
    the page it is quoting, and then neither number could be trusted.
    """
    if row["cls"]:
        return "fail" in row["cls"].lower()
    return row["value"].strip().lower() in ("present", "true", "missing")


def main() -> None:
    browsers = [name for name, engine in engines.REGISTRY.items()
                if getattr(engine, "runs_script", False)]

    parser = argparse.ArgumentParser()
    parser.add_argument("--engines", default=",".join(browsers),
                        help=f"comma separated, from {browsers}")
    parser.add_argument("--url", default=DEFAULT_URL,
                        help="the detector to load. A parameter because no "
                             "single vendor's checks are the measurement")
    parser.add_argument("--headful", action="store_true",
                        help="open real windows. Engines that cannot are "
                             "skipped by name rather than run headless beside "
                             "the others, which would not be a comparison")
    parser.add_argument("--settle", type=float, default=6.0,
                        help="seconds to let the page finish its own async "
                             "checks before reading the table. Reading early "
                             "reports a page mid-test as an engine failing it")
    parser.add_argument("--channel", default=None,
                        help="browser build for the Chromium engines")
    args = parser.parse_args()

    names = [n.strip() for n in args.engines.split(",") if n.strip()]
    unknown = [n for n in names if n not in engines.REGISTRY]
    if unknown:
        parser.error(f"unknown engines {unknown}, known: {engines.names()}")
    not_browsers = [n for n in names if n not in browsers]
    if not_browsers:
        parser.error(f"{not_browsers} do not run JavaScript, so a page that "
                     f"tests a browser has nothing to say about them")

    availability = engines.report_availability()
    sink = JsonlSink("detect_page")
    print(f"loading {args.url}")
    print(f"mode: {'headful' if args.headful else 'headless'}, direct, "
          f"no gateway\nraw rows -> {sink.path}\n")

    results = {}
    for name in names:
        if availability.get(name):
            print(f"  {name}: SKIPPED, {availability[name]}")
            continue
        engine = engines.REGISTRY[name]
        if args.headful and not engine.supports_headful:
            print(f"  {name}: SKIPPED, this engine is headless only, so a "
                  f"headful row for it would be a lie")
            continue
        try:
            with engines.get(name).open(direct=True, preset="none",
                                        headless=not args.headful,
                                        channel=args.channel) as session:
                page = session.new_page()
                try:
                    page.goto(args.url, timeout=60000)
                    page.wait_for_timeout(int(args.settle * 1000))
                    rows = page.evaluate(EXTRACT)
                finally:
                    page.close()
                version = session.version
        except Exception as exc:
            print(f"  {name}: FAILED, {type(exc).__name__}: {exc}")
            continue

        results[name] = {r["name"]: r for r in rows}
        bad = [r["name"] for r in rows if failed(r)]
        sink.write({"engine": name, "engine_version": version,
                    "headful": args.headful, "url": args.url,
                    "checks": len(rows), "failed": bad, "rows": rows})
        print(f"  {name}: {len(rows)} checks read, {len(bad)} called a failure "
              f"by the page")

    if not results:
        print("\nnothing to compare")
        return

    shown = list(results)
    width = max(20, *(len(n) + 2 for n in shown))
    every = []
    for info in results.values():
        for key in info:
            if key not in every:
                every.append(key)

    print("\n" + "=" * (34 + width * len(shown)))
    print(f"{'check':<34}" + "".join(f"{n:<{width}}" for n in shown))
    print("-" * (34 + width * len(shown)))
    for key in every:
        cells = []
        for name in shown:
            row = results[name].get(key)
            if row is None:
                cells.append("-")
            else:
                mark = "FAIL " if failed(row) else ""
                cells.append(f"{mark}{row['value']}"[:width - 2])
        # A row every engine passes identically is not evidence about any of
        # them, and printing it buries the rows that are.
        if len(set(cells)) > 1 or any(c.startswith("FAIL") for c in cells):
            print(f"{key[:33]:<34}" + "".join(f"{c:<{width}}" for c in cells))

    print("\n" + "=" * (34 + width * len(shown)))
    for name in shown:
        bad = [k for k, r in results[name].items() if failed(r)]
        print(f"  {name}: {len(bad)} failed - {', '.join(bad[:6]) or 'none'}")
    print("\nA page's checks are not the checks a search engine runs. Read a "
          "clean sheet here as 'nothing obvious', never as 'undetectable'.")
    print(f"\nraw rows: {sink.path}")


if __name__ == "__main__":
    main()
