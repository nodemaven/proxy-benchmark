"""Which machine a row was produced on.

This exists because the largest unexplained result in `data/runs/` is a
difference between two computers, and no row can name which computer it came
from. Measured 2026-08-26 with `scripts/probes/probe_and_hold.py`: one target,
one engine, one entry shape and one set of gateway parameters, driven from two
machines in overlapping hours, gave 39% (24/61) on the workstation against 0%
(0/84) on the VPS, which a two-sided Fisher exact test separates at p = 3.7e-11.
`RESULTS.md` splits those rows by their timestamp, which works only because the
two machines happened to run at different times - so host and date are one
variable under two names, and every explanation anyone has offered for that split
is untested rather than supported.

Three columns, and they are deliberately the ones that need no browser and no
network. An HTTP engine fills them as completely as a browser does, they cost
nothing to collect, and they cannot fail - which matters, because a provenance
column that is sometimes absent gets dropped from the analysis and the run goes
back to being unattributable.

**What is not here, and it is the more interesting half.** What the *page* sees -
the WebGL renderer, the screen size, the core count the browser reports, the
User-Agent, the TLS handshake - is not a property of the host. It varies by
engine, by headless mode and by browser build on one machine, so it belongs to
the session rather than here. `scripts/probes/engine_fingerprint.py` and
`scripts/probes/tls_echo.py` read those today, into their own run files, which is
the same gap one layer up: nothing joins a fingerprint to the attempt it
describes. The two probes do not even agree with each other. Read 2026-09-01, six
minutes apart, both naming `patchright`: `tls_echo_20260901T113512Z` recorded
`HeadlessChrome/149.0.7827.55` and `engine_fingerprint_20260901T114120Z` recorded
`Chrome/151.0.0.0`. Two different browsers, and nothing in either file says which
one any benchmark row was taken with.

Nothing in this module opens a socket or reads a file. `platform` and `os` only.
"""
import functools
import hashlib
import os
import platform

# Set it to `workstation`, `vps`, or whatever the machine is called in the notes.
# Read once per process.
ENV_LABEL = "NMBENCH_HOST"


def label():
    """The value of the `host` column, or None when the machine has no name.

    `NMBENCH_HOST` when it is set, so an operator can write `vps` and read the
    tables without keeping a lookup table beside them.

    Otherwise a short hash of the hostname. `data/runs/` is committed to a public
    repository and a hostname names somebody's infrastructure: `msk-1-vm-qgqr` is
    not a leak on the scale of an exit address, and it is still not ours to
    publish. The hash groups rows exactly as well, which is the whole job of this
    column.

    Over the hostname alone, not over the OS version or the core count, so the
    label survives a reboot, a kernel upgrade and a Python upgrade. A label that
    moved when the machine was patched would split one host into two and
    manufacture precisely the kind of between-machine difference this column
    exists to rule out. The price is that two machines sharing a hostname share a
    label - set `NMBENCH_HOST` on both if that ever happens here.

    Whatever `NMBENCH_HOST` holds is published verbatim. It is an operator label,
    not a secret store, and the masking in `sink.py` covers addresses rather than
    names.
    """
    named = (os.environ.get(ENV_LABEL) or "").strip()
    if named:
        return named[:40]
    node = (platform.node() or "").strip().lower()
    if not node:
        # None rather than "unknown", because a filled-in placeholder is
        # indistinguishable from a real label to anything grouping on this
        # column. The ambiguity with "this row predates the column" is resolved
        # by its neighbours: `host_os` and `host_cpus` are filled here and absent
        # there.
        return None
    return "h:" + hashlib.sha256(node.encode("utf-8", "replace")).hexdigest()[:8]


def os_name() -> str:
    """The operating system, kernel release and architecture in one string.

    All three, because each one reaches the target on its own path. The system
    and the architecture appear in the User-Agent's platform token, the kernel
    release decides which TLS and HTTP/2 stack the non-browser engines link
    against, and the architecture decides whether there is a GPU driver at all -
    which is what a headless VPS reporting SwiftShader is downstream of.

    Shaped `Windows 10 (AMD64)` and `Linux 6.8.0-45-generic (x86_64)`.
    """
    system = platform.system() or "unknown"
    release = platform.release() or "unknown"
    machine = platform.machine() or "unknown"
    return f"{system} {release} ({machine})"


@functools.lru_cache(maxsize=1)
def _measured() -> tuple:
    """Read the host once per process.

    A tuple rather than a dict so the cache cannot be mutated by a caller: every
    row in a run passes through `facts()`, and one caller writing into a shared
    dict would rewrite the host on every row already emitted and every row still
    to come. Tests clear it with `_measured.cache_clear()`.
    """
    return (
        ("host", label()),
        ("host_os", os_name()),
        # `os.cpu_count()` and not `navigator.hardwareConcurrency`: this is what
        # the machine has, not what the browser admits to. The two disagree on
        # purpose in some engines, and that disagreement is a measurement of the
        # engine - it cannot be made if only one of the numbers is on the row.
        ("host_cpus", os.cpu_count()),
    )


def facts() -> dict:
    """The host columns, ready to merge into a row."""
    return dict(_measured())
