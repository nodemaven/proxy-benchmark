"""Mask exit addresses in `data/runs/` down to the network they belong to.

`nmbench.gateway.ExitRegistry` states the intent for this repository: an exit is
recorded as a /24 prefix plus a label, and "the full address stays on the
console". Those addresses are the home connections of real people whose devices
carry the pool, and on the direct arm they are the operator's own line.

Five scripts wrote the full address into the row anyway - `gateway_health.py`,
`check_geoip.py`, `rtt_gap.py`, `benchmark.py` and `probe_and_hold.py` - so the
committed corpus contradicts the rule the package documents. This repairs the
rows that already exist; the scripts themselves were fixed at the same time, and
`tests/test_runs_are_publishable.py` is what stops it coming back.

Nothing analytic is lost. No analysis script reads `exit_ip` back out of a row:
they read `exit_prefix` and `exit_label`, which `ExitRegistry` derives, and every
claim in NOTEBOOK.md that counts distinct exits counts distinct /24s. The masking
is idempotent, so running it twice is safe and running it after a new experiment
is the intended workflow.

Free text is masked too. One Obscura error message carried the address inside a
transport exception, which is the case that argues for scanning every string in
the row rather than a list of fields nobody remembers to update.

Nested values are masked too, and that was found rather than designed. Both this
script and the test that guards it walked the top level of the row only, so a
`dsl_probe` row storing the gateway's whole CONNECT reply as
`headers: {"X-Proxy-Exit-IP": ...}` was skipped: the value at the top level is a
dict, not a string, and neither ever looked inside it. 12 rows across two files
published 4 residential addresses under a rule both files claimed to enforce.
Recursion is the fix, and it is the honest shape - a row is a JSON document, so
the scan has to be over the document rather than over its first level.

    python scripts/tools/redact_runs.py            # report, change nothing
    python scripts/tools/redact_runs.py --write    # rewrite the files
"""

import argparse
import glob
import ipaddress
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

SENDS_REQUESTS = False

IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# A browser version is four dotted numbers too. `user_agent` is the only field
# that carries one, and masking a version string would corrupt the engine
# fingerprint the row exists to record.
SKIP_FIELDS = {"user_agent"}


def mask(ip: str) -> str:
    """The same /24 and /48 rule `ExitRegistry.record` applies to a live exit."""
    if ":" in ip:
        return ":".join(ip.split(":")[:3]) + "::"
    return ".".join(ip.split(".")[:3]) + ".0"


def _is_host_address(text: str) -> bool:
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return False
    # A masked prefix is already a network address, so leaving it alone is what
    # makes a second run a no-op rather than a slow erosion of the last octet.
    return address.is_global and not text.endswith(".0")


def redact_value(field: str, value):
    """Mask every host address anywhere under `value`, whatever its shape.

    `field` is the key this value arrived under, and it is carried through the
    recursion so `SKIP_FIELDS` still protects a nested `user_agent`. Dict order
    is preserved, so a masked file differs from the original only in the octets
    that had to change.
    """
    if field in SKIP_FIELDS:
        return value, 0
    if isinstance(value, dict):
        out, changed = {}, 0
        for key, inner in value.items():
            out[key], n = redact_value(key, inner)
            changed += n
        return out, changed
    if isinstance(value, list):
        out, changed = [], 0
        for inner in value:
            masked, n = redact_value(field, inner)
            out.append(masked)
            changed += n
        return out, changed
    if not isinstance(value, str):
        return value, 0
    hits = [h for h in IPV4.findall(value) if _is_host_address(h)]
    if not hits:
        return value, 0
    for hit in hits:
        value = value.replace(hit, mask(hit))
    return value, len(hits)


def redact_row(row: dict) -> tuple[dict, int]:
    return redact_value(None, row)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--write", action="store_true",
                        help="rewrite the files; without it nothing is touched")
    parser.add_argument("--runs", default="data/runs",
                        help="directory of JSONL run files")
    args = parser.parse_args()

    total = 0
    touched = []
    for path in sorted(glob.glob(os.path.join(args.runs, "*.jsonl"))):
        lines = []
        changed = 0
        for line in open(path, encoding="utf-8"):
            stripped = line.strip()
            if not stripped:
                lines.append(line)
                continue
            row, n = redact_row(json.loads(stripped))
            changed += n
            lines.append(json.dumps(row, ensure_ascii=False) + "\n")
        if not changed:
            continue
        total += changed
        touched.append((os.path.basename(path), changed))
        if args.write:
            with open(path, "w", encoding="utf-8") as fh:
                fh.writelines(lines)

    for name, n in sorted(touched, key=lambda item: -item[1]):
        print(f"  {name:<44} {n:>4} addresses")
    verb = "masked" if args.write else "would mask"
    print(f"{verb} {total} host addresses across {len(touched)} files")
    if total and not args.write:
        print("nothing was written; pass --write to apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
