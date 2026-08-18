"""One run, every row, in one line each. For reading a fresh run by hand.

Deliberately dumb: no aggregation, no verdict arithmetic. It exists because a
40-row run is small enough to read, and reading it is what catches a new refusal
shape before an aggregate averages it away.

The columns are sized from the rows rather than from constants. Fixed widths
were what it had, and `patchright-direct` is four characters wider than the
field it was given, so the engine ran into the target and every line of a direct
run read as one word. A tool for reading rows by hand has to stay legible on
whatever the registry grows into next.
"""
import argparse
import json
from pathlib import Path

SENDS_REQUESTS = False

# The reason is free text from `judge` or from an exception, and a long one
# would push the fixed columns off the terminal. Last, and clipped.
REASON_WIDTH = 64


def read(path: Path, target: str = None) -> list:
    """The attempt rows of one run, as the columns this prints.

    Bookkeeping rows carry no engine - a stopped cell, a session that never
    launched - and are skipped, because they have no verdict to read.
    """
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if "engine" not in row:
            continue
        if target and row.get("target") != target:
            continue
        rows.append({
            # The engine spec carries the browser channel after a slash; the
            # name and the arm are what a reader is scanning for.
            "engine": row["engine"].split("/")[0],
            "target": row.get("target") or "-",
            "country": (row.get("params") or {}).get("country", "-"),
            "status": str(row.get("status")),
            "verdict": row.get("verdict") or "-",
            "html": str(row.get("html_len") or 0),
            "reason": (row.get("verdict_reason") or row.get("error") or "")[
                :REASON_WIDTH],
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run", type=Path)
    parser.add_argument("--target", default=None,
                        help="one target only, for a mixed run")
    args = parser.parse_args()

    rows = read(args.run, args.target)
    if not rows:
        print("no attempt rows here")
        return 0

    # `country` rather than `geo`: this is the exit the gateway was asked for,
    # and `geo` is the alignment axis on a different column entirely.
    headers = {"engine": "engine", "target": "target", "country": "country",
               "status": "http", "verdict": "verdict", "html": "html",
               "reason": "reason"}
    width = {key: max(len(headers[key]), max(len(r[key]) for r in rows))
             for key in headers}

    def line(values):
        return ("  ".join(values[k].ljust(width[k])
                          for k in ("engine", "target", "country", "verdict"))
                + "  " + values["status"].rjust(width["status"])
                + "  " + values["html"].rjust(width["html"])
                + "  " + values["reason"])

    print(line(headers))
    print("-" * 78)
    for row in rows:
        print(line(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
