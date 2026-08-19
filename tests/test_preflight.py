"""The runner's refusal to run a matrix it cannot run honestly.

This is the mechanism behind one of this repository's own rules: a capability
only some engines have must be refused by the runner, never dropped quietly. It
is covered here because that rule has failed three times, each time the same
shape and each time only visible in the output as an engine difference:

- `--humanize` was accepted for any matrix and only Camoufox implemented it, so
  a run with the flag compared humanized Camoufox against unhumanized
  everything else
- `--preset` defaulted to `light` and `supports_blocking` was not guarded at
  all, so the first mixed matrix blocked resources for two columns and not for
  the other three. Measured 2026-08-13 on one google_serp attempt each: 4 KB
  against 9.9 MB on the same refusal page, a 2000x engine difference produced
  entirely by our own flag, and blocking moves verdicts too
- `--geo align` was accepted for any engine declaring the capability while the
  runner passed only Camoufox's boolean, so patchright, cloak and zendriver ran
  unaligned and their rows said `geo=align`

None of the three broke a test, and none of them was visible in a row. That is
what makes preflight worth a suite rather than a reading: it is the only thing
standing between a flag and a number nobody can attribute, and the failure it
prevents looks exactly like a result.

Every case is derived from `engines.REGISTRY` rather than from a list of names
here. A hardcoded pair goes stale the moment an engine is added - which is how
the last one arrived - and the whole point of the registry is that nothing
outside it knows which engines exist.

The last class covers the plan warning rather than a refusal, and it is here
because it is the same moment: everything the runner tells you before it has
spent anything.

Offline in full. Preflight sends nothing by construction: it reads installed
packages, declared capabilities and whether credentials exist, which is why it
is allowed to run ahead of the `--dry-run` return. The subprocess cases all pass
`--dry-run` and `--direct`, so they need no credentials and reach no network.
"""
import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from nmbench import engines, matrix, providers
from nmbench.blocking import SCRIPT_BLOCKING
from nmbench.targets import TARGETS

ROOT = Path(__file__).resolve().parent.parent


def load():
    """`benchmark.py` is a script and stays one, so it is loaded from its path.

    Importing it runs `tolerate_unencodable_output`, which is harmless under
    pytest: the guard reconfigures the handles it can and passes on the ones it
    cannot.
    """
    path = ROOT / "scripts" / "benchmark.py"
    spec = importlib.util.spec_from_file_location("benchmark", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


benchmark = load()

# The four flags preflight guards, each with the engine attribute that decides
# whether a cell may carry it. Parametrized rather than written out four times,
# because the failure mode is one mechanism and a fifth capability should cost
# one line here.
CAPABILITIES = [
    ("headful", True, "supports_headful"),
    ("geo", "align", "supports_geo_align"),
    ("preset", "light", "supports_blocking"),
    ("humanize", True, "supports_humanize"),
]


class Refused(Exception):
    """What `parser.error` does: report and stop, never return."""


class Parser:
    def error(self, message):
        raise Refused(message)


def engines_with(attribute: str, value: bool) -> list:
    return sorted(name for name, engine in engines.REGISTRY.items()
                  if getattr(engine, attribute) is value)


def args(**overrides):
    """A parsed command line with the defaults preflight reads.

    `direct` defaults True so the credential check at the end of preflight does
    not fire: the autouse fixture strips the gateway variables, and a test about
    a capability must not pass because of a missing `.env`.
    """
    settings = {"headful": False, "geo": "off", "preset": "none",
                "humanize": False, "direct": True}
    settings.update(overrides)
    return argparse.Namespace(**settings)


def cells(*names, target="bing_serp", preset="none"):
    return [matrix.Cell(engine=name, target=target, preset=preset,
                        country="us", direct=True) for name in names]


def proxied(*names, country="us", provider=""):
    """Cells that need a gateway, which is what reaches the provider checks."""
    return [matrix.Cell(engine=name, target="bing_serp", preset="none",
                        country=country, direct=False, provider=provider)
            for name in (names or [sorted(engines.REGISTRY)[0]])]


def synthetic(name, **fields):
    """A provider built rather than loaded.

    Preflight is handed whatever `main` resolved and does two things with each
    one: derives its credential variables from its id, and asks it to build a
    username carrying the country. Both are properties of the object, so
    constructing it is more direct than writing a file - and it keeps these tests
    independent of which definitions happen to be shipped, which is the axis
    being varied here.
    """
    settings = {"id": name, "label": name, "host": "gw.example.invalid",
                "port": 8080, "known_params": frozenset({"country", "sid"})}
    settings.update(fields)
    return providers.Provider(**settings)


def chosen(*names):
    """The providers a matrix holds, resolved the way `main` resolves them.

    Real definitions rather than stand-ins. What preflight does with each one is
    derive its credential variable names from its id, and a fake would have to
    reimplement the derivation this is here to cover.
    """
    return {name: providers.load(name)
            for name in (names or [providers.default_name()])}


@pytest.fixture(autouse=True)
def every_engine_installed(monkeypatch):
    """Availability is a separate question and it is asked first.

    Without this, every test below would pass on a machine with no browsers by
    refusing the matrix for the wrong reason - and would then keep passing after
    someone deleted the capability checks entirely.
    """
    monkeypatch.setattr(engines, "report_availability",
                        lambda: dict.fromkeys(engines.REGISTRY))


class TestACapabilityOnlySomeEnginesHave:
    @pytest.mark.parametrize("flag, value, attribute", CAPABILITIES,
                             ids=lambda v: str(v))
    def test_a_mixed_matrix_is_refused(self, flag, value, attribute):
        """The whole rule in one assertion: the flag is either applied to every
        column or the run does not start."""
        able = engines_with(attribute, True)
        unable = engines_with(attribute, False)
        assert able and unable, (
            f"{attribute} is no longer an axis: every engine answers the same, "
            f"so this test proves nothing. Delete it or the flag.")
        with pytest.raises(Refused) as refusal:
            benchmark.preflight(Parser(), args(**{flag: value}),
                                cells(able[0], unable[0]), chosen())
        assert unable[0] in str(refusal.value), (
            "the refusal has to name the engine that cannot, or the operator "
            "is told a matrix is wrong without being told which column to drop")

    @pytest.mark.parametrize("flag, value, attribute", CAPABILITIES,
                             ids=lambda v: str(v))
    def test_every_engine_able_is_allowed(self, flag, value, attribute):
        """The other half, and the one that stops the guard being a refusal to
        run anything. An axis nobody can run is not measured either."""
        able = engines_with(attribute, True)
        benchmark.preflight(Parser(), args(**{flag: value}), cells(*able),
                            chosen())

    @pytest.mark.parametrize("flag, value, attribute", CAPABILITIES,
                             ids=lambda v: str(v))
    def test_the_flag_off_asks_nothing_of_anyone(self, flag, value, attribute):
        """An engine without the capability is a normal engine. Refusing it
        whenever it appears would make the registry a list of Camoufox."""
        benchmark.preflight(Parser(), args(),
                            cells(*engines_with(attribute, False)), chosen())

    def test_one_engine_that_cannot_is_enough(self):
        """Not a property of the mixture: a single-column matrix asking for a
        capability that column does not have is the same wrong run, and the
        row would still record the flag."""
        unable = engines_with("supports_headful", False)
        with pytest.raises(Refused):
            benchmark.preflight(Parser(), args(headful=True), cells(unable[0]),
                                chosen())


class TestTheRestOfPreflight:
    def test_an_engine_that_cannot_run_here_stops_the_matrix(self, monkeypatch):
        """A missing binary costs a message, never half a run. Reported by name
        so the answer is an install command rather than a bisect."""
        name = sorted(engines.REGISTRY)[0]
        monkeypatch.setattr(
            engines, "report_availability",
            lambda: {n: (f"{n} is not installed" if n == name else None)
                     for n in engines.REGISTRY})
        with pytest.raises(Refused, match="not installed"):
            benchmark.preflight(Parser(), args(), cells(name), chosen())

    def test_a_preset_the_target_cannot_survive_is_refused(self):
        """`validate_preset` is per cell rather than per engine, because it is
        the target that decides: blocking script against a target that builds
        its results in the browser returns the no-JS stub for every attempt and
        records our own preset as the target's refusal."""
        preset = sorted(SCRIPT_BLOCKING)[0]
        target = next(name for name, t in TARGETS.items()
                      if getattr(t, "needs_script", False))
        engine = engines_with("supports_blocking", True)[0]
        with pytest.raises(Refused, match=target):
            benchmark.preflight(Parser(), args(preset=preset),
                                cells(engine, target=target, preset=preset),
                                chosen())

    def test_a_proxied_matrix_without_credentials_is_refused(self):
        """The check is reached only when the run needs the gateway, and it says
        what to do. Without it the first cell opens a browser, fails to build a
        proxy URL and the run dies having launched one.

        Matched on the variable name rather than on the word "credentials",
        because naming the value is the whole job here. `config.credentials`
        refuses for two causes - no account, or a definition with no gateway
        address - and a message that described only the first sent anyone adding
        their own provider to check a login that was already set.
        """
        with pytest.raises(Refused, match="NODEMAVEN_LOGIN"):
            benchmark.preflight(Parser(), args(direct=False), proxied(), chosen())

    def test_the_direct_control_needs_no_credentials(self):
        """Engine screening holds the address constant and never touches the
        gateway, so it has to stay runnable on a machine with no account."""
        benchmark.preflight(Parser(), args(), cells(sorted(engines.REGISTRY)[0]),
                            chosen())


class TestTheProviderAxis:
    """Every provider in the matrix is checked, not the one that happens to be
    first.

    A run that started with one provider's credentials missing would write a full
    column for the gateway that answered and an error column for the one that
    could not, into one file, in one time window - which is the exact shape a
    provider comparison is read in. Nothing in the output would say the second
    column never had an account.
    """

    def credentials(self, monkeypatch, name):
        monkeypatch.setenv(f"{name.upper()}_LOGIN", "login")
        monkeypatch.setenv(f"{name.upper()}_PASSWORD", "secret")

    @pytest.mark.parametrize("missing", ["alpha", "omega"])
    def test_a_provider_without_credentials_is_refused_by_name(self, monkeypatch,
                                                               missing):
        """Parametrized over which one is missing because the loop is `sorted`,
        so a check that only read the first would pass on one of these two."""
        present = "omega" if missing == "alpha" else "alpha"
        self.credentials(monkeypatch, present)
        with pytest.raises(Refused) as refusal:
            benchmark.preflight(
                Parser(), args(direct=False), proxied(),
                {name: synthetic(name) for name in ("alpha", "omega")})
        assert missing in str(refusal.value)
        assert f"{missing.upper()}_LOGIN" in str(refusal.value), (
            "the refusal has to name the variable to set, or the operator is "
            "told an account is missing without being told what to fill in")

    def test_a_definition_with_no_gateway_address_names_the_host_variable(
            self, monkeypatch):
        """The likely first failure for a stranger, and it is not the account.

        `data/providers/_template.toml` ships `host = ""` and `port = 0`, so
        anyone adding their own gateway by copying it has a definition that
        cannot reach anything while their login is already in `.env`. Preflight
        asked `config.available()` until 2026-08-19 and answered every refusal
        with "set LOGIN and PASSWORD", which sends that reader to check the one
        value they got right.
        """
        self.credentials(monkeypatch, "alpha")
        with pytest.raises(Refused) as refusal:
            benchmark.preflight(Parser(), args(direct=False), proxied(),
                                {"alpha": synthetic("alpha", host="", port=0)})
        assert "ALPHA_HOST" in str(refusal.value)
        assert "ALPHA_LOGIN" not in str(refusal.value), (
            "the login is set, so naming it sends the reader to the one value "
            "that is not the problem")

    def test_both_credentialled_providers_are_allowed(self, monkeypatch):
        """The other half. A guard that refused every multi-provider matrix
        would leave the axis unmeasurable, which is the same as not having it."""
        for name in ("alpha", "omega"):
            self.credentials(monkeypatch, name)
        benchmark.preflight(Parser(), args(direct=False), proxied(),
                            {name: synthetic(name)
                             for name in ("alpha", "omega")})

    def test_a_provider_that_cannot_carry_the_country_is_refused(self, monkeypatch):
        """The country reaches the gateway inside the username, so a provider
        whose dialect has no country parameter cannot join a country axis. It has
        to be refused here for the reason every check in this file exists: the
        measured gateway answers an unknown parameter name with 200 and the
        setting dropped, so the run would complete and every row would claim a
        country that was never applied."""
        self.credentials(monkeypatch, "alpha")
        with pytest.raises(Refused, match="country"):
            benchmark.preflight(
                Parser(), args(direct=False), proxied(),
                {"alpha": synthetic("alpha",
                                    known_params=frozenset({"sid"}))})

    def test_a_direct_matrix_asks_nothing_of_any_provider(self, monkeypatch):
        """Direct never reaches a gateway, so a machine with no account at all
        still runs the control - and that is most of the engine screening."""
        benchmark.preflight(Parser(), args(), cells(sorted(engines.REGISTRY)[0]),
                            {"alpha": synthetic("alpha")})

    def test_the_axis_is_refused_when_nothing_leaves_through_a_gateway(self):
        """`--direct --providers a,b` would build one identical direct experiment
        per provider and present the copies as a comparison. Refused in `main`
        rather than in preflight, so it is checked through the real parser."""
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "benchmark.py"),
             "--dry-run", "--direct", "--preset", "none", "--engines", "http",
             "--targets", "bing_serp", "--queries", "1",
             "--providers", providers.default_name()],
            capture_output=True, text=True, timeout=120, cwd=ROOT)
        assert result.returncode != 0
        assert "identical copies" in result.stderr

    def test_an_unknown_provider_says_what_is_present(self):
        """A definition is a file, so the answer to a typo is a directory
        listing rather than a source read."""
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "benchmark.py"),
             "--dry-run", "--preset", "none", "--engines", "http",
             "--targets", "bing_serp", "--queries", "1",
             "--providers", "nosuchprovider"],
            capture_output=True, text=True, timeout=120, cwd=ROOT)
        assert result.returncode != 0
        assert "data/providers" in result.stderr
        assert providers.default_name() in result.stderr

    def test_a_parameter_no_provider_recognises_is_refused_before_the_plan(self):
        """`--param` is validated against every provider in the matrix, because a
        parameter one gateway knows and another does not would otherwise raise
        mid-run, after the earlier providers' cells had already spent traffic to
        learn something that was local all along."""
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "benchmark.py"),
             "--dry-run", "--preset", "none", "--engines", "http",
             "--targets", "bing_serp", "--queries", "1",
             "--param", "bogus=1"],
            capture_output=True, text=True, timeout=120, cwd=ROOT)
        assert result.returncode != 0
        assert "NOT be applied" in result.stderr
        assert "dry run: nothing was sent" not in result.stdout


class TestItRunsBeforeTheCostEstimate:
    """A dry run that prices a matrix the runner will refuse answered the wrong
    question, and this was live until 2026-08-13: a mixed blocking matrix passed
    the dry run and was rejected only once a real run was launched.

    Everything preflight reads is local, so running it under `--dry-run` sends
    nothing and costs nothing.
    """

    def test_an_impossible_matrix_does_not_get_a_dry_run(self):
        """End to end through the real parser, because the ordering being pinned
        is between two statements in `main` and nothing else can see it.

        `http` is the engine here for one reason: it is plain `requests`, so its
        `check` always passes and this cannot be the availability refusal
        wearing the right exit code. `--preset none` for the same reason - the
        default is `light` and this engine has no blocking either, so without it
        the run would be refused on an axis this test is not about.
        """
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "benchmark.py"),
             "--dry-run", "--direct", "--headful", "--preset", "none",
             "--engines", "http", "--targets", "bing_serp", "--queries", "1"],
            capture_output=True, text=True, timeout=120, cwd=ROOT)
        assert result.returncode != 0
        assert "headful" in result.stderr
        assert "dry run: nothing was sent" not in result.stdout

    def test_a_runnable_matrix_still_gets_one(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "benchmark.py"),
             "--dry-run", "--direct", "--preset", "none",
             "--engines", "http", "--targets", "bing_serp", "--queries", "1"],
            capture_output=True, text=True, timeout=120, cwd=ROOT)
        assert result.returncode == 0, result.stderr[-1500:]
        assert "dry run: nothing was sent" in result.stdout


class TestThePlanSaysWhenItHasLostTheInterleaving:
    """The other thing the runner has to say before it spends anything, and the
    only one that is a warning rather than a refusal.

    Cells are interleaved at batch granularity, which is what makes two engines
    comparable at all. `--batch` equal to `--queries` leaves exactly one batch
    per cell, so the round-robin has nothing to alternate and the cells run end
    to end: whatever drifts over the window lands on whichever went first.
    `benchmark_20260814T102344Z` spent 20 attempts finding that out on Walmart,
    where the first query of the window was served a full result page and every
    one after it met the PerimeterX hold-button, across three later browsers.

    Not guarded, because the sequential shape is correct when the cells share
    nothing. It is lost to a pair of flags rather than to a code path, so the
    runner says so and the operator decides.

    Driven through the real parser and the real planner: what is being pinned is
    a condition over the plan, and a fake plan would let the condition drift
    away from the arithmetic that produces it.
    """

    def plan(self, *extra):
        """Two cells from one engine and two targets.

        Two engines would be the more obvious way to get two cells and it is the
        wrong one here: every engine except `http` can be absent from a working
        checkout, and this class would then fail on the availability refusal
        while claiming to be about interleaving. It did, against an interpreter
        that had pytest and not `curl_cffi`. The warning counts cells and does
        not care which axis produced them, so the axis that needs no install is
        the one to use.
        """
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "benchmark.py"),
             "--dry-run", "--direct", "--preset", "none",
             "--engines", "http", "--targets", "bing_serp,ddg_serp", *extra],
            capture_output=True, text=True, timeout=120, cwd=ROOT)

    def test_one_batch_per_cell_is_reported(self):
        result = self.plan("--queries", "5", "--batch", "5")
        assert result.returncode == 0, result.stderr[-1500:]
        assert "one batch per cell" in result.stdout

    def test_a_matrix_that_still_interleaves_is_not_warned_about(self):
        """The warning has to be silent on a normal run or it becomes noise,
        and a warning nobody reads is the same as no warning."""
        result = self.plan("--queries", "10", "--batch", "2")
        assert result.returncode == 0, result.stderr[-1500:]
        assert "one batch per cell" not in result.stdout

    def test_a_single_cell_is_not_warned_about(self):
        """There is nothing to interleave with, so running in sequence is the
        only shape available and saying so would be wrong."""
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "benchmark.py"),
             "--dry-run", "--direct", "--preset", "none", "--engines", "http",
             "--targets", "bing_serp", "--queries", "5", "--batch", "5"],
            capture_output=True, text=True, timeout=120, cwd=ROOT)
        assert result.returncode == 0, result.stderr[-1500:]
        assert "one batch per cell" not in result.stdout
