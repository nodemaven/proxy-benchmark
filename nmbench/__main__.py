"""One entry point over the scripts: `python -m nmbench <command> [flags]`.

This is a dispatcher and deliberately not a framework. It finds the scripts on
disk, prints them grouped by whether they spend traffic, and runs the one asked
for with the remaining arguments handed over untouched. Every script keeps its
own argparse, its own `--help` and its own defaults, and still runs directly:
`python scripts/benchmark.py --dry-run` behaves exactly as before.

The alternative - one argparse with subcommands owning every flag in the
repository - was rejected. It would put the flags of eight unrelated experiments
into one file, so a change to a probe would touch the shared entry point, and the
one thing this repository cannot afford is a common code path between the runner
and the experiments that measure it. A dispatcher that only chooses a file cannot
develop an opinion about what the file does.

Commands are discovered, not listed. Adding a probe is a file in
`scripts/probes/`, the same rule that governs engines and providers.

    python -m nmbench                       # what exists, and what it costs
    python -m nmbench benchmark --dry-run
    python -m nmbench engine-fingerprint --engines chromium,camoufox
"""
import difflib
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Folder -> what running anything in it costs, and whether that is the default
# for the folder. The split is the point: a reader deciding whether to type a
# command should not have to open it first.
GROUPS = [
    ("", "the benchmark", True),
    ("probes", "probes: one question each, most of them spend traffic", True),
    ("analysis", "analysis: reads data/runs/, sends nothing", False),
    ("tools", "tools: regenerate committed inputs, send nothing", False),
]


def folder_path(folder: str) -> Path:
    return ROOT / "scripts" / folder if folder else ROOT / "scripts"


def discover() -> dict:
    """Command name -> path, for every runnable script in `scripts/`."""
    found = {}
    for folder, _, _ in GROUPS:
        directory = folder_path(folder)
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            found[path.stem.replace("_", "-")] = path
    return found


def summarize(path: Path) -> str:
    """The first line of the script's docstring, as its one-line description."""
    text = path.read_text(encoding="utf-8")
    start = text.find('"""')
    if start == -1:
        return ""
    return text[start + 3:].splitlines()[0].strip()


def spends_traffic(path: Path, default: bool) -> bool:
    """Whether running this costs requests, the folder unless it says otherwise.

    A script opts out with a module-level `SENDS_REQUESTS = False`. Read from the
    text rather than by importing, because importing a script to find out what it
    costs is exactly the thing that must not happen by accident.
    """
    if "SENDS_REQUESTS = False" in path.read_text(encoding="utf-8"):
        return False
    return default


def usage(commands: dict) -> None:
    print("usage: python -m nmbench <command> [flags]\n")
    print("Flags after the command belong to the command; -h shows them.\n")
    for folder, caption, sends in GROUPS:
        directory = folder_path(folder)
        names = [n for n, p in commands.items() if p.parent == directory]
        if not names:
            continue
        print(caption)
        for name in names:
            # Only where it contradicts the caption. Repeating "offline" under a
            # heading that already says so trains the eye to skip the marker.
            exception = sends and not spends_traffic(commands[name], sends)
            free = "  [offline]" if exception else ""
            print(f"  {name:<20}{summarize(commands[name])}{free}")
        print()


def main() -> int:
    commands = discover()
    argv = sys.argv[1:]

    if not argv or argv[0] in ("-h", "--help", "help"):
        usage(commands)
        return 0

    name, rest = argv[0], argv[1:]
    if name not in commands:
        alike = difflib.get_close_matches(name, commands, n=3, cutoff=0.5)
        print(f"unknown command {name!r}", file=sys.stderr)
        if alike:
            print(f"did you mean: {', '.join(alike)}", file=sys.stderr)
        print("run `python -m nmbench` for the list", file=sys.stderr)
        return 2

    path = commands[name]
    # argv[0] is the script path, exactly as if it had been run directly, so a
    # parser error prints the name of the file the flags actually belong to.
    sys.argv = [str(path), *rest]
    runpy.run_path(str(path), run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main())
