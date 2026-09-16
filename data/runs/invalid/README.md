# Quarantined runs

Rows here must not reach the readers that turn runs into published numbers -
`analyze_429.py`, `results_tables.py`, which writes RESULTS.md, and `report.py` -
which glob `data/runs/benchmark_*.jsonl` non-recursively and would otherwise take
them as evidence.

Most of them are here because the instrument was defective, and they are kept
rather than deleted because deleting measurements hides the defect. One is here
for the other reason a file can be unquotable: it was never a measurement in the
first place.

Moving a file is therefore the whole mechanism. There is no flag inside a row
that says "do not count me", and nothing downstream asks.

## google_429_20260810T165425Z.jsonl

The harness advertised `Accept-Encoding: gzip, deflate, br` while the `brotli`
package was not installed. Every response the server chose to compress with
brotli came back to `requests` as undecoded bytes. The content-based verdict
found none of its markers in those bytes and returned its fallback, `block`.

Consequence: every `block` in this file is unattributable. It may have been a
served result page, a challenge, or a real block - the evidence was destroyed
before the verdict ran. Affected: all of step 1, the 200-status rows of step 2,
all of step 3 bing.

The `captcha` rows are unaffected: Google serves the /sorry/ page uncompressed
at around 3.3 KB and those bodies decoded correctly.

Fixed in `engines.supported_encodings` (advertise only what can be decoded) and
`engines.looks_undecoded` (refuse to judge a body that did not decode, rather
than reporting a false block). `brotli` is now a pinned dependency.

## benchmark_20260911T082347Z.jsonl, benchmark_20260911T083637Z.jsonl

The first four-gateway run, on the OVH Windows VPS: `--engines
chromium,patchright --targets amazon_search --providers
nodemaven,oxylabs,decodo,brightdata --queries 100 --batch 10 --countries
us,any`. The small file is the 8-attempt warm-up, the large one is 300 attempts
over 2h08m, and all 16 of its cells stopped early. Three defects stack, and the
order matters because each one hides the one under it.

**`--countries any` was written onto the wire.** `any` is this harness's keyword
for "do not pin a country". NodeMaven has a wire spelling for it; Oxylabs,
Decodo and Bright Data have none, so the token was sent to three gateways that
do not accept it. The split is total and it needs no statistics:

| gateway | `any` arm | ok | block | error |
|---|---|---|---|---|
| nodemaven | 50 | 24 | 26 | 0 |
| oxylabs | 20 | 0 | 0 | **20** |
| decodo | 20 | 0 | 0 | **20** |
| brightdata | 20 | 0 | 0 | **20** |

Every one of those 60 is `ERR_TUNNEL_CONNECTION_FAILED`: the CONNECT was
refused, so no browser in those six cells was ever shown a page. The one gateway
that understands the token is the one gateway with no errors on the arm, which
is as clean a natural experiment as this tree has produced by accident.

**The breaker could not tell a refused tunnel from a refused page.** It counted
consecutive request failures, and all 16 cells died on `10 consecutive
failures` - with `--batch 10`, that is every cell stopping inside its first
batch. A gateway that never opened a tunnel and a target that served 10
challenges were charged identically, and both read as the engine's result.

**`error` rows leave the pass-rate denominator, so the wreck was invisible.**
Sixty attempts that never reached Amazon contributed no `judged` and no passes.
They did not drag any number down; they were simply absent. And `engine_label`
carried no gateway until 2026-09-14, so the surviving NodeMaven rows and the
three dead gateways' rows shared one row per engine, which printed NodeMaven's
number under a heading that claimed to cover all four.

Consequence: nothing in either file is quotable as a gateway comparison. The 190
`us` attempts are a real measurement of something, but their cells were stopped
by a breaker that was mis-counting, so the `n` behind each is an artifact of the
defect rather than of the matrix.

Measured on the VPS 2026-09-14, before the move: these two files were feeding
RESULTS.md and moving chromium's `amazon_search` row from `33% (19/58)` to
`41% (74/179)`. That is the published headline being set by a run whose
instrument was broken, which is the thing this directory exists to stop.

Fixed before the relaunch: `any` is resolved per provider and omitted from the
wire where the gateway has no spelling for it; the breaker counts failed
sessions and separates transport failure from target refusal; `report.py` puts
the gateway in the label whenever a run holds more than one, and prints a
session section whose denominator is exits rather than requests, so a cell whose
every tunnel died reads `unmeasured` instead of `0%`.

## benchmark_20260914T160048Z.jsonl

Not a defective run - not a run at all. Two attempts, one engine, one provider,
one country, sent on 2026-09-14 to answer a question about the *launcher* rather
than about any gateway: whether the harness works when it is owned by a Windows
scheduled task running as `nt authority\system` instead of by an interactive
shell. It does - the venv resolved, `.env` was found from the working directory,
chromium launched in session 0, two tunnels opened to two distinct exits, Amazon
answered, and rows and bodies were written.

It is here because `n=2` and because nothing about it was chosen to measure
anything: the engine, the provider and the country were picked to make the check
cheap. Both attempts read `block`, and that number is worth exactly nothing -
quoting a 0% from two attempts is how a smoke test becomes a headline.

The reason this needed a file at all is the same reason the 2026-09-11 pair
does: there is no flag inside a row that says "do not count me". A smoke test
lands in `data/runs/` looking exactly like evidence.
