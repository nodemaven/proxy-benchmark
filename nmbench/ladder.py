"""The rungs of the warm-up ladder, as names and as descriptions.

Pure data with no imports, and that emptiness is the design. These spellings are
needed by `scripts/probes/probe_and_hold.py`, which runs the ladder, by
`scripts/run_ladder.py`, which supervises it, and by
`scripts/analysis/runqueue.py`, which has to refuse a rung the probe would not
accept - and the third of those may not import anything third-party at module
level, because `tests/test_repository.py` runs every analysis script's `--help`
with the finder blocking `dotenv` and friends, holding down the README's promise
that a sceptic can re-derive any number from a clone with nothing installed.

Written in `probe_and_hold.py` on 2026-08-26, moved to `nmbench/warm.py` on
2026-09-23 and moved again the same hour, which is the part worth keeping.
`warm.py` is the module about warming and looked like the obvious home; it
imports `.engines.base`, which reaches `nmbench.config`, which reaches `dotenv`.
So the obvious home would have made six lines of string constants cost the queue
its cleanliness test. Measured rather than assumed - the import was blocked at
the finder and `warm` refused - and it is a small instance of a general thing:
where a constant lives is decided by what its readers can afford to import, not
by which module the constant is about.
"""
# What each rung of the warm-up ladder asks, in words and without a domain in
# them. The URLs live on the targets - a probe that knew a domain would be a
# probe that could warm one target better than another - but the *question* is
# the experiment's and belongs beside the code that carries it out.
#
# Here rather than in `scripts/probes/probe_and_hold.py`, where it was written on
# 2026-08-26 and lived until 2026-09-23. It moved when the dashboard needed to
# offer the ladder as something you can launch, because that made three modules
# need the vocabulary and `scripts/probes/` is not importable from any of them:
# it is a script, and importing it costs an engine import. `scripts/run_ladder.py`
# had already paid for that with a hand copy of the two aliases below, carrying a
# comment admitting it could go stale. One more copy in the queue would have made
# the spellings a thing you maintain in three places, and the failure mode is
# silent - a rung the queue accepts and the probe does not is a job that dies
# after the password has been typed.
LADDER = {
    "L0": "cold. The exit meets the target for the first time at the probe, "
          "which is how every row taken before 2026-08-26 was measured",
    "L1": "one page of the target's own, before the probe",
    "L2": "several of the target's own surfaces, on more than one host",
    "L3": "L2, preceded by third-party pages that report the exit to the "
          "target's infrastructure without a navigation to the target itself",
    "N1": "L1's depth with none of L1's pages: one third-party page instead of "
          "one of the target's own, so the two differ in whose page it was and "
          "in nothing else",
    "N3": "L3's depth with none of L3's target-owned pages: the same six "
          "visits, four of them swapped for third-party pages that carry the "
          "same check. N1's design at the depth where the ladder actually "
          "separates",
}

# Rungs that are a control on composition rather than a step in depth, each
# mapped to the rung it controls. They sit outside the L-chain's cumulativeness
# - a control that were a superset of the rung it controls would be that rung
# plus something, which is the confound it exists to remove - so anything
# reasoning about the chain has to skip them.
#
# Named here and not inferred from the leading letter. A convention carried in
# a string is exactly the kind of thing that survives until someone adds `N2`
# meaning something else, and the cost of getting it wrong is an invariant that
# quietly stops being checked.
#
# It is a mapping and not a set as of 2026-09-01, when N3 was added. The depth
# invariant - a control matches the delivered depth of what it controls - lived
# as `N1` and `L1` written into `tests/test_probe_and_hold.py`, so N3 would have
# been added with that check silently applying to nothing. Which rung a control
# answers is a property of the control, so it is declared with it.
NEUTRAL_RUNGS = {"N1": "L1", "N3": "L3"}

# Spellings accepted by --warm, mapped to the level they mean. `off` and `on`
# are kept because they are what the rows already on disk were run with, and
# they mean exactly L0 and L1 - the same two treatments under new names.
WARM_LEVELS = {"off": "L0", "on": "L1"}
WARM_LEVELS.update({level: level for level in LADDER})
WARM_LEVELS.update({level.lower(): level for level in LADDER})


# Where the runtime figures in `--identities` help come from, so the next person
# to change the ladder can redo them instead of guessing.
#
# Measured off `probehold_20260827T201123Z` on 2026-08-28: mean wall span per
# identity was 15.3s at `off`, 102.2s at L1, 172.8s at L2 and 242.6s at L3, and
# the run's total wall clock was 22% above the sum of those spans - browser
# launch, the inter-identity gap and session setup, which the spans do not
# cover. N1 is priced at L1's number because it is L1's depth.
#
# The method is checkable rather than asserted: applied to the four rungs that
# run had, it gives 10.8h against the 11h `run_ladder.py` already documented for
# 60 identities. Note that run stopped after 3h25m because three of its four
# rungs tripped the breaker, so the per-identity spans are real and the totals
# are extrapolations from them.
#
# Printed rather than left in a comment since 2026-09-03, and the cursor axis is
# why. `--identities` is per *cell*, not per rung, which was the same thing until
# an axis crossed the ladder: `--humanize off,trueman` doubles the cells and
# therefore the hours, silently, with no flag in the command looking like it
# costs anything. A number nobody sees before launching a thirteen-hour run is
# not a warning.
#
# Everything here is `google_serp` at `--dwell 20,45`, so it is an estimate for
# the shape the ladder is normally run in and not a general model. It ignores
# `--series`, which the source run held at 3, and it prices the probe at the
# rung's span rather than separately. `trueman` adds a walk of about a second
# per query, which is inside the noise of these spans and is not modelled.
#
# Here rather than in `scripts/run_ladder.py`, where it was measured and lived
# until 2026-09-23, for the same reason as everything above it: the queue has to
# tell an operator what a ladder costs *before* he presses the button, and the
# queue cannot import a script. Moved rather than copied - an estimate that
# disagrees with the run is worse than no estimate, because it is believed.
SPAN_S = {"L0": 15.3, "L1": 102.2, "N1": 102.2, "L2": 172.8, "N3": 242.6,
          "L3": 242.6}
SETUP_OVERHEAD = 1.22

# Below this the run cannot answer the question it is being launched to answer.
#
# Measured on the first ladder run, `probehold_20260826T152748Z`, 12 identities a
# rung. It printed four cells reading 3, 4, 3 and 3 served, which reads as a
# clean negative until the denominators are looked at: errors left 11, 9, 7 and
# 10 judged attempts, and at 11 against 9 the smallest difference Fisher can
# separate at p<0.05 is 0/11 against 4/9. About 40 points. A test that coarse
# returns "no effect" for almost anything put in front of it, and the run would
# then have been quoted as evidence the warm-up does not work.
#
# This paragraph used to justify the floor by saying the ladder exists to test a
# move from 20% to 75%, so a 40-point resolution would miss half of it. There is
# no such effect size: 20% to 75% was never claimed by anyone, it was assembled
# here out of an operator's single 75% and this harness's own baseline. Corrected
# 2026-08-27, see NOTEBOOK.md. The floor is unchanged, because the argument for
# it never needed the number - 40 points is too coarse to act on whatever the
# effect turns out to be, and sizing an experiment against a guessed effect size
# is how you end up measuring the guess.
#
# 40 identities a rung brings the detectable difference to roughly 20 points,
# which is the smallest number worth acting on. `run_ladder.py` defaults to 60
# because errors and short warm-ups take a share off the top before the test sees
# it - that first run lost 23% of its attempts that way.
#
# Overridable by both callers, because a deliberately underpowered smoke test is
# a legitimate thing to want. It just should not be the thing that gets left
# running overnight and then quoted.
MIN_IDENTITIES = 40

# The axes `--identities` is multiplied by. `probe_and_hold.py` builds its cells
# as a product over engine, target, country, param arm, rung, entry, geo and
# cursor mode, and `--identities` is per *cell* - so every one of these doubles
# the run when it gains a second value. Named rather than counted inline so that
# an axis a caller forwards but does not price shows up as a missing name instead
# of as an estimate that is quietly a fraction of the truth.
#
# The names are the supervisor's flags without their dashes, which is what lets
# one list serve both the command line and the queue's JSON. `--warm` is not in
# it: the rungs cost different amounts and are summed from `SPAN_S` rather than
# multiplied. `--interact` is not in it either, because nothing forwards it yet -
# if anything ever does, it belongs here the same day. That rule was tested on
# 2026-09-23, when `--params` began being forwarded and joined this list in the
# same edit.
#
# `params` splits on commas like the rest, which is not a coincidence worth
# relying on blindly - the probe joins the settings *within* one arm with `+`
# precisely so that the separator between arms stays the comma every other axis
# uses. `none,filter=medium+country=us` is two arms and not three.
CELL_AXES = ("engines", "targets", "countries", "entry", "geo", "humanize",
             "params")


def estimate_hours(plan) -> float:
    """Wall clock for the plan, from the spans measured above.

    `plan` is anything with the axes as attributes - an `argparse` namespace
    from the supervisor, a `SimpleNamespace` built from a queue job - because
    the two callers arrive with different objects and neither should have to
    build the other's.

    Multiplied by every axis that multiplies the cells, because `--identities`
    is per cell and a second value on any axis is a second full pass over the
    whole ladder.

    **It counted only the cursor modes until 2026-09-23 and was therefore short
    by a factor of the rest.** `--engines patchright,camoufox` was estimated at
    13h and is 26; three engines against two targets is 78h estimated as 13.
    That was survivable while the only caller was somebody typing the command,
    who had the axes in front of him as he typed them, and it stops being
    survivable now the dashboard offers them as multi-selects and prints this
    number as the thing you check before pressing the button. The bug was not
    that the maths was hard - `cells` is one comprehension in the probe - but
    that the docstring described what the function did, which read as a
    justification rather than as the omission it was.

    An unknown rung is priced at the deepest one. That makes a vocabulary this
    module has not caught up with pessimistic and never flattering, which is the
    only direction that cannot mislead somebody into launching something longer
    than he was told.
    """
    deepest = max(SPAN_S.values())
    rungs = [r.strip() for r in str(plan.warm).split(",") if r.strip()]
    seconds = sum(SPAN_S.get(WARM_LEVELS.get(r, r).upper(), deepest)
                  for r in rungs) * int(plan.identities)
    for axis in CELL_AXES:
        values = [v.strip() for v in str(getattr(plan, axis)).split(",")
                  if v.strip()]
        seconds *= max(len(values), 1)
    return seconds * SETUP_OVERHEAD / 3600
