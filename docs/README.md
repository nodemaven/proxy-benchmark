# docs/

Three documents, and the top-level [README](../README.md) is the fourth.

| File | For | Assumes |
|---|---|---|
| [quickstart.md](quickstart.md) | getting from an empty machine to a real row | never used a terminal |
| [findings_google_429.md](findings_google_429.md) | one write-up: which layer refuses at Google, isolated in five steps | the README |
| [findings_obscura_tls.md](findings_obscura_tls.md) | one write-up: what a synthesised ClientHello buys, and what it does not | the README |

Where to start depends on what is wanted.

- **A first measurement, with no proxy account and no terminal experience:**
  [quickstart.md](quickstart.md). Steps 1 to 5 send nothing at all and still
  print a real result.
- **The flags, the axes and the verdict enum:** the [README](../README.md).
- **The evidence behind a specific claim:** [NOTEBOOK.md](../NOTEBOOK.md), the
  working notebook, which names the run id behind every number and keeps the
  claims that did not survive replication next to the ones that did.
- **The rows themselves:** [data/runs/](../data/runs/), one JSONL row per
  attempt, with their own README on masking and on the four columns that are
  easy to misread.

A findings document is written when one question has been answered end to end
and the answer is worth reading without the notebook around it. Both of the ones
here are corrections in part: the Google write-up ends with two sufficient causes
where it started with one, and the Obscura write-up withdraws a detail it got
wrong. That is the intended shape rather than an accident of drafting.
