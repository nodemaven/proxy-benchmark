# The rows

Every measurement this repository has ever made, one JSONL row per attempt, one
file per run. 131 files, about 4 MB, covering 2026-08-10 to 2026-08-14.

They are committed on purpose. Every claim in `NOTEBOOK.md` and in the top-level
README names the run it came from, and a claim whose evidence is not in the
repository is a claim a forker has to take on trust. Several of those claims are
corrections of an earlier one in the same file, and the corrections were only
possible because the original rows were still there to re-read.

## Why they are here when you are going to measure your own

They are not a baseline to compare your numbers against, and they should not be
used as one. A pass rate here is a reading of the hours it was taken in, on one
pool, from one machine, in Moscow, against targets that change their minds. The
`us` figure in `NOTEBOOK.md` moved from 0% to 19% between two windows four hours
apart.

What they are good for:

- **Reading the verdict rules against real bodies.** The rules in `targets.py`
  were written by looking at markup, and these rows record which marker fired on
  which attempt. If a rule looks arbitrary, the row says what it was reading.
- **Re-deriving a claim before trusting it.** `scripts/analysis/` sends nothing
  and reads only this directory. `report.py`, `playbook.py` and `held.py` on a
  fresh clone reproduce the tables in `NOTEBOOK.md`.
- **Seeing what a run actually looks like** before spending traffic on your own:
  the column set, the shape of a failure, how much of a run is `error` rather
  than a verdict.
- **Calibration.** `calibrate.py` prices the next run from these, and
  `matrix.estimate` is why `--dry-run` can quote megabytes at all.

The bodies those verdicts were read from are **not** here. They are gzipped into
`data/artifacts/`, which is gitignored, because exit addresses turn up in
embedded links.

## Masking

Exit addresses are recorded as their /24 and the proxy username as `<login>`.
Those addresses are the home connections of the people whose devices carry a
residential pool, and on the direct arm they are the operator's own line. The
account name identifies the account.

`nmbench.gateway.ExitRegistry` applies this to live runs at the single point
every row passes through, `scripts/tools/redact_runs.py` repaired the rows
written before that existed, and `tests/test_runs_are_publishable.py` reads what
is actually on disk - not the source that produced it - and fails if a full
address appears anywhere in any row.

Nothing analytic is lost. No analysis script reads a full address back out; they
read `exit_prefix` and `exit_label`, and every claim that counts distinct exits
counts distinct /24s.

Two facts about that guard are worth knowing before trusting it. It has failed
twice. The first time, the rule was documented and not enforced and five scripts
wrote the full address anyway. The second time, both the guard and the repair
tool walked only the top level of the row, so twelve `dsl_probe` rows storing the
gateway's CONNECT reply under `headers` published four addresses under a check
that asserted none existed. Both are fixed and both are pinned by a test.

If you fork this and run your own experiments, run `redact_runs.py` before you
push, and run it again after adding a script that touches a gateway reply.

## Filename prefixes

| Prefix | Files | Written by | What it holds |
|---|---|---|---|
| `benchmark_` | 43 | `scripts/benchmark.py` | the matrix runs: engine x target x gateway parameters, interleaved, one row per attempt |
| `gateway_health_` | 37 | `probes/gateway_health.py` | CONNECT probes with no browser: is the gateway usable right now |
| `probehold_` | 19 | `probes/probe_and_hold.py` | the front-page entry protocol: one exit per identity, probe, then hold |
| `engine_fingerprint_` | 7 | `probes/engine_fingerprint.py` | 21 markers per engine, read on `about:blank`, sends nothing |
| `availability_` | 7 | a script that no longer exists | Camoufox pass rates, 2026-08-10 and 11 |
| `google_429_` | 5 | `probes/google_429.py` | the five-step layer isolation against Google |
| `tls_echo_` | 3 | `probes/tls_echo.py` | JA4, JA3, HTTP/2 fingerprint and cipher counts per engine |
| `walmart_recon_` | 2 | `probes/walmart_recon.py` | 33 bodies captured before any Walmart verdict rule was written |
| `dsl_probe_` | 2 | a script that no longer exists | the gateway parameter DSL: what each malformed input returns |
| `screen_override_` | 1 | `probes/screen_override.py` | device metrics override on and off, alternating by session |
| `rtt_gap_` | 1 | `probes/rtt_gap.py` | TCP-versus-TLS round trip gap |
| `geoip_check_` | 1 | `probes/check_geoip.py` | does the browser's identity match its exit address |
| `detect_page_` | 1 | `probes/detect_page.py` | what a detection page says about each engine |
| `traffic_` | 1 | a script that no longer exists | one cost measurement against the provider dashboard |

**Three prefixes have no producing script left in the tree, and the rows stay.**
A measurement outlives the instrument, and deleting the rows because the script
went would hide the evidence rather than tidy it.

- `availability_` was Camoufox-only and a strict subset of what
  `benchmark.py --engines camoufox` does. A second runner that could drift from
  the first is worse than no second runner.
- `traffic_` measured against the provider dashboard, which rounds to 0.01 GB.
  That is a resolution of about 10 MB, so it is not an instrument;
  `nmbench.relay` counts sockets instead and is what every byte figure now
  comes from.
- `dsl_probe_` is the exception worth reading: it is the only evidence behind
  the gateway behaviour table in `NOTEBOOK.md` - that the sticky session key is
  the whole recognised parameter set rather than `sid` alone, that a bad city
  returns 500 while a bad filter returns 407, and that an unknown parameter name
  returns 200 with the setting silently dropped. Those rows are load bearing and
  the probe that wrote them was a one-off. Re-verify before publishing any of
  it: none of it is documented by the provider, and none of it is guaranteed to
  still be true.

## invalid/

One quarantined file, with its own README explaining the defect. Rows produced
by a broken instrument are kept, because deleting them hides the defect, and
moved, because `analyze_429.py` must not read them as evidence.

## Reading a run

    python scripts/analysis/peek.py data/runs/benchmark_<stamp>.jsonl
    python scripts/analysis/report.py
    python scripts/analysis/held.py
    python scripts/analysis/playbook.py

All four are offline. None of them sends anything or needs credentials.

The columns are defined by `ROW_FIELDS` in `nmbench/engines/base.py`, and the
comments there say why each one exists. Four are easy to misread:

- **`verdict`** is `ok, captcha, consent, block, empty, error` and never a
  boolean. `empty` means our client came up short, `block` means the target
  refused, and `error` means the attempt never produced evidence at all.
- **`bytes`** is two different measurements and `relayed` says which: socket
  counts from the relay, or page-resource counts from `page.route`. Never pool
  them, and do not treat the second as a slightly smaller version of the first.
  The route counter sums `Content-Length`, and a chunked response carries none,
  so the resource it misses first is the largest one on the page. Median bytes
  per attempt over every row here, the same targets counted both ways: Google
  passes read 598 KB by route against 2052 KB by relay, Amazon passes 776 KB
  against 8136 KB. Only the engines declaring `needs_relay` are counted the
  second way, so that gap is the counting method and not an engine result.
- **`elapsed_ms`** is not comparable across `relayed`, because the relay adds a
  loopback hop. Every proxied row written before 2026-08-13 also went through a
  VPN tunnel on the operator's machine; the verdicts and the exits survive that,
  the timings do not.
- **`url`** on a typed row records the results URL rather than the front page
  that was actually landed on. That is deliberate and `entry_row_url` documents
  it: `was_served` compares the path asked for against the path landed on, so
  recording `/` would score every typed row as diverted by the target.
