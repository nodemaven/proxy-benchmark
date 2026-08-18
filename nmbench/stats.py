"""The one interval this repository quotes, and the one way it prints it.

Small enough to have been copied instead of imported, and it was: `held.py` and
`playbook.py` carried byte-identical definitions of both functions. That is the
failure mode this module exists to prevent rather than a tidiness complaint - two
tables in the same report would go on disagreeing about a confidence bound with
nothing failing anywhere, which is exactly the class of silent wrong number the
rest of the harness is built to refuse.

Nothing here sends anything or reads a file. It is imported by the analysis
scripts and is safe to import anywhere.
"""
import math

# 95%. Fixed rather than a parameter: every interval printed in this repository
# and every interval quoted in NOTEBOOK.md is at this level, and a per-call level
# would let two tables in one report differ without saying so.
Z = 1.96


def wilson(good: int, total: int) -> tuple:
    """95% interval on a proportion, Wilson rather than normal.

    The normal interval puts 0/10 at exactly zero and 10/10 at exactly one,
    which is how this repository once published `us = 0%` and had to withdraw
    the magnitude a day later - the second window put the same cell at 19%. The
    direction survived replication and the endpoint did not, and an interval that
    cannot express "no successes yet, and the rate is not zero" is what made the
    claim publishable in the first place. Wilson does not do that.

    It also does second duty as `playbook.py`'s sort key, so the shape of the
    lower tail is load-bearing rather than decorative: ranking by the bound is
    what stops a 1/1 cell from topping a table over a 40/60 one.

    An empty denominator returns (0, 0) rather than raising. A cell with no
    attempts is an ordinary row in these tables - a matrix prints every cell it
    planned, including the ones the breaker stopped before the first attempt -
    and `band` renders it as a dash.
    """
    if not total:
        return (0.0, 0.0)
    p = good / total
    d = 1 + Z * Z / total
    centre = (p + Z * Z / (2 * total)) / d
    half = Z * math.sqrt(p * (1 - p) / total + Z * Z / (4 * total * total)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def band(good: int, total: int) -> str:
    """A rate and its interval, as one fixed-width field.

    Percentages, so three digits is the widest a field can get and the columns
    cannot overflow the way a free-text one can. The point rate and the interval
    print together and always: a rate quoted without its width is the form in
    which every retracted claim in this repository was first written down.
    """
    if not total:
        return f"{'-':>8}{'':>13}"
    low, high = wilson(good, total)
    return f"{100 * good / total:>7.0f}%{100 * low:>6.0f}-{100 * high:<6.0f}"
