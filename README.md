<div align="center">

<!-- The mark is byte-identical to the one on the org profile (md5
     2fbcc1624ea51bacf95ca6e9c5c7c686, checked 2026-08-25) so the two pages read as
     one set. Relative rather than absolute, so it survives a fork and renders
     before this repository is public; this README is not shipped to a package
     index, so nothing here needs an absolute URL. -->
<a href="https://github.com/nodemaven"><img src="assets/nodemaven-mark.svg" alt="NodeMaven" height="56"></a>

# proxy-benchmark

**Measures at which layer a target blocks you, and what getting through costs.**

<!-- No CI badge here, deliberately. shields.io reads the workflow anonymously and
     this repository is internal, so the badge rendered a red
     "gate: repo or workflow not found" - measured 2026-08-25 by fetching the badge
     URL itself, not inferred. An earlier note here predicted "gate inaccessible";
     the real string is the harsher one, and it reads as a broken repository rather
     than a private one.
     Nothing is wrong with the workflow: it is named `gate`, active at
     .github/workflows/ci.yml, and its last three runs on `main` were green. A badge
     simply cannot say that to a client that cannot see the repository.
     Restore it the day the repository goes public, not before:
     [![gate](https://img.shields.io/github/actions/workflow/status/nodemaven/proxy-benchmark/ci.yml?style=flat-square&label=gate)](https://github.com/nodemaven/proxy-benchmark/actions/workflows/ci.yml)
     The row count below is re-derived, not carried forward:
     `cat data/runs/*.jsonl | wc -l` gave 12,173 across 156 files on 2026-08-25,
     where the badge had said 3,701 since the first commit. Re-run that command
     before changing the number. -->

[![license](https://img.shields.io/badge/license-MIT-blue?style=flat-square)](LICENSE)
<!-- Links to the workflow, not to pyproject.toml, because pyproject.toml declares no
     `requires-python` at all - the only thing backing "3.11 | 3.13" is the CI matrix
     at .github/workflows/ci.yml:50, which is exactly ["3.11", "3.13"]. A badge whose
     link does not contain its own evidence is the kind of thing this repository
     exists to not do. -->
[![python](https://img.shields.io/badge/python-3.11%20%7C%203.13-blue?style=flat-square)](.github/workflows/ci.yml)
[![rows](https://img.shields.io/badge/rows-12%2C173%20published-blue?style=flat-square)](data/runs)

[Quickstart](docs/quickstart.md) · [Commands](#command-reference) · [Findings](#what-the-rows-say) · [Reproduce](#reproduce-these-numbers) · [Notebook](NOTEBOOK.md)

</div>

Three layers can reject a request - the address, the browser, the handshake - and
one engine against one target tells you it failed, not where. This harness varies
one layer at a time and writes one JSONL row per attempt.

A gateway is a `.toml` file and an engine is a module plus one registry line, so
neither ever edits the runner. Point it at NodeMaven, at a competitor, or at
[a proxy you already own](#bringing-your-own-proxy).

New to this, or to Python? [docs/quickstart.md](docs/quickstart.md) needs no proxy
account and no terminal experience. This file assumes you know what a ClientHello
is.

## Contents

- [What it measures](#what-it-measures) - the three layers, and what reads each one
- [What the rows say](#what-the-rows-say) - the findings so far
- [Setup](#setup) - install, and the four browser downloads that surprise people
- [Running it](#running-it) - first matrix, front-page entry, your own proxy, a
  second provider, the axes
- [Command reference](#command-reference) - every command and every flag value on
  one page
- [What the rows mean](#what-the-rows-mean) - the verdict enum and the columns
  that are easy to misread
- [Operational safety](#operational-safety) - breaker, budget, shared pool
- [Reproduce these numbers](#reproduce-these-numbers) - each headline mapped onto
  the command it comes out of
- [Repository layout](#repository-layout) · [Contributing](#contributing) ·
  [Disclosure](#disclosure)

## What it measures

| Layer | What gives you away | Read with |
|---|---|---|
| IP reputation | datacenter range, a burnt residential exit, a country the target treats harshly | `--countries`, `probe-and-hold`, `gateway-health` |
| Browser signals | `navigator.webdriver`, empty plugin list, a SwiftShader renderer, a `HeadlessChrome` User-Agent | `engine-fingerprint`, `detect-page` |
| TLS handshake | ClientHello shape: cipher and extension counts, absence of GREASE | `tls-echo` |

Debugging fails when you inspect a layer above the one that is actually rejecting
you, which is the whole reason the axes are separate.

## What the rows say

- **DuckDuckGo sorts entirely on the `HeadlessChrome` substring.** 95 of 95 for
  the engines whose User-Agent omits it, 0 of 50 for the two that carry it,
  across three browser families and two drivers. A headless Chromium-family
  engine is not measuring that target, it is measuring the User-Agent.
- **The handshake explains neither Google nor Amazon.** Chromium, Patchright and
  Obscura emit a byte-identical ClientHello and their pass rates differ by 44 to
  nothing. Compare JA4, never JA3 - Chrome shuffles extension order per
  connection, so a JA3 difference between two Chromium engines is noise.
- **For Google the address is the whole of it.** P(pass given a served page) was
  83 of 83 and did not vary by country, while P(served) ran from 13% on `us` to
  62% on `ru`. Do not pin `us`. The ranking among the other settings must not be
  quoted: their intervals overlap almost entirely. **Those levels are a reading of
  2026-08-12**: six days later on the server, `country=any` was served 19 times in
  2673 attempts, 1%. The decomposition is unaffected - it is the second term that
  stays flat - but no absolute Google rate here should be read as current.
- **Amazon stopped separating the engines, and the correction is larger than the
  finding was.** On the workstation in August, Camoufox was served 90% of the time
  and every Chromium-family engine met the throttle. Re-measured on the server at
  7627 attempts, the **unmodified control is the best engine on the target** at
  96%, Camoufox is at 92% beside three Chromium-family engines, and only
  Patchright is clearly worse at 63%. The Firefox-against-Chromium reading did not
  survive; the direction on Patchright did.
- **The hold is real, warming is not.** 108 of 109 held rows passed after a
  served probe. Warming the exit first measured 32% against 30%, on the arm where
  the published claim was 20% to 75%.
- **Geo alignment buys nothing and can cost a lot.** Flat on Patchright,
  zendriver lost six sevenths of its yield. Do not turn it on.

Each has a denominator and a run id behind it in [NOTEBOOK.md](NOTEBOOK.md), and
several are corrections of an earlier claim in the same file that did not survive
replication. That is the intended shape: a number here is a reading of the hours
it was taken in.

## Setup

Python 3.11 or newer, run from a checkout. There is no `[project]` section to
install, because the committed query lists and `data/` are part of the instrument.

    python -m venv .venv
    .venv\Scripts\Activate.ps1          # macOS, Linux: . .venv/bin/activate
    pip install -r requirements-dev.txt
    python -m playwright install chromium
    python -m patchright install chromium
    python -m rebrowser_playwright install chromium
    python -c "import cloakbrowser; cloakbrowser.ensure_binary()"
    camoufox fetch
    copy .env.example .env               # macOS, Linux: cp .env.example .env

Playwright, Patchright, rebrowser and cloakbrowser pin four different Chromium
builds and **no two share a download**, so a fresh machine fetches four browsers -
the build is what several findings here are about. `zendriver`, `seleniumbase` and
`botasaurus` download nothing and drive the host's installed Chrome, so a machine
without Chrome loses three engines and the rest carry a build nobody pins.

None of it is mandatory. An engine whose dependency is missing reports itself
unavailable and names the install command; the rest of the matrix runs.
`--dry-run` prints that list, so run it first on a new machine.

**Headful on a headless host needs `xvfb-run -a`.** `--headful` is the difference
between `Chrome/...` and `HeadlessChrome/...` on the wire, which is the whole of
the DuckDuckGo finding. A virtual display does not restore the GPU, so WebGL falls
back to software and a headful server run is not a headful workstation run.

Obscura is not on PyPI. Download the **`-stealth`** archive, unpack it, put the
directory on PATH. The plain archive is a different build and the stealth patches
are the thing being measured.

`.env` holds `NODEMAVEN_LOGIN`, `NODEMAVEN_PASSWORD`, `NODEMAVEN_HOST` and
`NODEMAVEN_PORT`. **The prefix is the provider id**, so `oxylabs.toml` reads
`OXYLABS_LOGIN` and two accounts sit in one `.env` - which is what a matrix
interleaving two providers needs. `.env` is gitignored, `config.py` is the only
reader, and it resolves on first use so everything else imports and tests on a
machine with no account.

Before spending anything:

    make check                        # ruff plus the offline suite
    python scripts/benchmark.py --dry-run

## Running it

`python -m nmbench` lists every command, what it answers, and which ones spend
traffic. It is a dispatcher: the remaining flags go to the script untouched, and
every script still runs directly by path.

    python -m nmbench                            # what exists, and what it costs
    python -m nmbench benchmark --dry-run
    python -m nmbench engine-fingerprint         # offline, sends nothing

A first matrix, one engine against the unmodified control:

    python scripts/benchmark.py --engines patchright,chromium \
        --targets google_serp --queries 40 --batch 10 --headful

The `:direct` suffix puts the same browser on both sides of the gateway inside one
window; two runs an hour apart would measure the hour as well. A global `--direct`
forces every cell direct and cannot be partly undone by a spec that omitted the
suffix.

    python scripts/benchmark.py --engines chromium,chromium:direct,camoufox \
        --targets amazon_search --queries 100 --batch 10

Resume skips attempts already judged, so an interrupted run does not re-ask the
targets:

    python scripts/benchmark.py --resume data/runs/benchmark_<stamp>.jsonl

Then read what it said, and what it really cost:

    python scripts/analysis/report.py
    python scripts/analysis/calibrate.py

### Entering through the front page

Arriving at `/search?q=` is one request carrying a query string, with no keystroke
behind it, no referrer and no form submission - a shape no person produces. It is
now an axis: `entry` is on every row, `url` for that shape and `home` for landing
on the front page and typing into the box.

    python scripts/probes/probe_and_hold.py --engines patchright,zendriver \
        --identities 20 --series 3

The protocol is an operator's: one sticky exit per session, type on the front
page, drop the address if the probe is refused, hold it for a series if the probe
is served. Read the result with `scripts/analysis/held.py`.

### Bringing your own proxy

Any proxy works - bought from anyone, or running on a box you own - and no account
with anybody is needed. Four values in `.env`:

    CUSTOM_HOST=1.2.3.4
    CUSTOM_PORT=8000
    CUSTOM_LOGIN=your_login
    CUSTOM_PASSWORD=your_password

Check it before spending anything on it. Ten CONNECTs, a few hundred bytes,
nothing sent to any target. It is the only check that separates a wrong password
from an unreachable host, because the gateway answers both with a status that
names neither:

    python -m nmbench gateway-health --provider custom

Then run whatever you like through it:

    python scripts/benchmark.py --providers custom \
        --engines http --targets bing_serp --queries 20 --preset none

`data/providers/custom.toml` is already written for the shape most proxies have:
one endpoint, a login, a password, no settings encoded in the username. Nothing to
transcribe, no code.

A gateway with no session parameter cannot be asked for a different exit, so every
attempt leaves from one address. The runner prints this on the plan line:

| Still answerable | Closed |
|---|---|
| which browser gets past which target, what a target costs in bytes, whether your setup announces itself | exit yield, how many queries burn an address, whether rotation helps |

If your provider does sell countries or sticky sessions inside the username, copy
`_template.toml` and write the dialect down there instead.

### Adding a provider

A provider is a username format: gateways take country, sticky session and quality
filter inside the proxy username, and every vendor picks its own separators and
names. So it is a file rather than a module - data cannot branch, and
`tests/test_repository.py` reads the runner's source and fails if it ever compares
against a provider name.

    cp data/providers/_template.toml data/providers/oxylabs.toml
    # fill in the dialect, then set OXYLABS_LOGIN and OXYLABS_PASSWORD in .env
    python scripts/benchmark.py --providers nodemaven,oxylabs \
        --engines patchright --targets google_serp --queries 40 --batch 1

`--providers` is an axis like every other one: cells interleave at batch
granularity, because provider A at 10:00 against provider B at 14:00 measures the
afternoon. The cell key names the provider only when the axis is varied, so runs
recorded before the axis existed still match `--resume`.

**Every definition declares its provenance, and it is the first field to read.**
`status = "measured"` means rows in `data/runs/` came through that gateway;
`status = "documented"` means the dialect was transcribed from the vendor's
documentation and never sent a byte. `--dry-run` prints it.

That is load-bearing because **a wrong username is invisible**. The gateway
measured here answers an unrecognised parameter name with HTTP 200 and the setting
silently dropped, so the run completes and every row claims a setting that was
never applied. A name outside `known_params` is refused before a request exists,
and `--param` is validated against every provider in the matrix before the first
cell opens.

Only `nodemaven.toml` ships, and it is the only gateway any number here was
measured through.

### The axes

An option only some engines implement is the failure this harness is built
against: the run would compare a humanized Camoufox against an unhumanized
everything else, and that reads as an engine difference. Every engine declares
what it supports and the runner refuses a mixed matrix outright.

| Flag | Declared by | Engines that have it |
|---|---|---|
| `--preset` | `supports_blocking` | the Playwright-driven ones plus obscura - `page.route` is a Playwright API and obscura has its own |
| `--headful` | `supports_headful` | everything with a window, so everything except the two scriptless clients and obscura, whose `serve` has no such flag |
| `--geo align` | `supports_geo_align` | camoufox, patchright, rebrowser, cloak, zendriver, botasaurus |
| `--humanize` | `supports_humanize` | camoufox, cloak |
| typed entry | `supports_typing` | camoufox, chromium, patchright, rebrowser, cloak, zendriver |

The table is a summary and the code is the authority: `--dry-run` refuses a matrix
before it starts, rather than leaving a reader to check a list that has rotted.

**A mixed matrix needs `--preset none`.** The default is `light`, and blocking for
some columns and not others measured 4 KB against 9.9 MB on the same Google
refusal page (2026-08-13) - a 2000x engine difference produced entirely by the
flag. It moves verdicts too: a page that never loads its script is judged on
markup that was never finished.

**`--countries` needs no engine feature**, because the host country is the
alignment - the browser reports this machine's timezone and language list whatever
address it leaves from:

    python scripts/benchmark.py --engines camoufox,chromium,chromium:direct \
        --countries ru,us --targets bing_serp --queries 20 --preset none

A direct cell has no country, so the axis collapses for it and it is built once.

**`--geo align`** hands the browser the exit's own timezone through the browser's
emulation rather than by patching a JavaScript property, which reads back
unpatched from an iframe and from a Web Worker. The unmodified control stays at
`False`: the axis is read within one engine, aligned against unaligned, in one
window. Whichever was used is on every row.

**Each target draws from its own committed query list.** A shop and a search
engine have to run in one window and cannot take the same strings: asked
"photosynthesis exam questions", Amazon answers with an empty shelf, which is
indistinguishable from a soft refusal once it is a verdict. `--query-list` forces
one list on everything when that is the question.

## Command reference

Everything above on one page, for reading rather than for learning from. The code
is the authority: `python -m nmbench` lists every command and marks the ones that
send nothing, and `-h` prints the flags with the reasoning attached.

**Start here, by what you are trying to do.**

| I want to | Command | Spends |
|---|---|---|
| See what exists and what each thing costs | `python -m nmbench` | nothing |
| Check the tree is sane before anything else | `make check` | nothing |
| Know what a run would cost before running it | `python scripts/benchmark.py --dry-run` | nothing |
| Know which engines can run on this machine | `--dry-run` again - it prints the ones that cannot and why | nothing |
| See what each browser tells a page about itself | `python -m nmbench engine-fingerprint` | nothing |
| Check a gateway is alive and my username is right | `python -m nmbench gateway-health` | a few hundred bytes |
| Compare two engines on one target | `--engines patchright,chromium --targets google_serp` | traffic |
| Ask what the gateway itself contributes | `--engines chromium,chromium:direct` - the same browser on both sides, one window | traffic |
| Compare countries | `--countries us,any` | traffic |
| Compare two providers | `--providers nodemaven,custom` | traffic |
| Enter through the front page instead of a query URL | `python -m nmbench probe-and-hold` | traffic |
| Continue a run that was interrupted | `--resume` , or `--resume <path>` | traffic |
| Read what a run said | `python scripts/analysis/report.py` | nothing |
| Read one run line by line | `python scripts/analysis/peek.py <file>` | nothing |
| Find out what a run really cost, for the next estimate | `python scripts/analysis/calibrate.py` | nothing |

**Every flag of the matrix runner.** Defaults are what you get for saying nothing,
and two of them are worth knowing before a first run.

| Flag | Values | Default | What it changes |
|---|---|---|---|
| `--engines` | any of `http`, `curlcffi`, `chromium`, `patchright`, `rebrowser`, `cloak`, `camoufox`, `obscura`, `seleniumbase`, `zendriver`, `botasaurus`, comma separated, each optionally with `:direct` | `camoufox` | the frameworks under test. `:direct` runs that one around the gateway in the same matrix |
| `--targets` | `google_serp`, `bing_serp`, `ddg_serp`, `amazon_search`, `walmart_search`, `ipinfo` | `google_serp,bing_serp,ddg_serp` | who is asked. `ipinfo` is the cheap one: it answers with your exit address and judges nothing |
| `--queries` | a number, or `all` | `30` | how many strings are drawn from the target's list |
| `--query-list` | `serp_1000`, `amazon_1000`, `smoke` | each target's own | forces one list on the whole matrix. Only when that is the question - a shop asked a physics question answers with an empty shelf |
| `--batch` | a number | `10` | queries per browser. **This is the session**, and it is the unit every number describes |
| `--countries` | comma separated, `any` allowed | `us` | an axis. See the warning below |
| `--providers` | ids of files in `data/providers/` | `nodemaven` | an axis, interleaved at batch granularity |
| `--preset` | `none`, `light`, `aggressive` | `light` | resource blocking. **A mixed matrix needs `none`** and the runner refuses it otherwise |
| `--geo` | `off`, `align` | `off` | hands the browser the exit's own timezone. Measured 2026-08-14: buys nothing and costs zendriver most of its yield |
| `--headful` | flag | off | a real window. Changes `HeadlessChrome` to `Chrome` in the User-Agent, which is the whole of one target's answer |
| `--humanize` | flag | off | humanized cursor, where the engine has it. Refused for a matrix holding one that does not |
| `--direct` | flag | off | no proxy at all. A control, not a normal mode |
| `--channel` | e.g. `chrome` | bundled build | which Chromium build. It reaches the cell key, so two builds stay separable |
| `--param` | `KEY=VALUE`, repeatable | none | an extra gateway parameter. Every recognised one joins the sticky session key, so adding one moves you to a different exit |
| `--breaker` | a number | `10` | consecutive failures that stop a cell. A pool-safety setting, not a patience one |
| `--pause` | seconds | `5.0` | between attempts. This is a shared production pool |
| `--resume` | nothing, or a path | off | skips attempts already judged in that file, or in the newest run |
| `--dry-run` | flag | off | prints the plan and the cost, sends nothing |
| `--no-bodies` | flag | off | stops keeping response bodies, and gives up re-judging this run offline forever |
| `--sample-ok` | a number | `2` | passing bodies kept per engine and target. Failures are always kept in full |

**`--countries` defaults to `us`, and `us` is the worst setting measured** - 13%
of US exits served against 58% on `any`. A first run on the defaults looks worse
than this pool actually is. The default is not a recommendation: country is part
of the cell key, so changing it would stop all 44 committed benchmark files from
matching `--resume`. Pass `--countries any`, or both if the country is the
question - `us` and `any` in one window is what turns the gap into a finding
rather than into a flattering number.

**`--batch` is the other one.** `--batch 1` opens a fresh browser per query, so
every attempt is a new identity on a new exit; `--batch 10` is one identity doing
ten searches. Different experiments, different questions, both recorded on every
row. At `--batch 1` a Chrome-driving engine also pays a fresh profile's 43 MB
vendor fetch on every attempt.

## What the rows mean

**Verdicts come from page content, not HTTP status.** The same Google reCAPTCHA
page arrived once as 429 and once as 200 (2026-08-11), so a run judged by status
scores the second as a success. There is no boolean `success` column: the enum is
`ok, captcha, consent, block, empty, error`, and every row carries the reason and
the marker counts behind it.

**`empty` is not `block`.** Google hands a scriptless client a 92 KB "enable
JavaScript" scaffold that stays on `/search` and rejects nothing, so scoring it as
a block credits Google with a refusal it never made. 14 of 14 such rows carried
`enablejs` and none carried `recaptcha`.

**`error` is the harness, never the target.** An attempt that threw produced no
evidence, so it produces no verdict. A timed-out selector, a browser that would
not launch and a query that never reached the box are all `error`.

**A refused address diverts the request, and the status does not say so.** Google
answers a refusal by sending the request to `/sorry/`, with a 200 about a quarter
of the time. `report.was_served` - a 200 whose final URL keeps the host and path
asked for - is the test, and it is a property of the exchange, so nothing has to
know a target's name. Google is the only target here where it applies.

**The matrix carries an unmodified control.** `chromium` is Playwright's Chromium
with no arguments, no user agent override and no patches; `navigator.webdriver` is
`true` and stays that way, because without it a pass rate cannot be told apart
from the target letting everything through. `tests/test_engines.py` reads the
source of `ChromiumEngine.open` and fails if `args=` or `user_agent` appear.

**A batch is one session, and a session is the unit.** Ten queries through one
browser is one identity doing ten searches; ten browsers doing one query each is a
different experiment. The claim has been false once - until 2026-08-11 Camoufox
opened a fresh context per query and discarded its cookie jar while every other
engine carried one - and `session-continuity` is the offline probe that caught it.

**Cells interleave, never run in sequence.** Finishing one engine before starting
the next would measure the afternoon. Round-robin at batch granularity, one time
window, same query order. Exactly one batch per cell defeats it, and the runner
says so before it starts.

**`bytes` is two measurements and `relayed` says which.** Playwright engines count
through `page.route` and see page resources; the relay counts sockets and sees
request headers and TLS overhead as well. Never pool them. The relay figure is
what a provider bills, and it adds a loopback hop, so `elapsed_ms` is not
comparable across `relayed`. The provider dashboard rounds to 0.01 GB and is not
an instrument.

**Response bodies are kept, gzipped.** A verdict is one word about 92 KB of
markup, and the question that decides a report is usually asked after the run.
Non-`ok` bodies plus a sample of the passes, controlled by `--no-bodies` and
`--sample-ok`. The archive is gitignored, unlike `data/runs/`, because exit
addresses appear in embedded links. Re-reading 250 stored Amazon bodies offline
found an Akamai interstitial and an AWS WAF challenge filed as refusals, and moved
21 historical rows at no traffic cost.

## Operational safety

**The circuit breaker is not an error handler.** N consecutive failures stop a
cell and it stays stopped: every retry after a refusal confirms automation to the
target and degrades the exit ranges for every other customer on the account. There
is no "error, new sid, retry" path here.

N is measured. Over 129 cells and 1464 attempts, the chance an attempt succeeds
given the failures before it in its own cell is 75% at zero, 5.8% at five and 1.6%
from the sixth onward. Stopping at 5 records a partial refusal as a total one;
running past 10 spends about 98 retries per delivered page. `--breaker` defaults
to 10, and `CircuitBreaker` stays at 5 because every `google_429` run on disk was
measured there.

**Pause between requests.** 3-5 seconds minimum. This is a shared production pool
on a company account, not a lab.

**Never print or paste `Proxy-Authorization`.** It is base64, not encryption.

**`data/runs/` is committed, and masked.** Exit addresses are reduced to their /24
and the proxy username to `<login>` - those are real people's home connections, and
the username identifies the account. Masking happens at the one choke point every
row passes through, and `tests/test_runs_are_publishable.py` fails if a full
address ever reaches disk.

It is committed because every claim above names the run it came from, and several
of those claims are corrections that were only possible because the original rows
were still there. They are **not** a baseline for your own numbers: a rate here is
a reading of the hours it was taken in. `data/runs/README.md` says what each
filename prefix holds and what the masking guard has already missed twice.

**Estimate anything above ~100 requests.** `--dry-run` prices traffic from
per-target constants calibrated by `scripts/analysis/calibrate.py`, each carrying
the run it was read from, and names which targets were measured. Read the hours as
an order of magnitude.

## Reproduce these numbers

Every attempt this repository has made is committed under `data/runs/` as one
JSONL row, and every claim above is a count over those rows. `scripts/analysis/`
reads that directory and nothing else - no network, no credentials, no browser -
and imports only the standard library. A clone and three commands, with nothing
installed and nothing spent:

    python scripts/analysis/report.py --all      # the matrix runs
    python scripts/analysis/held.py              # the probe-and-hold runs
    python scripts/analysis/playbook.py          # what to try first, ranked by lower bound

Where each headline lands:

| Claim | Command and section |
|---|---|
| DuckDuckGo, 0 of 50 for the engines announcing the mode | `report.py --all`, `WHOSE FAILURE WAS IT`, the `ddg_serp` block: chromium 0/8 and 0/19, patchright headless 0/9 and 0/14 |
| DuckDuckGo, 95 of 95 for the ones that do not | `PASS RATE`, the `ddg_serp` column: camoufox 7/7 and 37/37, obscura 34/34 and 10/10, patchright headful 7/7. The winning side reads here rather than in the block above, because Obscura records no HTTP status and is absent from every served-versus-refused split |
| For Google the address is the whole of it | `WHOSE FAILURE WAS IT`, the `google_serp` block. `P(live)` is the address and `P(pass\|live)` is the engine, and it is the second column that does not move |
| Amazon used to invert it | same block, `amazon_search`: camoufox live on 47/48, 42/42 and 25/25, against no Chromium-family cell above 33%. This is the August workstation reading and it is the one that did not survive |
| Amazon no longer separates the engines | `report.py data/runs/benchmark_20260819T055927Z.jsonl`, the `amazon_search` block: chromium 419/436 live at 100% pass, camoufox 431/446, patchright 313/457. Read this one against the row above - the two are six days and one machine apart, and the notebook keeps both |
| The hold is real | `held.py`, `HOW LONG A GOOD EXIT LASTS`: 96%, 98% and 99% at positions 2, 3 and 4 |
| Warming does not replicate | `held.py`, `BY WARM-UP`, which prints the published 20%-to-75% claim next to its own denominator |
| Geo alignment costs yield | `held.py`, `BY GEO ALIGNMENT` |
| The unanswered-CONNECT floor | `report.py --all`, `DID THE RUN MEASURE THE TARGETS OR THE PATH TO THEM`: 23% of proxied attempts against 0% direct. Read the third caveat before reading that as anyone's - the cause turned out to be in the harness's own traffic |

Four caveats the tools print and a table cannot:

- **Pooled is the weaker reading.** `report.py --all` pools runs from different
  weeks into a number belonging to neither. It says so at the top and marks every
  incomplete cell; one file is stronger: `report.py data/runs/<file>.jsonl`.
- **`unmeasured` is not zero.** A cell stopped by ten burned exits in a row was
  never served a body, so it carries no observation of the engine at all. Printing
  0% there would hand the pool's condition to the framework, which is why the
  DuckDuckGo losers are quoted out of the served block and not the pass-rate table.
- **The CONNECT floor was the harness's own traffic, and every row above it
  predates the fix.** 23% against 0% put the failures on the proxied path rather than on a
  flaky local link, and a second machine on another line in another datacentre
  read 25%, which looked like proof that the path was the provider's problem. It
  was not that simple. HTTP proxy authentication is challenge-response, so a
  browser handed credentials opens the **first CONNECT of each session without
  one**, takes the 407 and retries; the gateway counts unauthenticated requests
  per address and bans on a threshold. At one session per attempt the harness
  generated one such CONNECT per attempt and banned itself, on both machines
  equally - which is why a second network could not see it. Measured inside one
  uninterrupted run either side of the gateway-side fix: `ERR_EMPTY_RESPONSE`
  207 of 1131 attempts before, 1 of 1004 after. `probes/proxy_auth_shape.py`
  reproduces the client half against a proxy on loopback and spends nothing.
  Full account in `NOTEBOOK.md`.
- **`held.py` with no argument pools every probe-and-hold window**, where
  `NOTEBOOK.md` quotes the three that varied geo. The aligned arm is the same 45
  probes either way; the unaligned arm picks up rows from windows where geo was not
  the axis and reads 42% rather than 46%. Direction survives, magnitude moves.

The aggregator is optional. The format is one JSON object per line and the columns
are defined by `ROW_FIELDS` in `nmbench/engines/base.py`, so counting something is
five lines. The DuckDuckGo split, from scratch:

```python
import collections, glob, json
tally = collections.Counter()
for path in glob.glob("data/runs/benchmark_*.jsonl"):
    for line in open(path, encoding="utf-8"):
        row = json.loads(line)
        if row.get("target") == "ddg_serp" and not row.get("direct"):
            tally[row.get("engine"), row.get("headless"), row.get("verdict")] += 1
print(sorted(tally.items(), key=str))
```

That prints camoufox 44 `ok`, obscura 44 `ok` and patchright headful 7 `ok` with
no refusal between them, against 27 and 23 `captcha` for the two headless Chromium
cells with no pass between them. 95 and 50, straight out of the rows.

`peek.py <file>` prints one line per attempt for reading a single run by hand.

Two things are deliberately not checkable. **The bodies are not published** -
gzipped into gitignored `data/artifacts/`, because exit addresses turn up in
embedded links. And **exit addresses are recorded as their /24**, because a
residential pool is other people's home connections. No analysis reads a full
address back out, so nothing above depends on the masked half.

## Repository layout

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
        rebrowser.py    a Playwright fork patching the Runtime.enable leak
        obscura.py      Rust browser with its own renderer, over CDP
        seleniumbase.py Chrome over ChromeDriver, the WebDriver family
        zendriver.py    Chrome over raw CDP, no WebDriver and no Playwright
        botasaurus.py   Chrome over raw CDP, a second one, for the 2x2
        curlcffi.py     scriptless client wearing Chrome's ClientHello
    scripts/            README: which of these can spend money
      benchmark.py      the matrix runner: engines x targets, one time window
      probes/           one file per question, each cheap and single-purpose
      analysis/         aggregation over data/runs/, sends nothing
      tools/            generators for committed inputs
    data/providers/     README: one .toml per gateway, and why it is not code
    data/queries/       README: committed inputs, one seed, two lists
    data/runs/          README: masking, filename prefixes, how to read a row
    docs/               README: quickstart and the two findings write-ups
    tests/              offline suite: verdicts, scheduler, DSL, hygiene

Every folder a reader lands in from the file list has its own README, because a
directory listing on GitHub is where navigation actually starts.

The split between `probes/` and `analysis/` tells a reader at a glance which files
can spend money: anything under `analysis/` only reads `data/runs/`. A probe that
happens to send nothing says so, and `python -m nmbench` marks it `[offline]`.

## Contributing

`CONTRIBUTING.md` has the rules that are not obvious from the code, most of them
there because the instrument has already been broken that exact way by a commit
that passed every test at the time. The two that catch people first: do not harden
the unmodified control, and nothing branches on an engine, provider or target name.

The most useful issue you can open is that a number here is wrong. Bring a
denominator.

    pip install -r requirements-ci.txt
    make check          # ruff plus the suite: offline, no credentials, no browser

## Disclosure

Obscura lists NodeMaven as a paid sponsor. That does not make the measurements
wrong, but a benchmark that scores a sponsor's product using a sponsored tool has
to declare the relationship. As it turned out, Obscura is not a candidate engine
here - it has never passed Google or Amazon - and it stays in the registry as the
control that separates "headless" from "announces headless" in the DuckDuckGo
finding.
