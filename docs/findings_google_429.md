# Google SERP returns 429 through NodeMaven residential

Measured 2026-08-10 and 2026-08-11. Raw rows in `data/runs/google_429_*.jsonl` and
`data/runs/availability_*.jsonl`. One discarded run is kept in
`data/runs/invalid/` with the reason it cannot be used.

## Question

Google SERP returned 429 on 6 of 6 requests through Camoufox and a US
residential exit, with no warm-up. At which layer is the rejection made, and is
Google SERP a viable target for an open-source scraper?

## Result table

Verdicts come from response content. `js-required` is derived in
`scripts/analysis/analyze_429.py` from the markers stored with each row: it is an HTTP
200 carrying Google's "enable JavaScript" page, which is neither a served result
nor a rejection.

| Step | Condition | Engine | n | Verdicts | Distinct exits |
|---|---|---|---|---|---|
| 1 | Google, us, one sticky session | http | 5 | captcha 3, js-required 1, error 1 | 1 |
| 2 | Google, us, fresh sid each | http | 5 | captcha 3, js-required 1, error 1 | 4 |
| 2 | Google, gb/de/fr/ca/jp, fresh sid each | http | 5 | js-required 4, captcha 1 | 4 |
| 3 | DuckDuckGo, us | http | 5 | captcha 4, error 1 | 1 |
| 3 | Bing, us | http | 5 | captcha 4, error 1 | 1 |
| 4 | Google, **no proxy**, local residential line | http | 6 | js-required 3, error 3, rejections 0 | 1 |
| 5 | Google, one session, five queries | camoufox | 5 | captcha 5 | 1 |
| 5 | Google, five sessions, one query | camoufox | 5 | captcha 4, js-required 1 | 3 |
| - | Google, fresh exit each, interleaved | camoufox | 5 | **ok 0, captcha 5** | 5 |
| - | Bing, fresh exit each, interleaved | camoufox | 5 | **ok 5** | 5 |
| - | DuckDuckGo, fresh exit each, interleaved | camoufox | 5 | **ok 5** | 5 |
| - | Google, **same exit as Bing and DDG** | camoufox | 10 | **ok 0**, captcha 8, js-required 1, error 1 | 10 |
| - | Bing, same exit as Google | camoufox | 10 | **ok 9**, error 1 | 10 |
| - | DuckDuckGo, same exit as Google | camoufox | 10 | **ok 10** | 10 |
| - | Google, headful, no resource blocking | camoufox | 3 | **ok 0**, captcha 2, error 1 | 3 |

Seven cells were stopped by the circuit breaker after five consecutive failures.
No cell was retried with a rotated sid.

## The same address, judged by three targets

`availability.py --paired` reused one sticky session for a whole round, so every
target in that round was served by the same exit. Seven rounds across two runs,
raw rows in `data/runs/availability_20260810T184646Z.jsonl` and
`data/runs/availability_20260810T190251Z.jsonl`.

That script no longer exists: it was Camoufox-only and a strict subset of what
`scripts/benchmark.py --engines camoufox --targets ... --batch 5` does, and a
second runner that could drift from the first is worse than no second runner.
The rows it wrote are kept, because a measurement outlives the instrument.

| Exit /24 | Operator | Google | Bing | DuckDuckGo |
|---|---|---|---|---|
| 97.140.216.0/24 | Verizon Business AS6167 | js-required | ok | ok |
| 73.133.184.0/24 | Comcast AS7922 | captcha | ok | ok |
| 98.41.176.0/24 | Comcast AS7922 | captcha | error (timeout) | ok |
| 174.101.164.0/24 | Charter AS20001 | captcha | ok | ok |
| 172.59.190.0/24 | T-Mobile AS21928 | captcha | ok | ok |
| 67.146.7.0/24 | Brightspeed AS19901 | captcha | ok | ok |
| 66.252.213.0/24 | Five Area Systems AS29843 | captcha | ok | ok |
| 98.61.190.0/24 | Comcast AS7922 | captcha | ok | ok |
| 50.114.14.0/24 | Ace Data Centers AS11798 | error (timeout) | ok | ok |
| 38.178.195.0/24 | Vyve Broadband AS35986 | captcha | ok | ok |

The last three rounds ran headful, with no resource blocking - a real window, a
real GPU, every resource allowed. Not a one-variable experiment and not meant to
be: it is an upper bound. If the best configuration available to this harness is
refused, the intermediate ones do not need testing.

This is the comparison the earlier rounds could not make. Eight of the ten
addresses were refused by Google in the same minute that Bing and DuckDuckGo
served them results through the same browser, the same session and the same
tunnel. Of the two that were not, one is the no-JS scaffold described below as an
instrument defect and the other is a 60-second navigation timeout. Neither is a
served result. Google's refusals carry `ready=false`: the harness waited for the
results container and it never appeared, because a rejection page has none.

Two details from these rounds are worth keeping.

**HTTP status is not evidence.** The refusal at 38.178.195.0/24 arrived as
**HTTP 200** with the identical body as the 429s - final URL `/sorry/index`,
`recaptcha` 5, `unusual traffic` 1. Same page, same rejection, different status
code. Any harness keying on status would have scored it as a success.

**The sticky session does not always stick.** In that round all three targets ran
under one sid with an identical parameter set, which is what `--paired` exists to
guarantee. Google and Bing were served from 38.178.195.0/24 Vyve Broadband; the
lookup before the DuckDuckGo attempt, seconds later, came back
172.56.252.0/24 T-Mobile - a different address on a different operator. The
gateway was asked for "keep IP as long as possible". Either the session moved
inside one minute, or the address lookup does not report the address the next
request will use. Both readings weaken exit attribution, and the harness cannot
currently tell them apart.

The three errors in step 4 are an earlier attempt at the direct control in which
every connection to Google failed instantly, before any request was sent. The
three `js-required` rows are the attempt that went through. Both are kept: the
control is only meaningful with the state of the local network on the record.

Exit operators observed, masked to /24 in the raw rows: Comcast AS7922,
Charter AS20001/AS33363/AS11427/AS12271, Verizon AS701/AS5650, AT&T AS7018,
T-Mobile AS21928, Midcontinent AS23260, PenTeleData AS3737, Brightspeed AS19901,
Cox AS22773, Tachus AS397412, CENIC AS2152, Ace Data Centers AS11798,
Verizon Business AS6167, Five Area Systems AS29843, Vyve Broadband AS35986,
Deutsche Telekom AS3320, Free SAS AS12322, BT AS2856, Videotron AS5769,
TELUS AS852, NTT Docomo AS4713, KDDI AS2516.

## Which layer

**IP reputation, and specific to Google.**

> **Amended 2026-08-11.** This section was written before the direct control was
> run with a browser rather than a plain client. The address is still sufficient
> to be refused, but it is not necessary: Camoufox was refused 3 of 3 from a
> clean home line that the plain client had not been refused from one minute
> earlier. See "Correction: two sufficient causes" below. Point 1 as originally
> written rests on a misreading of the direct control and is struck through
> there. Points 2 and 3 stand.

Three independent observations converge:

1. ~~**The client is not the cause.**~~ The same plain HTTP client, with the same
   queries and the same headers, was rejected from proxy exits and never once
   rejected from the local residential line. Direct requests returned Google's
   JavaScript page - an invitation to run a browser, not a refusal. Only the
   address changed between the two conditions.

   *This reads a non-refusal as an acceptance. Google never served that client a
   result either; it served the no-JS scaffold, which the harness was at the time
   scoring as `block`. A client Google will not give results to under any
   circumstances cannot be used to prove that the address is what got refused.
   The correct control is a browser, and it was refused.*

2. **The browser does not help.** Camoufox with JavaScript enabled scored 0 of 5
   against Google across five different exit addresses, and 0 of 4 again in the
   paired rounds, including three rounds headful with no resource blocking.
   Counting every Camoufox attempt against Google in this investigation - 6 in
   the original run, 10 in step 5, 5 in the availability run, 10 paired - the
   result is 28 rejections out of 30 judged attempts. Neither exception was a
   success: both returned Google's no-JS scaffold, one because resource blocking
   had removed the scripts and one because the harness snapshotted the page too
   early. The rejection arrives before the page renders, so no browser signal has
   been transmitted at the point it is made - which is why a headful window with
   a real GPU changes nothing.

3. **The pool is not globally burned.** In the same time window, on interleaved
   round-robin, the same pool scored 5 of 5 against Bing and 5 of 5 against
   DuckDuckGo through the same browser. Comcast, Charter and Verizon ranges
   appear on both the rejected-by-Google and the accepted-by-Bing side. The
   paired rounds tighten this from operator level to address level: the very
   address Bing had just served was refused by Google seconds later.

This rules out one competing explanation and, as of 2026-08-11, only weakens the
other. It is not a dead or universally flagged pool: the same operators serve two
other search engines through the same tunnel minutes apart. The claim that it is
not browser fingerprinting was too strong - see the correction below. What
observation 2 actually shows is that a real browser does not *rescue* a refused
address, which is a different statement from the browser being innocent.

A separate result for the control targets, in the opposite direction: Bing and
DuckDuckGo rejected the plain HTTP client 4 times out of 4 each - Bing with a
challenge interstitial under HTTP 200, DuckDuckGo with a 202 anomaly page - and
then accepted the same pool through Camoufox 5 times out of 5. Their gate is on
the client shape. Google's is on the address.

## Correction: two sufficient causes, not one

Measured 2026-08-11. The control that had been missing is a **browser on a clean
line**: everything before this ran the direct control through the plain HTTP
client, which Google will not serve results to under any circumstances, so it
could never have distinguished "the address was accepted" from "the client was
never in the running".

The clean isolation, two runs one minute apart from the same address:

| Time (UTC) | Client | Address | Result |
|---|---|---|---|
| 09:55:56 | plain HTTP | `37.230.157.0/24`, home line | stayed on `/search`, 92 KB scaffold, 3 of 3 |
| 09:56:56 | Camoufox | `37.230.157.0/24`, same line | redirected to `/sorry/index`, 3 of 3 |

Counting every attempt in the investigation, split by what actually came back:

| Condition | `/sorry/` refusal | no-JS scaffold | ok |
|---|---|---|---|
| plain HTTP, direct, home line | 0 of 3 | 3 | n/a |
| plain HTTP, via proxy | 7 of 13 | 6 | n/a |
| Camoufox, via proxy | 33 of 33 judged | - | 0 |
| Camoufox, direct, home line | 3 of 3 | - | 0 |

Read row by row: the address alone is sufficient, because the plain client was
refused only when it went through the proxy. The browser alone is also
sufficient, because Camoufox was refused from a line that had just not refused
the plain client. Neither is necessary. Two independent triggers, either of which
is enough on its own, which is why removing one of them changes nothing.

**Caveat, and it is a real one.** The direct line is a Moscow residential
connection (AS25513, MGTS). Google treats RU consumer ranges harder than US ones,
so the Camoufox-direct result may be the address again rather than the browser.
Settling it needs a clean US residential line, and the only one available here
would be a VPN, which is a datacenter address and therefore a worse control than
the one we have. This is stated as a limitation rather than worked around.

### Second caveat: the browser that was refused is Firefox

Every attempt in the table above used Camoufox, which is a patched Firefox. So
"the browser alone is sufficient" is strictly "*this* browser is sufficient", and
the two readings it cannot separate are:

- Google refuses automation signals, whatever renders them, or
- Google refuses this Firefox build, or Firefox on that line, for reasons that
  have nothing to do with automation

This was not answerable until 2026-08-11, because the harness had no browser in
it that was not an anti-detect framework. It now has three more - an unmodified
Chromium, Patchright, and Obscura - so the direct control can be re-run across
two engine families from the same address in one time window. Until that run
exists this stays an open confound and not a finding, and the conclusions above
are written to survive either answer: the address is sufficient on its own, and
that observation does not depend on which browser was used.

### What the engines look like before any target sees them

Read 2026-08-11 by `scripts/probes/engine_fingerprint.py` on `about:blank`. It
sends nothing, needs no credentials and touches no target, so it is free to
repeat. Headless, bundled builds, 21 markers of which the ones that differ:

| Marker | chromium | patchright | camoufox | obscura |
|---|---|---|---|---|
| `navigator.webdriver` | **true** | false | false | false |
| plugins / mimeTypes | 0 / 0 | 0 / 0 | 5 / 2 | 5 / 2 |
| WebGL renderer | ANGLE string | ANGLE string | ANGLE string | **null** |
| `window.chrome` | undefined | undefined | undefined | object |
| user agent | HeadlessChrome/148 | HeadlessChrome/149 | Firefox/152 | Chrome/145 |

`chromium` is the control and is deliberately not hardened: `navigator.webdriver`
is `true` because that is what an unmodified browser reports, and the test suite
fails if anyone adds launch arguments or a user agent override to it.

Three results are worth carrying into the report.

**Headless announces itself in the User-Agent, and no framework fixes it.** Both
Chromium-family engines send `HeadlessChrome` on the bundled build. On
`--channel chrome` Patchright still sends `HeadlessChrome/149.0.0.0`. Only
`--headful` produces `Chrome/149.0.0.0`. A single substring test on the
User-Agent refuses every headless cell in this matrix, and it is above the layer
any driver patch operates at.

**On `--channel chrome`, Patchright differs from the unmodified control in
exactly one marker.** Second reading, same day, control and Patchright on the
same channel:

| Marker | chromium (control) | patchright |
|---|---|---|
| `navigator.webdriver` | **true** | **false** |
| plugins / mimeTypes | 5 / 2 | 5 / 2 |
| WebGL renderer | ANGLE (Intel) | ANGLE (Intel) |
| `window.chrome` | object | object |
| user agent | HeadlessChrome/149 | HeadlessChrome/149 |
| screen | 1280x720 | 800x600 |
| languages | ru-RU | ru-RU,ru,en-US,en |

Nineteen of twenty-one markers identical. This corrects a claim written earlier
the same day, before the control had been run on the same channel: the plugins,
the mime types and the real GPU were credited to Patchright, and they are not
Patchright, they are Chrome. Any Playwright-based engine gets them by asking for
the installed browser instead of the bundled Chromium build, the control
included.

What remains is one boolean. `navigator.webdriver` is the most widely checked
marker there is, so one boolean is not nothing - but it is also the one any
script can set, and it is the whole of the measurable difference at this layer.

Two caveats, both real. This probe reads JavaScript, and Patchright's actual
claim is about the CDP layer: `Runtime.enable` leaks, isolated execution
contexts, artefacts a detector finds by timing rather than by reading a property.
`binding_leaks` and `stack_leak` came back clean for both engines, which means
the probe did not find the thing Patchright exists to fix rather than that the
fix is absent. And a 800x600 screen with a 764x485 viewport, which is what the
persistent context launches at by default, is itself unusual enough to be a
marker; the control's 1280x720 with a zero window-chrome delta is a headless tell
in the other direction. Neither engine is shaped like a desktop.

**Every engine contradicts its own exit address before it opens a page.** The
timezone reads `Europe/Moscow` and the language list `ru-RU` on all of them,
because that is the host machine, while the matrix asks the gateway for US
exits. A US residential address whose browser is set to Moscow time is an
inconsistency that costs a detector one comparison, and it is upstream of
everything else measured here. Camoufox can derive timezone and locale from the
exit address; the Chromium-family engines have no equivalent. That makes geo
alignment an axis of the matrix rather than a setting: switching it on for one
engine measures that feature and not the anti-detect patching, and a run that
does not record which engines had it cannot be read afterwards.

**Obscura exposes no WebGL context**, where every other engine returns a renderer
string. That is a stronger signal than a wrong one: browsers with no WebGL are
rarer in live traffic than browsers with a SwiftShader renderer, so a detector
keying on absence has a cleaner rule than one keying on a value.

One claim was drafted here and withdrawn before it reached the report: Obscura's
CDP `/json/version` banner says `X11; Linux x86_64` while the page itself
correctly reports Windows. The banner is build metadata on a local socket that no
target can read. It is not a fingerprint defect and must not be quoted as one.

### Resource blocking is not a factor

| Preset | n | Verdicts | Locally counted |
|---|---|---|---|
| `light` | 3 | captcha 3 | 1,119,000 bytes |
| `none` | 3 | captcha 3 | 1,141,800 bytes |

A 2% difference in bytes and no difference at all in outcome. Blocking is
therefore fixed at `light` for the benchmark rather than run as an axis, which
removes a third of the planned cells.

### What a Google refusal costs

About **1.14 MB per attempt** at `preset=none`, against a rendered DOM of 6.3 KB.
The gap is the reCAPTCHA bundle loading while the harness waits for a results
container that will never appear. The refusal itself is 3.3 KB to a plain client
that closes the page immediately.

### Client-side RTT gap

`scripts/probes/rtt_gap.py`, 6 alternating rounds:

| Path | Median TCP-to-TLS gap |
|---|---|
| direct | -3.1 ms |
| through the gateway | 276.3 ms |
| difference | **279.4 ms** |

This lands inside the 245-265 ms band that BADPASS (Chiapponi et al., ISPEC 2022)
reports for commercial residential proxies, which is a useful confirmation that
the exits are relayed through a residential device rather than served from a
datacenter pretending to be one.

It is **not** the explanation for the Google result, and the document should not
be read as offering it. BADPASS is a server-side measurement; ours is the
client-side mirror of it. More decisively: the direct path shows no gap at all
and is still refused. The number is a provider-architecture column for the
benchmark, nothing more.

One reliability datum from the same run: 1 CONNECT out of 6 hung for roughly 20
seconds and never replied, with valid parameters.

### Consequence for the instrument

The 92 KB scaffold was being scored `block`, which credited Google with refusals
it never made and is what let the original point 1 look sound. `targets.py` now
returns `empty` for it, with the reason recorded on the row. Validated against
the stored markers of every affected run: 14 scaffold rows carry `enablejs` or
`noscript` and zero carry `recaptcha`; 38 `/sorry/` rows carry `recaptcha` 224
times and `unusual traffic` 38 times. The two are cleanly separable in data
already collected, so no run had to be repeated to fix this.

## What this evidence does not establish

- **The paired comparison is four addresses.** It closes the question of whether
  Google and Bing were being judged by different exits, and the answer is that
  they were not. It is not a sample from which a pass rate can be read.
- **It does not separate IP reputation from TLS shape for the plain HTTP
  results.** `requests` differs from Camoufox in handshake as well as in
  JavaScript. Steps 1 and 2 show the browser is not the cause; they cannot split
  address from handshake on their own. The Camoufox result carries that weight
  instead.
- **Every browser attempt used one browser family.** Camoufox is Firefox, so
  nothing here separates "an automated browser is refused" from "this Firefox
  build is refused". Three more engines exist in the harness as of 2026-08-11
  and the re-run has not happened yet.
- **The local control ran from one address on one network** (AS25513, Moscow).
  Three requests. It proves the queries are well formed and that Google serves
  this client shape without objection from a clean line. It is not a sample.
- **No claim about the size of the affected pool.** Five to ten exits per cell.
  Enough to show variation between targets, not enough for a pass-rate figure
  with a confidence interval.
- **No claim about duration or cause of the rejection.** Whether these ranges are
  permanently listed, listed for hours, or shared with other customers scraping
  Google was not measured.
- **`X-Proxy-Exit-IP` was intermittent.** Most exit addresses came from an echo
  service instead. The echo runs in the same sticky session, so it reports that
  session's address, not necessarily the address of the next request through it -
  and on 2026-08-11 one paired round proved that gap is real, reporting two
  different operators for one unchanged parameter set within a minute. Every exit
  address in this document is therefore the address the session resolved to at
  lookup time, which is strong evidence and not a guarantee.
- **Gateway instability is inside these numbers.** 5 of 41 attempts in the step
  runs died with `RemoteDisconnected`, roughly 12%, plus several failed exit
  lookups, plus one Camoufox navigation to Bing that exceeded the 60 s timeout in
  the paired rounds. Those are recorded as `error` and excluded from pass rates,
  but they cost attempts and they are a finding in their own right.
- **`ddg_serp` targets `html.duckduckgo.com`,** the no-JS endpoint, not the
  JavaScript application a browser-based scraper would normally drive.

## Instrument defects found during this investigation

All five were defects in this harness, not in the provider, and each of them
initially looked like a target block. They are documented because a fork will hit
them, and because four of the five inflated the failure count of the target under
test - which is the direction of error a benchmark can least afford.

**Undecoded brotli read as a block.** The harness advertised
`Accept-Encoding: gzip, deflate, br` without the `brotli` package installed.
`requests` returned raw compressed bytes, the content-based verdict found none
of its markers and fell through to `block`. An entire run - every step 1 result,
the 200s of step 2, all of step 3 Bing - was unattributable. Those 43 KB "blocks"
were 92 KB "enable JavaScript" pages compressed. Fixed by advertising only
decodable encodings (`engines.supported_encodings`) and by refusing to judge a
body that did not decode (`engines.looks_undecoded`) rather than reporting a
false block. `brotli` is now a dependency. The run is quarantined in
`data/runs/invalid/`.

**Resource blocking that removed the thing being measured.** Step 5 first ran
with the `aggressive` preset, which blocks `script`. Against Google, which builds
results in the browser, that guarantees the no-JS page: the one Camoufox attempt
that was not rate-limited returned exactly the 92 KB stub the scriptless direct
control returned. `engines.validate_preset` now refuses `aggressive` for targets
listed in `targets.NEEDS_SCRIPT`, and the benchmark checks every cell before it
sends anything, because the failure it prevents is indistinguishable from a real
block in the output.

**A snapshot taken before the page existed.** `fetch_camoufox` navigated with
`wait_until="domcontentloaded"` and read `page.content()` immediately. Bing and
DuckDuckGo's html endpoint render on the server, so this was invisible for two of
the three targets. Google builds its results in the browser: the one paired round
that was not rate limited returned a 91,577-character "enable JavaScript" page
with nothing blocked and scripts allowed - the harness had photographed the
scaffold. Targets now declare a `ready_selector` and `engines.await_ready` waits
for it with a short timeout before the snapshot, recording `ready` on every row so
"waited and never saw the markup" is distinguishable from "never looked". A
rejection page never grows the selector, so the wait costs one timeout on refused
attempts and nothing on served ones.

**A verdict matching too loosely.** The Bing verdict keyed on the substring
`captcha`, which appears in the telemetry of a perfectly good results page. It
now keys on `captcha_header` and `challenge/verify`. Every row since stores the
title and marker counts the verdict was based on, so a disagreement can be
settled by re-reading rows instead of re-hitting the target.

**A verdict conflating "we came up short" with "we were refused."** Found
2026-08-11 and the most consequential of the five, because it is the one that
corrupted a conclusion rather than a count. The `block` fallback absorbed
Google's no-JS scaffold, so the plain HTTP client - which Google will not serve
results to under any circumstances - was recorded as having been refused by the
target. That is what made the direct control look like an acceptance test when it
was nothing of the kind, and it is why "the client is not the cause" survived as
long as it did. `judge` now returns `empty` for the scaffold with the reason
attached, and `block` is reserved for a refusal with no known interstitial, so a
new refusal shape gets investigated instead of quietly joining the wrong bucket.
The method was renamed from `verdict` to `judge` in the same change, so that any
call site still expecting a bare string fails loudly rather than continuing to
work in a way that looks plausible.

## Traffic

4.0 MB locally counted across 111 attempts, including the discarded run. This is
a lower bound: it excludes request headers, TLS overhead and any response
without `Content-Length`. Against a 100 GB quota the investigation is free; the
figure matters only as a per-request cost signal.

**The counter is too weak to carry a benchmark column.** Per target, per run:

| Run | Preset | Waits for results | Google | Bing | DuckDuckGo |
|---|---|---|---|---|---|
| 182910Z | light | no | 4 KB | 57 KB | 0 KB |
| 184646Z | light | no | 3 KB | 56 KB | 0 KB |
| 190251Z | light | yes | 363 KB | 59 KB | 0 KB |
| 083140Z | none | yes | 115 KB | 58 KB | 0 KB |

Three things are wrong with this, and only one of them is about the targets.

`html.duckduckgo.com` sends no `Content-Length` on any response, so the counter
records zero bytes for a target that plainly transferred a results page. A column
that reports 0 MB for a working target is not a lower bound, it is a blank.

Blocking images measured *more* traffic than not blocking them - 59 KB against
58 KB for Bing, and the `light` runs against Google are not comparable to the
`none` run at all. Whatever the counter is tracking, it is not the resource
blocking it was built to price.

The one real signal: waiting for the results container raised Google's cost from
3 KB to 363 KB per attempt. That is not a search page loading; a refused attempt
never has one. It is the reCAPTCHA widget on the `/sorry/` page fetching its own
assets while the harness waits for a container that will never appear. The
sharpest single row is 347 KB spent on one rejection.

This is worth stating for two reasons. Operationally, waiting for a selector on a
refused page buys the entire challenge bundle, so the wait needs a short timeout
and the ready flag is what tells you it was spent. For the benchmark, it confirms
what NOTEBOOK.md already requires and this investigation had been putting off: the
traffic column needs the local counting proxy that sits between engine and gateway
and counts bytes in both directions. `Content-Length` summing cannot do it.

## Recommendation

**Google SERP should not ship as a target of this benchmark.** Unchanged by the
2026-08-11 correction, and for a slightly different reason than before: a target
with two independent sufficient triggers is one where a provider cannot be
credited or blamed for the outcome, because removing the provider's contribution
leaves the refusal in place. The reasoning, not the verdict:

The measurement has to be interpretable by someone comparing providers. A Google
cell would report 0% for NodeMaven, and on this evidence it would report a low
number for any residential provider whose ranges Google has seen before - the
rejection tracks the address, and residential pools are shared and reused by
definition. A column where every provider scores near zero discriminates between
nothing. It measures Google's appetite for search-result scraping, which is
famously low and enforced by a dedicated infrastructure, not the quality of the
proxy under test.

It is also a bad measurement for cost reasons. Every Google attempt buys a
rejection page: 3.3 KB if the harness closes it immediately, up to 347 KB if it
waits for results that will never arrive and the reCAPTCHA widget loads instead.
Either way the traffic column would be comparing providers on the price of being
refused. And each attempt is a confirmed automation signal against ranges
other customers of the same pool depend on - the circuit breaker exists precisely
because hammering this target damages the thing being measured.

**Use Bing and DuckDuckGo as the search-engine targets instead.** Both scored 5
of 5 through Camoufox on this pool, and 6 of 7 and 7 of 7 again in the paired
rounds, and - more useful for a benchmark - both scored 0 of 4 through the plain
HTTP client. That spread is what makes a good
target: it separates engines, so the harness can show what a real browser buys
you and what it costs in megabytes. Google separates nothing because it refuses
everything.

Keep Google in the repository as a **documented negative result**, run at low
volume, reported as "not viable through residential proxies, rejected at the
address layer before any browser signal is read". That is honest, it is useful to
a developer choosing a target, and it costs almost no traffic. What it must not
be is a scored column in a provider comparison table.

One measurement worth adding before publishing: the pool is not uniformly
residential. `Ace Data Centers AS11798` is a hosting network, `CENIC AS2152` is a
research and education backbone, `Verizon Business AS6167` is a commercial rather
than a consumer range, and `T-Mobile AS21928` is a mobile carrier behind CGNAT -
all returned by a pool requested as residential. Mobile and datacenter addresses
are not interchangeable with consumer lines at the reputation layer, which is the
layer this investigation found to be decisive. Whether the Google rejections
correlate with those ranges is not answerable from this sample: Google refused
consumer Comcast and Charter lines just as flatly. It is a fair question to put to
every provider in the benchmark, and the harness already records the operator on
every row, so it becomes answerable as soon as the sample is large enough.
