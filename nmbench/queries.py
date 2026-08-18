"""Loading the committed query lists.

Inputs are files in the repository, never generated at run time. Two runs of the
same command on two machines must send the same strings in the same order, or
the pass rates are not comparable and nobody can check our numbers.

Taking a prefix rather than a sample is deliberate: `--queries 50` is the first
fifty of the committed order, so a short run is a strict subset of a long one and
the two can be compared directly.
"""
from pathlib import Path

from .targets import SEARCH_QUERIES

QUERY_DIR = Path(__file__).resolve().parent.parent / "data" / "queries"

# The ten-query set the probe scripts use. Named so a run can say which list it
# drew from without the reader having to guess.
BUILTIN = {"smoke": list(SEARCH_QUERIES)}


def available() -> list:
    names = set(BUILTIN)
    if QUERY_DIR.exists():
        names.update(p.stem for p in QUERY_DIR.glob("*.txt"))
    return sorted(names)


def load(name: str = "serp_1000", limit: int = None) -> list:
    """Return a query list by name, optionally the first `limit` of it."""
    if name in BUILTIN:
        queries = list(BUILTIN[name])
    else:
        path = QUERY_DIR / f"{name}.txt"
        if not path.exists():
            raise FileNotFoundError(
                f"no query list {name!r}: nothing would be sent. "
                f"Available: {available()}"
            )
        queries = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
                   if line.strip() and not line.startswith("#")]

    if not queries:
        raise ValueError(f"query list {name!r} is empty, so the run would do nothing")
    if limit is not None:
        if limit < 1:
            raise ValueError(f"--queries {limit} would send nothing")
        if limit > len(queries):
            raise ValueError(
                f"query list {name!r} holds {len(queries)}, asked for {limit}. "
                f"Repeating queries to reach a target number makes the pass rate "
                f"a measurement of the repeat, not of the list."
            )
        queries = queries[:limit]
    return queries
