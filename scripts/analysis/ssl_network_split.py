"""Did the client-side network produce the ERR_SSL_PROTOCOL_ERROR cluster.

Scratch analysis, 2026-09-01. The operator reports that two ladder runs went
out over free public wi-fi and one over a secured 5G link, and the SSL failures
look absent from the third. That is a claim about the near end of the path,
which nothing in `data/runs/` records, so the only thing available is whether
the rate differs across the runs he labelled - and whether anything else
differs across the same boundary.

Prints, per run: the SSL rate, the same rate per host, the navigation-index
profile, and the calendar window. The last one matters because two of the three
runs are truncated and one started before the run listed ahead of it.
"""
import argparse
import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from nmbench.breaker import is_transport_failure


def load(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def host_of(url):
    if not url:
        return "?"
    part = url.split("//", 1)[-1]
    return part.split("/", 1)[0]


def marker(row):
    err = row.get("error") or ""
    if "ERR_SSL_PROTOCOL_ERROR" in err:
        return "SSL"
    if "TUNNEL" in err or "PROXY" in err or "Proxy CONNECT" in err:
        return "TUNNEL"
    if err:
        return "other"
    return None


def analyse(path):
    rows = load(path)
    run_id = rows[0].get("run_id") if rows else "?"
    stamps = sorted(r.get("ts", "") for r in rows if r.get("ts"))

    print(f"\n=== {path.name}")
    print(f"    run_id {run_id}   rows {len(rows)}")
    if stamps:
        print(f"    window {stamps[0][:19]} .. {stamps[-1][:19]} UTC")

    # Every navigation, warm pages included. A warm page is a real navigation
    # through the same tunnel and carries the same error strings, so leaving it
    # out would drop most of the denominator.
    navs = rows
    errs = collections.Counter(marker(r) for r in navs)
    ssl = errs["SSL"]
    print(f"    navigations {len(navs)}   SSL {ssl} "
          f"({100 * ssl / max(len(navs), 1):.1f}%)   "
          f"TUNNEL {errs['TUNNEL']}   other errors {errs['other']}")

    transport = sum(1 for r in navs if is_transport_failure(r.get("error")))
    print(f"    classified transport failures {transport}")

    # Per host.
    per_host = collections.Counter()
    per_host_ssl = collections.Counter()
    for r in navs:
        h = host_of(r.get("url"))
        per_host[h] += 1
        if marker(r) == "SSL":
            per_host_ssl[h] += 1
    print("    per host:")
    for h, n in per_host.most_common(10):
        s = per_host_ssl[h]
        print(f"      {h:<28} {s:>4} / {n:<5} {100 * s / n:5.1f}%")

    # Navigation index inside a browser context. `position` is the index within
    # the identity; warm pages carry `warm_delivered`. Use the row order inside
    # each identity instead, which is what a context actually sees.
    seq = collections.defaultdict(int)
    per_index = collections.Counter()
    per_index_ssl = collections.Counter()
    for r in navs:
        ident = r.get("identity")
        i = seq[ident]
        seq[ident] += 1
        per_index[i] += 1
        if marker(r) == "SSL":
            per_index_ssl[i] += 1
    print("    per navigation index inside an identity:")
    for i in sorted(per_index)[:10]:
        n, s = per_index[i], per_index_ssl[i]
        print(f"      nav {i:<3} {s:>4} / {n:<5} {100 * s / n:5.1f}%")

    # Verdicts, so the runs can be compared on the thing they measure.
    verdicts = collections.Counter(r.get("verdict") for r in navs)
    print(f"    verdicts {dict(verdicts.most_common())}")

    # What the ladder delivered, since L3 changed between run 1 and the others.
    levels = collections.Counter(r.get("warm_level") for r in navs)
    print(f"    warm levels {dict(sorted(levels.items(), key=lambda kv: str(kv[0])))}")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    args = ap.parse_args()
    for name in args.runs:
        p = pathlib.Path(name)
        if not p.exists():
            p = ROOT / "data" / "runs" / name
        analyse(p)


if __name__ == "__main__":
    main()
