"""`data/runs/` is committed, so every row in it is published.

`nmbench.gateway.ExitRegistry` states the rule the repository runs on: an exit is
recorded as a /24 prefix and a label, and the full address stays on the console.
Those addresses are the home connections of the people whose devices carry the
pool, and on the direct arm the operator's own line.

The rule was documented and not enforced, and five scripts wrote the full address
into the row anyway. This suite reads what is actually on disk rather than the
source that produced it, because that is the only check that survives a new
script nobody thought to audit. It is offline and needs no credentials.

It reads the whole row and not its first level, which was the second time the
same lesson arrived. The scan walked `row.items()` and skipped anything that was
not a string, so a `dsl_probe` row holding the gateway's CONNECT reply as
`headers: {"X-Proxy-Exit-IP": ...}` passed: the top-level value is a dict. 12
rows across two files published 4 residential addresses while this file asserted
none did. A guard that inspects less than the thing it guards is worse than no
guard, because it is also an assurance.

`scripts/tools/redact_runs.py` is what repairs a file this fails on.
"""

import glob
import ipaddress
import json
import os
import re

RUNS = os.path.join(os.path.dirname(__file__), "..", "data", "runs")
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# A browser version is four dotted numbers too, and masking one would corrupt the
# fingerprint the row exists to record. It is the only field that carries one.
SKIP_FIELDS = {"user_agent"}


def _host_addresses(value: str):
    for hit in IPV4.findall(value):
        try:
            address = ipaddress.ip_address(hit)
        except ValueError:
            continue
        # Private and loopback addresses name a machine on the operator's own
        # LAN or the relay, which identifies nobody outside it.
        if address.is_global and not hit.endswith(".0"):
            yield hit


def _addresses_anywhere(value, field=None, path=""):
    """Every host address under `value`, with the path it was found at.

    Recursive because a row is a JSON document rather than a flat mapping, and
    the path is carried so a failure names the field to fix rather than only the
    file. `field` follows the recursion so `SKIP_FIELDS` protects a nested
    `user_agent` the same way it protects a top-level one.
    """
    if field in SKIP_FIELDS:
        return
    if isinstance(value, dict):
        for key, inner in value.items():
            yield from _addresses_anywhere(
                inner, key, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for inner in value:
            yield from _addresses_anywhere(inner, field, f"{path}[]")
    elif isinstance(value, str):
        for hit in _host_addresses(value):
            yield path, hit


def test_no_row_carries_an_unmasked_exit_address():
    offenders = []
    for path in sorted(glob.glob(os.path.join(RUNS, "*.jsonl"))):
        for number, line in enumerate(open(path, encoding="utf-8"), 1):
            if not line.strip():
                continue
            for field, hit in _addresses_anywhere(json.loads(line)):
                offenders.append(
                    f"{os.path.basename(path)}:{number} {field}={hit}")

    assert not offenders, (
        f"{len(offenders)} rows publish a host address. These are real people's "
        f"home connections. Run `python scripts/tools/redact_runs.py --write`, "
        f"then fix the script that wrote them so it records only what "
        f"`ExitRegistry.record` returns.\n" + "\n".join(offenders[:20]))


def test_an_address_nested_in_the_row_is_still_found():
    """The regression that made this file recurse.

    Pinned with a synthetic row rather than by asserting over disk, because the
    corpus is now clean and a passing scan proves nothing about the scan. The
    shape is the real one: `dsl_probe` stored the gateway's whole CONNECT reply
    under `headers`, so the address lived one level below anything the old check
    looked at. The masked sibling and the `user_agent` are in the row for the
    same reason - a scan that reported those would be unusable in a different
    way, and both were correct before the recursion and have to stay correct
    after it.

    The literals are `1.2.3.4` and `5.6.7.8` rather than the documentation
    ranges, and that is not cosmetic: `ipaddress` reports 203.0.113.0/24 and
    198.51.100.0/24 as not global, so a test written with TEST-NET addresses
    passes against a scan that finds nothing at all.
    """
    row = {
        "headers": {"X-Proxy-Exit-IP": "1.2.3.4"},
        "exit_prefix": "1.2.3.0",
        "user_agent": "Mozilla/5.0 Chrome/149.0.7632.79",
        "trace": [{"peer": "5.6.7.8"}],
    }
    found = dict(_addresses_anywhere(row))
    assert found == {"headers.X-Proxy-Exit-IP": "1.2.3.4",
                     "trace[].peer": "5.6.7.8"}


def test_the_run_directory_holds_only_rows():
    """A redirected console log is not evidence and is not masked.

    Runs were launched with stdout redirected into `data/runs/`, which put nine
    console logs beside the JSONL carrying 278 addresses between them. The rows
    are the evidence; the terminal output is a copy of it with the masking
    undone, so the directory has to refuse anything that is not a row.

    `README.md` is the one exception, by name rather than by extension. What
    this test is actually for is redirected output, which arrives as `.log`,
    `.txt` or no suffix at all; a file named exactly `README.md` is not that,
    and the directory is the one place a reader lands first when deciding
    whether these rows can be trusted.
    """
    allowed = {"README.md"}
    strays = [os.path.basename(p) for p in glob.glob(os.path.join(RUNS, "*"))
              if os.path.isfile(p) and not p.endswith(".jsonl")
              and os.path.basename(p) not in allowed]
    assert not strays, (
        f"{strays} are not JSONL rows. Console output prints the full exit "
        f"address by design, so it must not live in a committed directory.")
