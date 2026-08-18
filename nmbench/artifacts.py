"""Response bodies kept on disk so a verdict can be re-checked after the run.

Every verdict in this harness is a claim about a body: `targets.py` reads the
markup and decides. That makes the classifier the instrument, and this harness
has already had it wrong twice - `block` absorbed Google's no-JS scaffold, so
"the target refused us" and "our own client cannot run JavaScript" were recorded
as the same thing, and no row written before the fix could be re-read to tell
which one it had been. Ten hours of run time became unusable for a one-line
change in a classifier.

Keeping the bodies makes that failure cost nothing. `scripts/analysis/` can
re-judge a finished run offline, with no traffic, no pool reputation spent and
no waiting for a target to be in the same mood.

Not every body, though. Saving all of a six thousand attempt matrix is tens of
gigabytes of mostly identical successful pages. The policy is that failures are
kept in full - they are the rows anybody will argue about - and successes are
sampled, because their job is only to prove the classifier still recognises a
good page. `sample_ok` per cell, not per run, so a cell that passes constantly
cannot crowd out the evidence from one that passes rarely.

These files are gitignored, and not only for size. A results page carries the
exit address inside embedded links and redirect URLs, so a body committed to a
public repository publishes which addresses the account was assigned. The same
reasoning already applies to `data/samples/`.
"""
import gzip
import re
from datetime import UTC, datetime
from pathlib import Path

ARTIFACTS_DIR = Path(__file__).resolve().parent.parent / "data" / "artifacts"

# Kept in full. These are the rows a reader will question, and the ones a
# classifier change is most likely to reinterpret.
ALWAYS_KEEP = {"captcha", "block", "empty", "consent", "error"}


def slug(text: str) -> str:
    """A filename fragment that survives every filesystem we might run on.

    Dots are allowed because engine labels carry version-like fragments, which
    means `.` and `..` can come out the other side. Those are directory
    references, not names: joined onto a path they climb out of the run
    directory. Anything that reduces to them becomes "none" instead.
    """
    made = re.sub(r"[^A-Za-z0-9._-]+", "-", str(text or "none")).strip("-")[:80]
    return "none" if made.strip(".") == "" else made


def group_key(row: dict) -> str:
    """What the `ok` sample is counted against.

    Engine and target, not `cell`. The engine fills the row and the runner adds
    the cell key afterwards, so `cell` is still empty at the moment a body is
    offered here - keying on it would put every success in one bucket named
    None and the sample would be filled by whichever cell happened to run first.
    Engine already carries the preset and the direct flag, so this is the cell
    in everything but the gateway parameters.
    """
    return f"{row.get('engine')}|{row.get('target')}"


class ArtifactStore:
    """Bodies for one run, in one directory, named after the row they came from.

    The path is written back into the row. A body nobody can attribute to an
    attempt is not evidence, it is a file.
    """

    def __init__(self, name: str, run_id: str = None, sample_ok: int = 2,
                 enabled: bool = True):
        self.enabled = enabled
        self.sample_ok = sample_ok
        self.run_id = run_id or datetime.now(UTC).strftime(
            "%Y%m%dT%H%M%SZ")
        self.dir = ARTIFACTS_DIR / f"{name}_{self.run_id}"
        # Keyed by `group_key`, engine and target, deliberately not by cell. See
        # its docstring: the cell key does not exist yet when a body is offered.
        self.kept = {}
        self.written = 0
        self.bytes_written = 0

    def wanted(self, verdict: str, key: str) -> bool:
        """Whether this body is worth the disk, decided before it is compressed."""
        if not self.enabled:
            return False
        if verdict in ALWAYS_KEEP:
            return True
        return self.kept.get(key, 0) < self.sample_ok

    def save(self, row: dict, html: str) -> str:
        """Write the body and return the path recorded in the row, or None.

        Returns None rather than raising when there is nothing to save, because
        the caller is in the middle of a measurement: a body that could not be
        written is a lost debugging aid, never a lost attempt.
        """
        verdict = row.get("verdict")
        key = group_key(row)
        if not html or not self.wanted(verdict, key):
            return None

        if verdict not in ALWAYS_KEEP:
            self.kept[key] = self.kept.get(key, 0) + 1

        name = (f"{slug(row.get('engine'))}__{slug(row.get('target'))}"
                f"__{slug(verdict)}__{self.written:05d}.html.gz")
        path = self.dir / name
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            blob = html.encode("utf-8", errors="replace")
            with gzip.open(path, "wb") as fh:
                fh.write(blob)
            self.written += 1
            self.bytes_written += path.stat().st_size
            return str(path.relative_to(ARTIFACTS_DIR.parent.parent))
        except OSError:
            # A full disk stops the archive, not the run.
            return None

    def summary(self) -> str:
        if not self.enabled:
            return "bodies: not saved"
        if not self.written:
            return "bodies: none saved"
        return (f"bodies: {self.written} saved, "
                f"{self.bytes_written / 1024 / 1024:.1f} MB compressed -> "
                f"{self.dir}")


def read(path) -> str:
    """Read one saved body back, for the analysis scripts."""
    path = Path(path)
    if not path.is_absolute():
        path = ARTIFACTS_DIR.parent.parent / path
    with gzip.open(path, "rb") as fh:
        return fh.read().decode("utf-8", errors="replace")
