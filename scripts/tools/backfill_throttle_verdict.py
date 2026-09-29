"""Relabel the historical Amazon 503 rows from `block` to `throttle`.

`throttle` was split out of `block` in `nmbench/targets.py` on 2026-09-28. Every
row written from that point carries the new verdict; every row written before it
says `block` and means the same page. Left alone, the corpus holds one category
under two names, and any rate computed across the join is wrong in a direction
that depends on which files it happened to read.

**Why a migration and not a read-time rule.** There is no shared row loader here:
twelve-odd analysis sites call `json.loads` on their own. A reclassifier would
have to be inserted at each of them, and the failure mode of missing one is a
number that is quietly wrong rather than a crash. One pass over the files leaves
the corpus with a single vocabulary and nothing to remember.

**What it matches, and why the match is exact.** Measured over the 251 run files
on 2026-09-28: 476 rows carry the 503 reason, all on `amazon_search`, all
`block`, and the reason text is one byte-identical string from
`20260812T152451Z` to `20260917T100211Z`. A reason that had been reworded
mid-corpus could not be backfilled - there would be no way to tell a pre-wording
row from an unrelated block - so the stability of that string is the precondition
for this script existing at all. It is therefore matched exactly rather than by
substring: `THROTTLE_REASON` is imported from the module that writes it, so a
future reword makes this script report the rows it cannot place instead of
guessing at them. Those are printed as UNPLACED. **An UNPLACED count above zero
means this script is no longer the right tool**, not that a few rows were missed.

**What it writes.** The verdict changes and `verdict_was` is added, holding the
verdict the harness actually reached at the time. That keeps the run files honest
about their own history - without it the corpus would claim these rows were
judged `throttle` when they were judged `block` and relabelled afterwards - and
it makes the pass reversible. No analysis reads `verdict_was`; it is there for
the person who asks why a 2026-08 row disagrees with the code that produced it.

Idempotent. A row already carrying `throttle` is left alone whether it was
written that way or relabelled by an earlier run of this script.

    python scripts/tools/backfill_throttle_verdict.py           # report only
    python scripts/tools/backfill_throttle_verdict.py --write   # apply
"""

import argparse
import glob
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from nmbench.targets import THROTTLE_REASON

SENDS_REQUESTS = False

# The old name these rows carry, and the only one this pass will overwrite. A
# row sitting at any other verdict with this reason is a contradiction rather
# than a migration candidate, and is reported instead of touched.
OLD_VERDICT = "block"
NEW_VERDICT = "throttle"


def classify(row: dict) -> str:
    """One of: `relabel`, `already`, `unplaced`, `conflict`, `skip`.

    `unplaced` is the control. It fires on a row whose reason mentions 503 but
    is not the string this script knows, which is exactly the case a substring
    match would have swallowed without comment.
    """
    verdict = row.get("verdict")
    reason = row.get("verdict_reason") or ""
    if reason == THROTTLE_REASON:
        if verdict == OLD_VERDICT:
            return "relabel"
        if verdict == NEW_VERDICT:
            return "already"
        return "conflict"
    if "503" in reason and row.get("target") == "amazon_search":
        return "unplaced"
    return "skip"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--write", action="store_true",
                        help="rewrite the files; without it nothing is touched")
    parser.add_argument("--runs", default="data/runs",
                        help="directory of JSONL run files")
    args = parser.parse_args()

    totals = Counter()
    touched = []
    unplaced_reasons = Counter()
    conflicts = Counter()

    for path in sorted(glob.glob(os.path.join(args.runs, "*.jsonl"))):
        lines = []
        changed = 0
        for line in open(path, encoding="utf-8"):
            stripped = line.strip()
            if not stripped:
                lines.append(line)
                continue
            try:
                row = json.loads(stripped)
            except ValueError:
                # A truncated tail is a real thing in this corpus - a killed run
                # leaves half a line. Carrying it through unparsed preserves it
                # rather than deleting evidence to make a migration tidy.
                lines.append(line)
                totals["unreadable"] += 1
                continue
            verdict = classify(row)
            totals[verdict] += 1
            if verdict == "relabel":
                row["verdict_was"] = row["verdict"]
                row["verdict"] = NEW_VERDICT
                changed += 1
            elif verdict == "unplaced":
                unplaced_reasons[(row.get("verdict_reason") or "")[:70]] += 1
            elif verdict == "conflict":
                conflicts[row.get("verdict")] += 1
            lines.append(json.dumps(row, ensure_ascii=False) + "\n")
        if not changed:
            continue
        touched.append((os.path.basename(path), changed))
        if args.write:
            with open(path, "w", encoding="utf-8") as fh:
                fh.writelines(lines)

    for name, n in sorted(touched, key=lambda item: -item[1]):
        print(f"  {name:<44} {n:>4} rows")
    verb = "relabelled" if args.write else "would relabel"
    print(f"{verb} {totals['relabel']} rows across {len(touched)} files")
    print(f"  already {NEW_VERDICT}: {totals['already']}")
    if totals["unreadable"]:
        print(f"  unreadable lines left untouched: {totals['unreadable']}")

    if conflicts:
        print("\nCONFLICT - the throttle reason under some other verdict. This "
              "script did not touch them and the judge cannot produce them, so "
              "something else writes this reason:")
        for verdict, n in conflicts.most_common():
            print(f"  {n:>5}  verdict={verdict!r}")

    if unplaced_reasons:
        print("\nUNPLACED - an amazon_search reason mentioning 503 that is not "
              "the string this script knows. The exact match is deliberate: "
              "these are reported rather than guessed at. If the reason was "
              "reworded, this script is the wrong tool and the corpus needs a "
              "second rule with its own date:")
        for reason, n in unplaced_reasons.most_common():
            print(f"  {n:>5}  {reason!r}")

    if totals["relabel"] and not args.write:
        print("\nnothing was written; pass --write to apply")
    return 1 if (conflicts or unplaced_reasons) else 0


if __name__ == "__main__":
    raise SystemExit(main())
