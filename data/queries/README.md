# data/queries/

Two committed lists of 1000 queries each, generated once by
`scripts/tools/build_queries.py` with `seed=20260811` and checked in.

| File | Asked of | Why it is separate |
|---|---|---|
| `serp_1000.txt` | search engines | ordinary questions across ten subject areas |
| `amazon_1000.txt` | shops | products, so an empty shelf cannot read as a refusal |

Committed rather than generated at runtime, because reproducibility means a
stranger gets the same inputs. The header of each file records the list name, the
seed and the category count; regenerate rather than edit by hand, so the file and
the seed stay in step.

Three properties are deliberate.

**Topics are spread across ten everyday subject areas** rather than concentrated
on proxies and scraping. A thousand queries all about anti-detect tooling is a
biased sample of the web, and it hands the target a reason to look closer that
has nothing to do with the framework under test.

**The order is shuffled with a fixed seed.** Combinatorial generation groups
identical phrasings together, and sending "best X" a hundred times in a row is a
pattern in itself. Shuffling breaks the run of templates while keeping the file
reproducible.

**There are two lists because there are two kinds of target.** Amazon asked
"photosynthesis exam questions" returns `s-no-results`, which is the search
working, and nothing can separate that from a soft refusal after the fact. Both
lists are built the same way from the same seed, so neither is privileged.

## Which list a run uses

The target declares it. `query_list` is a property of the target and the runner
loads whatever it names without inspecting it, the same way it refuses to know
engine names - a shop and a search engine have to run in one time window to be
comparable and cannot be asked the same strings.

`--query-list` forces one list on the whole matrix when that is the question
being asked, and `python -m nmbench` prints the names it knows.
