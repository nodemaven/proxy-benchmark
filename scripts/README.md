# scripts/

Three folders, split by one question: **can this file spend money?**

| Folder | Sends requests | What lives here |
|---|---|---|
| `probes/` | yes, unless it says otherwise | one file per question, each cheap and single-purpose |
| `analysis/` | never | aggregation over `data/runs/`, offline, no credentials |
| `tools/` | never | generators and repair passes for committed inputs |

`benchmark.py` sits at the top level of this folder because it is the matrix
runner and belongs to neither half.

The split is the point. A reader deciding whether to run something should not
have to read it first, and a folder name is the cheapest place to put that.

## Running them

    python -m nmbench                    # every command, and which ones spend traffic
    python -m nmbench gateway-health     # dispatched to scripts/probes/gateway_health.py
    python scripts/probes/gateway_health.py --help

Both forms work and always will. `python -m nmbench` discovers commands from
disk and hands the remaining arguments over untouched; every script keeps its own
argparse and still runs by path. One argparse owning the flags of eight unrelated
experiments would put shared code between the runner and the probes that measure
it, which is the one dependency this repository cannot have.

## probes/

A probe that happens to send nothing declares `SENDS_REQUESTS = False`, so the
command list does not overstate what it costs. Five do:
`engine_fingerprint.py`, `geo_align_check.py`, `obscura_defects.py`,
`session_continuity.py` and `tls_clienthello.py`. They read the browser rather
than the network, which makes them the cheapest re-check available after an
engine upgrade.

`tls_clienthello.py` is the odd one of the five: it does open sockets, but both
ends are on this machine. It runs a listener that accepts, reads the first TLS
record and answers nothing, points each engine at it in turn, and computes the
JA4 from what arrived. That is the same value `tls_echo.py` asks `tls.peet.ws`
for, without needing a live host - which makes it the one to run from behind a
gateway, and the one to run when an engine is upgraded. `tls_echo.py` is still
the only route to the fingerprints that need a server which answers - the
HTTP/2 SETTINGS hash and the header order - and to `obscura`, which refuses to
navigate to `localhost` at all.

The rest reach a gateway or a target. Before running one, read
[Operational safety](../README.md#operational-safety) in the top-level README:
the pool is shared and production, and a retry against a target that has already
refused is a confirmed automation signal charged to everyone on the account.

## analysis/

Reads `data/runs/` and nothing else. `report.py`, `held.py` and `playbook.py`
reproduce the tables quoted in [NOTEBOOK.md](../NOTEBOOK.md) on a fresh clone with no
account, which is what makes those tables checkable rather than believable.

`peek.py` prints one run file. `calibrate.py` is what `--dry-run` quotes
megabytes from.

## tools/

`build_queries.py` produced the committed lists in `data/queries/` from one seed,
and they are committed rather than generated at runtime so a stranger gets the
same inputs.

`redact_runs.py` replaces exit addresses with their /24 in rows written before
that was enforced at the single point every live row now passes through. Run it
before pushing a fork, and again after adding anything that stores a gateway
reply: the guard has failed twice, and both times the address arrived nested
inside a field nothing walked.
