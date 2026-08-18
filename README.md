# NodeMaven benchmarks

[![gate](https://github.com/nodemaven/proxy-benchmark/actions/workflows/ci.yml/badge.svg)](https://github.com/nodemaven/proxy-benchmark/actions/workflows/ci.yml)

Measurement harness for proxy providers, browser engines and scraping targets.
It answers one question with numbers: **at which layer does a target detect you,
and what does it cost to get through.**

Provider-agnostic by design. Adding a competitor is a config entry, adding a
framework is a module and one registry line, and neither ever edits the runner:
a runner that can name a framework is a runner that can treat it differently.

One provider definition is shipped, `data/providers/nodemaven.toml`, and it is
the only one any of the numbers here were measured through. That is a statement
about this repository's data and not about its scope: the gateway dialect is a
file, so a second provider is a file, and every definition declares whether rows
exist behind it.

## The layer model

Every target checks a different layer, and debugging fails when you inspect a
layer above the one that is actually rejecting you. The whole harness exists to
vary one of these at a time.

| Layer | What gives you away | What this repository uses to read it |
|---|---|---|
| IP reputation | datacenter range, a burnt residential exit, a country the target treats harshly | `--countries`, `probe-and-hold`, `gateway-health` |
| Browser signals | `navigator.webdriver`, empty plugin list, a SwiftShader renderer, a `HeadlessChrome` User-Agent | `engine-fingerprint`, `detect-page` |
| TLS handshake | ClientHello shape: cipher and extension counts, absence of GREASE | `tls-echo` |

A single engine against a single target tells you it failed, not where.

## Layout

    nmbench/            the reusable package - this is what gets published
      config.py         credentials from .env, per provider, on first use
      providers.py      loads data/providers/*.toml: one gateway's dialect each
      proxy.py          username DSL builder + client-side validation
      gateway.py        CONNECT probe, exit address lookup, /24 masking
      relay.py          local authenticating CONNECT forwarder + byte counter
      breaker.py        circuit breaker, one per matrix cell
      console.py        keeps progress output from killing the run
      matrix.py         cells, round-robin scheduling, resume, cost estimate
      queries.py        loading the committed query lists
      blocking.py       resource blocking presets, byte counters
      targets.py        url building + content-based verdicts
      stats.py          the Wilson interval every rate here is quoted with
      sink.py           JSONL output, one file per run
      artifacts.py      gzipped response bodies, so a verdict can be re-read
      __main__.py       `python -m nmbench <command>`, one entry point
      engines/          one module per framework, one shared contract
        base.py         the row schema and the contract every engine implements
        http.py         plain requests client, the no-browser control
        chromium.py     unmodified Chromium (the control) and Patchright
        camoufox.py     patched Firefox over Playwright
        cloak.py        patched Chromium handing back a Playwright browser
        obscura.py      Rust browser with its own renderer, over CDP
        seleniumbase.py Chrome over ChromeDriver, the WebDriver family
        zendriver.py    Chrome over raw CDP, no WebDriver and no Playwright
        curlcffi.py     scriptless client wearing Chrome's ClientHello
    scripts/
      benchmark.py      the matrix runner: engines x targets, one time window
      probes/           one file per question, each cheap and single-purpose
      analysis/         aggregation over data/runs/, sends nothing
      tools/            generators for committed inputs
    data/providers/     one .toml per gateway, with its provenance
    data/queries/       committed inputs: a stranger sends the same strings
    data/runs/          raw results, one JSONL per run, never edited by hand
    docs/               findings, and the question each investigation started from
    tests/              offline suite: verdicts, scheduler, DSL, hygiene

The split between `probes/` and `analysis/` is so a reader can tell at a glance
which files can spend money. Anything under `analysis/` only reads `data/runs/`.
A probe that happens to send nothing says so, and `python -m nmbench` marks it
`[offline]`.

## Setup

    python -m venv .venv
    .venv\Scripts\Activate.ps1
    pip install -r requirements-dev.txt
    python -m playwright install chromium
    python -m patchright install chromium
    camoufox fetch
    copy .env.example .env      # then fill in credentials

Playwright and Patchright pin different Chromium builds and do not share a
download, so both installs are needed. Nothing above is mandatory: an engine
whose dependency is missing reports itself unavailable and the rest of the
matrix still runs.

Obscura is not on PyPI. Download the **`-stealth`** archive for your platform
from the project's releases, unpack it and put the directory on PATH - the plain
archive is a different build, and the stealth patches are the thing being
measured.

`.env` holds `NODEMAVEN_LOGIN`, `NODEMAVEN_PASSWORD`, `NODEMAVEN_HOST` and
`NODEMAVEN_PORT`. **The prefix is the provider's id**, so `oxylabs.toml` reads
`OXYLABS_LOGIN` and several accounts sit in one `.env` at once - which is not
tidiness: a matrix interleaving two providers needs both credential sets live in
one process, and interleaving is the only shape in which two providers are
comparable at all. `.env` is gitignored. Credentials are read in `config.py` and
nowhere else, on first use rather than at import, so the verdict logic, the
scheduler and the query lists can be imported and tested on a machine with no
account at all.

Before spending anything:

    make check                        # ruff plus the offline suite
    python scripts/benchmark.py --dry-run

`--dry-run` prints the plan, the estimated cost, and which engines can actually
run here and why the rest cannot - so a missing binary costs a message instead
of half a run.

## Running it

`python -m nmbench` lists every command, what each one answers, and which of
them spend traffic. It is a dispatcher, not a wrapper: it hands the remaining
flags to the script untouched, and every script still runs directly by path.

    python -m nmbench                            # what exists, and what it costs
    python -m nmbench benchmark --dry-run
    python -m nmbench engine-fingerprint         # offline, sends nothing

A first matrix, an engine against the unmodified control:

    python scripts/benchmark.py --engines patchright,chromium \
        --targets google_serp --queries 40 --batch 10 --headful

The `:direct` suffix runs that engine without the gateway, so the same browser
sits on both sides of the proxy inside one time window - two separate runs an
hour apart would measure the hour as well. A global `--direct` forces every cell
direct and cannot be partly undone by a spec that omitted the suffix.

    python scripts/benchmark.py --engines chromium,chromium:direct,camoufox \
        --targets amazon_search --queries 100 --batch 10

An interrupted run continues where it stopped, which matters: restarting from
zero would re-send queries the targets have already answered.

    python scripts/benchmark.py --resume data/runs/benchmark_<stamp>.jsonl

Afterwards, what it says and what it really cost:

    python scripts/analysis/report.py
    python scripts/analysis/calibrate.py

### Entering through the front page

Every row written before 2026-08-13 arrived at `/search?q=` by navigation: one
request carrying a query string, with no keystroke behind it, no referrer and no
form submission. That is a shape no person produces, and it is now an axis.
`entry` is on every row, `url` for that shape and `home` for landing on the
target's own front page and typing into its box.

    python scripts/probes/probe_and_hold.py --engines patchright,zendriver \
        --identities 20 --series 3

`probe-and-hold` implements an operator's protocol rather than ours: one sticky
exit per session, land on the front page, type, drop the address if the probe is
refused, and hold it for a series if the probe is served. Read the result with
`scripts/analysis/held.py`.

### Adding a provider, and running two of them against each other

A provider, from this harness's point of view, is a username format. Gateways
take their settings - country, sticky session, quality filter - inside the proxy
username, and every vendor picks its own separators, its own parameter names and
its own reaction to getting one wrong. That difference is the whole of it, so it
is a file rather than a module:

    cp data/providers/_template.toml data/providers/oxylabs.toml
    # fill in the dialect, then set OXYLABS_LOGIN and OXYLABS_PASSWORD in .env
    python scripts/benchmark.py --providers nodemaven,oxylabs \
        --engines patchright --targets google_serp --queries 40 --batch 1

`--providers` is an axis like every other one: cells are interleaved at batch
granularity, because provider A at 10:00 against provider B at 14:00 measures the
afternoon. `provider` is on every row, and the cell key names it only when the
axis is varied - so the runs already on disk, recorded before the axis existed,
still match `--resume`.

Definitions are data because data cannot branch. A Python module per provider
invites one `if provider == ...` in a place nobody reviews, and the argument this
whole repository rests on is that every arm went through the same code path;
`tests/test_repository.py` reads the runner's source and fails if it ever
compares against a provider name, the same rule that already governs engines.

**Every definition declares its own provenance, and the field is the first thing
to read.** `status = "measured"` means rows in `data/runs/` were produced through
that gateway from this machine. `status = "documented"` means the dialect was
transcribed from the vendor's own documentation on the date recorded and nothing
here has ever sent a byte through it, so a mistake in it is still possible.
`--dry-run` prints the status of every provider in the matrix for that reason.

The distinction is load-bearing rather than bookkeeping, because **a wrong
username is invisible**. The one gateway measured here answers an unrecognised
parameter name with HTTP 200 and the setting silently dropped: the connection
succeeds, the run completes, and every row claims a setting that was never
applied. Nothing the gateway replies can catch it, which is why a name outside
the definition's `known_params` is refused before a request exists, and why
`--param` is validated against every provider in the matrix before the first cell
opens.

### The axes, and why they are refused rather than dropped

An option only some engines implement is the failure mode this harness is built
against: a run with the flag would compare a humanized Camoufox against an
unhumanized everything else, and that reads as an engine difference. So every
engine declares what it supports and the runner refuses a mixed matrix outright.

| Flag | Declared by | Engines that have it |
|---|---|---|
| `--preset` | `supports_blocking` | the Playwright engines - `page.route` is a Playwright API |
| `--headful` | `supports_headful` | everything with a window |
| `--geo align` | `supports_geo_align` | camoufox, patchright, cloak, zendriver |
| `--humanize` | `supports_humanize` | camoufox, cloak |
| typed entry | `supports_typing` | the engines that can reach a search box |

**A mixed matrix needs `--preset none`.** The default is `light`, and a matrix
mixing Playwright engines with non-Playwright ones would block resources for
some columns and not others: measured 2026-08-13 on one Google attempt each, 4
KB against 9.9 MB on the same refusal page. That is a 2000x engine difference
produced entirely by our own flag, and blocking moves verdicts too, because a
page that never loads its script is judged on markup that was never finished.

**`--countries` is an axis and needs no engine feature**, because the host
country is the alignment. The browser reports this machine's timezone and
language list whatever address it leaves from, so an exit in the country the
machine is in is a consistent identity and an exit anywhere else is not:

    python scripts/benchmark.py --engines camoufox,chromium,chromium:direct \
        --countries ru,us --targets bing_serp --queries 20 --preset none

A direct cell has no country, so the axis collapses for it and it is built once.

**`--geo align` uses the engine's own feature instead**, handing it the exit's
timezone through the browser's emulation rather than by patching a JavaScript
property - a patched property is read back unpatched from an iframe and from a
Web Worker, and browser-level emulation is not. The unmodified control stays at
`False` and must not be given the feature to join the comparison: the axis is
read within one engine, aligned against unaligned, in one window.

Whichever was used is recorded on every row. A run where some engines were
aligned and the rows do not say so cannot be read afterwards.

**Each target draws from its own committed query list.** A shop and a search
engine have to run in one time window to be comparable, and they cannot be sent
the same strings: asked "photosynthesis exam questions", Amazon answers with an
empty shelf, and an empty shelf is indistinguishable from a soft refusal once it
is a verdict in a row. A target declares its list and the runner loads whatever
it names without inspecting it, the same way it refuses to know engine names.
`--query-list` forces one list on everything when that is the question.

## What the rows mean

**Verdicts come from page content, not HTTP status.** Measured 2026-08-11: the
same Google reCAPTCHA page arrived once as 429 and once as 200. A run judged by
status would have scored the second one as a success. There is no boolean
`success` column anywhere - the verdict enum is `ok, captcha, consent, block,
empty, error`, and every row carries the reason the verdict was reached along
with the marker counts it rested on, so a disagreement is settled by re-reading
the rows rather than by hitting the target again.

**`empty` is not `block`.** A client that cannot run JavaScript is handed a 92 KB
"enable JavaScript" scaffold by Google. It stays on `/search` and contains no
rejection of any kind. Counting it as a block credits Google with a refusal it
never made and makes the plain HTTP engine look rejected when it was merely
outmatched. Measured 2026-08-11: 14 of 14 such rows carried `enablejs` and not
one carried `recaptcha`.

**`error` is ours, never the target's.** An attempt that threw produced no
evidence, so it produces no verdict. A selector that timed out, a browser that
would not launch and a typed query that did not reach the box are all recorded
as `error` rather than as a refusal, because attributing our own failure to the
target is the one mistake that flatters every number downstream.

**A refused address diverts the request, and the status does not say so.** Google
answers a refusal by sending the request to `/sorry/`, and serves that diversion
with a 200 about a quarter of the time. `report.was_served` is the test that
separates them - a 200 whose final URL keeps the host and path that were asked
for - and it is a property of the exchange, so nothing has to know a target's
name. Google is the only target here where the split applies; Amazon, Bing,
DuckDuckGo and Walmart serve their refusals inline on the requested path.

**The matrix carries an unmodified control.** `chromium` is Playwright's Chromium
launched with no arguments, no user agent override and no patches;
`navigator.webdriver` is `true` and stays that way. Without it a pass rate cannot
be told apart from the target letting everything through, and "the anti-detect
framework passed" has no denominator. `tests/test_engines.py` reads the source of
`ChromiumEngine.open` and fails if `args=` or `user_agent` ever appear there,
because hardening the control silently is the one change that would invalidate
every number in the report without breaking a single other test.

**A batch is one session, and a session is the unit of measurement.** Ten queries
through one browser is one identity doing ten searches, which is what a scraper
looks like. Ten browsers doing one query each is a different experiment and gets
a different number. `--batch` is recorded on every row so the two are never
confused. That claim is about state and has been false once already: until
2026-08-11 Camoufox opened a fresh context per query and so discarded its cookie
jar between queries while every other engine carried one. `session-continuity`
is the offline probe that caught it and stops it coming back.

**Cells are interleaved, never run in sequence.** Finishing one engine before
starting the next would measure the afternoon. The runner goes round-robin
across matrix cells at batch granularity, inside one time window, with the same
query order. A matrix with exactly one batch per cell defeats this, because
round-robin has nothing to alternate; the runner says so before it starts.

**`bytes` is two different measurements and the row says which.** Playwright
engines count through `page.route`, which sees page resources. The relay counts
sockets, which sees everything the browser sent through the proxy, including
request headers and TLS overhead. Both are legitimate and they are not the same
number, so `relayed` is on every row and the two must never be pooled. The relay
figure is the one that matches what the provider bills. It also adds a loopback
hop, so `elapsed_ms` on a relayed row is not comparable against an unrelayed
one; verdicts and bytes are.

The provider dashboard is not an instrument: it rounds to 0.01 GB, so its
resolution is about 10 MB and measuring a 330-byte request against it is
meaningless.

**Response bodies are kept, gzipped.** A verdict is one word about 92 KB of
markup, and the question that decides a report is usually asked after the run.
Every non-`ok` body is archived plus a sample of the passes; `--no-bodies` turns
it off and `--sample-ok` sets the sample. The archive is gitignored, unlike
`data/runs/`: exit addresses appear in embedded links, so it is evidence for us
and a leak in a public repository. A full disk five hours into a run costs the
evidence, never the measurement.

That archive has already paid for itself twice. Re-reading 250 stored Amazon
bodies offline, at no traffic cost, found an Akamai interstitial and an AWS WAF
challenge that had both been filed as refusals, and moved 21 historical rows -
every one of them belonging to a scriptless client, because a browser clears
those challenges without noticing they existed.

## Operational safety

**The circuit breaker is not an error handler.** N consecutive failures stop a
cell and it stays stopped, because every retry after a refusal confirms
automation to the target and degrades the exit ranges for every other customer
on the account. There is no "error, new sid, retry" path in this repository.

N is measured rather than argued. Over 129 cells and 1464 attempts, the chance
an attempt succeeds given the failures immediately before it in its own cell is
75% at zero, still 5.8% at five, and 1.6% from the sixth onward. So stopping at
5 would record a partial refusal as a total one, and running far past 10 spends
about 98 confirmed-automation retries per delivered page. `--breaker` defaults to
10 in the matrix runner; `CircuitBreaker` itself still defaults to 5, because
every `google_429` run on disk was measured at that and raising it would make
new rows incomparable with the committed ones.

**Pause between requests.** 3-5 seconds minimum during exploration. This is a
shared production pool on a company account, not a lab.

**Never print or paste `Proxy-Authorization`.** It is base64, not encryption.
Anything pasted into a chat, an issue or an article is a leaked credential.

**`data/runs/` is committed, and masked.** Exit addresses are reduced to their
/24 and the proxy username to `<login>`: those are the home addresses of real
people, and the username identifies the account. Masking is applied at the one
choke point every row passes through rather than at each call site, and
`tests/test_runs_are_publishable.py` fails if a full address ever reaches disk.
`scripts/tools/redact_runs.py` is what was run over the rows written before the
rule existed.

**Estimate before launching anything above ~100 requests.** `--dry-run` prices
traffic from constants calibrated by `scripts/analysis/calibrate.py`, keyed per
target, each carrying the run it was read from. A target with no run behind it
is priced from a deliberately low default and the estimate names which targets
were measured, so a mixed matrix cannot imply a precision half of it does not
have. Read the hours as an order of magnitude.

## Findings

`NOTEBOOK.md` is the working notebook: every measured claim, the run it came from,
and the ones that did not survive replication. `docs/` holds the two write-ups
that outgrew it. The short version of what the rows say so far:

- **DuckDuckGo sorts entirely on the `HeadlessChrome` substring.** 95 of 95 for
  the engines whose User-Agent does not carry it, 0 of 50 for the two that do,
  across three browser families and two drivers. A headless Chromium-family
  engine is not measuring that target, it is measuring the User-Agent.
- **The handshake explains neither Google nor Amazon.** Chromium, Patchright and
  Obscura emit a byte-identical ClientHello and their pass rates differ by 44 to
  nothing. Compare JA4 and never JA3: Chrome shuffles extension order per
  connection, so a JA3 difference between two Chromium engines is noise.
- **For Google the address is the whole of it.** P(pass given a served page) was
  83 of 83 and did not vary by country, while P(served) ran from 13% on `us` to
  62% on `ru`. Do not pin `us`; the ranking among the other settings must not be
  quoted, because their intervals overlap almost entirely.
- **Amazon is the opposite** - a plain `requests` client is served, Camoufox is
  served 90% of the time, and every Chromium-family engine meets the throttle.
- **The hold is real and warming is not.** 108 of 109 held rows passed after a
  served probe; warming the exit first measured 32% against 30%, on the arm
  where the published claim was 20% to 75%.
- **Geo alignment buys nothing and can cost a lot.** Flat on Patchright, and
  zendriver lost six sevenths of its yield. Do not turn it on.

Every one of those has a denominator and a run id behind it in `NOTEBOOK.md`, and
several are corrections of an earlier claim in the same file that did not
survive being replicated. That is the intended shape: a number here is a reading
of the hours it was taken in.

## Contributing

`CONTRIBUTING.md` has the rules that are not obvious from the code, and most of
them exist because the instrument has already been broken in that exact way by a
commit that passed every test at the time. The two that catch people first: do not
harden the unmodified control, and nothing branches on an engine, provider or
target name.

The most useful issue you can open is that a number here is wrong. Several claims
in this repository are corrections of an earlier claim in the same file, and the
usual shape is that the direction survived replication and the magnitude did not.
Bring a denominator.

    pip install -r requirements-ci.txt
    make check          # ruff plus the suite: offline, no credentials, no browser

## Disclosure

Obscura lists NodeMaven as a paid sponsor. That does not make the measurements
wrong, but a benchmark that scores a sponsor's product using a sponsored tool
has to declare the relationship. As it turned out, Obscura is not a candidate
engine here - it has never passed Google or Amazon - and it stays in the
registry as the control that separates "headless" from "announces headless" in
the DuckDuckGo finding.
