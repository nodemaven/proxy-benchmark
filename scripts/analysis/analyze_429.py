"""Aggregate every google_429 run into the table the findings file needs.

Steps are run separately, so the evidence is spread over several files. This
reads all of them, in timestamp order, and prints one row per cell.

The five steps isolate one layer at a time against Google, so the row that
matters is usually the difference between two of them rather than any single
rate. Cells stopped by the circuit breaker are marked, because their denominator
is truncated by the harness and not by the target.

Usage:
    python scripts/analysis/analyze_429.py
"""
import json
import sys
from collections import Counter, OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nmbench.sink import RUNS_DIR

ORDER = ["ok", "js-required", "captcha", "consent", "block", "empty", "error", "skipped"]


def refine(row: dict) -> str:
    """Split the `block` verdict using the markers recorded with the response.

    Derived here rather than in targets.py: the verdict enum is the measurement
    contract and stays as it is. But `block` covers two events that mean
    opposite things for this question. Google answers a plain HTTP client with
    a 200 and a "JavaScript is required" page whatever the address is - that is
    the engine hitting a wall, not the address being refused. Confirmed against
    a direct request from a clean residential line, which produced exactly the
    same page with no proxy involved.
    """
    if row.get("verdict") != "block":
        return row.get("verdict")
    markers = row.get("markers") or {}
    if markers.get("enablejs") or (markers.get("noscript") and not markers.get("<h3")):
        return "js-required"
    return "block"


def load() -> list:
    rows = []
    for path in sorted(RUNS_DIR.glob("google_429_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                row["_file"] = path.name
                rows.append(row)
    return rows


def verdict_summary(rows) -> str:
    counts = Counter(refine(r) for r in rows)
    parts = [f"{v} {counts[v]}" for v in ORDER if counts.get(v)]
    return ", ".join(parts) or "-"


def main() -> None:
    rows = load()
    if not rows:
        print(f"no google_429_*.jsonl in {RUNS_DIR}")
        return

    attempts = [r for r in rows if r.get("verdict") != "cell_stopped"]
    stopped = {r["cell"] for r in rows if r.get("verdict") == "cell_stopped"}

    cells = OrderedDict()
    for row in attempts:
        cells.setdefault(row["cell"], []).append(row)

    # Built first and printed second, so the columns can be sized from the rows.
    # Fixed widths were what this had and they overflowed in both directions: a
    # cell key longer than 48 ran into the count, and a verdict summary that
    # exactly filled 34 left no gap at all, so `429:3, 200:1, None:1` and the
    # exit count printed as `None:11`. A reader could not tell how many exits a
    # cell had drawn, which is one of the two things the step comparison rests
    # on. Columns that silently merge are worse than columns that are wide.
    table = []
    for cell, cell_rows in cells.items():
        statuses = Counter(str(r.get("status")) for r in cell_rows)
        labels = {r.get("exit_label") for r in cell_rows if r.get("exit_label")}
        table.append({
            "step": str(cell_rows[0].get("step", "?")),
            "cell": cell,
            "n": str(len(cell_rows)),
            "verdicts": verdict_summary(cell_rows),
            "statuses": ", ".join(f"{k}:{v}" for k, v in statuses.most_common()),
            "exits": str(len(labels)),
            # Not a column of its own: it is a mark on the row, and giving it a
            # width would indent every unstopped cell for the sake of a few.
            "note": "  STOPPED" if cell in stopped else "",
        })

    headers = {"step": "step", "cell": "cell", "n": "n", "verdicts": "verdicts",
               "statuses": "statuses", "exits": "exits", "note": ""}
    width = {key: max(len(headers[key]), max(len(r[key]) for r in table))
             for key in headers}

    def line(values):
        return ("  ".join([values["step"].ljust(width["step"]),
                           values["cell"].ljust(width["cell"]),
                           values["n"].rjust(width["n"]),
                           values["verdicts"].ljust(width["verdicts"]),
                           values["statuses"].ljust(width["statuses"]),
                           values["exits"].rjust(width["exits"])])
                + values["note"])

    rule = max(len(line(headers)), 78)

    print(f"files: {len({r['_file'] for r in rows})}   attempts: {len(attempts)}")
    print("=" * rule)
    print(line(headers))
    print("-" * rule)
    for row in table:
        print(line(row))

    print("\nexit addresses seen (masked, full values were console only)")
    print("-" * rule)
    seen = {}
    for row in attempts:
        label = row.get("exit_label")
        if label:
            seen.setdefault(label, (row.get("exit_prefix"), row.get("exit_org"),
                                    row.get("params", {}).get("country")))
    # Two trailing spaces in every field below rather than a padded width, so a
    # value longer than its column pushes the line out instead of eating the
    # separator. Same defect as the table above, cheaper to avoid here because
    # nothing needs to line up across sections.
    for label, (prefix, org, country) in sorted(seen.items(),
                                                key=lambda kv: int(kv[0].split("_")[1])):
        print(f"  {label:<7}  {prefix!s:<18}  {country or '-'!s:<3}  {org or '-'}")
    if not seen:
        print("  none recorded")

    print("\nverdict totals per target")
    print("-" * rule)
    by_target = {}
    for row in attempts:
        by_target.setdefault(row.get("target", "?"), []).append(row)
    for target, target_rows in by_target.items():
        print(f"  {target:<14}  {len(target_rows):>3}  {verdict_summary(target_rows)}")

    print("\nevidence behind each verdict (one example per target and verdict)")
    print("-" * rule)
    examples = {}
    for row in attempts:
        key = (row.get("target", "?"), row["verdict"])
        if key not in examples:
            examples[key] = row
    for (target, verdict), row in sorted(examples.items()):
        markers = row.get("markers") or {}
        print(f"  {target:<14}  {verdict:<9}  status={row.get('status')!s:<5}"
              f"len={row.get('html_len', 0):<7}  {str(row.get('title'))[:38]!r}")
        if markers:
            print(f"       markers {markers}")
        if row.get("error"):
            print(f"       error   {str(row['error'])[:96]}")

    total_bytes = sum(r.get("bytes", 0) or 0 for r in attempts)
    print(f"\nlocally counted traffic: {total_bytes / 1024:.1f} KB "
          f"({total_bytes / 1024 / 1024:.2f} MB), lower bound - excludes headers,")
    print("TLS overhead and any response without content-length.")
    if stopped:
        print(f"\ncells stopped by the circuit breaker: {len(stopped)}")
        for cell in sorted(stopped):
            print(f"  {cell}")


if __name__ == "__main__":
    main()
