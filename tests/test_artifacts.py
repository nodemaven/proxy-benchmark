"""Artifact store tests.

The store exists so that a wrong classifier costs a re-run of the analysis
rather than a re-run of the measurement. That only holds if two things are true:
failures are never dropped, and a saved body can be traced back to the attempt
that produced it. Both are checked here.

Nothing reaches the network. Bodies are written to tmp_path.
"""
import gzip

import pytest

from nmbench import artifacts
from nmbench.artifacts import ALWAYS_KEEP, ArtifactStore, group_key, slug


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts, "ARTIFACTS_DIR", tmp_path)
    made = ArtifactStore("test", run_id="20260811T000000Z")
    made.dir = tmp_path / "run"
    return made


def row(verdict="ok", engine="camoufox/light", target="bing_serp", **extra):
    return {"verdict": verdict, "engine": engine, "target": target, **extra}


class TestWhatIsKept:
    @pytest.mark.parametrize("verdict", sorted(ALWAYS_KEEP))
    def test_every_failure_is_kept(self, store, verdict):
        """These are the rows somebody will argue about. Sampling them would
        throw away the evidence for the conclusion."""
        for _ in range(20):
            assert store.save(row(verdict), "<html></html>") is not None

    def test_successes_are_sampled(self, store):
        saved = [store.save(row("ok"), "<html></html>") for _ in range(10)]
        assert sum(1 for s in saved if s) == store.sample_ok

    def test_the_sample_is_per_engine_and_target(self, store):
        """A cell that passes constantly must not crowd out the evidence from
        one that passes rarely."""
        store.save(row("ok", target="bing_serp"), "<html></html>")
        store.save(row("ok", target="bing_serp"), "<html></html>")
        assert store.save(row("ok", target="bing_serp"), "<html></html>") is None
        assert store.save(row("ok", target="ddg_serp"), "<html></html>") is not None

    def test_engines_do_not_share_a_sample(self, store):
        for _ in range(store.sample_ok):
            store.save(row("ok", engine="camoufox/light"), "<html></html>")
        assert store.save(row("ok", engine="chromium/light"),
                          "<html></html>") is not None

    def test_an_empty_body_is_not_a_file(self, store):
        assert store.save(row("block"), "") is None


class TestDisabled:
    def test_nothing_is_written_when_switched_off(self, tmp_path, monkeypatch):
        monkeypatch.setattr(artifacts, "ARTIFACTS_DIR", tmp_path)
        off = ArtifactStore("test", enabled=False)
        off.dir = tmp_path / "run"
        assert off.save(row("captcha"), "<html></html>") is None
        assert not (tmp_path / "run").exists()

    def test_the_summary_says_so(self, tmp_path, monkeypatch):
        monkeypatch.setattr(artifacts, "ARTIFACTS_DIR", tmp_path)
        assert "not saved" in ArtifactStore("t", enabled=False).summary()


class TestTheBodyComesBack:
    def test_what_was_written_is_what_is_read(self, store, tmp_path, monkeypatch):
        body = "<html><body>unusual traffic — тест</body></html>"
        path = store.save(row("captcha"), body)
        assert path is not None
        written = next((tmp_path / "run").iterdir())
        with gzip.open(written, "rb") as fh:
            assert fh.read().decode("utf-8") == body

    def test_bodies_are_compressed(self, store, tmp_path):
        """A matrix run is thousands of near-identical pages. Uncompressed they
        do not fit on the machine doing the measuring."""
        store.save(row("block"), "<html>" + ("a" * 200_000) + "</html>")
        written = next((tmp_path / "run").iterdir())
        assert written.suffix == ".gz"
        assert written.stat().st_size < 20_000


class TestAttribution:
    def test_the_filename_names_the_attempt(self, store, tmp_path):
        store.save(row("captcha", engine="camoufox/light",
                       target="google_serp"), "<html></html>")
        name = next((tmp_path / "run").iterdir()).name
        assert "camoufox" in name and "google_serp" in name and "captcha" in name

    def test_names_do_not_collide(self, store, tmp_path):
        for _ in range(5):
            store.save(row("block"), "<html></html>")
        assert len(list((tmp_path / "run").iterdir())) == 5

    def test_a_slash_in_the_engine_does_not_make_a_directory(self, store, tmp_path):
        store.save(row("block", engine="camoufox-direct/aggressive"),
                   "<html></html>")
        assert len(list((tmp_path / "run").iterdir())) == 1

    @pytest.mark.parametrize("hostile", ["a/b", "a\\b", "a:b", "a*b", "..",
                                         "a b", "" , None])
    def test_slug_survives_a_filesystem(self, hostile):
        made = slug(hostile)
        assert not set(made) & set('/\\:*?"<>| ')
        assert made not in ("", ".", "..")


class TestGrouping:
    def test_the_key_does_not_depend_on_cell(self):
        """`cell` is added by the runner after the engine has filled the row, so
        it is still empty when a body is offered to the store. Keying on it
        would put every success in one bucket named None."""
        assert group_key(row()) == group_key(row(cell="something/else"))

    def test_engine_and_target_both_matter(self):
        assert group_key(row(engine="a")) != group_key(row(engine="b"))
        assert group_key(row(target="a")) != group_key(row(target="b"))


class TestFailureIsNotFatal:
    def test_an_unwritable_directory_does_not_stop_the_run(self, store,
                                                           monkeypatch):
        """A full disk loses a debugging aid. It must never lose an attempt."""
        def explode(*a, **k):
            raise OSError("no space left on device")

        monkeypatch.setattr(artifacts.Path, "mkdir", explode)
        assert store.save(row("captcha"), "<html></html>") is None
