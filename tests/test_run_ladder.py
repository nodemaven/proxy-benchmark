"""The ladder supervisor: what it puts on the probe's command line, and when
it decides an attempt is worth starting again.

New on 2026-09-23, with the stop button, and the first tests this script has
ever had. It was written 2026-08-26 and left untested for a month because
everything it does is either a string it hands to a subprocess or a decision it
makes about one, and both looked like things you could read. The retry loop is
what changed that: it branches on an exit code, and adding a third meaning to
that code - stopped, alongside finished and died - put a wrong answer one line
away.

Nothing here launches a browser, opens a socket or writes a run file. The loop
is driven by replacing `stream`, which is the only function in the script that
starts anything.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load():
    path = ROOT / "scripts" / "run_ladder.py"
    spec = importlib.util.spec_from_file_location("run_ladder_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ladder = load()


class Plan:
    """The arguments `build_command` reads, at their parser defaults."""

    engines = "patchright"
    targets = "google_serp"
    countries = "any"
    entry = "home"
    warm = "off,L1,N1,L2,L3"
    geo = "off"
    humanize = "off"
    identities = 60
    series = 3
    gap = "8,20"
    dwell = "20,45"
    breaker = 12
    redraws = 2
    params = "none"
    preset = "none"
    headless = False
    seed = None
    stop_file = None


def pairs(command):
    """The command as a dict, so a test can ask for one flag by name."""
    out = {}
    for index, item in enumerate(command):
        if item.startswith("--") and index + 1 < len(command):
            follows = command[index + 1]
            out[item] = None if follows.startswith("--") else follows
    return out


class TestWhatReachesTheProbe:
    def test_the_stop_file_is_forwarded(self):
        plan = Plan()
        plan.stop_file = "/tmp/job.stop"
        assert pairs(ladder.build_command(plan))["--stop-file"] == "/tmp/job.stop"

    def test_no_stop_file_means_no_flag(self):
        # Absent rather than empty. `--stop-file ""` would reach `StopWatch`,
        # which treats a falsy path as "no path" and would work by accident;
        # relying on that puts the behaviour of an unwatched run inside another
        # module's truthiness test.
        assert "--stop-file" not in ladder.build_command(Plan())

    def test_every_axis_is_passed_through(self):
        # The standing reason is in the note above `build_command`: this script
        # once held its own default for `--entry` while the probe held a
        # different one, and silently overrode the probe for a whole eleven-hour
        # run. The guard is that no axis is decided here, so all of them appear.
        got = pairs(ladder.build_command(Plan()))
        for flag in ("--engines", "--targets", "--countries", "--entry",
                     "--warm", "--geo", "--humanize", "--identities",
                     "--series", "--gap", "--dwell", "--breaker", "--redraws",
                     "--params", "--preset"):
            assert flag in got, flag


class TestWhenAnAttemptIsStartedAgain:
    """The loop in `main`, driven with `stream` replaced.

    Each test hands it a list of exit codes and reads how many it consumed,
    which is the only thing the loop decides.
    """

    def drive(self, monkeypatch, codes, argv=(), elapsed=0.0):
        seen = []
        clock = iter([0.0, elapsed] * (len(codes) + 2))

        def fake_stream(command, log_path):
            seen.append(command)
            return codes[len(seen) - 1]

        monkeypatch.setattr(ladder, "stream", fake_stream)
        monkeypatch.setattr(ladder, "preflight", lambda args: None)
        monkeypatch.setattr(ladder.time, "sleep", lambda s: None)
        monkeypatch.setattr(ladder.time, "time", lambda: next(clock))
        monkeypatch.setattr(sys, "argv",
                            ["run_ladder.py", "--underpowered-ok",
                             "--direct-ok", *argv])
        code = ladder.main()
        return code, len(seen)

    def test_a_clean_run_is_not_repeated(self, monkeypatch):
        code, attempts = self.drive(monkeypatch, [0])
        assert (code, attempts) == (0, 1)

    def test_an_early_death_is_restarted(self, monkeypatch):
        # The behaviour the retry exists for: a browser that would not install,
        # a proxy that stopped answering. The clock has barely moved, so the
        # rungs stay comparable and a second attempt is free.
        code, attempts = self.drive(monkeypatch, [1, 0], elapsed=12.0)
        assert attempts == 2
        assert code == 0

    def test_a_late_death_is_left_alone(self, monkeypatch):
        # Above `EARLY_DEATH_S` the rows already on disk are worth more than a
        # second attempt at a different hour, which is the confound the
        # interleaving exists to remove.
        code, attempts = self.drive(monkeypatch, [1, 0],
                                    elapsed=ladder.EARLY_DEATH_S + 1)
        assert attempts == 1
        assert code == 1

    def test_a_stop_is_not_a_startup_failure(self, monkeypatch):
        # The defect this file was written for. A stop leaves a non-zero code,
        # so before `EXIT_STOPPED` was given its own branch this landed in the
        # early-death arm and the supervisor restarted the thirteen-hour run
        # the operator had just asked it to end - after sleeping 30 seconds, so
        # it would not even have looked prompt about it.
        code, attempts = self.drive(monkeypatch, [ladder.EXIT_STOPPED, 0],
                                    elapsed=12.0)
        assert attempts == 1
        assert code == ladder.EXIT_STOPPED

    def test_a_stop_at_hour_two_is_also_final(self, monkeypatch):
        # Same answer from the other side of `EARLY_DEATH_S`, so the test does
        # not pass only because the late-death branch happened to catch it.
        code, attempts = self.drive(monkeypatch, [ladder.EXIT_STOPPED, 0],
                                    elapsed=ladder.EARLY_DEATH_S + 1)
        assert attempts == 1
        assert code == ladder.EXIT_STOPPED

    def test_the_stop_code_is_one_the_loop_can_tell_apart(self):
        # If a stop ever exited 0 or 1 the branch above would be unreachable or
        # would swallow ordinary failures. Pinned here as well as in
        # `test_stop.py` because this is the caller that reads it.
        assert ladder.EXIT_STOPPED not in (0, 1)


class TestTheEstimate:
    """`estimate_hours`, which is the only warning before a 13-hour launch."""

    def test_the_default_ladder_is_about_thirteen_hours(self):
        assert 12.0 < ladder.estimate_hours(Plan()) < 14.0

    def test_a_second_cursor_mode_doubles_it(self):
        # `--identities` is per cell, so a second mode is a second full pass.
        # This is the trap the estimate was made to print: nothing in
        # `--humanize off,trueman` looks like it costs anything.
        one, two = Plan(), Plan()
        two.humanize = "off,trueman"
        assert ladder.estimate_hours(two) == pytest.approx(
            2 * ladder.estimate_hours(one))

    @pytest.mark.parametrize("axis, second", [
        ("engines", "patchright,camoufox"),
        ("targets", "google_serp,amazon_search"),
        ("countries", "us,de"),
        ("entry", "home,url"),
        ("geo", "off,align"),
        ("params", "none,filter=medium"),
    ])
    def test_every_cell_axis_doubles_it(self, axis, second):
        # Cursor mode was the only one of these counted until 2026-09-23, so a
        # two-engine ladder was estimated at half its length. Parametrised
        # rather than written once, because the defect was an axis being
        # *absent* and a single example would have been satisfied by the one
        # axis that already worked.
        one, two = Plan(), Plan()
        setattr(two, axis, second)
        assert ladder.estimate_hours(two) == pytest.approx(
            2 * ladder.estimate_hours(one))

    def test_a_plus_inside_an_arm_is_not_a_second_arm(self):
        # `--params` is the one axis whose values have internal structure:
        # settings within one arm join with `+` so that the comma stays free to
        # separate arms, exactly as it does on every other axis. Pricing that
        # value as two cells would over-estimate a filter comparison by half,
        # and the error would be in the flattering direction on the axis the
        # dashboard was built to offer.
        one, joined = Plan(), Plan()
        joined.params = "filter=medium+country=us"
        assert ladder.estimate_hours(joined) == pytest.approx(
            ladder.estimate_hours(one))

    def test_axes_multiply_each_other(self):
        # Three engines over two targets is six passes and not five.
        one, many = Plan(), Plan()
        many.engines = "a,b,c"
        many.targets = "x,y"
        assert ladder.estimate_hours(many) == pytest.approx(
            6 * ladder.estimate_hours(one))

    def test_every_axis_the_supervisor_forwards_is_priced(self):
        # The guard against the next one going missing. Any axis on the probe's
        # command line that multiplies cells has to be in `CELL_AXES`; `--warm`
        # is summed instead, and the rest are not axes.
        flags = {item[2:] for item in ladder.build_command(Plan())
                 if item.startswith("--")}
        multiplying = flags - {"warm", "identities", "series", "gap", "dwell",
                               "breaker", "redraws", "preset", "seed",
                               "headless", "stop-file"}
        assert multiplying == set(ladder.CELL_AXES)

    def test_an_unknown_rung_is_priced_at_the_deepest(self):
        # `WARM_ALIASES` is a hand copy of the probe's own table, so it can go
        # stale. The fallback makes a stale copy pessimistic and never
        # flattering, which is the direction that cannot mislead anyone into
        # launching something longer than they were told.
        plan, deepest = Plan(), Plan()
        plan.warm, deepest.warm = "L9", "L3"
        assert ladder.estimate_hours(plan) == pytest.approx(
            ladder.estimate_hours(deepest))
