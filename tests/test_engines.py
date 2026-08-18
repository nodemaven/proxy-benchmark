"""Engine contract tests.

Nothing here launches a browser. What is checked is the property the comparison
rests on: every engine offers the same interface and emits the same columns, so
the runner cannot tell them apart and therefore cannot treat one of them
differently. An engine that drifts from the contract makes its column in the
report incomparable, and the drift would otherwise only show up as a missing key
in a data file six hours into a run.
"""
import pytest

from nmbench import engines
from nmbench.engines.base import (
    ROW_FIELDS,
    blank_row,
    keep_error_body,
    record_error,
    record_judgement,
    validate_preset,
)
from nmbench.targets import TARGETS

ENGINE_CLASSES = sorted(engines.REGISTRY.items())


class TestRegistry:
    def test_holds_every_engine(self):
        assert set(engines.names()) == {"http", "chromium", "camoufox",
                                        "patchright", "obscura", "cloak",
                                        "curlcffi", "seleniumbase",
                                        "zendriver", "rebrowser",
                                        "botasaurus"}

    def test_the_control_is_present(self):
        """Without an unmodified browser in the matrix there is no baseline, and
        a pass rate cannot be told apart from the target letting everything
        through. Removing this engine silently makes every other column
        unreadable, so its absence is a test failure and not a preference."""
        assert "chromium" in engines.REGISTRY

    @pytest.mark.parametrize("feature", ["supports_geo_align",
                                         "supports_humanize",
                                         "supports_typing"])
    def test_an_optional_feature_has_at_least_two_implementers(self, feature):
        """A capability only one engine has is not an axis, it is a confound.

        The runner refuses `--geo align` and `--humanize` on a matrix holding an
        engine that lacks them, which is the right rule and was added because
        `--humanize` silently humanized one column for a while. The consequence
        is that a feature with a single implementer can only ever be run as a
        matrix of one, and a matrix of one has no control in it: the number
        measures that engine's implementation of the feature, not the feature.

        Both flags were in exactly that state until a second Chromium-family
        engine arrived. Dropping back to one implementer would not break any
        test other than this one, and the flag would go on being accepted at the
        command line while producing runs nobody can attribute.
        """
        able = [name for name, engine in ENGINE_CLASSES
                if getattr(engine, feature)]
        assert len(able) >= 2, (
            f"only {able} implement {feature}, so a matrix using it holds no "
            f"engine to compare against")

    def test_the_key_is_the_engine_name(self):
        for name, engine in ENGINE_CLASSES:
            assert engine.name == name

    def test_get_returns_an_instance(self):
        assert isinstance(engines.get("http"), engines.REGISTRY["http"])

    def test_an_unknown_engine_names_the_known_ones(self):
        with pytest.raises(KeyError, match="camoufox"):
            engines.get("chrome")

    def test_availability_covers_every_engine(self):
        """Checked before a matrix starts, so a missing binary costs a message
        instead of half a run."""
        assert set(engines.report_availability()) == set(engines.REGISTRY)

    @pytest.mark.parametrize("name,engine", ENGINE_CLASSES)
    def test_the_interface_is_uniform(self, name, engine):
        for attribute in ("name", "check", "version", "open",
                          "supports_blocking", "supports_headful",
                          "supports_geo_align", "supports_humanize",
                          "supports_typing", "runs_script", "needs_relay"):
            assert hasattr(engine, attribute), f"{name} is missing {attribute}"

    @pytest.mark.parametrize("name,engine", ENGINE_CLASSES)
    def test_a_capability_is_declared_and_never_assumed(self, name, engine):
        """The runner refuses a run that would apply an option to some engines
        and silently drop it for others, which it can only do if every engine
        answers. A missing attribute would default to false somewhere and turn
        into a cell claiming an alignment it never had."""
        assert isinstance(engine.supports_geo_align, bool)
        assert isinstance(engine.supports_headful, bool)
        assert isinstance(engine.supports_humanize, bool)
        # `probe_and_hold.py` reads this to decide whether an engine can be
        # entered through the target's own front page. An engine that did not
        # answer would fall back to the URL path somewhere, and one entry column
        # would then hold two different clients while reading as an engine
        # difference.
        assert isinstance(engine.supports_typing, bool)
        # The runner reads this to decide whether to build a relay before the
        # session. An engine that did not answer would default to no relay
        # somewhere, and an engine that cannot send proxy credentials would then
        # leave from this machine's own address into rows labelled as pool
        # exits. That is the one failure mode that produces plausible numbers.
        assert isinstance(engine.needs_relay, bool)

    @pytest.mark.parametrize("name,engine", ENGINE_CLASSES)
    def test_check_returns_a_message_or_nothing(self, name, engine):
        result = engine.check()
        assert result is None or isinstance(result, str)

    @pytest.mark.parametrize("name,engine", ENGINE_CLASSES)
    def test_an_unavailable_engine_says_what_to_do(self, name, engine):
        result = engine.check()
        if result is not None:
            assert len(result) > 40, "the message has to name the fix"


class TestTheControlIsNotHardened:
    """The control only works if nobody improves it.

    Its whole job is to be caught. A well meaning patch here - an extra launch
    argument, a UA override, `--disable-blink-features=AutomationControlled` -
    turns the baseline into a second anti-detect engine, and every pass rate in
    the report silently loses the thing it was being compared against. The
    damage is invisible in the output, which is why it is pinned in a test.
    """

    def test_the_control_passes_no_launch_arguments(self):
        import inspect

        from nmbench.engines import chromium

        source = inspect.getsource(chromium.ChromiumEngine.open)
        assert "args=" not in source, (
            "the control must launch with Playwright's defaults, automation "
            "switches included"
        )
        assert "user_agent" not in source, (
            "overriding the User-Agent hides the HeadlessChrome token, which is "
            "one of the markers this engine exists to expose"
        )

    def test_the_control_uses_the_default_browser_build(self):
        engine = engines.REGISTRY["chromium"]
        signature = __import__("inspect").signature(engine.open)
        assert signature.parameters["channel"].default is None, (
            "the control has to be the stock build, or it is not reproducible "
            "for anyone forking this repository"
        )

    def test_the_control_takes_no_timezone(self):
        """Aligning the control's timezone with the exit would make it a third
        anti-detect engine and leave the matrix with no baseline."""
        engine = engines.REGISTRY["chromium"]
        assert not engine.supports_geo_align
        source = __import__("inspect").getsource(engine.open)
        assert "timezone_id" not in source


# Which engines are handed a zone, the marker proving each one uses it, and when
# in the launch it lands. See `TestTheTimezoneReachesTheBrowser` for why the last
# column is what makes this list four rows long rather than two.
#
# Browser-level emulation in every case, and that is the point: a JavaScript
# property patch is read back unpatched from an iframe and from a Web Worker, and
# every serious detector reads both.
GEO_INSTALLERS = [
    ("patchright", "timezone_id=timezone_id", "pre-context"),
    ("rebrowser", "timezone_id=timezone_id", "pre-context"),
    ("zendriver", "set_timezone_override", "post-tab"),
    ("botasaurus", "set_locale_and_timezone", "post-tab"),
]


class TestTheTimezoneReachesTheBrowser:
    """`supports_geo_align` is a promise, and these are the engines making it
    that are handed a zone rather than finding one.

    Camoufox looks the exit up in a bundled database and needs only a boolean;
    these four are handed the zone by the caller, which is why the option carries
    a value. A signature that quietly ignored it would produce a run whose rows
    all say `geo-align` and whose browsers all ran on Moscow time - the one
    outcome worse than not aligning at all, because it is unfalsifiable
    afterwards.

    The `moment` column is the reason the list has four entries instead of two.
    Measured 2026-08-14, `--geo align` left patchright flat and cost zendriver
    six sevenths of its yield, and the only difference the run could not rule out
    was when the override is installed: as a context option before any target
    exists, or on a tab that is already open. With one engine on each side that
    is an anecdote. Two on each side is what can tell "post-tab installation
    costs yield" from "zendriver specifically costs yield", so dropping either
    pair back to one member makes the axis unattributable again - which is
    exactly the state this repository already spent three runs in.
    """

    @pytest.mark.parametrize("name", [row[0] for row in GEO_INSTALLERS])
    def test_open_accepts_a_zone(self, name):
        engine = engines.REGISTRY[name]
        assert engine.supports_geo_align
        signature = __import__("inspect").signature(engine.open)
        assert "timezone_id" in signature.parameters
        assert signature.parameters["timezone_id"].default is None

    @pytest.mark.parametrize("name, marker",
                             [(row[0], row[1]) for row in GEO_INSTALLERS])
    def test_the_zone_is_used_and_not_merely_accepted(self, name, marker):
        source = __import__("inspect").getsource(engines.REGISTRY[name].open)
        assert marker in source

    @pytest.mark.parametrize("moment", ["pre-context", "post-tab"])
    def test_each_installation_moment_has_a_pair(self, moment):
        able = [name for name, _, when in GEO_INSTALLERS
                if when == moment and engines.REGISTRY[name].supports_geo_align]
        assert len(able) >= 2, (
            f"only {able} install the zone {moment}, so the geo axis cannot "
            f"separate the installation moment from the engine again")


class TestRawCdpCannotOutwaitTheRun:
    """A driver with no timeouts of its own has to be given one.

    Playwright bounds every call it makes. zendriver drives raw CDP, so a
    browser that dies mid-series leaves the run blocked on a reply that will
    never come - and it does not look like a failure, it looks like a slow
    identity: the process is healthy, the log is quiet, and there is no browser
    left to inspect. Measured twice on 2026-08-14, both times over ten minutes
    before it was noticed.

    Pinned in the source rather than exercised, because reproducing it means
    killing a browser out from under a live CDP call.
    """

    def test_every_cdp_call_is_bounded(self):
        from nmbench.engines import zendriver

        source = __import__("inspect").getsource(
            zendriver.ZendriverSession._run)
        assert "wait_for" in source, (
            "an unbounded CDP call turns a dead browser into a dead run"
        )

    def test_shutting_down_a_dead_browser_is_bounded_too(self):
        from nmbench.engines import zendriver

        source = __import__("inspect").getsource(zendriver.ZendriverEngine.open)
        assert "SHUTDOWN_CEILING_S" in source, (
            "browser.stop() is exactly the call a dead browser never answers"
        )

    def test_the_ceiling_clears_the_longest_legitimate_wait(self):
        """It is an outer bound, not a step timeout. A ceiling below the entry
        timeout would truncate a wait the harness deliberately allows and read
        as the target being slow."""
        from nmbench.engines import base, zendriver

        assert zendriver.CDP_CEILING_S > base.ENTRY_TIMEOUT_MS / 1000


class TestSessionContract:
    """Probes take a page from a session without knowing which engine it is.

    The alternative is code that branches on an engine name, which is the one
    thing that would let a comparison treat an engine differently by accident.
    """

    @pytest.mark.parametrize("session_class", [
        __import__("nmbench.engines.chromium", fromlist=["x"]).ChromiumSession,
        __import__("nmbench.engines.camoufox", fromlist=["x"]).CamoufoxSession,
        __import__("nmbench.engines.obscura", fromlist=["x"]).ObscuraSession,
        __import__("nmbench.engines.seleniumbase",
                   fromlist=["x"]).SeleniumBaseSession,
        __import__("nmbench.engines.zendriver",
                   fromlist=["x"]).ZendriverSession,
        __import__("nmbench.engines.botasaurus",
                   fromlist=["x"]).BotasaurusSession,
    ])
    def test_every_browser_session_offers_new_page_and_fetch(self, session_class):
        assert callable(session_class.new_page)
        assert callable(session_class.fetch)


class TestObscuraStatus:
    """Obscura's `goto` returns no response, so the status comes off the event.

    Every Obscura row was written with `status: null` while the other engines
    carried the real code, which is not a wrong number but a column that any
    analysis grouping by status drops without saying so. The fakes here carry no
    `is_navigation_request`, on purpose: that is the filter this browser breaks,
    because it follows redirects internally and reports the merged response with
    the flag false. Reintroducing it fails these tests instead of quietly
    nulling every redirected refusal, which is how Google serves `/sorry/`.
    """

    @staticmethod
    def watch(page):
        from nmbench.engines.obscura import _watch_navigation_status
        return _watch_navigation_status(page)

    def test_a_main_frame_document_status_is_recorded(self, fake_page):
        seen = self.watch(fake_page)
        fake_page.emit_response(503)
        assert seen["status"] == 503

    def test_a_subresource_does_not_overwrite_the_document(self, fake_page):
        seen = self.watch(fake_page)
        fake_page.emit_response(200)
        fake_page.emit_response(404, resource_type="script")
        assert seen["status"] == 200

    def test_another_frame_does_not_overwrite_the_document(self, fake_page):
        seen = self.watch(fake_page)
        fake_page.emit_response(200)
        fake_page.emit_response(403, frame="iframe")
        assert seen["status"] == 200

    def test_the_last_document_wins(self, fake_page):
        """The verdict is read off the page that ended up loaded."""
        seen = self.watch(fake_page)
        fake_page.emit_response(200)
        fake_page.emit_response(429)
        assert seen["status"] == 429

    def test_nothing_seen_stays_absent(self, fake_page):
        assert self.watch(fake_page)["status"] is None

    def test_the_fallback_is_wired_into_fetch(self):
        """The helper is only worth having if the row reads from it."""
        import inspect

        from nmbench.engines import obscura

        source = inspect.getsource(obscura.ObscuraSession.fetch)
        assert "_watch_navigation_status" in source
        assert "row[\"status\"]" in source


class TestEngineLabels:
    """A label that does not carry the build cannot be told apart afterwards."""

    def test_the_default_build_is_unlabelled(self):
        from nmbench.engines.chromium import label_for
        assert label_for("patchright") == "patchright"

    def test_a_named_build_reaches_the_label(self):
        from nmbench.engines.chromium import label_for
        assert label_for("patchright", "chrome") == "patchright-chrome"

    def test_the_two_builds_do_not_collide(self):
        from nmbench.engines.chromium import label_for
        assert label_for("patchright") != label_for("patchright", "chrome")


class TestRowSchema:
    def test_a_blank_row_has_every_column(self):
        row = blank_row("http", "2.32.0", "q", "https://example.com/")
        assert set(row) == set(ROW_FIELDS)

    def test_counters_start_at_zero_and_evidence_starts_absent(self):
        """Absent evidence and zero are not the same thing: a missing key turns
        into a silent zero the moment anything sums a column."""
        row = blank_row("http", "2.32.0", "q", "https://example.com/")
        assert row["bytes"] == 0 and row["blocked"] == 0 and row["allowed"] == 0
        assert row["status"] is None and row["verdict"] is None

    def test_extras_do_not_widen_the_schema(self):
        row = blank_row("http", "2.32.0", "q", "u", target="bing_serp",
                        preset="light")
        assert row["target"] == "bing_serp"
        assert set(row) == set(ROW_FIELDS)

    def test_no_boolean_success_column(self):
        """NOTEBOOK.md: the verdict enum, never a boolean."""
        assert "success" not in ROW_FIELDS


class TestRecording:
    def test_a_judgement_stores_its_reasoning(self):
        row = blank_row("http", "2.32.0", "q", "u")
        record_judgement(row, TARGETS["bing_serp"], "https://www.bing.com/search",
                         "", '<li class="b_algo">x</li>')
        assert row["verdict"] == "ok"
        assert row["verdict_reason"]
        assert row["markers"]["b_algo"] == 1

    def test_an_error_makes_no_claim_about_the_target(self):
        row = blank_row("http", "2.32.0", "q", "u")
        record_error(row, TimeoutError("read timed out"))
        assert row["verdict"] == "error"
        assert "never judged" in row["verdict_reason"]
        assert row["error"] == "TimeoutError: read timed out"


class TestAnErrorRowKeepsItsEvidence:
    """A timeout looking for a search box and a target refusing the front page
    produce the same row otherwise, and they fall on opposite sides of every
    question the typed entry shape asks."""

    def test_the_body_is_archived_and_the_verdict_stays_error(self, tmp_path,
                                                              monkeypatch):
        from nmbench import artifacts

        monkeypatch.setattr(artifacts, "ARTIFACTS_DIR", tmp_path)
        store = artifacts.ArtifactStore("test", run_id="20260813T000000Z")
        row = blank_row("zendriver", "149", "q", "u", target="amazon_search")
        record_error(row, TimeoutError("waiting for input#twotabsearchtextbox"))
        keep_error_body(store, row, lambda: "https://www.amazon.com/",
                        lambda: "<html>ref=cs_503</html>")
        assert row["verdict"] == "error"
        assert row["artifact"]
        assert row["final_url"] == "https://www.amazon.com/"
        assert row["html_len"] == len("<html>ref=cs_503</html>")

    def test_a_dead_browser_costs_the_body_and_not_the_row(self):
        def gone():
            raise RuntimeError("the transport is closed")

        row = blank_row("zendriver", "149", "q", "u")
        record_error(row, TimeoutError("waiting for the box"))
        keep_error_body(None, row, gone, gone)
        assert row["verdict"] == "error"
        assert row["artifact"] is None

    def test_no_judge_is_called_on_the_entry_page(self):
        """The query was never submitted, so there is nothing to judge. Amazon's
        own front page carries no result list and would fall to the catch-all,
        which would file our lost attempt as the target refusing."""
        row = blank_row("zendriver", "149", "q", "u", target="amazon_search")
        record_error(row, TimeoutError("waiting for the box"))
        keep_error_body(None, row, lambda: "https://www.amazon.com/",
                        lambda: "<html><body>front page</body></html>")
        assert row["verdict"] == "error"
        assert "never judged" in row["verdict_reason"]


class TestTheEntryTimeoutIsShared:
    """An engine that gives up on the entry page sooner than the engines beside
    it in the same matrix does not measure a harder target."""

    def test_the_typed_path_waits_the_same_everywhere(self):
        import inspect

        from nmbench.engines import zendriver as zendriver_module

        source = inspect.getsource(zendriver_module.ZendriverSession.search)
        assert "ENTRY_TIMEOUT_MS" in source
        # `ready_timeout_ms` is eight seconds and belongs to a different
        # question - whether the results finished rendering, where False is a
        # real answer. Waiting for the box on it cost two `error` rows on
        # 2026-08-13 and both fell in one arm of the axis under test.
        assert "self.ready_timeout_ms" not in source


class TestPresetValidation:
    def test_an_unknown_preset_is_refused(self):
        with pytest.raises(ValueError, match="unknown preset"):
            validate_preset("agressive", TARGETS["bing_serp"])

    def test_blocking_script_on_google_is_refused(self):
        """Every attempt would return the no-JS stub and be recorded as a failure
        caused by this harness rather than by the target."""
        with pytest.raises(ValueError, match="blocks script"):
            validate_preset("aggressive", TARGETS["google_serp"])

    def test_the_message_names_the_preset_to_use_instead(self):
        with pytest.raises(ValueError, match="'light'"):
            validate_preset("aggressive", TARGETS["google_serp"])

    @pytest.mark.parametrize("preset", ["none", "light"])
    def test_presets_that_keep_script_are_allowed_everywhere(self, preset):
        for target in TARGETS.values():
            validate_preset(preset, target)

    def test_aggressive_is_allowed_on_server_rendered_targets(self):
        for name in ("bing_serp", "ddg_serp", "ipinfo"):
            validate_preset("aggressive", TARGETS[name])


class TestBlockingPresets:
    def test_script_blocking_is_derived_not_hardcoded(self):
        from nmbench.blocking import PRESETS, SCRIPT_BLOCKING

        assert SCRIPT_BLOCKING == {name for name, types in PRESETS.items()
                                   if types and "script" in types}

    def test_light_keeps_the_page_working(self):
        from nmbench.blocking import PRESETS

        assert "script" not in PRESETS["light"]
        assert "stylesheet" not in PRESETS["light"]

    def test_none_blocks_nothing(self):
        from nmbench.blocking import PRESETS

        assert PRESETS["none"] is None


class TestHttpHelpers:
    def test_encodings_are_only_those_we_can_decode(self):
        """Advertising brotli without the decoder makes requests hand back raw
        bytes, and a content check on those reports a block for a good page."""
        advertised = engines.supported_encodings()
        assert "gzip" in advertised
        if "br" in advertised:
            pytest.importorskip("brotli")

    def test_undecoded_bytes_are_detected(self):
        assert engines.looks_undecoded("�" * 100) is True

    def test_normal_markup_is_not_flagged(self):
        assert engines.looks_undecoded("<html><body>hello</body></html>") is False

    def test_an_empty_body_is_not_flagged_as_undecoded(self):
        assert engines.looks_undecoded("") is False

    def test_headers_do_not_announce_a_python_client(self):
        joined = " ".join(engines.BROWSER_HEADERS.values()).lower()
        assert "python" not in joined and "requests" not in joined


class TestTheZendriverShimEvaluatesWhatTheProbesActuallyPass:
    """The CDP shims take Playwright's `page.evaluate` argument, and half the
    probes in this repository pass a block-bodied arrow.

    Pinned because the shim used to unwrap the arrow by text - everything up to
    the first `=>` was dropped and the remainder evaluated. That is correct for
    `() => expr` and cannot work for `() => { ... }`: what reaches V8 is a block
    statement, its `return` is illegal at the top level, and the engine answers
    `SyntaxError: Illegal return statement`. Measured 2026-08-18, it cost
    zendriver every row of `engine_fingerprint.py` and `detect_page.py` while
    the four expression-bodied probes passed, so the engine carrying this
    repository's largest unattributed result had no fingerprint at all and the
    gap looked like an engine that could not be read rather than a shim that
    could not parse.

    The two shims are asserted together on purpose. They were written months
    apart, botasaurus already called the arrow and zendriver still unwrapped it,
    and a difference between two adapters is a difference in what their columns
    mean.
    """

    def _shim(self, module):
        seen = {}

        class _Tab:
            def evaluate(self, expression, **ignored):
                seen["expression"] = expression
                return expression

        page = module._Page.__new__(module._Page)
        page.tab = _Tab()
        page.driver = _Tab()
        page._run = lambda value: value
        return page, seen

    def _expressions(self, script):
        from nmbench.engines import botasaurus, zendriver

        zen, seen = self._shim(zendriver)
        zen.evaluate(script)
        yield "zendriver", seen["expression"]

        page = botasaurus._Page.__new__(botasaurus._Page)
        page.driver = type("_D", (), {"run_js": staticmethod(lambda call: call)})()
        yield "botasaurus", page.evaluate(script)

    def test_a_block_bodied_arrow_survives_both_shims(self):
        """The shape `engine_fingerprint.py` and `detect_page.py` pass."""
        script = "() => { const v = 1; return {v}; }"
        for name, expression in self._expressions(script):
            compiled = expression[len("return "):] \
                if expression.startswith("return ") else expression
            assert "=>" in compiled, f"{name} unwrapped the arrow"
            assert compiled.rstrip().endswith("()"), \
                f"{name} left the arrow uncalled, so nothing runs"

    def test_an_expression_bodied_arrow_still_works(self):
        """The shape `geo_align_check.py` and `screen_override.py` pass, which
        the old unwrapping handled and which must not break in fixing the other."""
        for name, expression in self._expressions("() => ({a: 1})"):
            assert "({a: 1})" in expression, name

    def test_a_bare_expression_is_not_wrapped_into_a_call(self):
        """`location.href` is passed directly by the zendriver session itself.
        Calling it would be a TypeError on a string."""
        from nmbench.engines import zendriver

        page, seen = self._shim(zendriver)
        page.evaluate("location.href")
        assert seen["expression"] == "location.href"
