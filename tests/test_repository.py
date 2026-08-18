"""Repository hygiene: syntax, imports, conventions, and no leaked credentials.

The syntax check exists because most of this repository is scripts, and a script
is only compiled when it is run. A typo in `scripts/probes/` would otherwise be
found by an operator who has already spent traffic getting to it.

The credential checks are the ones worth keeping even if the rest is deleted.
`Proxy-Authorization` is base64, not encryption, and this repository is going to
be published.
"""
import ast
import compileall
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "nmbench"
SCRIPTS = ROOT / "scripts"

SOURCES = sorted(p for p in list(PACKAGE.rglob("*.py")) + list(SCRIPTS.rglob("*.py"))
                 if "__pycache__" not in p.parts)

# Scripts that parse their arguments. The others read sys.argv directly and would
# treat `--help` as work to do, which for a probe means sending a request.
ARGPARSE_SCRIPTS = sorted(p for p in SCRIPTS.rglob("*.py")
                          if "import argparse" in p.read_text(encoding="utf-8"))

# Every committed markdown file, enumerated rather than globbed recursively:
# `reports/` and `.venv/` are gitignored and `reports/` is deliberately in
# Russian, so a recursive scan would fail on a file that is never published.
DOCS = (sorted(ROOT.glob("*.md"))
        + sorted((ROOT / "docs").glob("*.md"))
        + sorted((ROOT / "data").rglob("README.md")))

# The issue templates and the workflow. They are configuration by extension and
# prose by content, and the prose is the most-read English in the repository: a
# stranger meets the issue template before any file the rules below already
# covered. It is also the least likely to be re-read by us, because nobody opens
# an issue against their own repository.
TEMPLATES = sorted((ROOT / ".github").rglob("*.yml"))


def test_the_whole_tree_compiles():
    assert compileall.compile_dir(str(PACKAGE), quiet=2, force=True)
    assert compileall.compile_dir(str(SCRIPTS), quiet=2, force=True)


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_every_module_has_a_docstring(path):
    """A file in this repository has to say what question it answers."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assert ast.get_docstring(tree), f"{path.name} has no module docstring"


@pytest.mark.parametrize("path", ARGPARSE_SCRIPTS, ids=lambda p: p.name)
def test_every_script_runs_from_its_new_location(path):
    """--help exercises the import chain and the path arithmetic, and sends
    nothing. It is the check that the folder restructure did not break anything."""
    result = subprocess.run([sys.executable, str(path), "--help"],
                            capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert result.returncode == 0, result.stderr[-1500:]
    assert "usage:" in result.stdout


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_no_hardcoded_credentials(path):
    """config.py is the single read point, and it reads the environment."""
    text = path.read_text(encoding="utf-8")
    if path.name == "config.py":
        return
    assert "NODEMAVEN_PASSWORD" not in text
    assert not re.search(r"proxy-?auth\w*\s*=\s*['\"]", text, re.IGNORECASE)


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_no_authorization_header_is_ever_printed(path):
    """Base64 pasted into an issue is a leaked credential."""
    for line in path.read_text(encoding="utf-8").splitlines():
        if "print(" in line and "uthorization" in line:
            pytest.fail(f"{path.name} prints an authorization header: {line.strip()}")


def test_the_template_rule_has_something_to_check():
    """A derived list that came back empty would leave the two prose rules below
    passing over nothing, which is the one way a source-reading test fails
    silently. `.github/` is the directory most likely to be moved by a tool
    rather than by hand."""
    assert TEMPLATES


@pytest.mark.parametrize("path", SOURCES + DOCS + TEMPLATES, ids=lambda p: p.name)
def test_no_em_dashes(path):
    """NOTEBOOK.md: hyphens, never em-dashes."""
    text = path.read_text(encoding="utf-8")
    assert "—" not in text and "–" not in text


@pytest.mark.parametrize("path", SOURCES + DOCS + TEMPLATES,
                         ids=lambda p: str(p.relative_to(ROOT)))
def test_source_and_docs_are_english_only(path):
    """The repository is published and read by strangers.

    Documentation is covered as well as source, and it is the half more likely
    to slip: this harness is operated in Russian and the notes it produces are
    written in Russian first. Anything committed has to arrive in English.
    """
    text = path.read_text(encoding="utf-8")
    found = re.findall(r"[Ѐ-ӿ]+", text)
    assert not found, f"{path.relative_to(ROOT)} carries Cyrillic: {found[:5]}"


def test_the_package_imports_without_credentials():
    """Verdicts, the scheduler and the query list must be testable on a machine
    that has no .env at all."""
    result = subprocess.run(
        [sys.executable, "-c",
         "import nmbench.matrix, nmbench.targets, nmbench.queries, nmbench.engines"],
        capture_output=True, text=True, timeout=120, cwd=ROOT,
        env={"PATH": "", "SYSTEMROOT": "C:\\Windows"} if sys.platform == "win32"
        else {"PATH": ""})
    assert result.returncode == 0, result.stderr[-1500:]


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_no_boolean_success_field(path):
    """NOTEBOOK.md: the verdict enum, never a boolean success flag."""
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"[\"']success[\"']\s*:", text)


PRINTS_REMOTE_TEXT = sorted(
    p for p in SOURCES
    if p.parent != PACKAGE
    and re.search(r"print\(.*\bget\(['\"]org['\"]\)", p.read_text(encoding="utf-8"),
                  re.DOTALL))


@pytest.mark.parametrize("path", PRINTS_REMOTE_TEXT, ids=lambda p: p.name)
def test_a_script_printing_a_remote_name_survives_the_console(path):
    """An ASN organisation name is written by whoever owns the exit, and the
    Windows console encodes in the system codepage. `probehold_20260813T201805Z`
    ended after four identities of a three-hour run on a `U+00DA` in one, with
    the gateway answering and the row already written.

    Ordered, because the guard has to be installed before the engines are
    imported: zendriver pulls in colorama, whose stdout wrapper has no
    `reconfigure` of its own and would leave the original handle strict.
    """
    text = path.read_text(encoding="utf-8")
    guard = text.find("tolerate_unencodable_output()")
    engines_import = text.find("from nmbench import")
    assert guard != -1, f"{path.name} prints an ASN name with no console guard"
    assert guard < engines_import, (
        f"{path.name} installs the guard after the nmbench imports, which is "
        f"after colorama has already wrapped stdout")


def test_every_runner_that_offers_geo_alignment_hands_over_the_zone():
    """A geo arm that is accepted and then not applied is worse than a refused
    one, because the row still says `geo=align`.

    Two names carry this axis, because the engines source the data two ways:
    Camoufox looks the exit up in a bundled database and takes a boolean
    (`geoip`), and the Chromium-family engines have to be handed the zone
    (`timezone_id`). Both end in the same browser state, so preflight accepts
    the flag for any engine declaring the capability - and a runner passing only
    the boolean silently ran patchright, cloak and zendriver unaligned while
    labelling their rows as aligned. That is the `--humanize` failure a third
    time and the first two were caught by a rule like this one.

    Read off the source rather than by running a matrix, for the reason
    `TestTheControlIsNotHardened` is: the failure is silent by construction, so
    nothing else in an offline suite can see it.
    """
    for name in ("benchmark.py", "probes/probe_and_hold.py"):
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        if "geoip" not in text:
            continue
        assert "timezone_id" in text, (
            f"{name} offers geo alignment and passes only the Camoufox "
            f"boolean, so every other engine runs unaligned and says it did "
            f"not")


@pytest.mark.parametrize("runner", ["benchmark.py", "probes/probe_and_hold.py"])
def test_the_runner_does_not_branch_on_an_engine_name(runner):
    """A runner that can name a framework is a runner that can treat it
    differently. Engine names belong in the registry and nowhere else.

    The names come from the registry rather than from a list here, so an engine
    added tomorrow is covered without anyone remembering to extend this. The
    hardcoded pair this replaced was `camoufox, obscura`, chosen when those were
    the only two engines in the tree; seven have been added since and none of
    them was checked.
    """
    from nmbench import engines

    text = (SCRIPTS / runner).read_text(encoding="utf-8")
    for name in engines.names():
        for line in text.splitlines():
            stripped = line.strip()
            if name in stripped.lower() and not stripped.startswith("#"):
                # Defaults, help text and docstrings may name one; comparisons
                # may not.
                assert "==" not in stripped and " in [" not in stripped, (
                    f"{runner} compares against the engine name {name!r}: "
                    f"{stripped}")


# Scripts that resolve a provider, derived from the imports rather than listed,
# so a probe that grows a --providers flag is covered without anyone extending
# this. The one that exists today is the matrix runner.
USES_PROVIDERS = sorted(
    p for p in SCRIPTS.rglob("*.py")
    if re.search(r"^from nmbench import .*\bproviders\b", p.read_text(encoding="utf-8"),
                 re.MULTILINE))


def test_the_provider_rule_has_something_to_check():
    """A derived list that came back empty would leave the rule below passing
    while checking nothing, which is the one way a source-reading test fails
    silently."""
    assert USES_PROVIDERS


@pytest.mark.parametrize("path", USES_PROVIDERS, ids=lambda p: p.name)
def test_no_script_branches_on_a_provider_name(path):
    """The same rule as engines and targets, applied to the axis it was written
    for last.

    A provider is a `.toml` file and a username format, and the argument this
    repository rests on is that every arm went through one code path. One
    `if provider == ...` in a place nobody reviews is enough to make a
    multi-provider comparison unpublishable, and it would not fail a single other
    test: the run completes and the numbers look like a provider difference.

    Names come from the definitions on disk, so a competitor added tomorrow is
    covered. `nmbench/providers.py` is outside the scope by construction, the way
    the engine registry is: it holds the fallback name because something has to.
    """
    from nmbench import providers

    text = path.read_text(encoding="utf-8")
    for name in providers.names():
        for line in text.splitlines():
            stripped = line.strip()
            if name in stripped.lower() and not stripped.startswith("#"):
                assert "==" not in stripped and " in [" not in stripped, (
                    f"{path.name} compares against the provider name {name!r}: "
                    f"{stripped}")


# Everything that could send a gateway parameter, which is a wider net than
# USES_PROVIDERS: the rule below is about code that does *not* import providers
# and therefore cannot have asked which name to use.
SENDS_PARAMS = sorted(p for p in SOURCES if p.name != "proxy.py")


@pytest.mark.parametrize("path", SENDS_PARAMS, ids=lambda p: p.name)
def test_the_session_parameter_is_asked_for_rather_than_spelled(path):
    """The sticky session key is asked of the provider, never written in.

    Eleven call sites wrote `"sid"` as a literal. It is the canonical name most
    definitions will choose and `aliases` already handles a gateway that only
    spells it differently, which is why the literal was right often enough to be
    copied eleven times - and it is wrong for a definition naming the parameter
    something else, which the schema allows and the loader checks.

    Under `strict` that is a loud refusal from `build_username`, so the cost is a
    harness that will not run against such a provider. The health probe passes
    `strict=False` on purpose, and there it is silent: the name goes out, is
    answered with 200 and dropped, and every attempt draws a fresh exit while the
    rows record one held session.

    Read off the source rather than by running anything, for the reason
    `TestTheControlIsNotHardened` is: in the silent case the run completes and
    the numbers look like a result.
    """
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "sid" not in stripped:
            continue
        assert not re.search(r"""["']sid["']\s*:""", stripped), (
            f"{path.name} writes the session parameter as a literal, which is "
            f"one gateway's spelling. Use proxy.session_params: {stripped}")
