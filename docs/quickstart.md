# Quickstart: from nothing to your first measurement

This page assumes you have never used a terminal. It gets you to a real
measurement, on your own machine, in about twenty minutes. Nothing here needs a
proxy account, and the first measurement costs you nothing but a few seconds of
your own internet.

If you already write Python for a living, skip this and read
[the README](../README.md) instead. It says the same things in a tenth of the
words.

## What you are about to do

Websites try to tell people apart from programs. When they decide you are a
program, they stop answering. This repository measures **where** that decision
gets made: sometimes it is your IP address, sometimes it is something your
browser says about itself, sometimes it is the shape of the very first packet
your computer sends before any page exists.

You are going to install the harness, ask it to describe a run without sending
anything, and then send a small real one and read what came back.

## Step 0. What you need

- A computer running Windows, macOS or Linux. Any of the three works.
- About 20 minutes.
- Roughly 2 GB of free disk if you want the browsers. The first measurement
  below does not need them.

You do **not** need a proxy account to get through this page. That comes at
step 8, and it is optional.

## Step 1. Get Python

Python is the language this is written in. Type this into a terminal:

**Windows.** Press the Start button, type `powershell`, press Enter. In the
window that appears:

    python --version

**macOS.** Press Cmd+Space, type `terminal`, press Enter. Then:

    python3 --version

**Linux.** Open your terminal application. Then:

    python3 --version

You want to see `Python 3.11.something` or higher. If you see a lower number, or
a complaint that the command was not found, install it from
[python.org/downloads](https://www.python.org/downloads/). On Windows, tick the
box that says **"Add python.exe to PATH"** on the first screen of the installer.
It is easy to miss and everything below depends on it.

From here on, wherever this page says `python`, type `python3` if you are on
macOS or Linux.

## Step 2. Get the code

If you have `git`:

    git clone https://github.com/nodemaven/proxy-benchmark.git
    cd proxy-benchmark

If you do not, and do not want it: open the repository page in a browser, click
the green **Code** button, choose **Download ZIP**, unpack it, and then in your
terminal type `cd ` (with the space) and drag the unpacked folder onto the
terminal window. That fills in the path for you. Press Enter.

You are now "inside" the folder, which is what every command below assumes.
`dir` on Windows or `ls` elsewhere should list `README.md`, `nmbench` and
`scripts`.

## Step 3. Make a sandbox

A virtual environment is a private copy of Python for this one project, so
installing things here cannot break anything else on your computer. Make one and
switch into it:

**Windows**

    python -m venv .venv
    .venv\Scripts\Activate.ps1

**macOS and Linux**

    python3 -m venv .venv
    source .venv/bin/activate

Your prompt should now start with `(.venv)`. That is how you know you are in it.
If you close the terminal, you have to run the activate line again - nothing
else, just that one.

> **Windows says "running scripts is disabled on this system".** Windows blocks
> scripts by default. Run this once, answer `Y`, then try activating again:
>
>     Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

## Step 4. Install what it needs

    pip install -r requirements-dev.txt

This takes a couple of minutes and prints a lot. It is finished when your prompt
comes back.

## Step 5. Check that it works, without sending anything

    make check

No `make` on your machine? Windows usually has none. Use these two instead:

    python -m pytest
    python -m ruff check .

You are looking for a wall of dots and no red. This runs about 950 tests, all of
them offline: no network, no account, no browser. It is the thing you run before
you spend anything, and it is deliberately quick enough to run every time.

## Step 6. Ask it what a run would cost, without doing it

    python scripts/benchmark.py --dry-run --engines http --targets bing_serp --queries 5 --batch 5 --direct --preset none

That prints something like:

    engines : ['http']
    targets : ['bing_serp']
    queries : bing_serp=serp_1000, 5 attempts left over 1 cells
    sessions: 1 (5 queries per browser)
    gateway : none, direct control
    geo     : off, so every engine reports this machine's own timezone and language list
    cost    : about 1.3 MB and 0.04 h

    dry run: nothing was sent

Read the flags, because you will change them constantly:

| flag | what it means |
|---|---|
| `--engines http` | use the plain client: no browser, no JavaScript. It needs nothing installed |
| `--targets bing_serp` | search on Bing |
| `--queries 5` | five search terms, taken from a list committed in this repository so everyone sends the same ones |
| `--batch 5` | all five through one session, which is what one scraper looks like |
| `--direct` | do not use a proxy, go from your own connection |
| `--preset none` | do not block images and scripts |

`--dry-run` is not a special mode you have to remember. Put it on any command
and it tells you what would happen instead of doing it.

## Step 7. Your first real measurement

Take `--dry-run` off the same line:

    python scripts/benchmark.py --engines http --targets bing_serp --queries 5 --batch 5 --direct --preset none

This sends five ordinary searches to Bing from your own connection. It is about
as much traffic as opening Bing five times in a browser, which is roughly what
it is.

It writes one file: `data/runs/benchmark_<timestamp>.jsonl`. One line per
attempt, and every line carries everything needed to interpret it - which engine,
which target, what came back, and why the harness decided what it decided.

Read it back:

    python scripts/analysis/peek.py data/runs/benchmark_<timestamp>.jsonl

Replace `<timestamp>` with the real name; the run printed it when it finished.
You get one line per attempt:

    engine  target     country  verdict  http     html  reason
    ------------------------------------------------------------------
    http    bing_serp  -        ok        200   183422  result list present

**`verdict` is the whole point of this repository.** It has six possible values
and none of them is "success":

| verdict | meaning |
|---|---|
| `ok` | the target answered with a real page of results |
| `captcha` | there was something to solve or click before results |
| `consent` | a cookie or consent wall was in the way and was not cleared |
| `block` | the target refused |
| `empty` | the page came back but our client could not make anything of it. **Our shortcoming, not a refusal** |
| `error` | the attempt never completed. **Ours, always. Never counted against the target** |

The last two lines matter more than they look. A harness that scored its own
failures as the target refusing would produce numbers that flatter whoever ran
it, and would never be caught doing it.

Congratulations - you have a row of real data. Everything after this is the same
thing with more interesting variables.

## Step 8. Add a browser, and a control

The plain client cannot run JavaScript, so most interesting targets are out of
reach for it. Install a browser the harness can drive:

    python -m playwright install chromium
    python -m patchright install chromium

Both, and it is not a typo. They pin different browser builds and do not share
the download, which is on purpose - the build is what several findings here are
about.

Now run an anti-detect framework against a plain unmodified browser, on the same
target, in the same ten minutes:

    python scripts/benchmark.py --engines patchright,chromium --targets bing_serp --queries 20 --batch 5 --direct --preset none

`chromium` is the **control**: Playwright's browser launched with no arguments,
no disguise and no patches. It announces itself as automated and is meant to keep
doing so. Without it, "the anti-detect framework passed" is not a claim, because
you cannot tell it apart from the target letting everybody through.

The two run round-robin inside one window rather than one after the other. Run
one framework at 10:00 and the other at 14:00 and you have measured the
afternoon.

    python scripts/analysis/report.py

That prints the pass rates with a confidence interval on each. Read the interval,
not the percentage. Five out of ten is 50%, and it is also "somewhere between 19%
and 81%", which is another way of writing "we do not know yet".

## Step 9. Optional: add a proxy account

Everything so far went out from your own address. To vary the address you need an
account with a proxy provider. Any provider - the harness ships one definition
and is built to take others.

    copy .env.example .env      # Windows
    cp .env.example .env        # macOS and Linux

Open `.env` in any text editor and fill in your login and password. The file is
ignored by git and must stay that way; it is the only place credentials live.

Check that the gateway answers before spending a run on it:

    python -m nmbench gateway-health

That opens a handful of connections and sends nothing to any target. It is the
cheapest check that can tell a wrong username format from a wrong password,
which the gateway itself cannot: it answers both with a status that names
neither.

Then drop `--direct` and add a country:

    python scripts/benchmark.py --engines patchright --targets bing_serp --countries us --queries 20 --batch 5 --preset none

**Read the safety rules in the README before running anything larger.** A shared
residential pool is other people's home connections. Hammering a target that has
already refused you degrades those addresses for everybody else on the account,
which is why this harness stops a cell after repeated failures and never retries
behind your back.

## Using a different provider

A provider, from this harness's point of view, is a username format. Gateways
take their settings - country, sticky session, quality filter - inside the proxy
username, and every vendor spells them differently. So a provider is a text file,
not code:

    cp data/providers/_template.toml data/providers/yourprovider.toml

Fill it in from that vendor's own documentation, put `YOURPROVIDER_LOGIN` and
`YOURPROVIDER_PASSWORD` in `.env`, and then:

    python -m nmbench gateway-health --provider yourprovider

Do that before a real run, because **a wrong username is invisible**. The gateway
measured here answers a parameter name it does not recognise with a cheerful HTTP
200 and silently drops the setting. The connection works, the run finishes, and
every row claims a setting that was never applied. No amount of reading the
output afterwards can catch it.

Once it answers, both providers can run against each other in one window:

    python scripts/benchmark.py --providers nodemaven,yourprovider --engines patchright --targets bing_serp --queries 40 --batch 1

## When something goes wrong

| What you see | What it is |
|---|---|
| `python: command not found` | Python is not installed, or not on PATH. On Windows, reinstall and tick "Add python.exe to PATH" |
| `running scripts is disabled on this system` | Windows script policy. See the box in step 3 |
| `No module named nmbench` | You are not in the repository folder. `cd` into it |
| `No module named pytest` / `ruff` | The virtual environment is not active. Re-run the activate line from step 3 |
| `make: command not found` | Normal on Windows. Use `python -m pytest` and `python -m ruff check .` |
| `<engine> is not installed` in `--dry-run` | That engine's browser was never downloaded. The line tells you the exact install command. The rest of the matrix still runs |
| `NODEMAVEN_LOGIN and NODEMAVEN_PASSWORD not set` | No `.env`, or it is empty. Step 9. Or add `--direct` and skip the gateway entirely |
| `no gateway address for <provider>` | The definition has no host and none is in `.env`. A file copied from `_template.toml` starts empty on purpose |
| Everything is `error` and nothing is a verdict | Nothing reached the network. Check your own connection first; `python -m nmbench connect-integrity` says whether something on your line is interfering |
| A run stopped early, on its own | Either the circuit breaker (a cell failed repeatedly and was stopped deliberately) or the transport watchdog (nearly everything was failing at once). Both are printed with a reason |

Nothing here is destructive. `data/runs/` is only ever appended to, and no
command in the repository deletes a measurement.

## Where to go next

- [The README](../README.md) - the same material for a technical reader, plus
  what every column means and why it means that.
- `python -m nmbench` - lists every command, what each one answers, and which of
  them spend traffic. The ones marked `[offline]` send nothing at all.
- `NOTEBOOK.md` - the working notebook. Every claim with the run it came from,
  including the ones that did not survive being measured a second time.
- `CONTRIBUTING.md` - if you want to change something. The most useful thing you
  can bring is evidence that a number here is wrong.
