"""`scripts/tools/backfill_throttle_verdict.py` - the one-way migration.

It ran once here on 2026-09-28 and relabelled 476 rows. This file exists because
it will run again: every host that carries its own `data/runs/` needs the same
pass, and a migration that has already been applied locally is exactly the kind
of code that is re-run somewhere else months later with nobody left who remembers
what it assumed.

The property under test is the refusal. The match is exact against
`targets.THROTTLE_REASON` rather than a substring search for "503", so a reason
that was reworded is *reported* instead of relabelled. A substring match would
have been shorter, would have looked more robust, and would have quietly folded
two differently-worded pages into one category - which is the same defect the
split was made to fix, committed by the tool that fixes it.
"""
import importlib.util
import json
from pathlib import Path

import pytest

from nmbench.targets import THROTTLE_REASON

ROOT = Path(__file__).resolve().parent.parent


def load():
    path = ROOT / "scripts" / "tools" / "backfill_throttle_verdict.py"
    spec = importlib.util.spec_from_file_location("backfill_throttle", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backfill = load()


def row(verdict, reason, target="amazon_search"):
    return {"verdict": verdict, "verdict_reason": reason, "target": target}


class TestWhatItRelabels:

    def test_the_old_verdict_with_the_known_reason(self):
        assert backfill.classify(row("block", THROTTLE_REASON)) == "relabel"

    def test_an_already_migrated_row_is_left_alone(self):
        """Idempotence, which is what makes it safe to run on a corpus nobody is
        sure about. A second pass over this repository reports 0 relabelled and
        476 already."""
        assert backfill.classify(row("throttle", THROTTLE_REASON)) == "already"

    def test_an_ordinary_block_is_not_touched(self):
        """The third of the corpus the split is *about*: an address refused
        outright, which is what everyone thought `block` meant all along."""
        assert backfill.classify(
            row("block", "error page instead of a result list")) == "skip"

    def test_a_pass_is_not_touched(self):
        assert backfill.classify(row("ok", "result list present")) == "skip"


class TestWhatItRefusesToGuess:

    def test_a_reworded_reason_is_reported_rather_than_relabelled(self):
        """The control. If Amazon's page or our own rule is reworded, a
        substring match on "503" would swallow the new wording into the old
        category without a word, and the corpus would carry two pages under one
        name - which is the exact defect this migration exists to undo."""
        assert backfill.classify(
            row("block", "503 throttle page, some newer wording")) == "unplaced"

    def test_the_known_reason_under_a_third_verdict_is_a_conflict(self):
        """Nothing in `targets.py` can produce this, so if it turns up something
        else is writing that reason string and the migration is not the whole
        story. Reported, not silently overwritten."""
        assert backfill.classify(row("captcha", THROTTLE_REASON)) == "conflict"

    def test_a_503_on_another_target_is_not_this_category(self):
        """`unplaced` is scoped to `amazon_search` on purpose. A Google row
        mentioning 503 is a different page with a different meaning, and
        reporting it here would fill the control's output with noise until the
        control stopped being read."""
        assert backfill.classify(
            row("block", "503 something", target="google_serp")) == "skip"


class TestTheCorpusItAlreadyRanOn:

    def test_the_repository_corpus_is_fully_migrated(self):
        """A regression guard rather than a unit test. If a run file lands here
        from a host that has not had the migration applied, this fails and names
        it - which is cheaper than finding out from a pass rate that pools two
        spellings of one category."""
        runs = ROOT / "data" / "runs"
        if not runs.exists():
            pytest.skip("no corpus in this checkout")
        stragglers = []
        for path in sorted(runs.glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    parsed = json.loads(line)
                except ValueError:
                    continue
                if backfill.classify(parsed) in ("relabel", "unplaced",
                                                 "conflict"):
                    stragglers.append(path.name)
                    break
        assert not stragglers, (
            "these run files still carry the pre-split verdict; run "
            "`python scripts/tools/backfill_throttle_verdict.py --write`: "
            + ", ".join(sorted(set(stragglers))))
