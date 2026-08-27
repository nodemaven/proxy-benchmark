"""Write the engine list in README.md from the registry.

The list of frameworks under test used to appear in one place: the `--engines`
row of the flag table, most of the way down the file. A reader deciding whether
this repository is worth their afternoon had to scroll past everything to find
out what it drives.

Generated rather than typed for the same reason the results block is. There is no
drift guard that catches a stale prose list - adding an engine is one import and
one registry line, and nothing about that change looks like it should have
touched the README. So the README asks the registry.

The one-line description of each engine is the first line of its module
docstring, which already existed and already says the plain-language thing:
"Camoufox: a patched Firefox driven through Playwright". Keeping the text there
rather than in a table here means it sits next to the code it describes and is
read by anyone opening the module.

    python scripts/engine_table.py             # print the block
    python scripts/engine_table.py --readme    # rewrite it in README.md
"""
import argparse
import io
import os
import sys
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nmbench import engines

README = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "README.md")

MARK_BEGIN = "<!-- ENGINES:BEGIN -->"
MARK_END = "<!-- ENGINES:END -->"

# What has to be on the machine before the engine can run, in the words the
# install step uses. Not derivable from the class: `check()` reports availability
# but not what would fix it, and the four Chromium builds are the surprise this
# column exists to give away early.
INSTALLS = {
    "http": "nothing beyond `requests`",
    "curlcffi": "`curl_cffi`, no browser",
    "chromium": "`playwright install chromium`",
    "patchright": "`patchright install chromium`",
    "rebrowser": "`rebrowser_playwright install chromium`",
    "cloak": "`cloakbrowser.ensure_binary()`",
    "camoufox": "`camoufox fetch`",
    "obscura": "a built Obscura binary",
    "zendriver": "the host's installed Chrome",
    "seleniumbase": "the host's installed Chrome",
    "botasaurus": "the host's installed Chrome",
}


def blurb(cls):
    """The first line of the engine module's docstring, minus the trailing dot.

    Two engines share `chromium.py`, so the module line describes both and is
    not specific enough on its own. Those two are the control and the thing it
    controls for, which is worth spelling out rather than collapsing.
    """
    special = {
        "chromium": "Stock Playwright Chromium. The unmodified control every "
                    "other engine is measured against",
        "patchright": "Patchright: Playwright with the automation tells patched "
                      "out",
    }
    if cls.name in special:
        return special[cls.name]
    doc = (sys.modules[cls.__module__].__doc__ or "").strip()
    return doc.split("\n")[0].rstrip(".")


def block():
    print(MARK_BEGIN)
    print()
    print(f"{len(engines.REGISTRY)} frameworks, one registry line each. Anything "
          f"missing from the machine reports itself unavailable and names the "
          f"install command, and the rest of the matrix still runs - "
          f"`--dry-run` prints that list.")
    print()
    print("| `--engines` | what it is | needs |")
    print("|---|---|---|")
    for name, cls in engines.REGISTRY.items():
        print(f"| `{name}` | {blurb(cls)} | {INSTALLS.get(name, '-')} |")
    print()
    print("Any of them takes a `:direct` suffix, which runs that engine around "
          "the gateway inside the same matrix, so the proxy and the no-proxy arm "
          "are measured in one window rather than an hour apart.")
    print()
    print(MARK_END)


def write_readme():
    with open(README, encoding="utf-8") as fh:
        text = fh.read()
    if MARK_BEGIN not in text or MARK_END not in text:
        print(f"{README} has no ENGINES:BEGIN/ENGINES:END markers. Add them "
              f"where the block belongs; this script will not guess at a "
              f"position in a hand-written document.", file=sys.stderr)
        return 1
    buf = io.StringIO()
    with redirect_stdout(buf):
        block()
    head = text.split(MARK_BEGIN)[0]
    tail = text.split(MARK_END, 1)[1]
    new = head + buf.getvalue().rstrip("\n") + tail
    if new == text:
        print("README.md already matches the registry, nothing written",
              file=sys.stderr)
        return 0
    with open(README, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(new)
    print("README.md engine block rewritten", file=sys.stderr)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--readme", action="store_true",
                        help="rewrite the marked block in README.md instead of "
                             "printing it to stdout")
    args = parser.parse_args()
    if args.readme:
        return write_readme()
    block()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
