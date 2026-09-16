"""The host columns: that every row carries them, and that they are publishable.

The column exists to close a confound rather than to add a field, so the tests
are about the two ways it could fail to close it.

**A row without the columns is a row back in the confound.** `data/runs/` is
appended to over hours and read months later; a path that quietly omits the host
produces rows indistinguishable from the historical ones, and the analysis goes
back to inferring the machine from the timestamp. So the assertion is on
`blank_row`, which every engine and every probe in this repository builds rows
with, and not on any engine in particular.

**A default that names the machine is a leak.** These files are committed to a
public repository. `sink.py` masks addresses and does not touch hostnames, so the
default value here has to be safe on its own.
"""
import json
import os
import platform

from nmbench import host, sink
from nmbench.engines.base import ROW_FIELDS, blank_row

COLUMNS = ("host", "host_os", "host_cpus")


def written(tmp_path, monkeypatch, row):
    """One row through the real sink, into a temporary directory."""
    monkeypatch.setattr(sink, "RUNS_DIR", tmp_path)
    out = sink.JsonlSink("test")
    out.write(row)
    return json.loads(out.path.read_text(encoding="utf-8").strip())


def fresh(monkeypatch, value=None):
    """Re-read the host with `NMBENCH_HOST` set or cleared.

    The facts are cached for the length of the process - a run writes thousands
    of rows and should not call into `platform` for each one - so a test that
    changes the environment has to clear the cache or it reads whatever the
    first test in the session happened to produce.
    """
    if value is None:
        monkeypatch.delenv(host.ENV_LABEL, raising=False)
    else:
        monkeypatch.setenv(host.ENV_LABEL, value)
    host._measured.cache_clear()
    try:
        return host.facts()
    finally:
        host._measured.cache_clear()


class TestEveryRowCarriesTheHost:
    def test_the_columns_are_in_the_published_schema(self):
        for column in COLUMNS:
            assert column in ROW_FIELDS, (
                f"{column} is written onto rows but is not in ROW_FIELDS, which "
                f"is the schema anyone forking this repository reads instead of "
                f"our code")

    def test_blank_row_fills_them(self):
        row = blank_row("engine", "1.0", "query", "https://example.invalid/")
        for column in COLUMNS:
            assert row[column] is not None, (
                f"{column} is None on a fresh row, so this run would be as "
                f"unattributable as the ones that made the column necessary")

    def test_the_caller_can_still_override(self):
        """A probe replaying rows from elsewhere must be able to say so.

        `extra` is applied last in `blank_row` for every other column and this
        one is not special. Pinned because the fix is a one-line reordering and
        the damage - a replayed row silently relabelled to the machine that
        replayed it - is invisible in the output.
        """
        row = blank_row("engine", "1.0", "q", "https://example.invalid/",
                        host="somewhere-else")
        assert row["host"] == "somewhere-else"

    def test_a_mutated_row_does_not_rewrite_the_next_one(self):
        """The cache is shared by every row in the run; a copy is not optional."""
        first = blank_row("engine", "1.0", "q", "https://example.invalid/")
        first["host"] = "clobbered"
        second = blank_row("engine", "1.0", "q", "https://example.invalid/")
        assert second["host"] != "clobbered"


class TestEveryRunFileCarriesTheHost:
    """The sink, not the call sites.

    About thirty places in this repository call `sink.write`, and most of them
    build the dict by hand: the bookkeeping rows a run emits when a cell stops,
    and every probe file, `engine_fingerprint` and `tls_echo` included. Those
    files are the ones that most need attributing, because a fingerprint that
    cannot be tied to a machine explains nothing about a difference between
    machines.
    """

    def test_a_hand_built_row_is_filled(self, tmp_path, monkeypatch):
        row = written(tmp_path, monkeypatch,
                      {"verdict": "cell_stopped", "cell": "x"})
        for column in COLUMNS:
            assert row[column] is not None

    def test_a_present_but_none_column_is_filled(self, tmp_path, monkeypatch):
        """`blank_row` builds from `dict.fromkeys`, so the keys exist as None.

        `setdefault` would see the key and leave it, which is how this change
        could ship looking correct and record nothing on the path that matters
        most.
        """
        row = written(tmp_path, monkeypatch, {"host": None, "host_cpus": None})
        assert row["host"] is not None
        assert row["host_cpus"] is not None

    def test_an_explicit_value_survives(self, tmp_path, monkeypatch):
        row = written(tmp_path, monkeypatch, {"host": "elsewhere"})
        assert row["host"] == "elsewhere"

    def test_an_operator_label_is_still_masked(self, tmp_path, monkeypatch):
        """The label is published verbatim, and verbatim goes through the mask.

        Nobody should name a host by its address, and the guarantee in this
        module is that `data/runs/` is publishable whatever reached it.

        The fixture is a real public address rather than the documentation range
        `203.0.113.0/24`, which is the obvious choice and does not work: `_mask`
        keys on `ip_address.is_global`, and every reserved range answers False
        there, so a test written with a documentation address passes an unmasked
        value and asserts nothing.
        """
        monkeypatch.setenv(host.ENV_LABEL, "8.8.8.8")
        host._measured.cache_clear()
        try:
            row = written(tmp_path, monkeypatch, {"engine": "x"})
        finally:
            host._measured.cache_clear()
        assert row["host"] == "8.8.8.0"


class TestTheDefaultLabelIsPublishable:
    def test_it_does_not_contain_the_hostname(self, monkeypatch):
        node = platform.node()
        if not node:
            return                       # nothing to leak on this machine
        label = fresh(monkeypatch)["host"]
        assert node.lower() not in label.lower(), (
            "the default label carries the hostname into a public repository. "
            "It has to be the hash")
        assert label.startswith("h:")

    def test_an_operator_label_is_used_verbatim(self, monkeypatch):
        assert fresh(monkeypatch, "vps")["host"] == "vps"

    def test_the_label_is_stable_for_one_machine(self, monkeypatch):
        assert fresh(monkeypatch)["host"] == fresh(monkeypatch)["host"]

    def test_a_blank_variable_falls_back_rather_than_writing_an_empty_label(
            self, monkeypatch):
        """`NMBENCH_HOST=` in a shell profile is an unset variable, not a name.

        An empty string here would group every row of every machine under one
        falsy label and read as a single host.
        """
        assert fresh(monkeypatch, "   ")["host"] == fresh(monkeypatch)["host"]


class TestTheOtherTwoColumnsSayWhatTheyClaim:
    def test_the_os_names_system_release_and_architecture(self):
        text = host.os_name()
        assert platform.system() in text
        assert platform.machine() in text
        assert "unknown" not in text or not platform.system()

    def test_the_cpu_count_is_the_machine_and_not_the_browser(self):
        """`os.cpu_count()`, so it can be compared against what an engine
        reports to a page. An engine that spoofs the core count is only
        measurable when both numbers are on record."""
        assert host.facts()["host_cpus"] == os.cpu_count()
