"""Stopping a run in flight: the watch in `nmbench/stop.py` and what it does
with the rows the run had already measured.

Tested here rather than left to the run because of what is being decided. By the
time anybody presses stop, the attempts are on disk - `JsonlSink.write` appends
per attempt - so the file is a complete record of an incomplete matrix, and the
two answers the operator is offered are "file it where nothing charts it" and
"delete it". Both are irreversible in the direction that matters: `discard`
removes measurements that cost metered proxy traffic, and `keep` that quietly
left the file in `data/runs/` would pool a truncated matrix into every chart on
the dashboard with nothing saying so.

That second one is the reason this file exists at all. The cells a stopped run
never reached are exactly the ones the round-robin had not got to yet, so what
is missing is chosen by position in the plan rather than at random - a partial
run pooled into the corpus does not make the numbers noisier, it moves them.

**Addressed to `nmbench.stop` and not to `scripts/benchmark.py`, since
2026-09-23, and one of these tests had to fail to make that a rule.** The
machinery moved into the package that day so the warm-up ladder could share it;
these tests went on loading the script by path and reading the names off it,
which still resolved, because the script imports them. `monkeypatch.setattr` on
that borrowed name does not - `dispose` reads `STOPPED_DIR` from the module that
defines it. So three tests ran against the real `data/runs/stopped/` and put a
file in it. It was a fixture's single fake row and the cost was nothing; the
same edit against a real run's path would have moved a real file, and nothing in
the failure said which directory had been written to.

Nothing here sends anything or opens a browser, and loading the package module
rather than the script means nothing imports an engine either.
"""
import importlib.util
import json
from pathlib import Path

import pytest

from nmbench import stop as benchmark


class FakeSink:
    def __init__(self, path):
        self.path = path


class FakeStore:
    def __init__(self, directory):
        self.dir = directory


@pytest.fixture
def run_files(tmp_path):
    """A run's two artefacts as they sit when a stop arrives: rows and bodies."""
    rows = tmp_path / "benchmark_20260923T090000Z.jsonl"
    rows.write_text('{"verdict": "ok"}\n', encoding="utf-8")
    bodies = tmp_path / "artifacts" / "benchmark_20260923T090000Z"
    bodies.mkdir(parents=True)
    (bodies / "one.html.gz").write_bytes(b"body")
    return FakeSink(rows), FakeStore(bodies)


class TestTheWatch:
    """`StopWatch`, which is asked once per attempt and must cost nothing."""

    def test_no_path_never_trips(self):
        watch = benchmark.StopWatch(None)
        assert watch.check() is False
        assert watch.tripped is False

    def test_a_missing_file_is_not_a_stop(self, tmp_path):
        watch = benchmark.StopWatch(tmp_path / "job.stop")
        assert watch.check() is False

    def test_the_file_appearing_is_a_stop(self, tmp_path):
        path = tmp_path / "job.stop"
        watch = benchmark.StopWatch(path)
        assert watch.check() is False
        path.write_text(json.dumps({"keep": "keep", "requested": "2026-09-23T09:00:00Z"}),
                        encoding="utf-8")
        assert watch.check() is True
        assert watch.keep is True
        assert "2026-09-23T09:00:00Z" in watch.reason

    def test_discard_is_read_off_the_file(self, tmp_path):
        path = tmp_path / "job.stop"
        path.write_text(json.dumps({"keep": "discard"}), encoding="utf-8")
        watch = benchmark.StopWatch(path)
        assert watch.check() is True
        assert watch.keep is False
        assert "discarded" in watch.reason

    def test_an_unparsable_file_still_stops_the_run(self, tmp_path):
        # The failure mode this avoids is the expensive one: a button that
        # looks pressed and does nothing. Carrying on because the operator's
        # answer was malformed spends the rest of the run and then asks him
        # again.
        path = tmp_path / "job.stop"
        path.write_text("{not json", encoding="utf-8")
        watch = benchmark.StopWatch(path)
        assert watch.check() is True

    def test_an_unparsable_file_keeps_the_rows(self, tmp_path):
        # And it defaults to the answer that destroys nothing. `keep` can be
        # undone by hand and `discard` cannot.
        path = tmp_path / "job.stop"
        path.write_text("{not json", encoding="utf-8")
        watch = benchmark.StopWatch(path)
        watch.check()
        assert watch.keep is True

    def test_it_stays_tripped_once_it_has_tripped(self, tmp_path):
        path = tmp_path / "job.stop"
        path.write_text(json.dumps({"keep": "discard"}), encoding="utf-8")
        watch = benchmark.StopWatch(path)
        assert watch.check() is True
        path.unlink()
        # The file being removed underneath a stopping run must not un-stop it.
        # The run is unwinding through two loops and an open browser by then and
        # there is no path back into the plan.
        assert watch.check() is True
        assert watch.keep is False


class TestWhereTheRowsGo:
    """`dispose`, which is the half of a stop that cannot be taken back."""

    def test_keep_moves_the_file_out_of_the_glob(self, run_files, monkeypatch,
                                                 tmp_path):
        sink, store = run_files
        stopped = tmp_path / "stopped"
        monkeypatch.setattr(benchmark, "STOPPED_DIR", stopped)
        said = benchmark.dispose(sink, store, keep=True)
        assert not sink.path.exists()
        assert (stopped / sink.path.name).exists()
        assert "kept raw" in said

    def test_keep_preserves_the_bytes(self, run_files, monkeypatch, tmp_path):
        sink, store = run_files
        stopped = tmp_path / "stopped"
        monkeypatch.setattr(benchmark, "STOPPED_DIR", stopped)
        benchmark.dispose(sink, store, keep=True)
        assert (stopped / sink.path.name).read_text(
            encoding="utf-8") == '{"verdict": "ok"}\n'

    def test_keep_leaves_the_bodies_alone(self, run_files, monkeypatch,
                                          tmp_path):
        # The rows moved and the bodies did not, which is asymmetric and
        # deliberate: nothing globs the artifact directory looking for runs, so
        # bodies in place cost disk and mislead nobody.
        sink, store = run_files
        monkeypatch.setattr(benchmark, "STOPPED_DIR", tmp_path / "stopped")
        benchmark.dispose(sink, store, keep=True)
        assert store.dir.exists()

    def test_discard_deletes_both(self, run_files):
        sink, store = run_files
        said = benchmark.dispose(sink, store, keep=False)
        assert not sink.path.exists()
        assert not store.dir.exists()
        assert "discarded" in said

    def test_discard_survives_a_run_with_no_bodies(self, tmp_path):
        # `--no-bodies` was passed, or the run was stopped before anything was
        # worth keeping. The directory never existed and removing it must not
        # be the thing that throws on the way out of a run that is already over.
        rows = tmp_path / "benchmark_x.jsonl"
        rows.write_text("{}\n", encoding="utf-8")
        said = benchmark.dispose(FakeSink(rows),
                                 FakeStore(tmp_path / "never"), keep=False)
        assert not rows.exists()
        assert "discarded" in said

    def test_a_move_that_fails_says_where_the_file_still_is(self, run_files,
                                                           monkeypatch,
                                                           tmp_path):
        # Reported and not raised. The run is over and its exit code is
        # decided; a traceback here would take the summary with it and leave
        # the operator with less than a sentence saying what to move by hand.
        sink, store = run_files
        blocked = tmp_path / "blocked"
        blocked.write_text("not a directory", encoding="utf-8")
        monkeypatch.setattr(benchmark, "STOPPED_DIR", blocked / "stopped")
        said = benchmark.dispose(sink, store, keep=True)
        assert str(sink.path) in said
        assert sink.path.exists()

    def test_the_stopped_directory_is_under_runs(self):
        # One level down, and that is the whole mechanism: every consumer here
        # globs `data/runs/*.jsonl` without recursing, so a file one directory
        # deeper is on disk and in no chart. A sibling directory elsewhere
        # would work equally well and would be somewhere nobody looks.
        assert benchmark.STOPPED_DIR.parent.name == "runs"
        assert benchmark.STOPPED_DIR.name == "stopped"


class TestTheExitCode:
    def test_a_stop_is_neither_success_nor_failure(self):
        # Zero says the run did what it was asked, which it did not, and 1 is
        # what every ordinary failure leaves. The queue does not read this - it
        # reads the stop mark on the job - but a hand-run benchmark has nothing
        # else, and a supervisor that only sees the code should be able to tell
        # a stop from a crash.
        assert benchmark.EXIT_STOPPED not in (0, 1)

    def test_a_stop_is_not_a_lost_transport(self):
        # Separate exceptions on purpose. One is the machine losing its
        # transport and is worth a gateway probe before restarting; the other
        # is a run that was working. Sharing a class would put "the operator
        # pressed stop" in the bucket the transport watch exists to keep clean.
        #
        # The only test here that loads the script, because `TransportLost`
        # stayed in it, and loaded inside the test rather than at import so the
        # other twelve do not pay for an engine import to check a directory
        # name.
        path = Path(__file__).resolve().parent.parent / "scripts" / "benchmark.py"
        spec = importlib.util.spec_from_file_location("benchmark_script", path)
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        assert not issubclass(benchmark.StoppedByOperator, script.TransportLost)
        assert not issubclass(script.TransportLost, benchmark.StoppedByOperator)
