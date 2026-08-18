"""Making the progress output survive the strings it prints.

A run prints text it did not author: the ASN organisation behind the exit, the
final URL, the message on an exception. All three come from the far end, and on
Windows the console encoder is the system codepage rather than UTF-8. The
combination is fatal by default - `print` raises `UnicodeEncodeError` and the
traceback ends the process.

Measured 2026-08-13. `probehold_20260813T201805Z` stopped after four identities
of a twelve-cell, three-hour run, on the fourth exit's organisation name: cp1251
is Cyrillic and has no `U+00DA`. Nothing about the measurement failed. The
gateway answered, the exit was live, the row was written, and then the harness
printed a progress line and died. On a `country=any` run drawing exits
worldwide, a Latin-1 accent in an ASN name is not an edge case, it is a matter
of how many identities go by first.

`errors="replace"` and not a switch to UTF-8: the same handle usually carries a
redirect to a log file, and changing its encoding partway would leave one file
written in two. A mangled character in a progress line costs nothing, because
the progress line is not the measurement - `exit_org` is written to the JSONL
row in full, and that is what any analysis reads.

Call this before the engines are imported. zendriver pulls in colorama, which
replaces `sys.stdout` with a wrapper that has no `reconfigure` but still writes
through the original handle, so reconfiguring first reaches both.
"""
import sys


def tolerate_unencodable_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            # Already wrapped, or not a text stream at all. Losing the guard is
            # the old behaviour and must never be the reason a run does not
            # start.
            pass
