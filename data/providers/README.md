# data/providers/

One `.toml` per gateway. **A provider is a username format, so it is data and
not code.**

| File | What it is |
|---|---|
| `nodemaven.toml` | residential, `status = "measured"` - every row in `data/runs/` came through it |
| `decodo.toml` | residential, `status = "measured"` |
| `oxylabs.toml` | residential, `status = "measured"` |
| `brightdata.toml` | an ISP zone, `status = "measured"` |
| `custom.toml` | your own proxy: host, port, login, password, nothing in the username |
| `_template.toml` | copy this to add a vendor that sells settings inside the username |

## Why a file rather than a module

Every vendor sells the same thing and spells it differently: its own separators,
its own parameter names, its own reaction to a mistake. That difference is the
whole of what a provider is from this harness's point of view.

A Python module per provider would invite one `if provider == ...` in a place
nobody reviews, and the argument this repository rests on is that every arm went
through the same code path. A data file cannot branch, and
`test_no_script_branches_on_a_provider_name` reads the source and derives the
names from whatever definitions are on disk, so a competitor added tomorrow is
covered without anyone extending the test.

Read through stdlib `tomllib`, so a new gateway costs a file and no dependency.

## Adding one

    cp data/providers/_template.toml data/providers/yourvendor.toml
    # fill in the format, then
    python -m nmbench gateway-health --provider yourvendor

The health probe opens a handful of CONNECTs and sends nothing to any target. It
is the cheapest check that separates a wrong username format from a wrong
password, because the gateway itself cannot: it answers both with a status that
names neither.

**Credentials are read from `.env` by the file's own id.** `oxylabs.toml` reads
`OXYLABS_LOGIN`, `OXYLABS_PASSWORD`, `OXYLABS_HOST` and `OXYLABS_PORT`, so two
accounts sit in one `.env` and a matrix can interleave them.

## `status` is load-bearing, not bookkeeping

`measured` means traffic has been sent through that gateway with this dialect and
the gateway was seen to honour it. `documented` means the dialect was transcribed
from the vendor's documentation on the date in `source_read` and nothing here has
ever sent a byte through it. Each legal value of each parameter is marked the same
way, with a third mark between the two: `accepted`, the gateway recognises the
value and nothing has been run through it. `network` - residential, isp,
datacenter or mobile - decides which arms may share a column, because an ISP arm
beside residential ones reads as a vendor ranking when it is a product class.
`--dry-run` prints the status of every provider in the matrix, and a `documented`
one is the first thing to suspect when its cells fail in a way the measured one's
do not.

The distinction exists because **a wrong username is invisible**. NodeMaven's
gateway answers an unrecognised parameter name with 200 and the setting
silently dropped: the connection succeeds, the run completes, and every row
claims a setting that was never applied. Nothing the gateway replies can catch
it. So a parameter outside `known_params` is refused before a request exists,
`--param` is validated against every provider in the matrix rather than the
first, and the session parameter is asked of the definition rather than spelled
`sid` in the caller.

Transcribe a new definition from the vendor's own fetched documentation, never
from memory. An invented username format is an invented technical claim about
somebody else's product, and it fails in exactly the silent way this section is
about. Promote to `measured` once traffic has been sent through it and its
parameters observed.
