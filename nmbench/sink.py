"""Append-only JSONL storage. One line per attempt, one file per run."""
import ipaddress
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from . import host

RUNS_DIR = Path(__file__).resolve().parent.parent / "data" / "runs"

IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# A browser version is four dotted numbers too, and masking one would corrupt the
# engine fingerprint the row exists to record.
_KEEP_VERBATIM = {"user_agent"}


def _mask(text: str) -> str:
    """Reduce every public host address in `text` to the network it sits in.

    `ExitRegistry` documents the rule and derives `exit_prefix` from it. This is
    the same arithmetic applied to whatever else ended up in the row.
    """
    def replace(match):
        hit = match.group(0)
        try:
            address = ipaddress.ip_address(hit)
        except ValueError:
            return hit
        # Private and loopback addresses name the relay or a machine on the
        # operator's own LAN, and identify nobody outside it.
        if not address.is_global:
            return hit
        return ".".join(hit.split(".")[:3]) + ".0"

    return IPV4.sub(replace, text)


class JsonlSink:
    def __init__(self, name: str):
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        self.run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        self.path = RUNS_DIR / f"{name}_{self.run_id}.jsonl"

    def write(self, row: dict) -> None:
        """Append one row, with exit addresses reduced to their network.

        `data/runs/` is committed, so anything reaching this function is
        published. The addresses behind a residential pool are the home
        connections of real people, and on the direct arm the operator's own
        line, so `ExitRegistry` records a /24 and a label and leaves the full
        address on the console.

        That rule was documented in `gateway.py` and enforced nowhere, and five
        scripts wrote the full address into the row regardless - two of them by
        copying the CONNECT reply wholesale, one by way of a transport exception
        that quoted the address inside its message. Guarding the five call sites
        would have left the sixth, so the guarantee lives at the single point
        every script already goes through.

        It masks rather than raises deliberately. A run is hours long and its
        rows are the measurement; discovering a new address-shaped field at hour
        four must cost the address, never the run.
        `tests/test_runs_are_publishable.py` is what turns a leak into a failed
        build rather than a silent one.
        """
        row = dict(row)
        row.setdefault("run_id", self.run_id)
        row.setdefault("ts", datetime.now(UTC).isoformat())
        # Which machine produced the row, for the same reason the masking is
        # here: counted 2026-09-02, there are 37 `sink.write` call sites in this
        # repository and 23 of them build the dict inline rather than passing a
        # row from `blank_row`. Every bookkeeping row - `cell_stopped`,
        # `session_failed` - and every probe file, including the two that read
        # fingerprints, is one of those. Filling this per call site would leave
        # the thirty-eighth, and the whole value of the column is that no run
        # lacks it.
        #
        # `setdefault` is not enough. `blank_row` builds from
        # `dict.fromkeys(ROW_FIELDS)`, so the keys are present and None, and
        # `setdefault` would leave them that way. Absent and None both mean "not
        # recorded" here, and an explicit value from the caller is left alone -
        # a probe replaying somebody else's rows has to be able to say whose
        # they were.
        for key, value in host.facts().items():
            if row.get(key) is None:
                row[key] = value
        # After the host is filled, so an operator who put an address in
        # `NMBENCH_HOST` gets it masked like anything else.
        row = {k: _mask(v) if isinstance(v, str) and k not in _KEEP_VERBATIM
               else v
               for k, v in row.items()}
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
