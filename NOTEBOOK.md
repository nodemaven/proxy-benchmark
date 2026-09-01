# NOTEBOOK.md

Working notes for the NodeMaven benchmark harness. Read this before writing any code
in this repository.

## Contents

This file is long because it keeps the claims that did not survive replication
next to the ones that did. Several sections are corrections of an earlier section
in the same file, so **where two of them disagree the later date wins**, and the
earlier one stays to show what the mistake looked like from the inside. If you
are about to write code, the last four sections are the ones that constrain you.

Orientation:

- [What this is](#what-this-is) · [Repository layout](#repository-layout) ·
  [Setup](#setup)
- [Three layers of anti-bot detection](#three-layers-of-anti-bot-detection) - the
  model every experiment here is designed against

The transport, in the order it was understood, each section correcting the one
above it:

- [Measured gateway behaviour](#measured-gateway-behaviour) - seven malformed
  inputs, seven replies, none of which names the cause
- [This line intercepts CONNECT](#this-line-intercepts-connect-and-the-direct-arm-never-controlled-for-it) -
  the diagnosis, and the hole it found in our own control
- [It was our own VPN client](#the-connect-interception-was-our-own-vpn-client-and-it-is-now-bypassed) -
  the attribution was wrong, the measurements held
- [The second network arrived](#the-second-network-arrived-and-the-connect-floor-is-the-gateways) -
  25% against 21.8% on two unrelated lines, so the floor is the gateway's - and
  the conclusion drawn from that does not survive the next section
- [The floor was an IP ban](#the-floor-was-an-ip-ban-and-this-harness-was-tripping-it-itself) -
  the gateway's, and triggered by the one unauthenticated CONNECT our own
  browsers send per session. **Read this before quoting any transport figure**

Findings about the pool:

- [Exit yield is a country axis](#exit-yield-is-a-country-axis-and-us-is-the-worst-of-them) -
  and `us = 0%` is the correction this whole file is written in the shadow of
- [The entry shape is a new axis](#the-entry-shape-is-a-new-axis-and-it-is-what-probe-and-hold-changes) -
  probe, hold, and the three traps that had to be fixed to ask it

Findings about the engines:

- [Measured engine behaviour](#measured-engine-behaviour) - 21 markers on
  `about:blank`, the cheapest re-check in the repository
- [Two engines were added for the geo axis](#two-engines-were-added-for-the-geo-axis-and-screening-them-inverted-one-premise) -
  one of them came back the opposite of its sales pitch
- [What the first eight-engine run exposed](#what-the-first-eight-engine-run-on-the-server-exposed) -
  four ways a row could have been written wrong
- [The viewport is not what Google reads](#the-viewport-is-not-what-google-reads-and-it-was-the-obvious-rival-explanation) ·
  [Obscura's two exclusions](#obscuras-two-exclusions-re-checked-2026-08-14-and-only-one-survives) ·
  [Verifying the Obscura build](#the-obscura-build-is-verified-by-its-handshake-never-by-the-flag)

Findings about the targets:

- [A refused address diverts the request](#a-refused-address-diverts-the-request-and-reading-the-status-alone-got-this-wrong) -
  reading the status alone got this wrong for eight days
- [Which engines have ever passed Google](#which-engines-have-ever-passed-google-and-the-denominator-that-shows-it) ·
  [Amazon answers four different ways](#amazon-answers-four-different-ways-and-two-of-them-are-2-kb) ·
  [Walmart and PerimeterX](#walmart-is-fronted-by-perimeterx-and-the-lock-is-on-the-address)
- **[The eight-engine matrix landed and the Amazon table did not survive it](#amazon-answers-four-different-ways-and-two-of-them-are-2-kb)** -
  7627 attempts, the unmodified control best at 96% and patchright worst at 63%,
  and Google collapsed to 1%. **Read it before quoting any Amazon or Google
  figure above**
- [The handshake was read](#the-handshake-was-read-and-it-is-not-the-discriminator) -
  and it explains neither target
- [DuckDuckGo is reading the User-Agent](#duckduckgo-is-reading-the-user-agent-and-the-split-is-total) -
  95 of 95 against 0 of 50, on one substring

Cost:

- [Chrome pays its vendor 43 MB per profile](#chrome-pays-its-vendor-43-mb-per-profile-and-the-pool-was-billed-for-it) -
  the largest single line of the traffic bill, for a request no target sees
- [What a run costs](#what-a-run-costs-and-why-one-constant-could-not-track-it) -
  and why one constant could not track it

The rules, which is what to read if you are about to write code:

- [Measurement rules](#measurement-rules) - verdicts from content, `error` is
  ours, a session is the unit
- [The axes](#the-axes-and-the-capabilities-that-are-refused-rather-than-dropped) -
  a capability only some engines have is refused, never dropped quietly
- [Operational safety](#operational-safety) - the breaker, the watchdog, and what
  a retry costs somebody else on this account
- [Code conventions](#code-conventions) · [Tests](#tests) · [Style](#style)

## What this is

A measurement harness for proxy providers, browser engines and scraping targets.
It answers one question with numbers: **at which layer does a target detect you,
and what does it cost to get through.**

Design constraint that overrides convenience: the harness must work against **any**
provider, not just NodeMaven. Adding a competitor means adding a config entry, never
editing the runner. This repository will be published as open source.

## Repository layout

    nmbench/            reusable package - this is what gets published
      config.py         credentials from .env, single read point, lazy
      providers.py      loads data/providers/*.toml, one gateway dialect each
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
      artifacts.py      gzipped response bodies, kept per verdict, for re-reading
      __main__.py       `python -m nmbench <command>`: finds scripts, runs one
      engines/          one module per framework, one shared contract
        base.py         ROW_FIELDS, blank_row, record_judgement, validate_preset
        http.py         plain requests client, the no-browser control
        chromium.py     unmodified Chromium (the control) and Patchright
        camoufox.py     patched Firefox over Playwright
        obscura.py      Rust browser with its own renderer, over CDP
        cloak.py        patched Chromium handing back a Playwright browser
        rebrowser.py    a Playwright fork patching the Runtime.enable leak
        seleniumbase.py Chrome over ChromeDriver, the WebDriver family
        zendriver.py    Chrome over raw CDP, no WebDriver and no Playwright
        botasaurus.py   Chrome over raw CDP, a second one, for the 2x2
        curlcffi.py     scriptless client wearing Chrome's ClientHello
    scripts/
      benchmark.py      the matrix runner
      probes/           one file per question, each cheap and single-purpose
      analysis/         aggregation over data/runs/, sends nothing
      tools/            generators for committed inputs
    data/providers/     one .toml per gateway: its username DSL and provenance
    data/queries/       committed inputs, generated once and checked in
    data/runs/          raw results, JSONL, never edited by hand
    tests/              offline suite, no network, no credentials
    Makefile            every target offline: install, test, lint, check, plan
    pyproject.toml      ruff and pytest config only, no build system

Rules:
- if code is needed by a second script, it moves into `nmbench/`
- a script that sends requests lives in `scripts/probes/`; one that only reads
  `data/runs/` lives in `scripts/analysis/`. The split is so a reader can tell at
  a glance which files can spend money. A probe that happens to send nothing
  declares `SENDS_REQUESTS = False` so the command list does not overstate it
- `python -m nmbench` discovers commands from disk and hands the remaining
  arguments to the script unchanged. It must stay a dispatcher: every script
  keeps its own argparse and still runs by path. One argparse owning the flags of
  eight unrelated experiments would put a shared code path between the runner and
  the probes that measure it, which is the one dependency this repository cannot
  have

## Setup

    python -m venv .venv
    .venv\Scripts\Activate.ps1          # Linux: . .venv/bin/activate
    pip install -r requirements-dev.txt
    python -m playwright install chromium
    python -m patchright install chromium
    python -m rebrowser_playwright install chromium
    python -c "import cloakbrowser; cloakbrowser.ensure_binary()"
    camoufox fetch
    copy .env.example .env              # Linux: cp .env.example .env

That cloakbrowser line read `cloakbrowser.download()` until 2026-08-18 and had
never worked: `download` is a module, so the call raises `TypeError: 'module'
object is not callable`. Nothing caught it because the machine the instruction
was written on already had the binary, and the engine reports available off the
binary rather than off the setup step. The clone is what ran it, the same way
the clone is what found the three missing requirements.

Four Chromium-family engines download a browser and **no two of them share a
download**: Playwright, Patchright and rebrowser each pin a different build, and
cloakbrowser ships its own patched one. That is the point of the pins in
`requirements-dev.txt` rather than an inconvenience, because the build is what
several results in this file are about, but it means a fresh machine fetches four
Chromiums before the first row. `zendriver`, `seleniumbase` and `botasaurus`
download nothing and drive **the host's installed Chrome**, so a box without
Chrome loses three engines from any matrix.

**Those three are the only engines here whose build is not ours to hold**, and
the move to the server changed it: the workstation runs Chrome 149 and the
server runs Chrome 151. Every pin in `requirements-dev.txt` exists to keep a
build constant across runs, and for `zendriver`, `seleniumbase` and `botasaurus`
there is no pin available - they take whatever the host has. So their columns
are comparable within one machine and carry a build change between the two, and
`engine_fingerprint.py` is what says how much of a change rather than leaving it
assumed.

`python scripts/benchmark.py --dry-run` prints which engines can actually run
here and why the rest cannot, before a matrix starts, so a missing binary costs a
message instead of half a run. Run it first on a new machine. The requirements
file declared neither patchright, rebrowser nor botasaurus until 2026-08-18 -
the machine that wrote every row on disk already had all three, so nothing failed
until the tree was cloned somewhere else, and what caught it was a clone rather
than a test.

**Headful on a headless host needs a display, and that is not a formality here.**
`--headful` is the difference between `Chrome/...` and `HeadlessChrome/...` on the
wire, which is the whole of the DuckDuckGo finding, so a server run wraps the
command in `xvfb-run -a`. What a virtual display does not restore is the GPU:
with no card the WebGL renderer falls back to a software rasteriser, and
"SwiftShader instead of GPU" is one of the three markers in the layer model
below. A headful run on a server is therefore not the same client as a headful
run on a workstation, and `engine_fingerprint.py` - which sends nothing and needs
no credentials - is what says by how much instead of leaving it assumed.

Obscura is not on PyPI. Download the **`-stealth`** archive for the platform from
the project's releases, unpack it, and put the directory on PATH - the plain
archive is a different build and the stealth patches are the thing under test.
`obscura --version` must answer before the engine will report available.

`.env` holds NODEMAVEN_LOGIN, NODEMAVEN_PASSWORD, NODEMAVEN_HOST, NODEMAVEN_PORT.
The prefix is the provider's id, so a second definition reads its own four
variables and both accounts sit in one `.env`. It is gitignored and must stay
that way. `config.py` resolves values on first use rather than at import, so the
verdict logic, the scheduler and the query lists can be imported and tested on a
machine with no account at all.

Before spending traffic: `make check` (ruff plus the test suite, all offline),
then `python scripts/benchmark.py --dry-run` for the cost.

## Domain knowledge

### Three layers of anti-bot detection

Every target checks a different layer of the stack. Debugging fails when you
inspect a layer above the one that is actually rejecting you.

| Layer | What gives you away | Fix |
|---|---|---|
| IP reputation | datacenter range -> 403, mobile -> captcha, residential -> pass | residential proxy |
| Browser signals | headless markers: navigator.webdriver, empty plugins, SwiftShader instead of GPU, CDP artifacts | headful browser under a virtual display |
| TLS handshake | ClientHello shape: cipher count, extension count, absence of GREASE | explicit curve preferences / a browser-shaped client |

Patching JS properties does not work: detectors inspect the property descriptor,
the prototype chain, a fresh iframe and a Web Worker, all of which return the
unpatched value.

This is why the harness always varies one layer at a time. A single engine
measuring a single target tells you it failed, not where.

### Measured gateway behaviour

Confirmed 2026-08-10 by raw CONNECT probes, raw data in `data/runs/`.
Do not assume any of this is documented - it is not. Re-verify before publishing.

Sticky sessions:
- the session key is the **whole recognised parameter set**, not `sid` alone
- `country=us, sid=A` and `country=us, sid=A, filter=medium` are two different sessions
- `ttl` does not participate in the key, `filter` does
- consequence: adding or removing any parameter silently moves you to another IP

**The gateway cuts a value at the separator, measured 2026-08-20.** `separator`
and `pair_separator` are both `-` in this dialect, so `sid-order-4417` is
ambiguous by construction: `sid=order-4417`, or `sid=order` followed by a
parameter named `4417`. `probes/sid_separator.py` settles it in twelve CONNECTs
and no target traffic - `sid_separator_20260820T202737Z.jsonl`, `country=de`,
three arms interleaved, four rounds each. `order8e3bf9-4417` and `order8e3bf9`
drew **one exit address**, 4 of 4 each; `order8e3bf94417` drew a different one,
4 of 4. The session key is the whole recognised parameter set, so one exit for
two usernames means one parsed key: the tail was cut off. A caller using a
hyphenated order id would share one exit with every order beginning `order`, and
the connection would succeed the whole time.

**The obvious version of that probe measures nothing, and it was written first.**
Comparing `sid-order-4417` against `sid-order4417` gives two distinct session
keys under *either* reading, so the gateway holds two exits whichever is true
and the experiment is empty. The discriminating arm is `sid-order` alone, which
is byte-identical to the cut form of the hyphen arm. The third arm decides
nothing and stays anyway: two arms agreeing proves nothing unless a third is seen
to disagree in the same window, or "every arm gave one exit" reads as a result
when it is a pool that stopped rotating. `nmbench/proxy.py` does not refuse a
separator inside a value, which is what let the probe send the ambiguous username
at all; the published SDK does refuse it, and this run is why that refusal is no
longer inference.

Error handling, seven inputs and seven different reactions, none of which names
the cause:

| Input | Gateway reply |
|---|---|
| bad country | 406 Not Acceptable |
| bad region | 406 Not Acceptable |
| bad city | 500 Internal Server Error |
| bad filter value | 407 Proxy Authentication Required |
| bad ttl value | 407 Proxy Authentication Required |
| empty value | no reply, connection hangs ~20s |
| unknown parameter name | 200, parameter silently ignored |

The 407 cases are actively misleading: they send the user to check credentials.
The unknown-parameter case is worse: the connection succeeds with settings that
were never applied.

Other observations:
- `X-Proxy-Exit-IP` is returned on the CONNECT reply, so exit IP costs nothing
  to obtain - but it is **not guaranteed**, one response arrived without it and
  with a differently cased reason phrase, suggesting more than one implementation
  behind the gateway
- with no `country` parameter the default country is not stable

### The CONNECT interception was our own VPN client, and it is now bypassed

**Resolved 2026-08-13, later the same day.** The interception described below
was real and every measurement of it holds. The attribution did not: it was not
the ISP and not a middlebox on the line, it was **a VPN client on the
workstation itself** - Happ, running `sing-box` and `xray`, with a `happ-tun`
adapter holding the `0.0.0.0/0` default route. Every packet was leaving through
a Cloudflare-fronted remote, which is exactly why three unrelated hosts answered
CONNECT in one voice and signed it `CF-RAY: -`.

Fixed without stopping Happ, by routing the gateway addresses around the tunnel:

    route add <gateway ip> mask 255.255.255.255 <lan gateway> metric 1 if <lan if>

Deliberately without `-p`, so it rolls back on reboot instead of becoming
permanent state nobody remembers adding. After it, all three DNS nodes answer an
unauthenticated CONNECT with **407 Proxy Authentication Required** - which is
what a working proxy does, and what the interception was suppressing.

**Re-read 2026-08-18, and the state of this machine is not what anyone here
believed.** Happ, `sing-box` and `xray` are all running, and `happ-tun` still
holds the `0.0.0.0/0` default at metric 0. What makes the gateway reachable is
the workaround above, still in force: three `/32` routes, one per
`gate.nodemaven.com` address, via the LAN gateway at metric 1, and
`Find-NetRoute` confirms each of them wins. So the transport was never fixed by
turning the VPN off - it has been fixed by three route entries the whole time,
and the belief that the client had been stopped would have been discovered by a
run rather than by a check.

Three things follow, and the first is the one with a deadline on it.

- **Those routes are not in the persistent store.** `Get-NetRoute -PolicyStore
  PersistentStore` holds one entry and it belongs to an unrelated adapter. That
  was the right call when it was made and it is a live hazard now: a reboot
  silently restores full interception, every CONNECT starts being answered by
  the tunnel again, and a matrix inherits it as errors spread evenly across
  every engine. Before any long run, re-add them and re-check, in that order.
- **The route table cannot tell you whether traffic is tunnelled, so it must not
  be read as if it could.** `Find-NetRoute` sends `ipinfo.io` to `happ-tun`, and
  ipinfo.io nonetheless reports the operator's own ISP - the same address and the
  same ASN as when the request is bound to the LAN adapter instead. So a request
  routed *into* the tunnel came back out of the local line, and the two readings
  are indistinguishable. The packets reach `sing-box` and `sing-box` decides, by
  its own policy, which of them to send through a remote; that decision is
  invisible from outside. The `/32` routes matter precisely because they take the
  gateway addresses out of that decision altogether, and they are the only
  guarantee available.
- **The cheap re-check below needs its discriminator restated.** It said `gws`
  means clean. Measured today, an unauthenticated CONNECT to `www.google.com:80`
  answers **405 Method Not Allowed with no `Server` header at all**, and
  `example.com:80` answers 400 `Server: cloudflare` - which is example.com's own
  edge, since it answers that way on an ordinary GET too. Read literally the old
  rule reports neither clean nor dirty. The signature of the interception was
  never the string `gws`: it was **three unrelated hosts answering in one voice**,
  all 400, all `Server: cloudflare`, all `CF-RAY: -`. Different answers from
  different hosts is the clean reading, whatever those answers are.

Two consequences larger than the fix itself.

- **Every proxied row in `data/runs/` was taken through the VPN tunnel.** Happ
  has been up since 2026-08-10, which covers the country-yield runs, the engine
  matrices and the CONNECT-failure floor. Those rows are not void - the traffic
  did reach the gateway and the exits are real residential addresses - but they
  were measured over an extra encrypted hop to a foreign endpoint. Verdicts and
  exit addresses survive that; `elapsed_ms` does not, and is not comparable
  against anything measured after the bypass.
- **The 15-20% floor of unanswered CONNECTs has still never been measured on a
  clean path.** Now it can be. Hold the support ticket until the floor is
  reproduced with the bypass in place. The reasoning in the next section is
  unchanged and was right to withhold it; only the name of the box has changed,
  and it turns out to have been ours. *(The floor was resolved on 2026-08-20 and
  the ticket was closed rather than sent - see "The floor was an IP ban". The
  hold was right and the reason given for it was not the reason it mattered.)*

  **First reading with the bypass verified rather than assumed, 2026-08-18:
  `gateway-health` opened 8 of 10 tunnels**, two `TimeoutError`, `country=us`,
  median `tcp_ms` 28.6 and `connect_ms` 458.1, six distinct /24s across the six
  exits that were named. 20% of ten is one run of ten and settles nothing on its
  own - it is consistent with the floor and could not distinguish it from an
  ordinary afternoon - but it is the first such figure taken where the route
  table was checked instead of the VPN client being presumed off. Three of the
  ten replies came back `200 OK` with no exit address, against six
  `200 Connection established` with one, which is the second implementation
  behind the gateway showing up again at about the rate this file already
  records.

The original diagnosis follows, because the method is worth keeping and the
controls in it are what made the cause findable at all.

### This line intercepts CONNECT, and the direct arm never controlled for it

Measured 2026-08-13. Every proxied attempt from this machine now fails, and the
cause is not the gateway.

A CONNECT to `gate.nodemaven.com:8080` returns `400 Bad Request` with
`Server: cloudflare`. It returns it with valid credentials, with a garbage
`Proxy-Authorization`, and **with no authorization header at all** - a working
proxy answers an unauthenticated CONNECT with 407, so the refusal happens before
anything reads the credential. All three DNS nodes behave identically, and none
of their addresses is in a Cloudflare range.

The controls are what settle it. Sending the same CONNECT to servers that are
not proxies at all:

| target | CONNECT | ordinary GET, same host and port |
|---|---|---|
| `example.com:80` | 400, `Server: cloudflare` | 200, `Server: cloudflare` |
| `www.google.com:80` | 400, **`Server: cloudflare`** | 200, **`Server: gws`** |
| `1.1.1.1:80` | 400, `Server: cloudflare` | 301, `Server: cloudflare` |

Google does not answer as `cloudflare`. On GET each host answers in its own
voice, so the line is healthy and DNS is honest; on CONNECT all three answer in
one voice. Every reply also carries `CF-RAY: -`, and a real Cloudflare edge
always populates CF-RAY. Something between this machine and the internet is
answering CONNECT itself and signing it with a forged identity.

Two consequences, and the second is the expensive one.

- **No proxied run can be made from this line until it clears.** Direct arms
  still work, so engine comparisons that hold the address constant are still
  available, and that is most of the engine screening. Anything about exits,
  countries, sticky sessions or yield is unavailable.
- **The 15-20% floor of unanswered CONNECTs may not be the gateway's.** This
  file argued that it was, on the grounds that the direct arm showed 0 errors in
  454 attempts against 360 in 1654 through the pool, and called the direct arm
  "the control that does" rule out our own line. That reasoning has a hole in
  it: **the direct arm never issues a CONNECT.** It controls for a flaky link
  and it does not control for a middlebox that treats CONNECT differently from
  everything else - which is exactly what is now demonstrated to exist here. A
  box that dropped a fraction of CONNECTs would produce the whole observed
  pattern: no direct errors, a fifth of proxied attempts failing, spread evenly
  across every country and parameter because it never looks at the username.

  This does not show the floor was ours. It shows the evidence cannot tell.
  `ERR_EMPTY_RESPONSE` at 107 and `ERR_TUNNEL_CONNECTION_FAILED` at 32 are
  equally consistent with both stories. **The support ticket has to be held**
  until the floor is reproduced from a line that does not intercept CONNECT,
  because as it stands it is an accusation the data does not support. The
  correct control is a second network, not a second arm.

  *Resolved 2026-08-20, and this paragraph is the closest anything here came.
  "The direct arm never issues a CONNECT" was the right observation, and the
  cause did turn out to live in a CONNECT the direct arm cannot emit - just not
  a middlebox eating them. It was the gateway banning the address over the one
  **unauthenticated** CONNECT our own browsers open per session. A second
  network was the wrong control for that, because both networks ran the same
  browsers. See "The floor was an IP ban".*

The cheap re-check, and it needs no credentials: send an unauthenticated CONNECT
to two or three unrelated hosts and compare the replies to each other. One voice
across all of them - the same status, the same `Server`, `CF-RAY: -` - is the
interception. Different answers from different hosts is a clean line, and the
answers themselves are not the test: `www.google.com:80` returned `Server: gws`
in 2026-08-13 and returns 405 with no `Server` header at all in 2026-08-18, both
on a clean path.

### The second network arrived, and the CONNECT floor is the gateway's

The control this file kept asking for is the VPS. It is a different machine on a
different line in a different datacentre, with no VPN client on it and nothing
in front of CONNECT, and `connect_integrity.py` says so rather than the operator
saying so.

Measured 2026-08-18 from the server: **25% of CONNECTs were accepted and never
answered**, against 21.8% pooled over the whole history of the workstation. Two
lines with nothing in common except the gateway produce the same floor, and the
difference between them is not significant. The competing story - a middlebox on
the operator's line that treats CONNECT differently from everything else - cannot
survive that, because the second line has no such box and shows the same
fraction.

**So the support ticket is supported and may be sent.** It has been held since
2026-08-13 on the correct ground that the evidence could not distinguish the
gateway from our own transport. It can now: the reproduction is one line, the
control is another, and the numbers are quoted with the Wilson interval like
everything else here.

**That paragraph is wrong and the ticket was never sent. The next section says
why.** It is left standing because the control it rests on was sound and the
conclusion still did not follow, which is the more useful thing to have written
down than the conclusion would have been.

The same measurement re-confirms the multiple implementations, and gives the
discriminator a name. A 200 that carries `X-Proxy-Exit-IP` arrives as
`Connection established`; the ones that arrive as `OK` or as
`Connection Established` do not carry it. The reason phrase is therefore a free
label for which implementation answered, available on every CONNECT the relay
already parses, and any per-implementation figure has to be split on it rather
than pooled - a rate averaged over three back ends is a rate belonging to none of
them.

### The floor was an IP ban, and this harness was tripping it itself

**Resolved 2026-08-20, and the heading above is right only in the narrowest
sense.** The floor was the gateway's. It was not a defect, it was a security
control reacting to correct browser behaviour coming from our own address, and
the ticket was never sent.

The gateway runs an IP banner: it counts unauthenticated and malformed requests
per address and bans the address on reaching a threshold. HTTP proxy
authentication is challenge-response, so a browser handed credentials opens the
**first CONNECT of a session without one**, takes the 407, and retries with the
credential. At `--batch 1` the profile is fresh on every attempt, so this
harness emitted one unauthenticated CONNECT per attempt for hours at a stretch
and banned its own address. A fix deployed on the EU cluster at 21:00 UTC on
2026-08-19 resets the counter whenever any request from that address
authenticates.

The regime change is visible from outside and is measured **inside one
uninterrupted run**, `benchmark_20260819T055927Z.jsonl`, rather than across two
runs an hour apart - one process, one `run_id`, eight engines, read at 2135 rows:

| | attempts | all errors | `ERR_EMPTY_RESPONSE` |
|---|---|---|---|
| before 21:00 UTC 19.08 | 1131 | 298, 26.3% | **207, 18.3%** |
| after | 1004 | 94, 9.4% | **1, 0.10%** |

Three controls, and the third is the one that matters. The same hour on
consecutive days gives 26 of 73 against 0 of 62 at 06:00 UTC and 20 of 66
against 0 of 50 at 07:00 UTC, so it is not the time of day. The process never
restarted. And **only one error class moved**: `empty_response` went 207 to 1
while navigation timeouts went 72 to 79 and tunnel failures 13 to 8 - so this is
not the run getting generally healthier, it is one specific failure
disappearing, and a 9.4% error rate remains that is now mostly navigation
timeouts and is a different problem.

**The split that names the mechanism was in every run on disk and nobody asked
for it.** Engines divide by who opens the tunnel: `zendriver`, `seleniumbase`
and `botasaurus` go through `nmbench/relay.py`, which puts
`Proxy-Authorization` on the first CONNECT it sends upstream and therefore never
emits an unauthenticated one; the rest are handed credentials and open their own.

| | before | after |
|---|---|---|
| through the relay | 10/421, 2.4% | 10/375, 2.7% |
| browser opens its own CONNECT | 288/710, 40.6% | 84/628, 13.4% |
| the same, excluding the `chromium` control | 185/568, 32.6% | 17/502, 3.4% |

The control is excluded from the third row because it fails Google 125 of 125
in this run at exactly 60 s, identically on both sides of the cut, while passing
Amazon in the same hours - an engine-target property that has nothing to do with
transport and would otherwise sit in the denominator on one side only.

**The browser half of that was measured rather than reasoned about, and it cost
no traffic.** `scripts/probes/proxy_auth_shape.py` runs a proxy on loopback that
challenges exactly like the gateway and then joins the tunnel straight to the
target, so it measures the client and touches no pool. Chromium under Playwright,
Chromium under Patchright with a persistent profile, and Camoufox all behave
identically: one CONNECT with no credential, the 407, the retry, and from then
on the credential is on every tunnel immediately. **It is one unauthenticated
CONNECT per browser session, not per tunnel** - the credential is cached for the
life of the session - so the volume is one per attempt at `--batch 1` and not
tens per page. That is small, and it lands where it is expensive: it is the
CONNECT that opens the navigation, so losing it kills the whole attempt rather
than degrading it.

**Why an always-authenticated probe measured a floor at all.** `gateway_health`
authenticates on its first CONNECT and cannot trip the counter. It shared an
address with the matrix, and the ban is on the address, so the 47 of 140 in the
section above is collateral rather than a reading of the gateway's health. Same
probe, same VPS, same 40+40 shape, re-run 2026-08-20: **0 of 80.**

Two lessons, and the first generalises past this incident.

- **A control that varies the environment cannot see a cause that lives in your
  own traffic.** The second network was the right control for the hypothesis on
  the table - a middlebox on the operator's line - and it killed that hypothesis
  correctly. What it held constant without anyone noticing was the harness: both
  machines ran the same engines emitting the same unauthenticated CONNECT into
  the same counter. Two lines agreeing rules out a per-line cause and says
  nothing about a per-behaviour one. Before quoting a control, say what it
  varied and what it silently held fixed.
- **The discriminating split had already appeared once and was correctly
  dismissed.** The probe-and-hold section records the floor "lopsided - 8 on
  patchright against 2 on zendriver. Too few to read as an engine property."
  That is patchright opening its own CONNECTs against zendriver behind the relay,
  it points the right way, and 10 events genuinely could not carry it. The
  judgement was right and the finding was still sitting there. When an
  underpowered asymmetry turns up on an axis nobody is varying deliberately, it
  is worth a cheap probe rather than a note.

**What this does to the rows already on disk.** Every proxied attempt made
before 21:00 UTC on 2026-08-19, from either machine, was made from an address
subject to this. Verdicts and exit addresses survive it exactly as they survived
the VPN correction - a request that completed, completed - but the error rate of
that period is not a property of the gateway's health, and pooling across the
boundary mixes two transports. Worse than that, the contamination is **uneven
across engines**: a third of the browser-CONNECT engines' attempts died before
any page existed while the relayed engines lost 2%, so any figure using attempts
as its denominator is biased between columns, in a direction that flatters the
relayed engines. Rates quoted as ok-given-served are largely protected, because
`error` is excluded from them by construction, and that convention is what keeps
most of this file readable across the boundary.

**One asymmetry is unexplained and is deliberately left open.** The gateway team
states the ban is IP-level. If it were blanket the relayed engines should have
died beside the others - same address, same minutes - and `relay.py` does not
retry, so it cannot be absorbing failures as latency. They did not: 10 in 421
against 185 in 568. Either the counter or the enforcement is scoped narrower
than the address, or the control behaves more selectively than intended. Agreed
with the gateway team to accumulate data rather than argue it; the running
matrix produces both client shapes from one address, so it is the instrument for
this without any new work.

Deploy geography, from the gateway team on 2026-08-20, because it decides
whether a future run crosses another boundary: **RU traffic routes to the EU
cluster**, which was fixed on 2026-08-19, so this run is on one regime from
21:00 UTC onward. US completed about 10:15 UTC on 2026-08-20 and SG is pending,
and neither touches a run made from this VPS.

### Exit yield is a country axis, and US is the worst of them

Measured 2026-08-12 by two runs of the same experiment four and a half hours
apart, `benchmark_20260812T142303Z` and `...T185158Z`: patchright, google_serp,
`--batch 1` so every attempt draws a fresh sid and therefore a fresh exit,
`filter=medium`, headful. 225 attempts, and the 197 that recorded an exit drew
189 distinct /24s - re-counted 2026-08-18, where this file said "every exit was
a distinct /24". Eight collisions out of 197 change nothing in the table and the
sentence was still a claim nobody had counted.

Read under `was_served`, so a `/sorry/` page counts as a refused address whether
it arrived as a 429 or as a 200. `completed` excludes the attempts the gateway
never answered, because a dead CONNECT says nothing about the quality of the
address behind it.

| country | completed | served | diverted | P(served) | 95% |
|---|---|---|---|---|---|
| us | 38 | 5 | 33 | **13%** | 6-27% |
| de | 36 | 16 | 20 | 44% | 30-60% |
| gb | 42 | 19 | 23 | 45% | 31-60% |
| any | 38 | 22 | 16 | 58% | 42-72% |
| ru | 34 | 21 | 13 | **62%** | 45-76% |

P(pass given a served page) was **83 of 83** and did not vary by country. So the
decomposition `success = P(good exit) x P(engine passes | good exit)` does not
merely separate cleanly here - the second term is 1.0, and every bit of the
country variation sits in the first. For Google the engine is not the bottleneck
at all; the address is the whole of it. An earlier probe agrees: 86% of US exits
were already dead.

Three consequences for how runs are designed:

- **`us` is the finding, and it is the only country comparison that is one.**
  Pooling the four other settings against it in the second run gives 5/26
  against 53/102, Fisher exact p = 0.0036. Nothing else in the table separates:
  44, 45, 58 and 62 sit on intervals that overlap almost entirely, so the
  ranking among the good settings must not be quoted. The actionable sentence is
  "do not pin `us`", never "pin `ru`".
- **`country=any` is a high-yield setting and the least reproducible one.**
  It moved 69% -> 52% between the two windows while `de`, `gb` and `ru` barely
  moved (36->48, 38->48, 64->61). The default country with no parameter is also
  not stable, so a rate measured on `any` cannot be re-derived later. Use it,
  but never as the only cell: `us` and `any` in one window is what turns the gap
  into the finding instead of into a flattering number.
- **A 15-20% floor of unanswered CONNECTs is the gateway's, not ours.** 37 of
  225 attempts got no reply, spread across every country including `any`, which
  rules out the username DSL and any one parameter.

  Spread across countries does not on its own rule out our own line, because a
  flaky local link would fail every country equally too. The direct arm is the
  control that does, and it was already on disk: **0 errors in 454 direct
  attempts against 360 in 1654 through the pool**, 21.8%, over every run here.
  Not one attempt on this machine's own line failed to connect. The failures are
  proxy-layer by name as well as by arm - `ERR_EMPTY_RESPONSE` 107,
  `ERR_TUNNEL_CONNECTION_FAILED` 32, `NS_ERROR_PROXY_*`, plus 83 navigation
  timeouts. This is the reproducible case to send to provider support, and the
  direct arm is what makes it an accusation rather than a complaint.

  *The floor was the gateway's and it was ours as well, which is the one
  combination this file kept treating as two options - see "The floor was an IP
  ban". Nothing was sent to support. The country table above is unaffected:
  every rate in it is read under `completed`, which drops the unanswered
  CONNECTs before the arithmetic starts.*

The single most important caveat is what replication did to this table. The
first window read `us = 0%` and this file said so, hedged as "no US exit got
past Google in this window, not zero". The hedge was right and the number was
the tail of one hour: the second window put `us` at 19%, and pooled it is 13%.
The direction survived replication, the magnitude did not. Any country figure
here is a reading of the hours it was taken in, and a single window is worth
about as much as `us = 0%` turned out to be.

### Measured engine behaviour

Read 2026-08-11 by `scripts/probes/engine_fingerprint.py`, which evaluates 21
markers on `about:blank`. It sends nothing and needs no credentials, so it is the
cheapest way to re-check any of this. Headless, bundled builds:

| Marker | chromium | patchright | camoufox | obscura |
|---|---|---|---|---|
| `navigator.webdriver` | **true** | false | false | false |
| plugins / mimeTypes | 0 / 0 | 0 / 0 | 5 / 2 | 5 / 2 |
| WebGL renderer | ANGLE | ANGLE | ANGLE | **null** |
| `window.chrome` | undefined | undefined | undefined | object |
| user agent | HeadlessChrome/148 | HeadlessChrome/149 | Firefox/152 | Chrome/145 |

Three things follow, and all three belong in the report:

- **Every *Playwright-family* headless engine announces itself in the
  User-Agent, and the cause is the build, not the mode.** `HeadlessChrome`
  survives on the bundled build and on `--channel chrome`, and no amount of
  driver patching reaches it.

  This file said "only `headful` gives `Chrome/149`" until 2026-08-13, and that
  was wrong. Playwright and Patchright do not download Chromium for headless
  runs, they download a separate **Chromium Headless Shell**, and the shell is
  what carries the substring. `--channel chromium` does not fix it
  (`HeadlessChrome/148` and `/149`). Three engines screened the same day return
  a clean `Chrome/...` while headless - zendriver and seleniumbase's `headless2`
  on the host's Chrome 149, cloakbrowser on its own patched Chromium 146. So the
  marker tracks which binary was launched, and headful was only ever a way of
  not launching the shell.

  The operational consequence is unchanged and the price is not: an engine that
  sends `HeadlessChrome` is refused by one substring test, and DuckDuckGo does
  exactly that - but avoiding it no longer costs a display.
- **On `--channel chrome`, Patchright differs from the unmodified control in
  exactly one marker: `navigator.webdriver`.** Plugins, mime types, the real
  Intel GPU and `window.chrome` all come back on that channel - and they come
  back for the control too, because they are Chrome rather than the bundled
  Chromium build. Whatever else Patchright buys is not visible at the JavaScript
  layer this probe reads. An earlier note in this file credited those values to
  Patchright; that was measured without running the control on the same channel
  and was wrong.
- **Obscura has no WebGL context at all**, where every other engine returns a
  renderer string. Absence is a stronger signal than a wrong value: a browser
  with no WebGL is rarer in real traffic than one with a SwiftShader renderer.

- **Every engine reports the host locale and timezone**, `ru-RU` and
  `Europe/Moscow` on this machine, while the matrix requests `country=us` exits.
  A US address with a Moscow timezone is a one-line inconsistency check, so
  whether alignment is on has to be recorded on every row or the engine
  comparison is unattributable. See "Geo alignment is an axis, not a setting".

  Read across every `benchmark_*.jsonl` on 2026-08-13, the `geo` column is
  `off` on 1907 rows and absent on 84. **Alignment has never been on in any run
  in this repository.** That is worth stating rather than assuming, because it
  removes geo as an explanation for the Amazon gap for free: Camoufox at 84% and
  Patchright at 9% were both misaligned, so the difference cannot be the
  timezone. It also means every row on disk shares one baseline, and the first
  aligned run will be comparable against all of them.

One caveat that cost a wrong claim already: Obscura's CDP `/json/version` banner
reports `X11; Linux x86_64` while the page itself correctly reports Windows.
That banner is build metadata on a local socket, it is not what a target reads,
and it must not be quoted as a fingerprint defect.

**That last sentence was reasoned from where the string was found, and it is now
measured.** Reasoning from the location was never enough, because
`navigator.userAgent` and the CDP banner are both read through CDP and neither
can see what the browser put on the wire - and upstream carries an open report
that this build leaks the same `X11; Linux x86_64` in its *request header*. A
header and a JavaScript value disagreeing about the operating system is a
contradiction no real browser produces, available to the cheapest possible
detector on the first byte of the first request. If it were true here, every
Obscura row in `data/runs/` was measured on a client giving itself away for free
and none of its refusals could be read as its anti-detection failing.

`probes/obscura_defects.py` now asks it from both ends of one request: the header
as a server on this machine received it, against `navigator.userAgent` in the
page. Measured 2026-08-18 on obscura 0.2.0 under `--stealth`, the two are **byte
identical**, `Mozilla/5.0 (Windows NT 10.0; Win64; x64) ... Chrome/145.0.0.0` on
the wire and in the page. The report does not describe this build on the `serve`
path, the claim above survives, and it is now guarded rather than argued.

Two details of the check are worth keeping. It compares the operating system and
not the whole string, because a build may legitimately answer a different Chrome
version to the network than V8 reports, and a full-string test would fail on that
and read as this defect. And header names are case-insensitive while `dict()` over
a parsed message is not, so the first version filed a lower-cased `user-agent`
under a key nothing looked up and reported that the browser had sent no agent at
all - a check whose failure mode is an absent string can manufacture the defect it
exists to find.

### Two engines were added for the geo axis, and screening them inverted one premise

`rebrowser` and `botasaurus` were added on 2026-08-18 to answer the question the
geo run could not: alignment left patchright flat at 34% against 35% and cost
zendriver six sevenths of its yield, 57% against 9%, and the only thing separating
those two engines was **when the override is installed** - Playwright takes
`timezone_id` as a context option, before any target exists, and zendriver sends
`Emulation.setTimezoneOverride` to a tab already open on `about:blank`. One engine
on each side of that is an anecdote. Rebrowser is a second pre-context installer
and botasaurus a second post-tab one, so the 2x2 can separate "post-tab
installation costs yield" from "zendriver specifically costs yield". Neither of
those sentences is currently the one the data supports.

Both were screened by `engine_fingerprint.py` before their first run, headless,
against the unmodified control on the same machine minutes apart. The screening
is the point of this section: one of them came back the opposite of its sales
pitch.

| marker | botasaurus | zendriver | rebrowser | chromium |
|---|---|---|---|---|
| `navigator.webdriver` | false | false | **true** | true |
| plugins / mimeTypes | 5 / 2 | 5 / 2 | 0 / 0 | 0 / 0 |
| `window.chrome` | object | object | undefined | undefined |
| WebGL renderer | Intel | Intel | Google Vulkan | Google Vulkan |
| `binding_leaks` | none | none | **`__pwInitScripts,__playwright_builtins__`** | none |
| user agent | `Chrome/149` | `Chrome/149` | `HeadlessChrome/136` | `HeadlessChrome/148` |

- **Rebrowser is the only engine in the registry a `window` enumeration
  identifies outright, and it differs from the unmodified control in that one
  marker and no other.** Both leaked names are plain enumerable properties of
  `globalThis`, so three lines of JavaScript on any page name the driver.
  `navigator.webdriver` is true on the fork exactly as on the control - it does
  not touch it. The cause is the base version rather than the `Runtime.enable`
  patch the fork is sold on: `__pwInitScripts` is in this driver's
  `lib/server/page.js` and **nowhere in stock Playwright 1.60's `lib/` at all**,
  so upstream removed it somewhere between 1.52 and 1.60 and the fork is pinned
  behind that. It also ships Chromium 136 against Playwright's 148, and headless
  it announces `HeadlessChrome`, which puts it in the 0 of 50 half of the
  DuckDuckGo table. That is a reason to run it and not a reason to drop it - a
  popular fork sold as undetectable carrying a driver global the stock driver has
  already removed is what this repository is for - but a refusal in its column
  must not be read as the `Runtime.enable` patch failing.
- **Botasaurus and zendriver are the same fingerprint**, 20 of 21 markers
  identical, the exception being `inner` at 784x497 against 764x485, which is
  window chrome. That is what the 2x2 needed: the two post-tab installers are
  indistinguishable to anything reading this layer, so a divergence between them
  under alignment is attributable to the installation timing rather than to
  anything a page can see. Had they differed, the pair would have bought nothing.
- **A matrix holding rebrowser and not `chromium` cannot read it.** Its launch
  path is the control's - plain `launch()` plus `new_context()` - where Patchright
  opens a persistent context with `no_viewport=True`. Against the control the
  driver API, the launch call and the context are identical, so the fork is the
  only variable; against anything else it is one of two. The build is not held
  constant by that pairing and `--channel chrome` is what closes it.
- Both raw-CDP engines report `screen` 800x600 on a 1920x1080 machine while
  headless, which is Chrome's own default window. Recorded and not fixed:
  `screen_override.py` measured the metrics override at 10 of 10 both ways
  against Google, so correcting it would be an unmeasured change to one launch
  path buying a marker no target here has been shown to read.

**Zendriver had no fingerprint row at all until that run, and the cause was our
own shim.** Its `_Page.evaluate` unwrapped a Playwright arrow by text, dropping
everything up to the first `=>` and evaluating the remainder. That is correct for
`() => expr` and cannot work for `() => { ... }`: what reaches V8 is a block
statement, its `return` is illegal at the top level, and the engine answers
`SyntaxError: Illegal return statement`. Four probes here pass an
expression-bodied arrow and two pass a block-bodied one, so `geo_align_check.py`
and `screen_override.py` worked while `engine_fingerprint.py` and
`detect_page.py` failed outright - on the engine carrying this repository's
largest unattributed result. The botasaurus adapter, written months later,
already called the arrow instead of unwrapping it; zendriver now does the same,
and `TestTheZendriverShimEvaluatesWhatTheProbesActuallyPass` asserts both shims
together, because a difference between two adapters is a difference in what their
columns mean.

### What the first eight-engine run on the server exposed

The move to the VPS is documented above as a build change. It is also a load
change - 2 vCPU and 4 GB against a workstation - and a locale change, and the
first run that put all eight engines in one matrix found something in four of
them. None of these is a result about a target; all four are ways a row could
have been written wrong.

**zendriver declared the browser dead after 2.5 seconds, and its own error
message named the wrong cause.** `browser_connection_timeout` 0.25 s times
`browser_connection_max_tries` 10 is the whole of the patience it ships with,
and the exception it raises when that runs out blames running as root and
suggests `no_sandbox=True`. Measured 2026-08-18: the account is uid 1000, and
the engine launches fine standalone, fine with a relay, fine after each of the
four other Chromium engines in turn, and fine as the only cell of a matrix. It
failed both of its cells **only** in the full eight-engine run, where fourteen
browsers had already been launched on a 2-vCPU box and a 46 MB Amazon page was
in flight. That is a cold start exceeding 2.5 s, not a sandbox. The tries are
now 60, so the wait is 15 s; the launch itself is unchanged.

Acting on the message instead would have added `--no-sandbox` to the browser
under test to fix a timeout - a fingerprint change to the thing being measured,
bought for nothing. An engine's own diagnostics are written for its usual
audience and this repository is not it.

**SeleniumBase UC mode moves sixteen option branches, and the flag this file
named was not one of them.** The claim here until 2026-08-19 was that
`_set_chrome_options` adds `--disable-background-networking` on the ordinary
path and loses it under `uc=True`. Read against the installed source,
seleniumbase 4.51.12, that is false: line 2773 adds it unconditionally, outside
every branch - which is also what the 43 MB section of this file says two screens
below. Two sections of one notebook disagreed, and the wrong one was the one
about to be filed upstream.

The general form survives and now has a count. Between lines 2297 and 2864 there
are **sixteen separate `is_using_uc(...)` branches**, and they move the options
class itself (2353), `mobile_emulator` (2437), `user_data_dir` (2535),
`--disable-3d-apis` (2769), `--disable-renderer-backgrounding` (2771),
`IsolateOrigins` and `site-per-process` (2811), `--remote-debugging-pipe` with
webextensions and BiDi (2828), and `--no-pings` with `--homepage` (2833). So
**anything handed to this engine can be lost this way**, and a setting that
matters has to be verified on the browser's real command line rather than in the
call that asked for it. `/proc/<pid>/cmdline` is where that is checked, and see
the trap in the next paragraph before doing so.

How the wrong version got written is the part worth keeping. The flag was
missing from one early command line for an unrelated reason, UC mode was the
salient difference, and the explanation was written down without being read off
the source. A cause that fits is not a cause that was checked, and this one
survived four days inside a file whose own next section contradicted it.

**Chrome rewrites its own argv into one space-separated string**, so
`/proc/<pid>/cmdline` for a Chrome process cannot be split on NULs the way the
interface promises. Split it and you get a single element, no element matches
`--type=`, every renderer is mistaken for the browser process, and an `any()`
over an empty set answers False. That is exactly the failure mode this file
warns about elsewhere: a check whose negative result looks like a missing
feature manufactures the defect it exists to find. It reported both flags
missing on both engines, twice, and both flags were there. Read the whole line
as one string.

**The unaligned baseline is not the same baseline it was, and every row on disk
was written against the old one.** The geo-alignment axis is defined in this file
against a `ru-RU`, `Europe/Moscow` machine requesting US exits - "a US address
with a Moscow timezone is a one-line inconsistency check". The server is **UTC**
with `LANG=C.UTF-8`, so `geo: off` there is a browser reporting UTC and no
regional locale at all. That is not the same client. It is less obviously
contradictory than Moscow, and it is also not any real person's configuration,
so it must not be read as "the server is aligned by default". What it does mean
is that the 1907 `geo: off` rows from the workstation and the `geo: off` rows
from the server are two different arms of the axis the file says it is holding
constant, and any comparison across the move has to say which side it is on.

**Camoufox needs its addon directory populated and nothing checks that it is.**
`~/.cache/camoufox/addons` has to hold `UBO`; a `camoufox fetch` that was
interrupted leaves the directory there and empty, and the engine still reports
available, because `CamoufoxEngine.check` asks whether the package imports and
nothing more. This is the same shape as the `cloakbrowser.download()` line in
the setup section - an availability check that reads the installer rather than
the thing installed - and the same fix applies: `--dry-run` is what tells you,
so run it on a new machine before a matrix rather than after one.

**Per-tunnel byte attribution has to use the tunnel's own thread, not
differences.** `Relay._pump` runs in the thread `Relay._tunnel` is running in,
so a `threading.local()` set at CONNECT time and read in a patched `_count`
gives exact per-host bytes. Differencing the relay's global counters around each
tunnel does not, because tunnels overlap: a long-lived one -
`mtalk.google.com:5228` is the usual culprit - is charged for everything alive
beside it, and the per-host sums come out at several hundred MB against a 43 MB
total. The first version of the probe did that and the numbers were nonsense in
a way that looked like a finding.

### The viewport is not what Google reads, and it was the obvious rival explanation

Patchright headful passed Google 10 of 10 on 2026-08-12 where the unmodified
control passed none, and the two launch paths differed in a second way nobody
chose: Patchright opens a persistent context with `no_viewport=True`, the
control takes Playwright's default 1280x720, and a viewport is what makes
Playwright install a device metrics override. So the control was not merely
reporting a small window, it was reporting a 1280x720 *monitor* on a 1920x1080
machine, headful, where a real browser has no reason to. Of three engines on the
detector sheet, the only one reporting the true screen was the only one Google
admitted, which is three points and a coincidence is cheap at that size.

`scripts/probes/screen_override.py` changes that one thing and keeps the driver,
the profile handling and the session class, alternating the two arms by session
so ten minutes of Google's mood cannot read as a change in the browser. Measured
2026-08-12 direct from the operator's line, `screen_override_20260812T104316Z`:
**10 of 10 both ways.** The metrics override buys nothing and costs nothing at
this target, so the headful recipe is the driver rather than the screen.

Read as a refutation of the rival explanation and not as a result about
viewports: one target, one address, one window, and 20 attempts cannot see a
small effect. What it closes is the reading that the entire Patchright result
was an artefact of how the control happened to be launched.

### A refused address diverts the request, and reading the status alone got this wrong

Google answers a refusal by sending the request to `/sorry/`, and it serves that
diversion with a **200 about a quarter of the time**. So the status does not
separate "the exit was refused" from "the engine was shown a page and failed
it", and for eight days this file said it did.

The correct test is whether the target answered on the path that was asked for.
`report.was_served` is that test - a 200 whose final URL keeps the host and path
of the request - and it is a property of the exchange, so nothing has to know a
target's name. Checked 2026-08-12 against every row on disk: it fires on 386 of
386 Google captchas and on nothing else. Amazon, Bing, DuckDuckGo and Walmart
all serve their refusals inline on the requested path, which is why those
targets are excluded from the split rather than shown wrong - their refusal and
their result page are indistinguishable at this layer.

The correction moved both headline numbers, in opposite directions:

| claim | was | is |
|---|---|---|
| patchright, ok given a live page | 46/62 | **46/46** |
| camoufox's Google denominator through the pool | 20 | **2** |

`tests/test_report.py` pins the rule, including the 200-plus-`/sorry/` case that
caused this.

### Which engines have ever passed Google, and the denominator that shows it

Read 2026-08-12 over every `benchmark_*.jsonl` in `data/runs/`, google_serp
rows only. `diverted` is `/sorry/` at any status: the exit was refused before
any page existed, so counting it against the engine hands the pool's problem to
the framework.

Through the pool:

| engine | attempts | served | diverted | no reply | ok given served |
|---|---|---|---|---|---|
| patchright | 390 | 107 | 210 | 73 | **107/107** |
| camoufox | 90 | **2** | 72 | 16 | 0/2 |
| chromium, the control | 141 | **0** | 117 | 24 | - |
| http, no browser | 15 | 4 | 1 | 10 | 0/4 |
| obscura | 35 | **0** | 25 | 10 | - |

Direct, from the operator's own line, so the address is held constant:

| engine | attempts | served | diverted | no reply | ok given served |
|---|---|---|---|---|---|
| patchright | 20 | 11 | 9 | 0 | **11/11** |
| camoufox | 10 | **0** | 10 | 0 | - |
| chromium, the control | 50 | **0** | 50 | 0 | - |
| http, no browser | 20 | **20** | 0 | 0 | 0/20, all `empty` |
| obscura | 22 | 1 | 21 | 0 | 0/1 |

- **The direct arm is the strongest engine evidence in this repository, and it
  was already on disk.** `20260812T091926Z` ran camoufox and patchright
  round-robin in one run, on one physical line: camoufox was served 0 of 10 and
  patchright 10 of 10. One address, one window, interleaved, so the pool cannot
  account for any of it. Under a Fisher exact test that is p ~ 5e-6.
- **Camoufox is indistinguishable from the unmodified control against Google.**
  Both were diverted on every attempt ever made: 0 served of 100 for camoufox,
  0 of 191 for chromium. The old "0/20 pass rate" understated this by implying
  camoufox reached pages and failed them. It has been shown two pages in its
  entire history here, both through the pool, and both came back `empty`, which
  is our own client falling short rather than Google refusing. It passes Bing,
  DuckDuckGo and Amazon in the same runs, so the browser works and Google
  specifically sees it - and its Amazon column is the strongest in the
  repository, so this is not a browser that fails at scraping.
- **Patchright is 118 of 118 on pages Google actually served**, across both arms
  and every run on disk. The conditional term is not 93% and never was; it is
  every page it was ever shown. The control's 0 of 191 is what makes that a
  claim rather than an observation.
- **Google burns a clean residential address in about ten queries.**
  `20260812T091926Z` served patchright 10 of 10; `20260812T092642Z`, seven
  minutes later on the same line, served 1 of 10. That is a sequence effect and
  it is the whole reason patchright's direct column reads 11/20 rather than
  20/20 - not an engine property, and a cost finding in its own right.
- **The plain HTTP client is never diverted at all**, 20 of 20 served direct,
  and every one of them the `enablejs` scaffold. Read narrowly: the diversion is
  not a function of the address alone, since the same line that is refused for
  chromium is served for `requests`. It must **not** be read as the scriptless
  client doing better - it is never shown a result page either, it is shown a
  scaffold it cannot render, and it may simply be sorted into a bucket where the
  check never runs.
- **Obscura's rows carry no HTTP status, so its `served` column is instrument
  blindness and not a measurement.** Read 2026-08-12 over every run: of its 128
  non-error rows, 14 have a status. The other four engines are at 100% of 281,
  407, 205 and 550. `was_served` needs the status, so it is structurally false
  for Obscura and every served-versus-diverted figure for that engine has to be
  struck rather than read.

  Its verdicts are unaffected, because they are read off the body: on those,
  Obscura passes Bing 22 of 22 and DuckDuckGo 44 of 44, and never passes Google
  or Amazon at all. So the browser works and its two failures are real, but they
  cannot be attributed to the address or the engine the way the others can. An
  earlier version of this section said Google was "unmeasured, not failed" for
  Obscura on the grounds that 35 of 35 attempts got no reply; the real figure is
  10 no-reply against 44 content-judged captchas, so the engine was refused and
  it was the summary that was wrong.

  **That defect is fixed, and the 11% figure above is a pooling artefact rather
  than a current reading.** Re-read per file 2026-08-14: the 166 blank rows all
  predate the adapter fix of 2026-08-12, and the two run files written after it
  are 4 of 4 and 10 of 10. Pooling a corpus across the date a bug was fixed
  reports the bug as a rate, and this file did so for two days. The rows already
  on disk stay struck - they were written blind and no re-read recovers a column
  that was never recorded - but the engine is not the one described above.

### Obscura's two exclusions, re-checked 2026-08-14, and only one survives

Both are now asked by `scripts/probes/obscura_defects.py`, which sends nothing,
needs no credentials and takes about a minute. It drives a real
`ObscuraSession` against a server on this machine, so it covers the code that
writes rows rather than a copy of it, and it needs `--allow-private-network` -
a flag the engine keeps off everywhere else, because this browser blocks
loopback as an SSRF fix and its CDP port accepts navigations from anything that
can reach it. Run it after any Obscura upgrade.

- **The status is recorded, including through a redirect.** Three responses,
  measured on obscura 0.2.0: a plain 200 and a plain 503 come back from
  `page.goto` correctly, and a **302 to that 503 returns None from goto** and is
  recovered only by the response-event watcher. The redirect is the case worth
  the probe: Google refuses an exit by sending it to `/sorry/`, so on the target
  this repository cares most about the refusal *is* a redirect. A check that
  asked only for a plain 200 would pass on a build that had lost every Google
  refusal, which is why `_watch_navigation_status` must not be deleted as
  redundant - deleting it keeps the successes and drops the refusals, the shape
  of error that flatters.
- **Typed entry is still unavailable, and the recorded reason was wrong.**
  `supports_typing` was False on the grounds that the status was missing, so
  when the status was fixed the flag looked free to flip. It was flipped, and
  one query is what refuted it: **this renderer lays out a `<textarea>` with
  height 0**. On one local page every other element reports a correct box and
  the textarea reports 1264x0, against 168x36 on Chromium for identical markup
  in the same probe minutes apart. Playwright resolves it to `hidden`,
  `wait_for_selector(state="visible")` waits its full 60 s, and every typed
  attempt on every target is recorded as an `error`.

  It lands where it costs the most: Google's search box is a textarea, which
  this repository already knew from the zendriver clearing bug. Waiting on
  `attached` instead would type into an element this renderer has not laid out,
  which is a different client from the one every other engine presents, so this
  is refused rather than worked around - the grounds `--humanize` established.
  `ObscuraSession.search` is written and works; only the flag is False, so a
  fixed browser costs a one-line change.

  **The browser was fixed upstream on 2026-08-23 and the flag still must not be
  flipped on that news.** This defect was filed as `h4ckf0r0day/obscura` issue
  **#685** on 2026-08-20 and closed as completed three days later by PR **#690**,
  written by the maintainer `xrip`, which gives `<textarea>` an intrinsic control
  box. So the reason recorded above has expired - which is the second time this
  one flag has carried a reason that stopped being true, the first being the
  missing HTTP status, and the first time it was flipped on the strength of an
  argument rather than a measurement it was wrong within one query.
  `obscura_defects.py` exists precisely so this is not argued again: the flag
  flips when that probe reports a non-zero box on a build containing #690, and
  the build in the setup instructions is 0.2.0, which predates it. Until someone
  runs it, the correct state of this entry is "fixed upstream, unverified here".

**`supports_headful` is False and it does not disqualify this engine the way it
disqualifies a Chromium one.** `obscura serve` has no headless or headful flag
at all, so there is nothing to turn on. The reason headful matters elsewhere is
the `HeadlessChrome` substring, and this browser does not carry it: it reports
`Chrome/145`, and DuckDuckGo - the target that sorts entirely on that
substring - serves it 44 of 44 against 0 of 50 for the engines that announce
the mode. So the `fetch` path is a fair column for this engine, and the earlier
claim that both defects had to be fixed before it belonged in an engine column
was true of the status and never true of the mode.

**Obscura is not a candidate engine, decided 2026-08-14, and it stays in the
registry as evidence.** The instrument was repaired and the engine still does
not earn a column on the targets this repository is for. It has never passed
Google: 0 of 47 judged rows across both arms, 44 of them captcha. Amazon is 0 of
15, twelve of them direct, which puts it with the other Chromium-family losers.
Walmart served it 1 of 5, which is the unmodified control's own figure on that
target.

Its two wins do not transfer. DuckDuckGo 44 of 44 and Bing 22 of 22 are
explained by the User-Agent substring and not by anything the browser does
better - patchright headful reports the same clean `Chrome/...` and gets the
same result. So the engine buys nothing patchright does not already have, and
loses on every target where patchright wins. Typed entry is unavailable and
`supports_geo_align` is False, so it cannot join either axis currently being
run.

**It must not be deleted from the registry.** It is the case that separates
"headless" from "announces headless" in the DuckDuckGo finding: headless,
sharing Chromium's exact JA4, and served 44 of 44 while the two engines carrying
the substring are served 0 of 50. Remove the engine and that control goes with
it, leaving the finding resting on one headful window of patchright. The cost of
keeping it is a module nobody schedules.

### The Obscura build is verified by its handshake, never by the flag

`--stealth` is accepted by the plain release archive and does nothing there, so
the flag is not evidence the feature exists - the build is. The check is two
direct fetches of a TLS echo, the build acting as its own control, so it needs no
reference fingerprint, no gateway and no quota:

    obscura fetch --stealth --quiet https://tls.peet.ws/api/all
    obscura fetch --quiet https://tls.peet.ws/api/all

Measured 2026-08-11, obscura 0.2.0. This file recorded until 2026-08-19 that the
archive it was unpacked from was named `obscura-win.zip`, with no `-stealth`
suffix, and was the stealth build regardless. **No asset by that name exists in
the v0.2.0 release** - it publishes `obscura-x86_64-windows.zip` and
`obscura-x86_64-windows-stealth.zip`, and its README carries a table of the four
variants - and the archive is no longer on this machine, so the name cannot be
re-checked. Treat it as mis-transcribed. Nothing rests on it: the evidence that
this build impersonates is the handshake below, which is the whole point of the
section, and an archive name would not have been evidence even if it had been
right:

| | `--stealth` | no flag |
|---|---|---|
| JA4 | `t13d1516h2_8daaf6152771_d8a2da3f94cd` | `t13d1011h1_61a7ad8aa9b6_3fcd1a44f3e3` |
| JA3 hash | `1a12f109f41ce9151a78ff627b70512d` | `6a299af22b8c6e28dddaffc01426446b` |
| h2 fingerprint | Chrome's | absent, the connection was HTTP/1.1 |

15 ciphers and 16 extensions, GREASE in four places and `X25519MLKEM768` first,
against 10 ciphers, 11 extensions, no GREASE and no h2 offered at all. Re-run
this after any Obscura upgrade: a build swap is invisible in the rows, and every
row would go on claiming a stealth run.

**That instruction could not be followed between 2026-08-18 and 2026-08-31, and
the reason was the echo rather than anything here.** Measured 2026-08-18:
`tls.peet.ws` served OpenSSL's default self-signed placeholder - subject and
issuer both `Internet Widgits Pty Ltd`, no SAN, `notAfter` 2023-08-29 and so two
years expired. Every client on this machine refused it, `requests` included,
while `www.google.com`, `example.com`, `api.ipify.org` and `ipinfo.io` all
verified on the same run. The build check and the whole JA4 table were therefore
unavailable while that held.

**The block is unblocked as of 2026-09-01.** `tls_echo.py` ran end to end on the
VPS and on the workstation the same hour and both returned handshakes, so the
service is answering again. Two things to carry rather than assume:

- The runs went out with `ssl_verify=0`, so the paragraph below about skipping
  verification applies to them: what they measure is the handshake as seen by
  whoever terminated it, and on the workstation that is behind the Happ gateway.
  The VPS arm is the one with no interceptor in the path.
- The VPS patchright JA4 is `t13d1516h2_8daaf6152771_d8a2da3f94cd`, which is the
  value in the table above **character for character**. The workstation reports
  `t13d1516h2_8daaf6152771_806a8c22fdea` - same ciphers, same extensions, third
  segment different. Which of the gateway or the build produced that difference
  is not established and must not be written down as either.

It is written down because the failure impersonates a finding. Obscura refuses
the certificate with a `CERTIFICATE_VERIFY_FAILED` out of its bundled BoringSSL,
quoting a build path on the vendor's own CI machine, which reads exactly like a
defect in the engine under test - it was read that way once, and then blamed on
the VPN tunnel that was being unwound the same week. Neither was involved.
`tls_echo.py` now verifies the echo from Python before it launches anything, so
the answer arrives as one sentence about a certificate rather than as five stack
traces about five engines. Skipping verification is not the workaround: the value
of this endpoint is that it reports the handshake it received, and a handshake to
a host that is not who it claims to be measures the interceptor.

It also fixes how the Obscura column is read. This is the only engine here whose
TLS layer is synthesised - Camoufox is Firefox and the Chromium-family engines
are Chromium, so their handshakes are genuine as a side effect of being real
browsers. Impersonation is not an advantage over them at this layer, it is parity
on a layer the others get for free. Write-up and the limits of the claim:
`docs/findings_obscura_tls.md`.

### The entry shape is a new axis, and it is what probe-and-hold changes

Every row written before 2026-08-13 arrived at `/search?q=` or `/s?k=` by
navigation. That is one request carrying a query string with no keystroke behind
it, no referrer and no form submission - a shape no person produces, and one the
harness could not previously vary because nothing here could reach a search box.
`entry` is now on every row, `url` for the old shape and `home` for landing on
the target's own front page and typing, and `scripts/probes/probe_and_hold.py`
is what asks the question.

The protocol it implements is an operator's, not ours: one sticky exit per
session, land on the front page, type, and if the probe is refused drop the
address rather than retry it; if the probe is served, hold that exit and run a
series on it with 8-20 s between queries. Two figures travel with it - a 75% the
operator had reached on a warmed exit, and about 88% for the queries following a
served probe - so `--warm on,off` is a real arm and `position` is on every held
row.

**Neither figure is a claim, and this entry called them claims for two weeks.**
Corrected 2026-08-27 after the person who relayed them read it back. What was
actually said is that an operator had once got to 75%. That is one number, with
no denominator, no arm it was measured against and no attribution to any part of
the protocol. It never was "20% to 75%": the 20% is ours, it is this harness's
own baseline, and pairing the two into an effect size was done here and nowhere
else.

What the mistake looked like from the inside: the pairing is what made the arm
worth building, and once built, the arm needed something to be an arm *against*.
A single reported number does not fill that slot and an effect size does, so the
number quietly became one. Nothing was invented at any single step - the 75% is
real and was said, the 20% is real and was measured - and the sentence that
joined them was never checked against either source. It then hardened: `README.md`
promoted it to "the published claim", `probe_and_hold.py` to "the biggest claimed
effect here", and `run_ladder.py` sized the experiment against it. By the time it
was caught, a number nobody had claimed was setting our sample size.

The general form is the third instance of the same error in this notebook, after
`OptimizationHints` and `norotate`: something that arrived from a credible source
was allowed to stand in for a measurement. This one is the worst of the three,
because here the source was credible *and accurate* - it was the arithmetic done
on top of it that was ours.

Both figures now have a denominator, and they do not survive equally.
Measured 2026-08-13 into 2026-08-14 by `probehold_20260813T202606Z`: patchright
and zendriver, google_serp, `country=any`, entry `home`, 12 cells interleaved at
identity granularity, 120 fresh exits and 229 attempts in one window of 1 h 54.
Read with `scripts/analysis/held.py`.

| axis | arm | probes | P(ok) | 95% |
|---|---|---|---|---|
| warm | off | 60 | 32% | 21-44% |
| warm | on | 60 | 30% | 20-43% |
| params | none | 40 | 28% | 16-43% |
| params | filter=medium | 40 | 40% | 26-55% |
| params | filter=high | 40 | 25% | 14-40% |
| engine | patchright | 60 | 32% | 21-44% |
| engine | zendriver | 60 | 30% | 20-43% |

- **The hold is real and it is the strongest thing in the run.** 108 of 109 held
  rows passed, by position 36/37, 36/36, 36/36. The operator's 88% is if
  anything conservative here. Read as conditional and censored, which is what
  the column means: every held row follows a probe that was served, and a series
  stops at its first refusal, so later positions are drawn from the survivors.
  The consequence for cost is direct - the expensive event is finding an exit
  Google will serve, and once found it is worth a series rather than one query.
- **One page of warming is measured and absent.** 32% against 30%, intervals
  almost coincident, and two extra page loads per identity is what it costs to
  keep asking. Say what this is not: it is not a refutation of the operator's
  75%, which was one number on another provider, another country mix and another
  hour, and which was never offered as the result of a controlled arm in the
  first place. There is nothing here for it to contradict. What the cell says is
  that this pool, in this window, with one page, did not move.
- **The filter ladder does not separate.** `medium` reads highest and its
  interval overlaps both neighbours over most of their range, so the ranking
  must not be quoted, on exactly the grounds that `de`/`gb`/`any`/`ru` must not
  be. What the run does establish is the shape of the question: three arms at 40
  probes each cannot resolve a ten-point difference, so a filter run worth doing
  needs a different order of magnitude.
- **The two engines are a dead heat**, 32% against 30%, interleaved in one
  window. That is worth having rather than disappointing: zendriver drives raw
  CDP with no WebDriver and no Playwright, patchright is a patched Playwright,
  and entering Google through its own front page they are indistinguishable. It
  also says the remaining variance does not live on the engine axis.
- **The unanswered-CONNECT floor appears again at 8%**, 10 of 120 identities,
  and lopsided - 8 on patchright against 2 on zendriver. Too few to read as an
  engine property, but this is the first measurement of the floor taken with the
  VPN tunnel bypassed, so it is the first one that can be attributed to the
  gateway rather than to our own line.

  *That lopsidedness was the answer and it is 10 events, so it could not have
  been read as one. Patchright opens its own CONNECTs and zendriver goes through
  the relay, which is exactly the split that separated the two groups at n=1131
  six days later - see "The floor was an IP ban". Recorded here because the
  dismissal was correct on the evidence available and the pattern was still
  worth carrying forward as a thing to re-check at scale.*

Four things had to be built or fixed to ask it at all, and three of them are
traps rather than features.

- **`url` on a typed row records the results URL, not the front page.** It looks
  like a lie and is the opposite: `report.was_served` compares the host and path
  asked for against the one landed on, so recording `/` would compare it against
  `/search` and mark every typed row as diverted. The harness would be scoring
  its own entry shape as a refusal by the target. `entry_row_url` documents it.
- **Google covers its own front page with a consent wall, and `/search` never
  carries one.** Measured 2026-08-13: `consent.google.com` appears 5 times on the
  front page body and 0 times on the `/search` body and 0 on `/sorry/`. It is an
  overlay, not a redirect - the address stays `/?hl=en` - so the URL-matching
  consent rule could not see it, and the box is `is_visible()` underneath while
  the panel eats every pointer event. Before the fix that read as a 30 s
  actionability timeout recorded as `error`: our own client failing to reach a
  target that had refused nothing. The target now declares which button clears
  it, `consent_dismissed` is on every typed row, and `GoogleSerp.judge` scores an
  undismissed wall as `consent` instead of letting it fall through to the no-JS
  test. It is intermittent - three fresh profiles out of three met it in one
  window and none in another - which is exactly why the column exists.
- **A held session's search box still holds the previous query, and appending to
  it produces rows that are wrong and look right.** Measured on the first
  three-query series: query two landed on `q=kimchi+mistakesmortgage+rates` and
  query three on `q=kimchi+mistakesmortgagswimming`. All three came back `ok`,
  every column was internally consistent, and no analysis in this repository
  could have caught it. `base.verify_box` now reads the box after the last
  keystroke and refuses to press Enter unless it holds exactly the query the row
  records; a mismatch is an `error`, which is ours, rather than a verdict, which
  would be a claim about the target. The zendriver path needed its own version
  of both the clearing and the check - zendriver's `clear_input` reaches for the
  value setter on `HTMLInputElement.prototype` and Google's box is a textarea, so
  it raised `Illegal invocation` and the append survived the call.
- **A browser that will not launch costs one identity, not the run.** zendriver
  answered `Failed to connect to browser` on one launch and started normally a
  minute later; unhandled, that ended a run with eleven other cells still to
  answer. It is written as `session_failed` rather than `error`, because `error`
  means an attempt reached the network and this one never did.

`supports_typing` is declared by every engine and the probe refuses one that
answers False, for the reason `--humanize` demonstrated: an option silently
dropped for some columns reads as an engine difference. Obscura and seleniumbase
answered False on one shared ground - they recorded no HTTP status, so
`was_served` was structurally unavailable and their rows could not be split into
served and refused, which is the only split the entry question is asked through.

That ground is spent for Obscura and its flag is still False, which is the one
combination worth stating: the status has been recorded since 2026-08-12 and is
now checked, and what keeps the engine out is a **zero-height `<textarea>` in
its own renderer**, measured 2026-08-14 and covered above. The two reasons are
unrelated and fixing the first did not touch the second - which is the argument
for the probe rather than the note. A declared capability that is only argued
about is one nobody re-checks, and this one had been carrying a reason that had
stopped being true.

**Both of its reasons have now stopped being true, and the flag is still False.**
The zero-height textarea was fixed upstream by PR #690 on 2026-08-23. That makes
this the same sentence for the third time, so the rule is worth stating plainly
rather than re-deriving: **a capability flag moves on a probe result, never on a
changelog.** The seleniumbase half of the paragraph above is untouched by any of
this and still rests on the missing status.

### Measurement rules

**Verdicts come from content, not HTTP status.** A blocked page usually returns 200
with a stub body. `targets.py` decides by inspecting the response body. Never add a
boolean `success` field - use the verdict enum: ok, captcha, consent, block, empty, error.

**A verdict without its reasoning is not reviewable.** `target.judge(url, title,
html)` returns a `Judgement(verdict, reason)`, and `record_judgement` stores the
reason plus the marker counts the verdict rested on. This exists because "block"
meant two completely different things until 2026-08-11 and no row written before
that could be re-read to tell which. The method is named `judge` and not
`verdict` so that any call site left over from the old contract fails loudly
instead of returning a string that still looks plausible.

**`empty` means our client came up short; `block` means the target refused.**
Google hands a scriptless client a 92 KB "enable JavaScript" scaffold that stays
on `/search` and carries no rejection at all. Scoring it as a block credits
Google with a refusal it never made. Discriminator, measured 2026-08-11: the
scaffold carries `enablejs` and never `recaptcha`; the refusal redirects to
`/sorry/` and carries `recaptcha` and `unusual traffic`. The `block` fallback
must stay narrow - a new refusal shape should be reported and investigated, not
absorbed into `empty`.

**A session is the unit of measurement.** N queries through one browser is one
identity doing N searches. N browsers doing one query each is a different
experiment and gets a different number. `--batch` is recorded on every row.

That claim is about state, not about process lifetime, and it has already been
false once. Until 2026-08-11 Camoufox called `browser.new_page()`, which opens a
fresh context every time, so it alone discarded its cookie jar between queries
while the other four carried one across the batch. Its tenth query arrived from
an identity that had been searching for five minutes and had never accepted a
cookie - not the naive scraper we meant to measure and not a human either, and
not the same experiment the other columns were running. Fixed by creating one
context per session, verified by comparing 13 markers between a page made from
the browser and a page made from an explicit context: no difference, so the fix
bought a cookie jar without moving the fingerprint.

`scripts/probes/session_continuity.py` is what caught it and what stops it
coming back. It sends nothing and needs no credentials. The offline suite cannot
cover this, because it is browser-free by design, so this check has to be run by
hand after any engine upgrade.

**Nothing downstream branches on an engine name.** Adding a framework is a module
in `nmbench/engines/` and one line in `REGISTRY`. A runner that can name a
framework is a runner that can treat it differently without anyone noticing, so
the test suite asserts the runner contains no comparison against an engine name.

**A matrix without an unmodified control measures nothing.** `chromium` launches
Playwright's Chromium with no arguments, no user agent override and no patches -
`navigator.webdriver` is `True` and it is meant to stay that way. Without it, a
pass rate cannot be told apart from the target letting everything through, and
"the anti-detect framework passed" is not a claim, it is an observation with no
denominator. The test suite reads the source of `ChromiumEngine.open` and fails
if `args=` or `user_agent` appear, because hardening the control silently is the
one change that would invalidate every number in the report without breaking a
single test otherwise.

**The control belongs on both sides of the gateway, in the same time window.**
An engine spec may carry a `:direct` suffix, so `chromium,chromium:direct` puts
the same browser through the proxy and around it inside one run. Two separate
runs an hour apart measure the hour as well. A global `--direct` still forces
every cell direct, so it cannot be partly undone by a spec that omitted the
suffix.

**Keep the body the verdict was read from.** A verdict is one word about 92 KB of
markup, and the question that decides the report is usually asked after the run
("was that scaffold or a refusal", "did the consent wall change shape"). Re-running
to find out costs traffic and asks the targets again. `artifacts.py` gzips every
non-`ok` body plus a sample of the passes, keyed by engine and target. Artifacts
are gitignored: exit addresses turn up in embedded links, so the archive is
evidence for us and a credential leak in a public repository.

**The provider dashboard is not an instrument.** It rounds to 0.01 GB, so its
resolution is ~10 MB. Measuring a 330-byte request against it is meaningless.
Traffic must be measured by a local counting proxy that sits between the engine and
the gateway and counts bytes in both directions. `Content-Length` summing is a lower
bound only: it misses headers, TLS overhead and chunked responses.

`nmbench/relay.py` is that counting proxy, and it exists for a second reason:
Chrome takes no credentials in `--proxy-server`, so every engine driving Chrome
directly rather than through Playwright cannot reach the pool at all without
one. An engine declares `needs_relay` and the runner builds one per session -
per session and not per run, because the username carries the gateway parameters
and the parameter set is the sticky session key. `zendriver` refuses a pool arm
outright when handed no relay, rather than launching unproxied and writing this
machine's own address into rows labelled as exits.

**The `bytes` column is now two different measurements and the row says which.**
Playwright engines count through `page.route`, which sees page resources. The
relay counts sockets, which sees everything the browser sent through the proxy -
request headers, TLS overhead, and the browser's own background chatter. Those
are both legitimate and they are not the same number, so `relayed` is on every
row and the two must never be pooled. The relay figure is the one that matches
what the provider bills. The relay also adds a loopback hop, so **`elapsed_ms`
on a relayed row is not comparable against an unrelayed one**; verdicts and
bytes are.

### Chrome pays its vendor 43 MB per profile, and the pool was billed for it

Measured 2026-08-19 on the server, browser opened on `about:blank` with nothing
navigated, 60 s, bytes charged to the CONNECT authority that carried them. A
fresh-profile Chrome spends **43.2 MB of a 43.4 MB idle window** on
`optimizationguide-pa.googleapis.com` and under 100 KB on every other host in
the run. It is the Optimization Guide fetching an on-device model. At `--batch
1` the profile is fresh on every attempt, so it is paid again on every attempt:
about **43 GB per thousand attempts** of a 100 GB shared quota, billed as
residential traffic, for a request no target ever sees and no verdict ever
reads.

It is the whole of the gap that looked like an engine property. Before this was
attributed, seleniumbase and botasaurus read 40 times the bytes of the
Playwright engines on the same target, and the obvious explanation - that
`page.route` undercounts because it sees page resources rather than sockets -
turned out to be worth 1.7x, not 40x: camoufox at Amazon is 817,150 counted
through route against 1,410,665 counted through sockets in the same attempt. The
factor of 1.7 is the real instrument difference and it is what the paragraph
above is about. The rest was Chrome talking to Google.

**Launch flags do not reach it, and this was established rather than assumed.**
`--disable-background-networking` and `--disable-component-update` are verified
present on the real browser command line for both engines and they remove
everything else. SeleniumBase additionally ships `OptimizationHintsFetching`,
`OptimizationTargetPrediction` and `OptimizationGuideModelDownloading` in its own
`--disable-features` list, that list is on the command line too, and the fetch
still happens: 43,041,458 bytes to that host in a 90 s window with all three
names in force. Guessing further feature names until one appears to work is how
a four-day run gets launched on a hope.

`zendriver` is the control that makes this a flag question and not a host-Chrome
question: same Chrome 151, same box, same relay, **72 KB idle**, where
seleniumbase on the same host Chrome read 43.4 MB. Its own `Config` sets both
flags by default, and Playwright sets them too - they are in its driver's switch
list - which is why patchright idles at 43 KB. So the uncontrolled variable was
never the browser family.

**What that does not license is reading the flags as the cause, and the first
hour of the matrix corrected it.** zendriver's own rows carry
`relay_blocked=1`: it asks for the model too, and is refused at the relay like
the other two. Its 72 KB idle window was a window in which it happened not to
ask, not a window in which a flag stopped it. Botasaurus says the same thing
from the other side - 43.6 MB, then 46 KB, then one refused request at 520 KB
across three windows of the same length. **The fetch is intermittent on every
engine that drives Chrome**, and a single idle window measures the coin rather
than the engine. The flags remove the rest of the background traffic and they do
not govern this request; nothing observed here has.

By the same argument the Playwright engines cannot be said never to make it.
They are not relayed, so nothing in this repository can see their CONNECTs at
all, and their low idle figures are `page.route` counts of a browser that was
not asked to navigate. What is measured is that the relayed engines make it and
that the relay refuses it.

**The relay refuses that one hostname**, because it is the only place left that
can refuse it deterministically, it applies identically to all three relayed
engines, and it cannot touch a verdict - no target is behind it. Verified the
same day on seleniumbase, same engine minutes apart: **43,283,925 bytes idle
without the block against 201,771 with it**, `blocked=1`. Matched on the host
exactly and never as a suffix, so the rule cannot grow into `googleapis.com` and
start eating a target's own traffic; `blocked` is counted and reaches the row as
`relay_blocked`, because without it "the browser never asked" and "it asked and
was refused here" are the same row; and `block_hosts=()` prices it again.

Two things this does not license. It is **not** a hardening of the browser under
test - nothing on the command line changes, no fingerprint moves, and the
unmodified `chromium` control is untouched, which is the rule that makes it
acceptable at all. And the fetch is **intermittent on botasaurus**: two of its
idle windows came back 43.6 MB and 46 KB, and a third asked once and was refused
at 520 KB total. A single idle window is not a measurement of this, which is
exactly how the whole question started as a mystery about `chromium_arg` giving
178 KB once and 43.2 MB the next time.

One neighbour worth recording and deliberately not blocked: both botasaurus
windows spent about 460 KB on an `r*.gvt1.com` CDN node. That is real, it is the
same class of vendor traffic, and at 460 KB per session it is three orders of
magnitude below the model fetch. Refusing more hosts than the measurement
requires is how a measurement harness turns into a browser nobody else runs.

**Our own `--disable-features` was discarding the vendor's, and every botasaurus
row before 2026-09-01 carries the consequence.** Chrome honours only the last
such switch. botasaurus-driver ships
`--disable-features=IsolateOrigins,site-per-process` in `default_arguments` and
appends caller arguments after it, so the engine's
`--disable-features=OptimizationHints` landed last, won, and threw the vendor's
value away whole. Site isolation was therefore left **on** in an engine whose
vendor turns it off, in every row this repository has written for it, and no
column said so. Measured 2026-09-01 with
`lab/probes/probe_botasaurus_flag_order.py` against botasaurus-driver 4.0.101:
names lost `['IsolateOrigins', 'site-per-process']`.

The fix is `merged_disable_features` in `nmbench/engines/botasaurus.py`, which
reads the vendor's value out of the vendor's own source and emits the union, so
the surviving switch carries every name either side asked for and the result no
longer depends on which one lands last. It refuses to launch rather than guess if
the vendor's shape changes. Same fix as botasaurus-driver #29 upstream, applied
on our side of the call.

Two limits on what this is. **It is not a correction to any published number** -
nothing here measured what site isolation is worth to a verdict, so this is a
difference between rows before and after the date, not a retraction. And the
engine comment that used to explain why the flag "reaches the browser by luck"
was **wrong about the mechanism**: it named `Config.browser_args`, a property
returning `sorted(default_arguments + arguments)`, and warned that a value
sorting before `IsolateOrigins` would go silently dead. `Browser.start` calls
`Config.__call__` instead (`core/browser.py:326`), which preserves insertion
order, so ours wins by position and the alphabet is irrelevant - a value named
`AAAOptimizationHints` still wins, arm 3 of the same probe. The explanation
fitted every character of the evidence, predicted the right outcome, and had
never been checked against the launch path; the real defect was sitting beside it
unnoticed for the whole time the warning was there.

### The axes, and the capabilities that are refused rather than dropped

**A capability only some engines have must be refused by the runner, not dropped
quietly - and that now includes blocking.** `supports_headful`,
`supports_geo_align` and `supports_humanize` were guarded; `supports_blocking`
was not, and the default is `--preset light`, so the first matrix mixing
Playwright engines with non-Playwright ones silently blocked resources for two
columns and not for the other three. Measured 2026-08-13 on one google_serp
attempt each: 4 KB for a blocked engine against 9.9 MB for an unblocked one on
the same refusal page. That is a 2000x engine difference produced entirely by
our own flag, and blocking can move the verdict too, since a page that never
loads its script is judged on markup that was never finished. Mixed matrices now
require `--preset none`.

Preflight also runs **before** the dry-run return, so `--dry-run` refuses an
invalid matrix instead of printing a cost estimate for a run that cannot start.

**One variable per experiment.** Provider, engine and target are three independent
axes. Changing two at once produces a number nobody can interpret.

**Geo alignment is an axis, not a setting.** The harness runs on a `ru-RU`,
`Europe/Moscow` machine and requests US exits, so by default every engine
presents a timezone and a language list that contradict its address. The choice
is recorded on the row rather than settled in code, and what is not acceptable
is a run where some engines were aligned and the row does not say so.

**The distinction is who sources the data, not which browser family can apply
it, and this file had it wrong until 2026-08-14.** It said Camoufox could align
and "the Chromium-family engines cannot", which described what was unwritten
rather than what was impossible. Camoufox looks the exit up in a bundled
database and needs only a boolean; the Chromium engines have to be handed the
zone, which the harness already knows because `gateway.echo` returns it. Both
then apply it the same way, through Chromium's own emulation
(`Emulation.setTimezoneOverride`) rather than by patching a JavaScript property -
Playwright takes `timezone_id` on the context and zendriver reaches the CDP
method directly. That distinction is the load-bearing one: a patched property is
read back unpatched from an iframe and from a Web Worker, and browser-level
emulation is not.

Verified 2026-08-14 by `scripts/probes/geo_align_check.py`, which sends nothing
and needs no credentials. Both engines asked for `Asia/Kolkata` reported offset
-330 in the page **and in a Web Worker**, and both reported `Europe/Moscow` when
handed nothing. Two things it caught that a weaker check would not: the browser
answers with ICU's canonical spelling, so `Asia/Kolkata` comes back as
`Asia/Calcutta` and a name comparison condemns an override that worked; and the
unaligned arm has to be tested too, because an engine that reported the zone
whatever it was handed would collapse the axis into one arm and every difference
in it would read as zero.

**The axis was run, and alignment does not help - on one engine it costs half
the yield.** Measured 2026-08-14 by three runs of one design,
`probehold_20260814T052300Z`, `...T065213Z` and `...T072651Z`: patchright and
zendriver, google_serp, `country=any`, `filter=medium`, entry `home`, warm off,
aligned and unaligned interleaved at identity granularity. 102 probes. The last
run was stopped by hand at identity 18 of 20, which censors the tail of that
window and nothing else, because the arms are interleaved.

| arm | probes | ok | P(ok) | 95% |
|---|---|---|---|---|
| geo off | 57 | 26 | 46% | 33-58% |
| geo align | 45 | 10 | 22% | 13-36% |

Pooled that is Fisher exact p = 0.021, and 0.0054 with the unanswered CONNECTs
dropped. **It must not be read as a result about alignment**, because splitting
it by engine is what the run was interleaved to allow, and the split does not
survive:

| engine | geo off | geo align | p |
|---|---|---|---|
| patchright | 10/29, 34% | 8/23, 35% | 1.0 |
| zendriver | 16/28, 57% | 2/22, 9% | **0.0008** |

Patchright is flat to two significant figures and zendriver loses six sevenths
of its yield. An axis acting on the identity the browser presents would act on
both, because both end up in the same state: `geo_align_check.py` reads the same
zone and the same offset back from the page and from a Web Worker on either
engine, so what a detector can see is not what differs between them. What
differs is **when the override is installed** - Playwright takes `timezone_id`
as a context option, so it is in place before any target exists, and zendriver
sends `Emulation.setTimezoneOverride` to a tab already opened on `about:blank`.
The zendriver refusals are genuine rather than ours: 32 of them are `/sorry/`
with a recaptcha.

That is a hypothesis and this run does not settle it. What it settles is
operational and is enough to act on: **do not turn geo alignment on**, and in
particular never on zendriver. The axis had no denominator at all until now; it
has one, and the answer is that it buys nothing on the engine where it is free
and costs most of the yield on the engine where it is not.

One thing the design bought that was not the question. Every cell pays the
`gateway.echo` call, so `exit_timezone` is on the unaligned rows too and the
same zone can be read aligned against not: over the 11 zones appearing in both
arms it is 13/26 unaligned against 5/16 aligned. Same direction, far too few to
carry anything, and it is there as a control against the two arms having drawn
different countries rather than as a second measurement.

**Timezone is aligned and the language list is deliberately not.** Every marker
a verdict rests on in `targets.py` is an English string and the URLs pin
`hl=en`, so aligning the locale invites the target to answer in another language
and be judged by a rule that cannot match it - which would move verdicts in the
aligned arm and read as the target reacting to alignment when it was us. The two
halves are not equally suspicious either: English on a German address is an
ordinary person, a Moscow timezone on a Texan address is not a person at all.

The mechanism: `--countries ru,us` puts both an aligned and a misaligned exit in
one time window and needs no engine feature, because the host country is the
alignment. `--geo align` uses the engine's own feature instead, and every engine
declares `supports_geo_align`, so the runner refuses the flag outright when the
matrix holds one that cannot. Silently dropping an option for one engine is the
failure this attribute exists to prevent. The unmodified `chromium` control
stays at False and must not be given the feature to join the comparison: the
axis is read within one engine, aligned against unaligned in one window.

Two costs the design has to pay, and both are in the code rather than in a note.
`gateway.identify` prefers the CONNECT reply header, which carries the address
and no timezone, so an aligned cell has to ask the echo service instead - about
330 bytes. **Every cell in such a run pays that call, including the unaligned
arm that has no use for the answer**, because a request through the exit before
the browser opens is warming, and warming is its own axis; paying it in one arm
only would put the warm-up inside the geo comparison. And an exit whose lookup
names no zone is dropped as `identity_skipped` rather than run, because a row
claiming an alignment that did not happen is unfalsifiable once the exit is
gone.

`--humanize` had exactly that failure until 2026-08-11: it was accepted for any
matrix, and only Camoufox implements it. A run with the flag compared humanized
Camoufox against unhumanized everything else and would have read as an engine
difference. Every engine now declares `supports_humanize` and the runner refuses
a mixed matrix. Humanized input is measurable as its own axis - same engine, flag
on and off, in one time window - and that is the only way it produces a number
anyone can attribute.

**Providers must be interleaved, not run in sequence.** Running provider A at 10:00
and provider B at 14:00 measures the target's mood, not the providers. The runner
goes round-robin across matrix cells inside one time window, with the same query order.

`--providers a,b` is that axis and it is a cell like every other one, so the
interleaving is the scheduler's rather than the operator's. Two things about it
are worth knowing before using it.

- **The cell key names the provider only when the axis is varied.** A cell on the
  default provider carries no provider segment at all, which is what keeps the
  132 run files already on disk resumable - they were recorded before the axis
  existed. That is the `geo` precedent and it is the reason the axis was free to
  add: the interleaved multi-provider run is the only shape whose keys change.
  The provider is on every row regardless, so a row whose key does not name a
  gateway still says which one answered it.
- **A direct cell has no provider.** A request that never reaches a gateway
  cannot be attributed to one, so the axis collapses for it exactly as the
  country axis does. `--direct --providers a,b` is refused outright rather than
  producing one identical direct experiment per provider and presenting the
  copies as a comparison.

**A provider is a username format, so it is data and not code.** Every vendor
sells the same thing and spells it differently - its own separators, its own
parameter names, its own reaction to a mistake - and that difference is the whole
of what a provider is from this harness's point of view. So it is one `.toml`
under `data/providers/`, read through stdlib `tomllib`, which means a competitor
costs a file and no dependency. It sits beside `data/queries/` for the same
reason that does: a committed input a stranger gets identically.

A Python module per provider was rejected for the reason the engine registry
exists. A module invites one `if provider == ...` in a place nobody reviews, and
the argument this whole repository rests on is that every arm went through the
same code path. A data file cannot branch.
`test_no_script_branches_on_a_provider_name` reads the source and derives the
names from the definitions on disk, so a competitor added tomorrow is covered
without anyone extending the test.

**Every definition declares its own provenance, and the field is load-bearing.**
`status = "measured"` means rows in `data/runs/` were produced through that
gateway from this machine. `status = "documented"` means the dialect was
transcribed from the vendor's documentation on the date recorded and nothing here
has ever sent a byte through it. `--dry-run` prints the status of every provider
in the matrix, and a `documented` one is the first thing to suspect when its
cells fail in a way the measured provider's do not.

The distinction is not bookkeeping, because **a wrong username is invisible**.
The one gateway measured here answers an unrecognised parameter name with 200 and
the setting silently dropped: the connection succeeds, the run completes, and
every row claims a setting that was never applied. Nothing the gateway replies
can catch it. Four things follow, and each one is the only check available rather
than a courtesy:

- a parameter outside the definition's `known_params` is refused before a request
  exists, and the message says the setting will NOT be applied
- `--param` is validated against **every** provider in the matrix at preflight,
  not the first. A parameter one gateway knows and another does not would
  otherwise raise mid-run, after the earlier providers' cells had already spent
  traffic to learn something that was local all along
- the session parameter is asked of the provider rather than written in, through
  `proxy.session_params`, and eleven call sites wrote `"sid"` before it existed.
  `sid` is the canonical name most definitions will pick, and `aliases` already
  covers a gateway that merely spells it differently on the wire - which is why
  the literal was right often enough to be copied eleven times. Against a
  definition whose canonical name is something else the two failure modes are not
  equally visible. Under `strict` it is refused by `build_username` before
  anything is sent, so the cost is a harness that will not run. Under
  `strict=False`, which `gateway_health.py` passes deliberately and is the only
  place in the tree that does, the name goes out, is answered with 200 and
  dropped, and **every attempt draws a fresh exit while every row records one held
  session**. Holding an exit is the whole of the probe-and-hold method, so that is
  the arm it silently destroys, and
  `test_the_session_parameter_is_asked_for_rather_than_spelled` reads the source
  for the literal rather than running anything, because in the silent case the run
  completes and the numbers look like a result
- credentials are checked per provider and named, because a run started with one
  provider's account missing would write a full column for the gateway that
  answered and an error column for the one that could not, into one file, in one
  window - which is exactly the shape a provider comparison is read in

**Only NodeMaven is shipped, and it is the only provider any number here was
measured through.** That is a statement about the data and not about the scope. A
`documented` competitor definition is worth having and has to be transcribed from
the vendor's own fetched documentation, never written from memory: an invented
username format is an invented technical claim about somebody else's product, and
it would fail silently in the one way this section is about.

**The first thing a new definition gets is
`python -m nmbench gateway-health --provider <id>`**, which sends a handful of
CONNECTs and no target traffic. It is the cheapest check that can separate a wrong
username format from a wrong password, because the gateway itself cannot: it
answers both with a status that names neither. The probe prints the definition's
`status` and says so when it is `documented`, so a refusal is read as possibly the
transcription rather than the account, and `provider_status` is on every row for
the same reason. Point it at a definition before a matrix, and promote to
`measured` in the commit that adds the first run.

**Fixed inputs live in the repository.** Query lists are committed, not generated at
runtime. Reproducibility means a stranger gets the same inputs.

**The query list belongs to the target, not to the run.** A target declares
`query_list` and the runner loads whatever it names without inspecting it, the
same way it refuses to know engine names. This exists because a shop and a
search engine must run in one time window to be comparable and cannot be asked
the same strings: Amazon asked "photosynthesis exam questions" returns
`s-no-results`, which is the search working, and nothing can separate that from
a soft refusal after the fact. `data/queries/amazon_1000.txt` is the product
list, built by the same generator and the same seed as `serp_1000` so neither is
privileged. `--query-list` still forces one list on the whole matrix when that
is the question being asked.

### Amazon answers four different ways, and two of them are 2 KB

**Amazon's `ok` rule is verified, its refusal rules are not.** Measured
2026-08-11, direct from the operator's line: a plain `requests` client with
browser headers received the full result list, HTTP 200, 919 KB, `s-search-result`
x18 and `s-main-slot` x1. So the pass rule rests on a real body, and the target
itself is soft - no browser, no proxy and no TLS work was needed to be let in,
which is a cost finding in its own right and the opposite end of the scale from
Google.

**The refusal Amazon actually serves is a throttle, and it was captured on
2026-08-12.** 23 archived bodies from `benchmark_20260812T111353Z` and
`...T112029Z`, read back offline at no traffic cost. Every one was 2,316-2,317
bytes: the "Sorry! Something went wrong!" stub, tagging its own links
`ref=cs_503` and linking `/dogsofamazon`. No captcha, no interaction gate,
nothing to solve.

The status is mostly but not always 503. Across the 54 refusals in those runs
plus `...T112846Z`, 47 answered 503 and 7 answered 200, and the byte counts come
in two clusters: 2,316-2,317 for the bodies above and 2,433-2,434 for a second
shape. So the rule keys on the markup and not on the status, the same as
everywhere else here. An earlier draft of this section asserted all of them were
HTTP 200 at 2,317 bytes; that was carried over from the Walmart finding and was
wrong on both counts.

**The second cluster was captured on 2026-08-13, and it is not a refusal at
all - it is Akamai Bot Manager.** 5 bodies from `benchmark_20260813T120848Z`,
direct arm, HTTP 200, 2,308-2,369 bytes. There is no stub text, no captcha
image, no interaction gate and no `ref=cs_503` anywhere on it. What there is:

    <meta http-equiv="refresh" content="5; URL='/s?k=...&bm-verify=AAQAAAAN...'">
    <script> var i = 1786622965; var j = i + Number("9598" + "22629"); </script>
    xhr.open("POST", "/_sec/verify?provider=interstitial", false)

`bm-` is Akamai, and the whole challenge is the script. A client that runs it
computes the token, POSTs it and is let through; a client that cannot is stuck
on a 2 KB page forever. So Amazon has at least three distinct answers - the
result list, the `ref=cs_503` throttle, and this - and the two small ones mean
opposite things. The throttle refuses the address. This one challenges the
client and offers it a way through.

`AmazonSearch.judge` now scores it `captcha`, on the same grounds as the
continue-shopping gate: there is something to clear. Keyed on `/_sec/verify` and
`bm-verify`, which name the mechanism, and placed after the result test for the
reason `WalmartSearch` documents - a served page carrying a dormant challenge
inline must read as served. Both markers separate the archived set completely
and in both directions: present on 5 of 5 interstitials, absent from all 4
served bodies and from every throttle body.

**A fourth answer arrived the same day, from a third vendor, and only the front
door could see it.** One body from `probehold_20260813T185327Z`, through the
pool, 2,005 bytes. Not the throttle, not Akamai, and carrying `ref=cs_503`
nowhere:

    window.gokuProps = {"key":"AQIDAHjcYu...","iv":"...","context":"..."};
    <script src="https://....us-east-1.token.awswaf.com/.../challenge.js">
    <div id="challenge-container"></div>
    AwsWafIntegration.getToken().then(() => { window.location.reload(true); });

This is AWS WAF, and it does what Akamai does: hands the client a token to
compute and reloads itself, so a browser clears it without noticing and a
scriptless client never gets past it. `AmazonSearch.judge` scores it `captcha`
for that reason, keyed on `gokuProps` and `awswaf`, after the result test like
its neighbours. Across all 296 archived Amazon bodies the two markers are on 1
of 1 of these and on none of the 67 served, 186 throttled or 21 Akamai bodies.
The reasons for the two vendors are deliberately different strings, so Amazon
moving between them shows up instead of averaging out.

Two things about how it was found are worth more than the rule.

- **It was served on the front page, so eight days of runs entering at `/s?k=`
  could not have met it.** The entry axis paid for itself on its second run, and
  it means the shapes catalogued here describe the search URL rather than the
  target.
- **It exists on disk only because `error` rows started keeping their bodies
  hours earlier.** The row it came from is verdict `error` - the query was never
  submitted, so nothing was judged - and before `keep_error_body` it would have
  read `Timeout waiting for element` with nothing behind it, indistinguishable
  from our own selector being wrong. One body is a shape and not a rate, and it
  is the difference between a known unknown and an invisible one.

**Re-judging the archive moved 21 historical rows, and every one belongs to a
scriptless client.** All 250 archived `amazon_search` bodies re-read offline at
no traffic cost: 21 rows move from `block` to `captcha`, and they are `http` 11,
`http-direct` 8, `curlcffi-direct` 2. **Not one browser.** That is the reading
confirming itself - a browser executes `triggerInterstitialChallenge()` and
never learns the page existed, so this interstitial is invisible to every engine
in the matrix that runs scripts, and it is the entire Amazon failure mode for
the two that do not.

It also disposes of the hypothesis that prompted the run. The proposal was that
**Amazon penalises the Chrome-shaped ClientHello**, which fitted every row then
on disk: camoufox (Firefox TLS) served, all Chromium-family engines throttled,
and `curlcffi` - Chrome's handshake with no browser behind it - refused on its
first request while plain `http` was served 40 of 45 direct. The test was those
two engines interleaved on one line in one window, 6 queries each, and it came
back **3/6 against 3/6**. A dead heat. Wearing Chrome's ClientHello buys nothing
at Amazon, and the first `curlcffi` refusal was the ordinary variance in that
50%. The handshake is now the wrong answer to this target for the second time,
and the variable that actually sorted these two engines' failures was whether
they run scripts.

**17 rows stay in the catch-all and their cause is known.** They are all
`camoufox-light`: 13-53 KB fragments with no title, no nav, no footer and no
result list, every one `ready=False`. This file already diagnosed them on
2026-08-12 as our own client giving up before the page finished, which is
`empty` by this repository's own definition. They are deliberately **not**
getting a rule, because the only thing distinguishing them is the absence of
everything, and a rule keying on absence is what the catch-all already is.
`ready` is on every row, so an analysis can split them out without a verdict
that would misfire on the first genuinely new refusal shape. What removes them
at the source is the blocking guard added the same day: they were produced by a
matrix where the preset applied to some engines and not others.

**That throttle is aimed at the browser, not at the address, and this section
said the opposite until 2026-08-12.** The claim was "through the pool Amazon
refuses the address, it does not challenge the browser", reasoning from a plain
`requests` client being served 919 KB direct. It was drawn from one arm with no
engine varied in it, and the first run that did vary one reversed it.

`benchmark_20260812T161720Z` put three engines through the same pool in the same
hour, round-robin, one fresh exit per attempt. Over every run on disk:

| engine | attempts | served | `ref=cs_503` throttle | ok given served |
|---|---|---|---|---|
| camoufox | 127 | **114** | **1** | 97/114 |
| patchright | 131 | 20 | **113** | 11/20 |
| chromium, the control | 60 | 10 | 38 | 5/10 |
| http, no browser | 35 | 25 | 0 | 14/25 |

Camoufox is served 90% of the time and has met the throttle once in 127
attempts. The two Chromium-family engines meet it on most of theirs, and
patchright - the engine that beats every other one at Google - is the worst of
the three here. The addresses cannot account for it: they are drawn from one
pool, interleaved, a distinct /24 per attempt.

So Amazon's cost sits on the browser after all, and it does not run along the
patching: Firefox is served, Chromium is throttled, patched or not.

**Every Amazon figure in this section is a reading of August 2026 on a
workstation, and the first run from the server disagrees with it.** A smoke run
on 2026-08-18, eight engines and one query each, was served on **6 of 7** engines
that reached the target, including the two Chromium-family engines this table
puts at 9% and 12%. Seven attempts is not a rate and this is deliberately not
entered as one - it is a flag on the table above, not a replacement for it. What
changed at the same time is the machine, the Chrome build, the locale baseline
and the entry into a new hour, so nothing here can even name which. The
eight-engine matrix running now answers it at 500 attempts per engine; until it
lands, quote the Amazon columns above with the date attached or not at all. The
`us = 0%` correction is the precedent: the direction survived replication and the
magnitude did not.

**It landed, and the table above does not survive it. Read this before quoting
any Amazon figure.** `benchmark_20260819T055927Z.jsonl`, the server, eight
engines against two targets in 16 interleaved cells, 7627 attempts over 130.2 h
from 06:00 UTC on 2026-08-19. Pass rate is over judged attempts, so the 12% of
attempts that never completed are excluded from it and shown separately.

| engine | amazon, pass | amazon, P(live) | P(pass given live) |
|---|---|---|---|
| **chromium, the unmodified control** | **96% (419/436)** | 96% (419/436) | **100% (418/419)** |
| rebrowser | 95% (420/440) | 96% (425/443) | 98% |
| botasaurus | 94% (419/444) | 96% (429/445) | 98% |
| camoufox | 92% (412/446) | 97% (431/446) | 95% |
| seleniumbase | 92% (426/464) | no status recorded | - |
| zendriver | 92% (371/404) | 96% (331/346) | 95% |
| cloak | 80% (352/439) | 81% (357/439) | 97% |
| **patchright** | **63% (288/457)** | 68% (313/457) | 90% |

- **The unmodified control is the best engine on this target**, at 96% with 100%
  of the pages it was served passing. The previous table had it at 10 of 60
  served, and the entire Amazon story in this file was built on Firefox being
  served where Chromium was throttled. **That story is gone.** Camoufox is at 92%
  and so are three Chromium-family engines; the spread that used to run from 90%
  to 9% now runs from 96% to 63%, and the top six engines sit inside four points
  of each other. Two different browser families, patched and unpatched, are now
  indistinguishable here.
- **What survived replication is the direction on patchright and nothing else.**
  It was the worst Chromium-family engine at 9% and it is the worst engine here
  at 63%. The `us = 0%` precedent holds again, and this time it is the whole of
  what holds: the ranking of one engine survived, the magnitude moved by 54
  points, and every other engine's position changed.
- **Patchright's loss is the address, not the page.** 90% of the pages it was
  served passed, against 100% for the control - so the gap is almost entirely in
  P(live), 68% against 96%. Whatever Amazon is doing to it happens before a page
  exists. The old reading, that Chromium-family engines meet a throttle aimed at
  the browser, would predict the opposite split.
- **Nothing here names the cause and the run cannot.** The machine, the Chrome
  build, the locale baseline and the calendar month all changed between the two
  tables, exactly as the paragraph above warned, and this run varies none of
  them. It replaces a set of numbers; it does not explain why they moved.

**Google collapsed to almost nothing in the same window, and that is the larger
result.** 19 live responses in 2673 attempts, 1%, against the 13-62% by country
that the yield table records for August 12. Every engine is at 0% or 1%, so
nothing separates them:

    botasaurus  ok=5 captcha=452     camoufox   ok=1 captcha=451
    seleniumbase ok=3 captcha=452    cloak      ok=1 captcha=436
    rebrowser   ok=2 captcha=433     patchright ok=1 captcha=441
    zendriver   ok=1 captcha=457     chromium   error=500, cell stopped

The country table is not being corrected by this, because it is not the same
experiment - `country=any` here against a country axis there, six days later, on
another machine. What it does mean is that **the engine axis is unreadable at
this target right now**: at 1% the difference between one engine and another is
one or two pages, and no comparison can be built on that. A Google engine
comparison needs the probe-and-hold protocol, which finds an exit that will serve
at all and then holds it, rather than a matrix spending 450 fresh identities per
engine to be refused 450 times.

**The control's Google cell is `unmeasured`, not `0%`, and the breaker is why.**
It was stopped after 500 consecutive failures having never been served a single
body, so there is no denominator to divide by and reporting it as a zero would
invent one. This is the breaker doing the job the operational-safety section
describes, and it is the first time a cell in this repository has been stopped
for the whole of its run.

One caveat on the whole table. This run straddles 21:00 UTC on 2026-08-19, the
moment the gateway's IP-ban counter started being reset by an authenticated
request, so its 12% error rate pools two transports and must not be quoted as
one. The pass rates are read over judged attempts, which excludes `error` by
construction, and that is what keeps them comparable across the boundary.

The obvious candidate is the TLS handshake, which no JavaScript patching reaches
and which Camoufox gets for free by being Firefox. Two rows already on disk
argue against it, and both sat in this file's own tables while it called TLS the
only explanation on offer. Verdicts, pool arm only, so the direct arm cannot
flatter anything:

| engine | amazon_search | ddg_serp |
|---|---|---|
| camoufox | **97/115** | **62/62** |
| obscura | 0/3, no data | **44/44** |
| http, no browser | **14/29** | 0/16 |
| chromium, the control | 5/43 | 0/27 |
| patchright | 11/124 | 7/30 |

- **Obscura is Chromium and DuckDuckGo serves it 44 of 44**, where the two
  Playwright-driven Chromium engines get 0 and 23%. The browser family alone
  does not sort these.
- **A plain `requests` client passes Amazon 48% through the pool**, five times
  patchright's rate. Its handshake is the least browser-shaped thing in this
  repository, so a rule selecting on handshake shape would refuse it first.

The two targets do not agree with each other either: `obscura` and `http` swap
ends between them, so they cannot be read as one phenomenon. The only property
shared by the losers on both is **Chromium driven by Playwright or Patchright** -
not Chromium as such, and not the handshake on its own. Obscura drives raw CDP,
Camoufox is Playwright over Firefox, and both are served.

### The handshake was read, and it is not the discriminator

Measured 2026-08-12 by `scripts/probes/tls_echo.py`, direct against a TLS echo,
one request per engine. It sends nothing to any target and touches no gateway,
and `direct` costs nothing in accuracy: a CONNECT proxy tunnels TLS end to end,
so the ClientHello that arrives is the one the engine emitted either way.

| | http | chromium | camoufox | patchright | obscura |
|---|---|---|---|---|---|
| JA4 | `t13d1812h1_85036bcba153_375ca2c5e164` | `t13d1516h2_8daaf6152771_d8a2da3f94cd` | `t13d1617h2_86a278354501_3cbfd9057e0d` | **same as chromium** | **same as chromium** |
| peetprint | `94e481d3` | `1d4ffe9b` | `fd4547ee` | `1d4ffe9b` | `1d4ffe9b` |
| h2 akamai | none, HTTP/1.1 | `52d84b11` | `6ea73faa` | `52d84b11` | `52d84b11` |
| ciphers / extensions | 18 / 12 | 16 / 18 | 16 / 17 | 16 / 18 | 16 / 18 |
| GREASE | none | cipher + 2 ext | **none** | cipher + 2 ext | cipher + 2 ext |

**Chromium, Patchright and Obscura are one handshake.** Identical JA4, identical
peetprint, identical HTTP/2 fingerprint, identical cipher and extension counts.
Obscura synthesises its ClientHello and the other two emit a real Chrome one, and
the result is indistinguishable to anything reading this layer.

That settles DuckDuckGo against the TLS explanation, and not by inference. Both
`20260811T154830Z` and `...T213030Z` ran all four engines round-robin in one
window through one pool:

| engine | ddg_serp, shared windows | JA4 |
|---|---|---|
| obscura | **44/44** | chromium's |
| camoufox | **39/39** | Firefox's |
| chromium | **0/24** | chromium's |
| patchright | **0/23** | chromium's |

Byte-identical handshakes, 44 of 44 against 0 of 47, same hour, same pool.
Whatever DuckDuckGo is sorting on, it is above the ClientHello.

Amazon is not explained by the handshake either, and it fails in the opposite
direction. The plain client has the least browser-shaped ClientHello here - no
GREASE, 12 extensions, no HTTP/2 offered at all - and Amazon serves it 48%
against Patchright's 9%. A rule keying on handshake shape would refuse that
client first.

Two cautions for anyone re-running this:

- **Compare JA4, never JA3.** The three Chromium engines show three different
  JA3 hashes, and chromium alone gave `f1fbad15`, `f269b51d` and `b0c8ea09` on
  three consecutive connections. Chrome shuffles extension order per connection;
  JA3 preserves order and JA4 sorts it. A JA3 difference between two Chromium
  engines is noise, and reading it as signal would have rescued the very
  hypothesis this probe refutes.
- **Camoufox sends no GREASE**, where all three Chromium engines do. It is a
  genuine Firefox handshake and Firefox does not use GREASE, so this is correct
  behaviour and not a defect. It is worth noting only because "no GREASE" is
  otherwise the standard tell for tooling, and here it appears on the engine
  with the best pass rates in the repository.

So the handshake is measured, it is shared by engines whose pass rates differ by
44 to nothing, and it explains neither target.

### DuckDuckGo is reading the User-Agent, and the split is total

The next candidate was the driver: Playwright-driven Chromium loses on both
targets, Obscura drives raw CDP and wins. `engine_fingerprint.py`, read
2026-08-12, refuses it. `binding_leaks` is null and `stack_leak` is `no` on all
four engines, Playwright-driven and not. Whatever else separates them, the page
cannot see the driver through either marker.

The same table answers DuckDuckGo outright, in a row that has been sitting in
this file since 2026-08-11. Pool arm, verdicts, split by the one setting that
changes the User-Agent:

| engine | headless | User-Agent | ddg_serp |
|---|---|---|---|
| camoufox | headless | `Firefox/152` | **44/44** |
| obscura | headless | `Chrome/145` | **44/44** |
| patchright | **headful** | `Chrome/149` | **7/7** |
| patchright | headless | `HeadlessChrome/149` | **0/23** |
| chromium | headless | `HeadlessChrome/148` | **0/27** |

95 of 95 for the engines whose User-Agent does not say `HeadlessChrome`, 0 of 50
for the two that do. Fisher exact p = 4e-40. The `HeadlessChrome` substring is
the only property that sorts this table: it crosses the browser family, it
crosses the patching, and it crosses the driver.

**Patchright is the control on itself.** Same engine, same patches, same pool,
7/7 headful against 0/23 headless, p = 5e-07. Nothing else about it moved.
Obscura is the other half of the argument: it is headless, it shares Chromium's
JA4 exactly, and it passes 44 of 44 - because it reports `Chrome/145` rather
than announcing the mode.

This is the marker this file already called the one no patching reaches. It
turns out to be sufficient on its own for one target in the matrix. The
consequence for any run: **a headless Chromium-family engine is not measuring
the target, it is measuring the User-Agent**, and a matrix that leaves it
headless has spent its budget on that.

Two limits on how far this reads:

- The headful Patchright rows come from `...T095540Z`, where it ran alone, so
  the within-engine comparison is across windows and not interleaved. Camoufox
  and Obscura are in-window against the headless Chromium engines and carry the
  weight; Patchright is confirmation, not the load-bearing evidence. A single
  run with `patchright` headful and headless in one matrix would close it.
- The plain HTTP client is 0/12 through the pool and 45/45 direct, with a
  User-Agent that never says `HeadlessChrome`. So DuckDuckGo has a second
  refusal path that keys on the address, and this rule describes the browser
  arm only.

**Amazon is not this.** Patchright headful is 11 of 115 there, with the same
clean `Chrome/149` that carries it through DuckDuckGo, while Camoufox is 77 of
90 headful and 20 of 25 headless. Amazon is unexplained by the handshake and
unexplained by the User-Agent, and Chromium-family loses it in both modes. The
two targets were never one phenomenon and this is the third measurement saying
so.

One count in that table needs reading carefully. Camoufox's 17 non-`ok` served
rows are not refusals: they are 13-53 KB pages carrying the nav and no result
list, every one of them `ready=False` where all 97 passes are `ready=True`. That
is our own client giving up before the page finished, which is `empty` by this
repository's own definition, and the catch-all `block` fallback absorbed it. The
narrowness rule cuts both ways and it was only ever written for one direction.

It also corrected a rule that was passing for the wrong reason. The refusal had
been caught by `something went wrong on our end`, which is the alt text of an
image on that page - a coincidence that would have quietly moved every one of
these into the catch-all fallback the day Amazon reworded the sentence. The rule
now keys on `ref=cs_503`, which names the throttle instead of describing the
symptom, and `test_the_throttle_is_named_rather_than_matched_by_prose` pins it.

The remaining interstitial rules were still written from public descriptions and
have never fired. Capture one before any Amazon captcha number leaves this
repository. Two are load-bearing: the character-entry captcha is served from
`/errors/validateCaptcha`, so the captcha test has to run before the `/errors/`
test or every captcha is filed as a block; and `continue shopping` is an
interaction gate rather than a refusal, recorded as `captcha` because an engine
that clears one may not clear the other.

A trap in the same body: the passing page contains the string `consent` twice.
A naive consent rule of the kind Google needs would fire on a perfectly good
Amazon result list. This is the Bing "captcha appears in the telemetry of a good
page" problem again, and it is why marker rules get read off a body first.

### Walmart is fronted by PerimeterX, and the lock is on the address

**Walmart is the third target, and all of its rules were read off bodies.**
Measured 2026-08-12 by `scripts/probes/walmart_recon.py`, 18 responses direct
from the operator's line plus 15 through the pool. It was chosen because it
holds the vertical and the query list fixed against Amazon and changes only the
defence in front of them: Amazon runs its own stack, Walmart is fronted by
PerimeterX. Every one of the 33 responses arrived as HTTP 200, refusals
included, so no rule consults the status.

It is the only target here that exercises two layers from one address:

| arm | direct | note |
|---|---|---|
| `http`, plain requests | 0/5 | challenged before any page logic, so the refusal is on the handshake or the headers |
| `chromium`, the control | 1/5 | served the first query, challenged for the rest of the session |
| `patchright` | 5/5 | held the whole session |

That first row is the TLS layer, which the README claims to measure and which
neither Google nor Amazon ever exercises. It is also the only place Obscura's
synthesised handshake can be shown to buy anything, and it is measurable direct,
with no gateway and no pool reputation spent.

Through the pool, `country=us, filter=medium`, patchright, 3 sessions of 5:
15/15, three different /24s. So Walmart's difficulty is the browser, not the
address - the exact opposite of Amazon, where the address is everything and a
plain client is served, and of Google, where 86% of US exits are already dead.

**The PerimeterX lock is on the address and survives a browser change**, which
this file recorded as a session property until 2026-08-14. Measured that day by
`benchmark_20260814T102344Z`, four engines on one direct address in eight
minutes: the first query of the window was served a genuine 1.87 MB result page
and every one of the following 19 was the hold-button challenge, **including the
first query of each of the three browsers launched afterwards**. A fresh process
clears nothing. The earlier reading - "served the first query, challenged for
the rest of the session" - happened to be taken on a run where the session and
the address ended together.

The operational consequence is that **the direct arm cannot compare engines on
this target at all.** One address that locks after a query hands the result to
whichever engine is scheduled first, and no amount of interleaving fixes that,
because the thing being consumed is not per-engine. A Walmart engine comparison
has to be run through the pool with a fresh exit per session, which is also the
arm where the target is passable at all.

**A cell count of one defeats the interleaving, and the scheduler is not what is
wrong.** That run set `--batch 5` with `--queries 5`, which is exactly one batch
per cell, so the round-robin had nothing to alternate and walked the engines in
sequence. Interleaving is what makes two engines comparable, and here it was
lost to a pair of flags rather than to a code path. The runner now says so
before it starts.

**The Walmart trap, and it is the worst one so far.** A served 2.1 MB page
carrying 63 products also carries the entire PerimeterX modal inline and hidden:
the heading "Robot or human?", the prompt text, and `id="px-captcha"` holding a
live token. The string `recaptcha` appears twice on every single passing page,
and `consent` seven times. Any challenge test placed before the result test
scores a perfectly good page as a captcha. Hence the rule order in
`WalmartSearch.judge`: result stack first, challenge second, and
`tests/test_targets.py::TestWalmart::test_a_served_page_carrying_the_dormant_challenge_is_still_ok`
fails if anyone swaps them.

Two markers separate the set cleanly and in opposite directions, so neither the
ordering nor a single string is load-bearing on its own:
`data-testid="item-stack"` is 1-2 on all served and 0 on all refusals;
`class="re-captcha"` is 1 on all refusals and 0 on all served.

Zero-result pages needed their own rule, the same way `s-no-results` does on
Amazon. Two of three nonsense queries returned `0 results for "..."` with **no**
product markers at all, so the obvious "product tiles present" rule would have
filed a working search as a refusal.

`needs_script` is True for Walmart as a conservative default, not a
measurement: no scriptless client has ever got past the handshake, so whether
the markup survives without JavaScript is unknown, and the wrong guess would let
our own preset masquerade as the target's refusal.

### What a run costs, and why one constant could not track it

**Estimator constants are calibrated, not guessed, and bytes are per target.**
`matrix.estimate` prices traffic from `MEASURED_BYTES`, keyed by target name,
each entry carrying the run it was read from by `scripts/analysis/calibrate.py`.
Keying on the target is a table lookup and not the runner branching on a name:
nothing behaves differently, only the arithmetic does.

The single global byte constant was not stale, it was the wrong shape. Measured
2026-08-12, two runs in one afternoon:

| run | batch | ok | KB per attempt | s per attempt, wall |
|---|---|---|---|---|
| google_serp `130452Z` | 1 | 6% | 776 | 40.2 |
| walmart_search `141501Z` | 5 | 100% | 2109 | 12.8 |
| old global constant | - | - | 275 | 25 |

The two quantities move independently and in opposite directions. Bytes follow
the target and its verdict mix - a refused Google page is small, a served
Walmart page is 1-2 MB - while wall clock follows `--batch`, because a browser
launch is per session and not per attempt. One constant cannot track both: the
old figure predicted 3.9 MB for the Walmart run that cost 30.2 MB, while being
roughly right for Google the same afternoon.

A target with no run behind it is priced from `DEFAULT_BYTES`, deliberately not
the mean of the table - an unmeasured target should read as cheap and unknown
rather than borrow authority from a number taken from somewhere else. The
estimate returns a `basis` naming which targets were measured, and the runner
prints it, so a mixed matrix cannot imply a precision half of it does not have.

Time is still one blended constant and is knowingly wrong in both directions: it
runs about 20% short at `--batch 1` and about half long at `--batch 5`, and the
per-session launch is blended into the per-attempt figure and then added a
second time. Fitting a per-attempt and a per-session term to the two runs above
is arithmetically easy and was rejected: they differ in target, batch size and
verdict mix at once, so two points cannot separate three effects and the result
would be exactly the confident wrong number this repository exists to avoid.
Read the hours as an order of magnitude. Re-run calibrate after any run that
changes the shape, and add a `MEASURED_BYTES` entry the first time a new target
completes a real run.

## Operational safety

**Circuit breaker is mandatory.** A run of consecutive failures heats the pool: each
retry confirms automation to the target and degrades the exit ranges for every other
customer. Rule: N consecutive failures on a matrix cell stops that cell, marks it in
the report and backs off exponentially. Never implement naive "error -> new sid -> retry".

N is not one number across the repository, and that is deliberate. `--breaker`
defaults to 10 in the matrix runner, because a cell that refuses 4 times and then
passes is a partial refusal and stopping at 5 records it as a total one - the
benchmark is measuring the shape of the refusal, so it has to see enough of it.
`CircuitBreaker` itself still defaults to 5, because `probes/google_429.py` uses
that default and every google_429 run in `data/runs/` was measured at it; raising
it there would silently make new rows incomparable with the committed ones.
Whichever N is in force, the cost is N confirmed-automation retries per cell
against a target that has already refused, so raising it is a pool-safety
decision and not a patience one.

**The run-level watchdog was tuned against a floor that has since moved, and it
stopped a healthy run.** `TransportWatch` stops the whole matrix when a recent
window is mostly `error` across several cells, which is the only way a shared
transport failure can be told from a cell that is genuinely refused. Its share
was 0.6 over a window of 20, and on 2026-08-19 that ended an eight-engine run at
attempt 564 of 8000, after eight hours in which nothing had changed: the error
rate was 33% in the first hour and 34% in the eighth, all of it
`ERR_EMPTY_RESPONSE` from the gateway's unanswered-CONNECT floor, spread evenly
over all sixteen cells.

The threshold is an absolute line, so what it really asks is whether a window is
improbable under the run's own error rate - and it does not know that rate. At
34%, 12 errors in 20 has probability about 0.017, which is rare once and certain
across the 8000 windows of a four-day run. The watchdog was guaranteed to fire.
It is now 0.85, about 3e-7 per window at the same baseline, and detection costs
nothing: a transport failure is not 60% errors, it is essentially all of them -
every interception and every dead gateway in `data/runs/` reads near 100% - so
17 of 20 still trips inside one window, about 17 minutes at the observed pace.

The constant matters less than the shape of the mistake. **A fixed threshold
cannot tell "the errors are new" from "the errors have always been this high",
and the second is the normal condition on this gateway.** If the floor moves
again this number has to be re-derived against it rather than nudged, and the
two tests beside it are written as that derivation: one drives the watch at the
measured floor for a full run length and requires it not to trip, the other
drives a total failure and requires it to trip inside one window.

**The floor it was re-derived against disappeared the day after, and 0.85
stays.** The 34% baseline was the IP ban of the next-but-one section; from 21:00
UTC on 2026-08-19 the same uninterrupted run reads 9.4%, and the fraction that
was `ERR_EMPTY_RESPONSE` went from 18.3% to 0.10%. A threshold derived against a
baseline that then falls errs in the safe direction - it is now roughly 1e-19 per
window rather than 3e-7, and a genuine transport failure is still near 100% and
still trips inside one window. What it does mean is that **0.85 is not evidence
about the current gateway**, it is a number carried over from a condition that no
longer exists, and the sentence above about the normal condition on this gateway
described August 2026 and not the gateway.

**N=10 is now measured rather than argued.** Read 2026-08-12 over every
`benchmark_*.jsonl`, 129 cells and 1464 attempts, as the chance an attempt
succeeds given how many failures came immediately before it in its own cell:

| failures before | attempts | ok | P(recover) |
|---|---|---|---|
| 0 | 632 | 474 | 75% |
| 1 | 155 | 33 | 21% |
| 3 | 102 | 6 | 5.9% |
| 5 | 69 | 4 | **5.8%** |
| 6 | 63 | 1 | 1.6% |
| 7-9 | 185 | 1 | 0.5% |

Both halves of the documented choice survive contact with the data. Stopping at
5 would be wrong: recovery is still 5.8% there, so a cell that refuses five
times and then passes is a real partial refusal and truncating it records a
total one. Going far past 10 is also wrong: from the sixth failure the rate
collapses to about 1.5% and never recovers. 294 attempts in the whole history
were spent at six or more consecutive failures and they returned 3 pages - 98
attempts per delivered page, against 1.7 in a healthy cell, and every one of
them a confirmed-automation retry on a shared production pool.

Two limits on reading this. The buckets at 10 and 11 hold 4 and 3 attempts
because the breaker stops there, so the tail is censored by the very mechanism
being evaluated and only runs with a raised limit populate it at all. And
recovery after a long streak is not impossible, just priced absurdly: the
longest failure run ever followed by an `ok` was **19**, once, in
`patchright/us/filter-medium` on Google.

The residual bias is downward and small - a stopped cell loses that 1-2% tail -
and `report.py` marks those cells so the number never presents itself as a
complete sample.

**Pause between requests.** 3-5 seconds minimum during exploration. This is a shared
production pool on a company account, not a lab.

**Never print or paste `Proxy-Authorization` headers.** It is base64, not encryption.
Anything pasted into Slack, an issue or an article is a leaked credential. Strip auth
headers from any verbose output before sharing.

**Budget awareness.** 100 GB total quota shared across all pool types. Estimate cost
before launching anything above ~100 requests.

**A run must not die on a string it printed, and one did.**
`probehold_20260813T201805Z` stopped after four identities of a twelve-cell,
three-hour run with a `UnicodeEncodeError`. Nothing about the measurement
failed: the gateway answered, the exit was live, the row was already written,
and then the progress line carried the exit's ASN organisation name into a
console whose encoder is the system codepage. cp1251 is Cyrillic and has no
`U+00DA`. On a `country=any` run drawing exits worldwide, a Latin-1 accent in an
ASN name is not an edge case, it is a question of how many identities go by
first - the fourth, here.

`console.tolerate_unencodable_output` reconfigures the handles with
`errors="replace"`, and every script that prints an ASN name calls it **before**
importing the engines: zendriver pulls in colorama, whose stdout wrapper has no
`reconfigure` of its own and would leave the original handle strict.
`errors="replace"` rather than a switch to UTF-8 because that handle usually
carries a redirect to a log file, and changing its encoding partway would leave
one file written in two. The mangled character costs nothing - `exit_org` is in
the JSONL row in full, and that is what analysis reads.

The general rule this is an instance of: **text from the far end is data, and
printing it is an operation that can fail.** ASN names, final URLs and exception
messages are all written by somebody else. `test_a_script_printing_a_remote_name_survives_the_console`
derives the list of scripts from the source rather than naming them, and found a
sixth on its first run.

## Code conventions

- Domain types are plain structures: no DB tags, no HTTP tags, no save methods
- DTOs live at the boundary and are mapped by hand
- Credentials are read in `config.py` and nowhere else, always from the environment
- Every experiment writes JSONL through `sink.py`, one row per attempt, including
  the full parameter set used - results that cannot be attributed are worthless
- Errors carry the consequence, not just the symptom: say what will happen to the
  user, not that a value is invalid
- After the main code: Makefile, linter config, tests, README explaining decisions

## Tests

`tests/` is offline in full: no network, no credentials, no browser. The autouse
fixture strips the gateway variables from the environment, so a test that tried
to open a connection would fail rather than spend traffic.

What is covered, and why each one is worth a test:

- verdicts, including the scaffold-versus-`/sorry/` discrimination. Every number
  the benchmark produces is a count of verdicts, so a wrong verdict function does
  not produce a wrong number, it produces a confident wrong number
- the username DSL, because the gateway's own reaction to a mistake is useless
  (200 with the setting silently dropped) or misleading (407 for a bad `filter`)
- the scheduler: interleaving is what makes two engines comparable, and resume is
  what stops an interrupted run from re-sending answered queries
- the circuit breaker, including that `error` counts toward tripping
- the engine contract: same interface, same columns, so the runner cannot tell
  the frameworks apart
- that the control is still unmodified. `TestTheControlIsNotHardened` reads the
  source of `ChromiumEngine.open` and fails on `args=` or `user_agent`. Someone
  will eventually try to make the control pass more often; that is a helpful
  instinct everywhere else in the repository and it destroys the baseline here
- the artifact store: which verdicts are kept, that bodies are attributed to the
  row that produced them, and that an unwritable directory loses the archive and
  not the run. A full disk five hours into a run must cost the evidence, never
  the measurement
- repository hygiene: the whole tree compiles, every argparse script still runs
  from its folder after a move, no credential is hardcoded, no authorization
  header is ever printed, no em-dashes, no Cyrillic in source

Most of this repository is scripts, and a script is only compiled when it is run.
Without the syntax check a typo in `scripts/probes/` is found by an operator who
has already spent traffic getting to it.

## Style

- English in code, comments, commit messages and documentation
- Hyphens, never em-dashes
- No marketing language. This repository is read by people who will fork it.
