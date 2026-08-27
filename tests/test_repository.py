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
import json
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


MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
MD_HEADING = re.compile(r"^(?:#{1,6})\s+(.*?)\s*$", re.M)


def _anchors(text):
    """GitHub's slug rule, the subset of it these files use: lowercase, drop
    punctuation, spaces to hyphens. Backticks and full stops in a heading are
    dropped rather than kept, which is why `## Step 9. Optional: send it through
    a proxy` is reached as `#step-9-optional-send-it-through-a-proxy`."""
    out = set()
    for title in MD_HEADING.findall(text):
        slug = re.sub(r"[^\w\s-]", "", title.strip().lower())
        out.add(re.sub(r"\s+", "-", slug))
    return out


@pytest.mark.parametrize("path", DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_link_inside_the_repository_resolves(path):
    """A link that goes nowhere gets removed or fixed, not left with a note.

    Added 2026-08-27 after the table of contents in README.md spent a day
    pointing at `#disclosure`, a section that had been deleted the day before.
    Nothing caught it: the file it lives in is the file, the anchor is valid
    markdown, and on GitHub a dead anchor scrolls nowhere rather than erroring,
    so it is invisible to everyone except the reader who clicked it.

    Only in-repository targets are checked - relative paths and same-file
    anchors. An external URL needs the network and would make the suite fail on
    somebody else's outage, which is a worse failure than the one being
    prevented. Those are checked by hand when they are written.
    """
    text = path.read_text(encoding="utf-8")
    own = _anchors(text)
    broken = []
    for label, target in MD_LINK.findall(text):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        if target.startswith("#"):
            if target[1:] not in own:
                broken.append(f"[{label}]({target}) - no such heading in this file")
            continue
        rel, _, fragment = target.partition("#")
        if not rel:
            continue
        dest = (path.parent / rel).resolve()
        if not dest.exists():
            broken.append(f"[{label}]({target}) - no such path")
        elif fragment and dest.suffix == ".md" and fragment not in _anchors(
                dest.read_text(encoding="utf-8")):
            broken.append(f"[{label}]({target}) - no such heading in {rel}")
    assert not broken, f"{path.relative_to(ROOT)}: " + "; ".join(broken)


def test_the_engine_list_in_the_readme_is_the_registry():
    """The list of frameworks at the top of README.md is generated, so check it.

    Adding an engine is one import and one registry line, and nothing about that
    change looks like it should have touched a prose section of the README. The
    results block below it has the same shape and no such guard, which is
    survivable there because a stale results table is contradicted by the run
    files next to it. A stale engine list is contradicted by nothing: it reads as
    a complete answer to "what does this drive" and a reader has no way to tell
    it is a year old.

    Runs the generator rather than re-implementing it, so this fails when the
    README drifts and not when the wording changes.
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    begin, end = "<!-- ENGINES:BEGIN -->", "<!-- ENGINES:END -->"
    assert begin in readme and end in readme, (
        "README.md has lost the ENGINES:BEGIN/ENGINES:END markers, so nothing is "
        "checking that its engine list matches nmbench.engines.REGISTRY")
    published = begin + readme.split(begin, 1)[1].split(end, 1)[0] + end
    result = subprocess.run([sys.executable, str(SCRIPTS / "engine_table.py")],
                            capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert result.returncode == 0, result.stderr[-1500:]
    generated = result.stdout.replace("\r\n", "\n")
    assert published.strip() == generated.strip(), (
        "README.md engine block no longer matches the registry - regenerate it "
        "with `python scripts/engine_table.py --readme`")


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


ANALYSIS = sorted(p for p in (SCRIPTS / "analysis").glob("*.py")
                  if "__pycache__" not in p.parts)


def test_the_analysis_scripts_are_a_list_that_can_go_stale():
    """The rule below is derived from a directory, so an empty one would leave it
    passing over nothing - the one way a source-reading test fails silently."""
    assert ANALYSIS, "no analysis scripts found, so the rule below checks nothing"


@pytest.mark.parametrize("path", ANALYSIS, ids=lambda p: p.name)
def test_re_deriving_a_published_number_needs_nothing_installed(path):
    """The README tells a sceptic to check our numbers with a clone and three
    commands, and says explicitly that nothing has to be installed first.

    That is a promise about imports and it rots in one commit: the day somebody
    reaches for a dataframe in `report.py`, the verification path silently grows
    a dependency install, and the person it was written for is the one who finds
    out. A verification path a sceptic can be blocked on is worth less than no
    claim at all, so the promise is pinned here rather than in prose.

    Read off the AST rather than by running the scripts, because an import that
    only fires inside a function would pass a smoke run on this machine - every
    third-party package the rest of the repository needs is installed here - and
    fail on the fresh clone this is about. `nmbench` is ours and `report` is a
    sibling in the same directory; everything else has to be in the standard
    library that ships with the interpreter.
    """
    ours = {"nmbench", "report"}
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    outside = sorted(imported - ours - sys.stdlib_module_names)
    assert not outside, (
        f"{path.name} imports {outside}, which is not in the standard library. "
        f"The README promises the analysis scripts run on a bare interpreter, "
        f"so this either moves into nmbench or the promise comes out of the "
        f"README - not both.")


# Import every third-party package out of the interpreter, and fail loudly on the
# attempt rather than falling back to something. Reading `sys.modules` afterwards
# would not do: the machines this suite runs on have the whole of
# `requirements-dev.txt` installed, so a `import requests` inside `nmbench` would
# succeed here and fail only on the fresh clone the promise is written for.
BLOCK_THIRD_PARTY = """
import sys, runpy
allowed = set(sys.stdlib_module_names) | {"nmbench", "report", "__main__"}


class Guard:
    def find_spec(self, name, path=None, target=None):
        root = name.split(".")[0]
        if root not in allowed and not root.startswith("_"):
            raise ImportError("third-party import on the verification path: "
                              + name)
        return None


sys.meta_path.insert(0, Guard())
sys.argv = [sys.argv[1], "--help"]
runpy.run_path(sys.argv[0], run_name="__main__")
"""


@pytest.mark.parametrize("path", ANALYSIS, ids=lambda p: p.name)
def test_nothing_the_analysis_scripts_reach_needs_installing_either(path):
    """The same promise, asked of the whole import chain rather than one file.

    The AST check above reads the script and stops there, so it cannot see a
    `nmbench` module that grows a dependency - and every analysis script imports
    `nmbench`. This runs each one with every third-party package refused at the
    finder, which is the only way to ask the question on a machine that has them
    all installed.

    `--help` rather than the real work: it exercises every module-level import in
    the script and everything those pull in, costs milliseconds, and reads no
    run files. The pair is complementary and neither is redundant - a lazy import
    inside a function would survive this and is caught by the AST, and a
    dependency two levels down inside `nmbench` would survive the AST and is
    caught here.
    """
    result = subprocess.run([sys.executable, "-c", BLOCK_THIRD_PARTY, str(path)],
                            capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert result.returncode == 0, (
        f"{path.name} cannot run on a bare interpreter:\n"
        f"{result.stderr[-1500:]}")


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

    Both directions, since 2026-08-26. The rule guarded only the first one and
    the second was live in `probe_and_hold.py` the whole time: it passed the
    zone and pinned the boolean to `False`, so `--geo align` was accepted for
    camoufox and cloak - both declare the capability - and both would have run
    unaligned under a row saying otherwise. It never fired, because the only 86
    `geo=align` rows on disk are patchright and zendriver, checked row by row
    rather than assumed, but a one-sided rule against a symmetric failure is
    half a rule.

    Read off the source rather than by running a matrix, for the reason
    `TestTheControlIsNotHardened` is: the failure is silent by construction, so
    nothing else in an offline suite can see it.
    """
    for name in ("benchmark.py", "probes/probe_and_hold.py"):
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        if "geoip" not in text and "timezone_id" not in text:
            continue
        assert "timezone_id" in text, (
            f"{name} offers geo alignment and passes only the Camoufox "
            f"boolean, so every other engine runs unaligned and says it did "
            f"not")
        assert "geoip" in text, (
            f"{name} offers geo alignment and passes only the zone, so "
            f"camoufox and cloak run unaligned and say they did not")
        assert 'geoip": False' not in text and "geoip': False" not in text, (
            f"{name} hands the geo axis a hardcoded `geoip=False`, which is an "
            f"aligned arm that is not aligned on the two engines that take the "
            f"boolean. Wire it to the same condition as `timezone_id`")


def test_the_run_level_geo_flag_is_never_reassigned_inside_the_loop():
    """The flag that decides where an identity is sourced from is set once.

    `probe_and_hold.py` decides per run whether any cell is aligned, because an
    aligned run has to source every identity from the echo service - the
    unaligned arm included, since that call is a request through the exit
    before the browser opens, which is warming, and paying it in one arm only
    would put the warm-up inside the geo comparison.

    On 2026-08-26 a per-cell flag was written under the same name inside the
    loop, so each iteration overwrote the run-level one. A cell that followed an
    unaligned cell then asked `gateway.identify`, which prefers the CONNECT
    reply header, which carries no zone - and the aligned identity was dropped
    for want of a timezone the run had stopped asking for. It read as a patchy
    geoip database, because `identify` falls back to the echo service whenever
    the header is absent, so the loss was intermittent in the way a patchy
    database would be.

    Checked structurally rather than by running a matrix: the failure needs a
    live gateway and two cells in a specific order, and it is silent - the run
    completes and the rows it does write are correct. Only the missing ones say
    anything, and they say it in the shape of a different bug.
    """
    tree = ast.parse((SCRIPTS / "probes/probe_and_hold.py").read_text(
        encoding="utf-8"))
    stores = [node.id for node in ast.walk(tree)
              if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)]
    assert stores.count("run_has_aligned_cell") == 1, (
        "the run-level geo flag is assigned more than once in "
        "probe_and_hold.py. It is read inside the identity loop to choose "
        "between gateway.echo and gateway.identify, so a second assignment "
        "changes which service later identities are sourced from")
    assert "aligning" not in stores, (
        "`aligning` is the name that caused this: it read as both the "
        "run-level flag and the per-cell one. Per-cell names in that loop are "
        "`cell_*`")


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


def test_every_published_row_came_through_a_published_gateway():
    """No committed row names a gateway whose definition is not committed too.

    README says "only `nodemaven.toml` ships, and it is the only gateway any
    number here was measured through", and until 2026-08-27 nothing enforced it.
    The gap is not theoretical: a provider is chosen with `--providers` and the
    run file is named `benchmark_<stamp>.jsonl` either way, so the gateway a row
    went through appears **inside** the rows and never in the filename. Nothing
    about `git add data/runs/` looks different for a row taken through somebody
    else's account, which makes it exactly the kind of thing that gets published
    by accident rather than by decision.

    Deliberately checked against the definitions on disk rather than against the
    literal string `nodemaven`. A run through a gateway whose `.toml` is
    committed is a comparison this repository is offering to be checked on; a run
    through one that is not is a number a reader cannot reproduce and cannot
    audit, which is the property that matters and the only one worth a test. So
    publishing a competitor column stays possible - it costs committing the
    definition in the same change, which is the friction this is for.

    Rows written before the `provider` field existed carry no claim about a
    gateway and are skipped rather than assumed: 4290 of them on 2026-08-27,
    against 9074 that name one.
    """
    from nmbench import providers

    shipped = set(providers.names())
    assert shipped, "no provider definitions on disk, so this test checks nothing"

    offenders = {}
    named = 0
    for path in sorted((ROOT / "data" / "runs").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            name = row.get("provider")
            if name is None:
                continue
            named += 1
            if name not in shipped:
                offenders.setdefault(path.name, set()).add(name)

    assert named, ("no committed row names a provider, so this test would pass "
                   "on an empty data directory")
    assert not offenders, (
        "these run files were taken through a gateway whose definition is not "
        f"in data/providers/, so a reader cannot reproduce them: {offenders}. "
        "Either commit the definition in this change, or keep the run file out "
        "of the repository - see .git/info/exclude, which is per-clone and does "
        "not travel.")


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


# The scripts that drive a long matrix of their own, derived rather than named:
# both build a TransportWatch, which is the thing only a multi-cell runner has.
LONG_RUNNERS = sorted(
    p for p in SOURCES
    if p.parent.name in ("scripts", "probes") and "TransportWatch(" in
    p.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", LONG_RUNNERS, ids=lambda p: p.name)
def test_a_session_that_will_not_start_costs_one_batch_and_not_the_run(path):
    """A launch that fails must be a row, never a traceback.

    `probe_and_hold.py` has carried this since 2026-08-13 and `benchmark.py`
    did not, so the cheap probe survived what the expensive matrix could not.
    Measured 2026-08-18, a 16-attempt smoke run on the server died at cell 9 of
    16 on zendriver's `Failed to connect to browser` - the transient that file
    already records as starting normally a minute later - with seven cells
    unanswered and no summary written. Over the tens of hours a real matrix
    runs, a launch that hiccups once is a certainty rather than an edge case.

    Both halves are asserted because the fix has a failure mode of its own. A
    catch-all wide enough to hold a vendor's bare `Exception` is also wide
    enough to hold `TransportLost`, and swallowing that would leave the
    transport guard printing its verdict while the run carried on through a
    dead gateway - the one error that spends the whole budget quietly.

    Derived from `TransportWatch(` rather than from a list of filenames, for the
    reason this file's other checks are: the bug was that one of two runners had
    the guard, and a test naming the runners it knows about cannot fail on the
    third one somebody adds.

    The ordering half is read through `ast` and not by searching the text.
    Written as a text search first, it failed on `probe_and_hold.py` for a
    perfectly correct inner `except Exception` around the page visit, which
    catches nothing this is about. Only the `try` that encloses the raise can
    swallow it, so that is the one asked.
    """
    source = path.read_text(encoding="utf-8")
    assert '"session_failed"' in source, (
        f"{path.name} has no session_failed verdict, so a browser that will "
        f"not launch ends the run instead of costing one batch")

    def raises_transport_lost(node):
        return any(isinstance(inner, ast.Raise)
                   and "TransportLost" in ast.dump(inner)
                   for inner in ast.walk(node))

    def catches(handler, name):
        return handler.type is not None and name in ast.dump(handler.type)

    guarded = 0
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Try) or not any(
                raises_transport_lost(stmt) for stmt in node.body):
            continue
        for index, handler in enumerate(node.handlers):
            if not catches(handler, "Exception"):
                continue
            guarded += 1
            earlier = node.handlers[:index]
            assert any(catches(h, "TransportLost") for h in earlier), (
                f"{path.name} line {handler.lineno} catches Exception on the "
                f"try that raises TransportLost, without re-raising it first. "
                f"The transport guard would print its verdict and the run "
                f"would carry on through a dead gateway.")
    assert guarded, (
        f"{path.name} raises TransportLost but no enclosing try catches "
        f"Exception, so this check read nothing and must be revisited")
