"""The matrix: which cells exist, in what order they run, and what is left.

A cell is one combination of the independent axes - engine, target, and the
gateway settings. A pass rate only means something inside a cell, because
comparing across two axes at once produces a number nobody can attribute.

Cells are interleaved at batch granularity rather than run one after another.
Finishing Camoufox before starting Obscura would measure the afternoon; the
targets' own mood moves faster than the difference we are trying to see.

Work is grouped into batches because a session is the unit of measurement. Ten
queries through one browser is one identity doing ten searches, which is what a
scraper actually looks like; ten browsers doing one query each is a different
experiment and gets a different number. The batch size is recorded so the two
are never confused.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

from . import providers


@dataclass(frozen=True)
class Cell:
    engine: str
    target: str
    preset: str
    country: str
    direct: bool = False
    headful: bool = False
    geo: str = "off"
    extra: tuple = ()
    provider: str = ""

    @property
    def key(self) -> str:
        parts = [f"benchmark/{self.target}/{self.engine}-{self.preset}",
                 "direct" if self.direct else self.country]
        parts += [f"{k}-{v}" for k, v in self.extra]
        if self.headful:
            parts.append("headful")
        # Only when it is on. An absent segment keeps the keys of every run
        # recorded before the axis existed valid, so --resume still matches them.
        if self.geo != "off":
            parts.append(f"geo-{self.geo}")
        # Empty for the default provider, which is what every row recorded
        # before this axis existed came through, so those keys stay valid too.
        # The key is a resume identity and not the row's attribution: the
        # provider id is written to every row either way, so a row whose key
        # does not name a gateway still says which one answered it.
        if self.provider:
            parts.append(f"provider-{self.provider}")
        return "/".join(parts)

    @property
    def params(self) -> dict:
        """Gateway parameters for this cell, without the session id."""
        if self.direct:
            return {}
        return {"country": self.country, **dict(self.extra)}


@dataclass
class Batch:
    cell: Cell
    index: int
    queries: list = field(default_factory=list)


def parse_engine(spec: str) -> tuple:
    """`chromium` or `chromium:direct`, returning the name and whether it bypasses.

    The suffix exists so one matrix can hold the same engine with and without
    the gateway. Those two cells answer different questions - direct isolates
    the browser, proxied isolates the address - and they are only comparable
    when they run in the same time window against the same targets. Two separate
    runs an hour apart measure the hour as well.
    """
    name, _, mode = spec.strip().partition(":")
    if mode and mode != "direct":
        raise ValueError(
            f"unknown engine mode {mode!r} in {spec!r}: the only suffix is "
            f"':direct', which runs that engine without the gateway"
        )
    return name, mode == "direct"


def build_cells(engines: list, targets: list, preset: str, countries: list,
                direct: bool = False, headful: bool = False, geo: str = "off",
                extra: dict = None, provider_names: list = None) -> list:
    """Cells for every provider, engine, target and country. Specs may carry
    `:direct`.

    A global `direct` still forces every cell direct, so `--direct` keeps
    meaning "send nothing through the gateway" and cannot be partly undone by a
    spec that omitted the suffix.

    Country is an axis because the browser reports the machine's own timezone
    and language list, so an exit in the country the machine is actually in is a
    consistent identity and an exit anywhere else is not. Running both in one
    time window is what turns that into a measurement instead of an opinion.

    A direct cell has no country - it leaves from the machine's own address -
    so the country axis collapses for it. Without that, two countries would
    produce two cells with the same key, and the second would silently be
    treated as a resume of the first.

    The provider axis collapses for a direct cell in exactly the same way and
    for the same reason: a request that never reaches a gateway cannot be
    attributed to one, and leaving the name on would run one identical direct
    experiment once per provider while presenting the copies as a comparison.

    A cell on the default provider carries no name at all, so a matrix that
    does not vary the axis produces the keys that are already on disk. That is
    the `geo` rule and it is what makes the provider axis free to add: the
    interleaved multi-provider run - the only shape in which two providers are
    comparable - is also the only shape whose keys change.
    """
    extra_items = tuple(sorted((extra or {}).items()))
    default = providers.default_name()
    names = list(provider_names or [default])
    cells, seen = [], set()
    for provider in names:
        for spec in engines:
            name, spec_direct = parse_engine(spec)
            is_direct = direct or spec_direct
            for target in targets:
                for country in countries:
                    cell = Cell(
                        engine=name, target=target, preset=preset,
                        country=country, direct=is_direct, headful=headful,
                        geo=geo, extra=extra_items,
                        provider="" if is_direct or provider == default
                                 else provider)
                    if cell.key in seen:
                        continue
                    seen.add(cell.key)
                    cells.append(cell)
    return cells


def plan(cells: list, queries, batch_size: int,
         done: set = None) -> list:
    """Round-robin batches, with anything already recorded removed.

    `queries` is one list used by every cell, or a mapping of target name to
    list. Targets do not share inputs by nature: a shop asked "playwright vs
    puppeteer" answers that it sells nothing of the sort, which is a measurement
    of the query list and not of whether the request was accepted. The mapping
    is how a search engine and a shop can run in the same time window, which is
    the only way their columns are comparable.

    Resume is not a convenience. A run of two thousand attempts will be
    interrupted, and restarting from zero would re-send queries the targets have
    already answered, which is the pool-heating pattern the circuit breaker
    exists to prevent.
    """
    if batch_size < 1:
        raise ValueError("batch size must be at least 1")
    done = done or set()

    per_cell = {}
    for cell in cells:
        pool = queries[cell.target] if isinstance(queries, dict) else queries
        remaining = [q for q in pool if (cell.key, q) not in done]
        per_cell[cell.key] = [
            remaining[i:i + batch_size]
            for i in range(0, len(remaining), batch_size)
        ]

    batches = []
    depth = max((len(v) for v in per_cell.values()), default=0)
    for index in range(depth):
        for cell in cells:
            chunks = per_cell[cell.key]
            if index < len(chunks):
                batches.append(Batch(cell=cell, index=index,
                                     queries=chunks[index]))
    return batches


def _rows_of(paths: list):
    """Every row in `paths`, skipping what cannot be read.

    A missing file and an unparseable line are both passed over rather than
    raised on, because the caller is always a resume: the run that wrote the
    file may have been killed mid-line, and refusing to start over the last
    half-written row of a four-day run would cost more than the row is worth.
    """
    for path in paths:
        path = Path(path)
        if not path.exists():
            continue
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


def load_completed(paths: list) -> set:
    """Every (cell, query) pair already judged in earlier runs.

    Rows that errored are deliberately not treated as done: an error is the
    harness failing, not the target answering, and it is the one outcome worth
    retrying.
    """
    done = set()
    for row in _rows_of(paths):
        cell, query, verdict = (row.get("cell"), row.get("query"),
                                row.get("verdict"))
        if cell and query and verdict and verdict != "error":
            done.add((cell, query))
    return done


def providers_named(paths: list) -> set:
    """Every gateway the proxied rows in `paths` positively name.

    A cell on the default provider carries no provider segment in its key, and
    the default is whatever `NMBENCH_PROVIDER` says it is. So one gateway's run
    and another's produce byte-identical keys, and resuming across the two would
    both skip attempts the second gateway never made and file its answers under
    a key that reads as the first. That is the failure the provider axis exists
    to prevent, arriving through the one door that does not go past `build_cells`.

    Rows written before the column existed carry no provider at all, and are not
    evidence of a mismatch: 2271 of the 2285 rows committed here are in that
    state, so treating an absent value as a conflict would refuse every resume
    on disk. Only a name that is present and different is a conflict. Direct rows
    name no gateway for the same reason the axis collapses for them: nothing they
    did went through one.
    """
    return {row["provider"] for row in _rows_of(paths)
            if row.get("provider") and not row.get("direct")}


# Bytes per attempt, measured per target, because one global constant cannot
# work here: the figure is dominated by what the target returns, and the two
# targets measured on 2026-08-12 differ by a factor of three in the same run
# shape. Keying on the target is a table lookup, not the runner branching on a
# name - nothing downstream behaves differently, only the arithmetic does.
#
# Every entry carries the run it was read from, so a stale number can be dated
# instead of argued about. Re-read with scripts/analysis/calibrate.py.
#
# Both entries were taken at `--preset light`, which is the default, so the
# preset is inside the number rather than beside it. Over every route-counted
# google_serp row on disk, read 2026-08-15: `light` is 606 KB mean and 390 KB
# median across 780 attempts, `none` is 1145 KB and 1159 KB across 332. A
# mixed-engine matrix - which `supports_blocking` forces to `--preset none` -
# therefore spends roughly double to triple what this table quotes, and the
# estimate is a floor there rather than a figure.
#
# Not repaired with a second key, because those two arms differ in engine and
# verdict mix as well as in preset, and a table fitted to them would be the
# confident wrong number this whole design rejects. It is written down instead,
# so a reader can discount it in the direction it is known to be wrong.
MEASURED_BYTES = {
    # benchmark_20260812T130452Z: 80 attempts, batch 1, 6% ok. Most of this is
    # refusal bodies plus one browser launch per attempt, not result pages.
    "google_serp": 776_000,
    # benchmark_20260812T141501Z: 15 attempts, batch 5, 100% ok, 1.0-1.7 MB of
    # markup each. A passing Walmart page is the most expensive body here.
    "walmart_search": 2_109_000,
    # amazon_search has more completed runs than either of these and is
    # deliberately absent. Its bodies are bimodal - a 2.3 KB throttle stub or a
    # ~900 KB result list, with almost nothing between - so the mean per attempt
    # is just the pass rate in other units, and the pass rate is what a run is
    # trying to find out. Three amazon-only runs read 33, 461 and 869 KB per
    # attempt at 11%, 56% and 50% ok. Any one of them entered here would price
    # the next run by assuming its answer.
}

# For a target with no measurement. Deliberately not the mean of the table: an
# unmeasured target should read as "cheap and unknown" rather than borrow the
# authority of a number taken from somewhere else. It comes from the 2026-08-11
# run, which was mostly small refusal bodies, and it is a floor.
DEFAULT_BYTES = 275_000


def estimate(batches: list, bytes_per_attempt: int = None,
             seconds_per_attempt: float = 25.0,
             seconds_per_session: float = 8.0) -> dict:
    """What the plan will cost before any of it is sent.

    Bytes are per target, from `MEASURED_BYTES`, falling back to
    `DEFAULT_BYTES`. `bytes_per_attempt` overrides the table for the whole plan
    and exists for asking "what if"; it is not what the runner passes.

    A single global byte constant was not merely stale, it was the wrong shape.
    It predicted 3.9 MB for the 2026-08-12 Walmart run that cost 30.2 MB, an
    error of about eight times, while being roughly right for Google in the same
    afternoon. The two quantities move independently: bytes follow the target
    and its verdict mix, wall clock follows `batch`, because a browser launch is
    per session and not per attempt. No constant can track both.

    The returned `basis` names which targets were priced from a measurement, so
    a caller can print the estimate without implying more precision than exists.

    Time is still one blended constant and is knowingly wrong in two directions.
    24.9 s came from the 2026-08-11 run, where 12 of 27 cells stopped at the
    breaker limit and the backoff series alone was about a third of the wall
    clock; it also has the per-session launch already blended into it, which
    `seconds_per_session` then adds a second time. Measured against the two runs
    of 2026-08-12 it undershoots by about 20% at `--batch 1` and overshoots by
    about half at `--batch 5`. Fitting a per-attempt and a per-session term to
    those two runs is arithmetically easy and was rejected: they differ in
    target, batch size and verdict mix at once, so two points cannot separate
    three effects and the result would be a confident wrong number. Read the
    hours as an order of magnitude and settle it with calibrate afterwards.
    """
    attempts = sum(len(b.queries) for b in batches)
    seconds = attempts * seconds_per_attempt + len(batches) * seconds_per_session

    total_bytes, basis = 0, {}
    for batch in batches:
        target = batch.cell.target
        if bytes_per_attempt is not None:
            per, source = bytes_per_attempt, "override"
        elif target in MEASURED_BYTES:
            per, source = MEASURED_BYTES[target], "measured"
        else:
            per, source = DEFAULT_BYTES, "default"
        total_bytes += per * len(batch.queries)
        basis[target] = source

    return {
        "cells": len({b.cell.key for b in batches}),
        "sessions": len(batches),
        "attempts": attempts,
        "megabytes": round(total_bytes / 1024 / 1024, 1),
        "hours": round(seconds / 3600, 2),
        "basis": basis,
    }
