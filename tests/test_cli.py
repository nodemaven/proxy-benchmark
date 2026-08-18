"""The dispatcher: `python -m nmbench <command>`.

Two properties matter and the rest is convenience. Commands must be discovered
from disk, so adding a probe stays a file and never an edit to a shared entry
point. And the dispatcher must not run anything the operator did not name: it
sits in front of scripts that spend money on a shared production pool, so an
unknown command has to be a refusal, not a guess at the nearest match.

Nothing here executes a probe. The command list is built by reading files.
"""
import subprocess
import sys
from pathlib import Path

import pytest

from nmbench import __main__ as cli

ROOT = Path(__file__).resolve().parent.parent


class TestDiscovery:
    def test_finds_the_runner(self):
        assert cli.discover()["benchmark"] == ROOT / "scripts" / "benchmark.py"

    def test_finds_every_script(self):
        on_disk = {p.stem.replace("_", "-")
                   for p in (ROOT / "scripts").rglob("*.py")
                   if "__pycache__" not in p.parts and not p.name.startswith("_")}
        assert set(cli.discover()) == on_disk

    def test_underscores_become_hyphens(self):
        """The file is `engine_fingerprint.py`; nobody types an underscore."""
        assert "engine-fingerprint" in cli.discover()

    def test_a_command_is_never_hardcoded(self):
        """Adding a probe is a file. A dispatcher holding a list of names is a
        second place to forget, and the one that is only noticed by an operator
        who cannot find the command they just wrote."""
        source = Path(cli.__file__).read_text(encoding="utf-8")
        for name in cli.discover():
            assert f'"{name}"' not in source


class TestDescriptions:
    @pytest.mark.parametrize("name", sorted(cli.discover()))
    def test_every_command_describes_itself(self, name):
        assert cli.summarize(cli.discover()[name])

    def test_the_summary_is_the_first_docstring_line(self, tmp_path):
        path = tmp_path / "x.py"
        path.write_text('"""First line.\n\nSecond paragraph.\n"""\n', encoding="utf-8")
        assert cli.summarize(path) == "First line."

    def test_a_file_without_a_docstring_is_not_an_error(self, tmp_path):
        path = tmp_path / "x.py"
        path.write_text("value = 1\n", encoding="utf-8")
        assert cli.summarize(path) == ""


class TestCost:
    """What a command costs is shown before it is typed, not discovered after."""

    def test_a_probe_spends_traffic_by_default(self):
        assert cli.spends_traffic(cli.discover()["google-429"], default=True)

    def test_a_script_can_declare_itself_offline(self):
        assert not cli.spends_traffic(cli.discover()["engine-fingerprint"],
                                      default=True)

    def test_the_declaration_is_read_and_not_imported(self, tmp_path):
        """Importing a script to learn what it costs would run it."""
        path = tmp_path / "x.py"
        path.write_text("raise SystemExit('imported')\nSENDS_REQUESTS = False\n",
                        encoding="utf-8")
        assert not cli.spends_traffic(path, default=True)


class TestDispatch:
    def run(self, *args):
        return subprocess.run([sys.executable, "-m", "nmbench", *args],
                              capture_output=True, text=True, timeout=120, cwd=ROOT)

    def test_no_arguments_lists_the_commands(self):
        result = self.run()
        assert result.returncode == 0
        assert "benchmark" in result.stdout

    def test_an_unknown_command_runs_nothing(self):
        result = self.run("nonsense")
        assert result.returncode == 2
        assert "unknown command" in result.stderr

    def test_a_typo_is_named_but_not_executed(self):
        result = self.run("benchmrk")
        assert result.returncode == 2
        assert "benchmark" in result.stderr

    def test_flags_reach_the_script(self):
        """--help proves the handover without sending anything."""
        result = self.run("benchmark", "--help")
        assert result.returncode == 0
        assert "--engines" in result.stdout

    def test_the_script_owns_the_error_message(self):
        """The flags belong to the script, so its parser has to be the one that
        rejects them, or an operator is sent to the wrong file to find out why."""
        result = self.run("benchmark", "--nope")
        assert result.returncode != 0
        assert "benchmark.py" in result.stderr
