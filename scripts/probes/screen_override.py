"""Whether the passing recipe is the patched driver or the untouched screen.

Patchright headful passed Google 10 times out of 10 on 2026-08-12 where the
unmodified control passed none, and that was read as the driver patches doing
the work. Reading the two launch paths afterwards showed they differ in a second
way nobody chose: Patchright opens a persistent context with `no_viewport=True`,
the control opens `browser.new_context()` and takes Playwright's default 1280x720
viewport, and a viewport is what makes Playwright install a device metrics
override. So the control does not merely report a small window - it reports a
1280x720 *monitor* on a machine whose monitor is 1920x1080, in headful mode,
where a real browser has no reason to.

The detector sheet is what surfaced it: of three engines, the only one reporting
the true screen was the only one Google admitted. That is three data points and a
coincidence is cheap at that size, so this probe changes the one thing and keeps
everything else, including the driver.

Arm A is the shipped Patchright launch, which is the configuration that scored
100%. Arm B is the same driver, the same profile handling and the same session
class with a 1280x720 viewport, which turns the metrics override back on. If B
collapses, the recipe was never "use Patchright", it was "do not lie about the
monitor", and that is a different and much cheaper thing to tell a reader.

The arms alternate by session rather than running one after the other, because
Google's mood over ten minutes is exactly the confound this cannot afford: two
sequential arms would let a change in the target read as a change in the browser.

This sends real queries to Google from the operator's own address. It touches no
gateway and spends no pool traffic, and it is the same address and the same
first queries as the runs it is meant to be compared against.

Usage:
    python scripts/probes/screen_override.py
    python scripts/probes/screen_override.py --queries 6 --batch 3
    python scripts/probes/screen_override.py --width 1920 --height 1080
"""
import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nmbench import artifacts, queries, targets
from nmbench.breaker import CircuitBreaker
from nmbench.engines.chromium import ChromiumSession, PatchrightEngine
from nmbench.sink import JsonlSink

# Read once per session, not per query: it is a property of how the context was
# launched, and printing it is what makes the arm label checkable rather than
# something the reader has to take on trust.
SCREEN = """
() => ({
  screen: `${screen.width}x${screen.height}`,
  inner: `${innerWidth}x${innerHeight}`,
  depth: screen.colorDepth,
  dpr: devicePixelRatio,
})
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", type=int, default=10,
                        help="queries per arm. Both arms always get the same "
                             "list in the same order")
    parser.add_argument("--batch", type=int, default=5,
                        help="queries per browser. 5 matches the run this is "
                             "compared against, and a session is the unit of "
                             "measurement, so changing it changes the question")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720,
                        help="the viewport arm B asks for, which is also the "
                             "screen it will then report. Defaults are "
                             "Playwright's, because the control's numbers are "
                             "what this is explaining")
    parser.add_argument("--pause", type=float, default=5.0,
                        help="seconds between attempts")
    parser.add_argument("--breaker", type=int, default=10,
                        help="consecutive failures that stop one arm. 10 "
                             "matches the matrix runner: an arm that refuses "
                             "and then passes is a partial refusal, and "
                             "stopping at 5 would record it as a total one")
    parser.add_argument("--headless", action="store_true",
                        help="run headless, which is not the question. The "
                             "recipe under test is headful and both arms are "
                             "headful by default")
    parser.add_argument("--channel", default=None)
    args = parser.parse_args()

    engine = PatchrightEngine()
    unavailable = engine.check()
    if unavailable:
        print(f"cannot run: {unavailable}")
        return

    target = targets.TARGETS["google_serp"]
    words = queries.load(target.query_list, limit=args.queries)
    if len(words) < args.queries:
        print(f"only {len(words)} queries available")

    arms = {
        "no-viewport": None,
        f"{args.width}x{args.height}": {"width": args.width,
                                        "height": args.height},
    }
    sink = JsonlSink("screen_override")
    store = artifacts.ArtifactStore("screen_override", run_id=sink.run_id)
    breakers = {name: CircuitBreaker(f"screen/{name}", limit=args.breaker)
                for name in arms}
    rows = {name: [] for name in arms}
    seen = dict.fromkeys(arms)

    print(f"target  : google_serp, {len(words)} queries per arm, "
          f"batch {args.batch}")
    print(f"arms    : {', '.join(arms)}  (same driver, same address, "
          f"{'headless' if args.headless else 'headful'}, direct)")
    print(f"raw rows -> {sink.path}\n")

    # Sessions alternate between the arms so both are exposed to the same ten
    # minutes of whatever mood Google is in.
    batches = [words[i:i + args.batch]
               for i in range(0, len(words), args.batch)]
    for batch in batches:
        for name, viewport in arms.items():
            if breakers[name].tripped:
                continue
            try:
                run_session(engine, name, viewport, batch, target, args,
                            sink, store, breakers[name], rows[name], seen)
            except Exception as exc:
                print(f"  {name}: FAILED, {type(exc).__name__}: {exc}")
            if breakers[name].tripped:
                print(f"  {name}: stopped, {breakers[name].reason}")

    print("\n" + "=" * 78)
    print(f"{'arm':<16}{'screen':<14}{'inner':<14}{'n':>4}{'pass':>7}  verdicts")
    print("-" * 78)
    for name in arms:
        got = rows[name]
        judged = [r for r in got if r["verdict"] != "error"]
        passed = [r for r in judged if r["verdict"] == "ok"]
        counts = {}
        for row in got:
            counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
        rate = f"{100 * len(passed) // len(judged)}%" if judged else "-"
        info = seen[name] or {}
        print(f"{name:<16}{info.get('screen', '?'):<14}"
              f"{info.get('inner', '?'):<14}{len(judged):>4}{rate:>7}  {counts}")

    print("\n" + "=" * 78)
    read(rows, arms)
    print(f"\nraw rows: {sink.path}\nbodies:   {store.written} saved -> "
          f"{store.dir}")


def run_session(engine, name, viewport, batch, target, args, sink, store,
                breaker, collected, seen) -> None:
    """One browser, one arm, N queries.

    The viewport is applied by launching the context the way the arm asks for
    rather than by resizing afterwards: a page resized after load has already
    told the target what it was, and the override is installed at context
    creation either way.
    """
    from patchright.sync_api import sync_playwright

    with sync_playwright() as pw:
        profile = tempfile.mkdtemp(prefix="screen-")
        kwargs = {"no_viewport": True} if viewport is None \
            else {"viewport": viewport}
        context = pw.chromium.launch_persistent_context(
            user_data_dir=profile, headless=args.headless,
            channel=args.channel, **kwargs)
        try:
            browser = context.browser
            session = ChromiumSession(
                context, label=f"patchright-{name}", preset="light",
                direct=True, params={}, headless=args.headless,
                version=f"{browser.version if browser else 'unknown'} / "
                        f"{engine.version()}",
                ready_timeout_ms=8000, store=store)

            if seen[name] is None:
                page = session.new_page()
                try:
                    page.goto("about:blank")
                    seen[name] = page.evaluate(SCREEN)
                finally:
                    page.close()
                print(f"  {name}: {seen[name]}")

            for query in batch:
                row = session.fetch(target, query)
                row["arm"] = name
                row["viewport"] = viewport
                sink.write(row)
                collected.append(row)
                print(f"  {name:<14} {query[:26]:<28}"
                      f"{row['status'] or '-'!s:<5}{row['verdict']:<8}"
                      f"{row['html_len']:>8}")
                wait = breaker.record(row["verdict"])
                if breaker.tripped:
                    return
                time.sleep(max(wait, args.pause))
        finally:
            context.close()
            shutil.rmtree(profile, ignore_errors=True)


def read(rows, arms) -> None:
    """Say what the two numbers mean, including when they mean nothing."""
    names = list(arms)
    rates = []
    for name in names:
        judged = [r for r in rows[name] if r["verdict"] != "error"]
        if not judged:
            print("an arm produced no judged attempt, so there is nothing to "
                  "compare and the hypothesis is neither supported nor refuted")
            return
        rates.append(len([r for r in judged if r["verdict"] == "ok"])
                     / len(judged))

    gap = rates[0] - rates[1]
    if gap >= 0.5:
        print(f"The only change was the viewport, and the pass rate moved "
              f"{gap:.0%}. The screen is doing the work that was credited to "
              f"the driver patches: reporting a monitor the machine does not "
              f"have is enough to be refused, in headful, with every patch "
              f"still in place.")
    elif gap <= -0.5:
        print(f"The overridden arm did {-gap:.0%} better, which contradicts "
              f"the hypothesis and is odd enough to be worth re-running "
              f"before it is written down anywhere.")
    else:
        print(f"Both arms landed within {abs(gap):.0%} of each other, so the "
              f"metrics override is not what Google is reading here and the "
              f"driver patches keep the credit. The detector sheet's screen "
              f"column was a coincidence at n=3.")
    print("One target, one address, one hour. This says nothing about Amazon, "
          "which refuses on the address, and nothing about a pool exit.")


if __name__ == "__main__":
    main()
