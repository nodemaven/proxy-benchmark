# Contributing

This repository is an instrument. Most of the rules below exist because the
instrument has already been broken in that exact way, and the commit that broke
it passed every test at the time.

Read `NOTEBOOK.md` before writing code. It is the working notebook: every measured
claim, the run it came from, and the ones that did not survive replication.

## The gate

    make check

Ruff plus the whole test suite. It is offline in full - no network, no
credentials, no browser - so it runs on a laptop with no account and it runs in
CI. An autouse fixture strips the gateway variables from the environment, so a
test that tried to open a connection fails rather than spends traffic.

    pip install -r requirements-ci.txt      # what the gate needs, 5 packages
    pip install -r requirements-dev.txt     # plus the six browser frameworks

The gate needs none of the frameworks: every engine imports its own inside a
method, which is what lets the contract tests read the registry and the engine
source on a machine with nothing installed. Install the dev list when you are
changing an engine and actually launching it.

Before spending traffic, and this is not a formality:

    python scripts/benchmark.py --dry-run

It prints the plan, the estimated cost, and which engines can run here and why
the rest cannot, so a missing binary costs a message instead of half a run.

## What a change has to respect

**Do not harden the control.** `chromium` is Playwright's Chromium launched with
no arguments, no user agent override and no patches. `navigator.webdriver` is
`true` and stays that way. Without it a pass rate cannot be told apart from the
target letting everything through, and "the anti-detect framework passed" has no
denominator. `tests/test_engines.py` reads the source of `ChromiumEngine.open`
and fails if `args=` or `user_agent` appear. Making the control pass more often
is a helpful instinct everywhere else in this repository and it destroys the
baseline here.

**Verdicts come from page content, never from the HTTP status.** The same Google
reCAPTCHA page arrived once as 429 and once as 200; a run judged by status would
have scored the second as a success. There is no boolean `success` column
anywhere and there must not be one - the enum is `ok, captcha, consent, block,
empty, error`, and `judge` returns the reason alongside it so a disagreement is
settled by re-reading rows rather than by asking the target again.

**`error` is ours and `block` is the target's.** An attempt that threw produced no
evidence, so it produces no verdict. A selector that timed out, a browser that
would not launch, a typed query that did not reach the box: all `error`.
Attributing our own failure to the target is the one mistake that flatters every
number downstream. `empty` is the third case - our client came up short, as when
Google hands a scriptless client a 92 KB "enable JavaScript" scaffold that
contains no refusal at all.

**Nothing branches on a name.** Not an engine name, not a provider name, not a
target name. A runner that can name a framework is a runner that can treat it
differently without anyone noticing, and the argument this whole repository rests
on is that every arm went through one code path. `tests/test_repository.py` reads
the runner source and fails on a comparison against any name in the registries,
deriving the names from the registry so an engine added tomorrow is covered.

Adding an engine is a module in `nmbench/engines/` and one line in `REGISTRY`.
Adding a provider is a `.toml` in `data/providers/` and no code at all.

**A capability only some engines have is refused, not dropped.** `--humanize` was
accepted for any matrix when only Camoufox implemented it, so a run with the flag
compared a humanized Camoufox against an unhumanized everything else and would
have read as an engine difference. Every engine now declares
`supports_humanize`, `supports_geo_align`, `supports_headful`,
`supports_blocking` and `supports_typing`, and the runner refuses a mixed matrix
outright. If you add an option that not every engine can honour, it gets an
attribute and a refusal, never a silent skip.

**One variable per experiment, interleaved in one time window.** Provider, engine
and target are independent axes. Provider A at 10:00 against provider B at 14:00
measures the afternoon. The scheduler goes round-robin at batch granularity, and
a matrix with exactly one batch per cell defeats that because round-robin has
nothing to alternate.

## Data

**`data/runs/` is evidence and is never edited by hand.** It is append-only, one
row per attempt, and a crash usually leaves the measurement intact.

**It is also committed, so every row in it is published.** Exit addresses are
reduced to their /24 and the username to `<login>`: those are the home addresses
of real people. Masking happens at the one choke point every row passes through,
`tests/test_runs_are_publishable.py` fails if a full address reaches disk, and
`scripts/tools/redact_runs.py` repairs a file that fails it. Nothing but `.jsonl`
and the README belongs in that directory - a redirected console log is the same
evidence with the masking undone.

**Never paste `Proxy-Authorization`, a proxy username or a full exit address**
into an issue, a PR or a commit message. The header is base64, not encryption.
`.env` is gitignored and stays that way; credentials are read in `config.py` and
nowhere else, always from the environment.

Response body archives and `data/samples/` are gitignored because a results page
carries the exit address inside embedded links.

## Adding a provider

    cp data/providers/_template.toml data/providers/<vendor>.toml

Fill in the dialect, then point the cheapest probe at it before any matrix:

    python -m nmbench gateway-health --provider <vendor>

A handful of CONNECTs, no target traffic, and the only check that can separate a
wrong username format from a wrong password - the gateway itself cannot, because
it answers both with a status that names neither.

**`status` is load-bearing, not bookkeeping.** `documented` means the dialect was
transcribed from the vendor's own fetched documentation on the date recorded and
nothing here has ever sent a byte through it. `measured` means rows exist in
`data/runs/`. Promote it in the same commit that adds the first run and not
before. Never write a dialect from memory: an invented username format is an
invented technical claim about somebody else's product, and it fails silently,
because at least one gateway answers an unrecognised parameter name with 200 and
the setting quietly dropped.

The session parameter is asked of the provider through `proxy.session_params`,
never written as `"sid"`. That is one gateway's spelling, eleven call sites wrote
it, and against a definition that names the parameter something else every
attempt draws a fresh exit while every row records one held session.

## Operational safety

This is a shared production pool on a company account, not a lab.

**The circuit breaker is not an error handler.** N consecutive failures stop a
cell and it stays stopped. There is no "error, new sid, retry" path in this
repository and a patch adding one will not be merged: every retry after a refusal
confirms automation to the target and degrades the exit ranges for every other
customer on the account.

**Pause between requests**, 3-5 seconds minimum while exploring. **Estimate
before launching anything above about 100 requests.** The 100 GB quota is shared
across all pool types, and the provider dashboard is not an instrument - it rounds
to 0.01 GB, so measuring a 330-byte request against it is meaningless.

A script that sends requests lives in `scripts/probes/`. One that only reads
`data/runs/` lives in `scripts/analysis/`. The split is so a reader can tell at a
glance which files can spend money, and a probe that happens to send nothing
declares `SENDS_REQUESTS = False` so the command list does not overstate it.

## Claims

A finding needs a denominator, a run id and the control that ran in the same
window. Counts, not rates: "3 of 40", never "about 8%".

Quote a rate with the interval `nmbench.stats` gives it. Several claims in
`NOTEBOOK.md` are corrections of an earlier claim in the same file, and the usual
shape is that the direction survived replication and the magnitude did not -
`us = 0%` was published here and turned out to be the tail of one hour. If two
arms overlap on their intervals, say the ranking must not be quoted, and say it in
the file rather than leaving the reader to notice.

Adding a target means reading marker rules off real archived bodies, never from a
vendor's public description of its own defences. Rule order matters and is
load-bearing: a served Walmart page carries the entire PerimeterX modal inline
and hidden, with `recaptcha` on it twice, so a challenge test placed before the
result test scores a perfect page as a captcha.

## Style

- English in code, comments, commit messages and documentation. The tests fail on
  Cyrillic, which is not arbitrary: this harness is operated in Russian and the
  notes it produces are written in Russian first.
- Hyphens, never em-dashes. Also enforced.
- Every module has a docstring saying which question it answers. Enforced.
- No marketing language. This repository is read by people who will fork it.
- Errors carry the consequence and not just the symptom: say what will happen to
  the operator, not that a value is invalid.
- Comments explain why, not what. If removing the comment would not confuse a
  future reader, do not write it.

## Dependencies

Adding one needs a reason in the requirements file next to it. Two pins there are
load-bearing rather than tidiness: `playwright==1.60.0` ships the Chromium build
the control runs on, so an unpinned upgrade silently re-baselines every number in
the repository without touching a line of code or failing a test. Moving it is a
decision to re-baseline.

There is deliberately no `[project]` or `[build-system]` in `pyproject.toml`. The
repository is run from a checkout, because the committed query lists and `data/`
are part of the instrument and an installed copy would separate the code from its
inputs.
