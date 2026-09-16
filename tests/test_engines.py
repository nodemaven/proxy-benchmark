"""Engine contract tests.

Nothing here launches a browser. What is checked is the property the comparison
rests on: every engine offers the same interface and emits the same columns, so
the runner cannot tell them apart and therefore cannot treat one of them
differently. An engine that drifts from the contract makes its column in the
report incomparable, and the drift would otherwise only show up as a missing key
in a data file six hours into a run.
"""
import inspect
import pathlib

import pytest

from nmbench import engines
from nmbench.engines.base import (
    HUMANIZE_MODES,
    ROW_FIELDS,
    blank_row,
    browser_build,
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
                                         "supports_typing",
                                         "supports_chrome_binary"])
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

    @pytest.mark.parametrize("mode", ["engine", "trueman"])
    def test_a_humanize_mode_has_at_least_two_implementers(self, mode):
        """The same rule as above, for the capability that stopped being a bool.

        `supports_humanize` was a boolean until 2026-09-03 and was covered by
        the parametrized test above. It is now `humanize_modes`, a set, because
        there are two different movers and they are alternatives rather than a
        stack - running Camoufox's own hand and ours together would compose two
        models into one path and produce a movement neither describes. The
        argument for two implementers is unchanged: a mode only one engine has
        measures that engine's implementation and not the mode.
        """
        able = [name for name, engine in ENGINE_CLASSES
                if mode in engine.humanize_modes]
        assert len(able) >= 2, (
            f"only {able} offer humanize mode {mode!r}, so a matrix using it "
            f"holds no engine to compare against")

    @pytest.mark.parametrize("name,engine", ENGINE_CLASSES)
    def test_every_engine_can_be_left_unhumanized(self, name, engine):
        """`off` is the control arm and every engine has to be able to run it.

        An engine that offered only `trueman` could not appear in a matrix
        beside its own unhumanized self, which is the only comparison that
        isolates the cursor from everything else the binary does.
        """
        assert "off" in engine.humanize_modes, (
            f"{name} cannot run unhumanized, so the pointer axis has no "
            f"control column")

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
                          "supports_geo_align", "humanize_modes",
                          "supports_typing", "runs_script", "needs_relay",
                          "accepts_relay", "supports_chrome_binary"):
            assert hasattr(engine, attribute), f"{name} is missing {attribute}"

    @pytest.mark.parametrize("name,engine", ENGINE_CLASSES)
    def test_a_capability_is_declared_and_never_assumed(self, name, engine):
        """The runner refuses a run that would apply an option to some engines
        and silently drop it for others, which it can only do if every engine
        answers. A missing attribute would default to false somewhere and turn
        into a cell claiming an alignment it never had."""
        assert isinstance(engine.supports_geo_align, bool)
        assert isinstance(engine.supports_headful, bool)
        # A set and not a bool since 2026-09-03. `frozenset` specifically: it is
        # a class attribute inherited by four subclasses, and a mutable one
        # would let a single engine's edit reach every sibling that inherited
        # the same object.
        assert isinstance(engine.humanize_modes, frozenset)
        assert engine.humanize_modes <= set(HUMANIZE_MODES), (
            f"{engine.humanize_modes - set(HUMANIZE_MODES)} is not a humanize "
            f"mode the runner can be asked for, so it could never be selected "
            f"and the declaration would be decoration")
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
        # A second and different question: whether the engine can be *pointed*
        # at a relay, which is not the same as being unable to work without one.
        # `patchright` is the case that forced the split - it reaches the pool
        # perfectly well on its own, and its rows carried no exit address and no
        # ClientHello for that reason, which is the gap `nmbench.relay` closes.
        #
        # Declared rather than discovered by passing `relay_address` and seeing
        # what happens: every `open` in this package ends in `**ignored`, so an
        # engine that does not handle it swallows the address, dials the gateway
        # itself, and produces rows that say `relayed` with nothing behind it.
        assert isinstance(engine.accepts_relay, bool)
        # An engine that cannot reach the pool without a relay and cannot be
        # given one has no pool arm at all. That combination is unreachable
        # today and this is what keeps it that way, because the failure it
        # produces is a run that starts and then refuses every cell.
        assert not (engine.needs_relay and not engine.accepts_relay), (
            f"{name} declares it needs a relay and cannot take one")
        # The runner reads this to refuse a matrix mixing a pinned engine with
        # one that cannot be pinned. An engine that did not answer would be
        # handed `chrome_binary` and drop it into `**ignored`, and its row would
        # sit beside the pinned ones carrying a browser 15 majors away from
        # theirs with nothing in the file saying so.
        assert isinstance(engine.supports_chrome_binary, bool)

    @pytest.mark.parametrize("name,engine", ENGINE_CLASSES)
    def test_an_engine_that_accepts_a_relay_names_it(self, name, engine):
        """`accepts_relay = True` has to be visible in the signature.

        Without this the flag is decoration, and decoration here is worse than
        nothing: `**ignored` catches `relay_address` silently, so an engine that
        declares True and forgot the parameter dials the gateway itself while
        every row it writes says `relayed`. Nothing downstream can see that -
        the bytes are right, the verdicts are right, and only the exit column is
        empty, which is what it looks like when the gateway declines to name an
        exit.

        It caught one for real on 2026-09-06: `RebrowserEngine` inherits the
        flag from `ChromiumEngine` and overrides `open` with its own copy of the
        proxy line, so adding the option to the parent silently made a promise
        the subclass did not keep.

        Read off `inspect.signature` rather than by calling anything, so it
        costs no browser and cannot go stale the way a hand-kept list would.
        """
        if not engine.accepts_relay:
            return
        parameters = inspect.signature(engine.open).parameters
        assert "relay_address" in parameters, (
            f"{name} declares accepts_relay but its open() does not name "
            f"relay_address, so a caller's address would fall into **ignored "
            f"and the arm would reach the gateway directly while its rows said "
            f"relayed")

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


# The vendor argument each engine hands the path to. Named per engine because
# there is no agreement between the drivers: Playwright takes `executable_path`,
# zendriver `browser_executable_path`, botasaurus `chrome_executable_path` and
# SeleniumBase `binary_location`. The marker is what separates an engine that
# forwards the path from one that accepts it into `**ignored`.
CHROME_BINARY_TAKERS = [
    ("chromium", "executable_path=chrome_binary"),
    ("patchright", "executable_path=chrome_binary"),
    ("rebrowser", "executable_path=chrome_binary"),
    ("zendriver", "browser_executable_path=chrome_binary"),
    ("botasaurus", "chrome_executable_path=chrome_binary"),
    ("seleniumbase", "binary_location"),
]


class TestTheBrowserCanBeHeldFixed:
    """`supports_chrome_binary` is a promise about the largest uncontrolled
    variable in this repository.

    Measured 2026-09-02 in `tls_clienthello_20260902T180555Z.jsonl`: the TLS
    fingerprint an engine presents is decided by its Chrome major and not by the
    library driving it - rebrowser on 136, cloak on 146, seleniumbase on 149,
    zendriver and botasaurus all land on one value, while chromium and patchright
    on 151 land on another, and the entire difference is three ML-DSA signature
    algorithms. The engines in the registry span majors 136 to 151, systematically
    by engine, so any engine-to-engine number carries a browser difference inside
    it unless the browser is pinned.

    An engine that declared the capability and dropped the path would be worse
    than one that refused: the run would report a pin, the rows would carry the
    label, and the browser spread would still be there with nothing left in the
    file to detect it. That is the same failure `--humanize` produced for real,
    which is why this is a test and not a convention.
    """

    @pytest.mark.parametrize("name, marker", CHROME_BINARY_TAKERS)
    def test_open_accepts_a_path(self, name, marker):
        engine = engines.REGISTRY[name]
        assert engine.supports_chrome_binary
        signature = __import__("inspect").signature(engine.open)
        assert "chrome_binary" in signature.parameters
        assert signature.parameters["chrome_binary"].default is None

    @pytest.mark.parametrize("name, marker", CHROME_BINARY_TAKERS)
    def test_the_path_is_forwarded_and_not_merely_accepted(self, name, marker):
        source = __import__("inspect").getsource(engines.REGISTRY[name].open)
        assert marker in source

    def test_the_declared_set_is_exactly_the_measured_one(self):
        """The list above is what the runner's refusal is built on, so a new
        engine that can take a path has to appear in both or the guard will
        refuse a matrix it should have allowed."""
        declared = {name for name, engine in ENGINE_CLASSES
                    if engine.supports_chrome_binary}
        assert declared == {name for name, _ in CHROME_BINARY_TAKERS}

    def test_a_pinned_run_is_labelled_as_one(self):
        """Intent in the label, outcome in the version. The path itself is not
        in the label - it is machine-specific and would make a useless cell key -
        so `-pinned` is the only thing that says a run was not on the engine's
        own build, and a table mixing pinned and unpinned rows needs it."""
        from nmbench.engines.chromium import label_for

        assert label_for("chromium") == "chromium"
        assert label_for("chromium", "chrome") == "chromium-chrome"
        assert label_for("chromium", None, "/opt/chrome") == "chromium-pinned"


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


class TestTheBuildIsReadableOffTheRow:
    """`browser_build`, which exists because a slice threw the build away.

    The Playwright engines read `browser.version` and record `151.0.7922.34`.
    zendriver and botasaurus expose no such handle, so both took the User-Agent
    and cut it to 40 characters - which keeps `Mozilla/5.0 (Windows NT 10.0;
    Win64; x64` and drops the one token anybody would want. Measured 2026-09-02
    on `tls_clienthello_20260902T180555Z`, eight engines share a TLS fingerprint
    that is decided by the Chrome build, and these two were the only ones whose
    rows could not say which build they ran. The variable the finding turns on
    was cut off by a slice.
    """

    def test_the_chrome_build_is_taken_out_of_the_user_agent(self):
        assert browser_build(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/151.0.7922.34 Safari/537.36"
        ) == "151.0.7922.34"

    def test_firefox_is_read_too(self):
        """Camoufox is not in this path today, but the fallback below is silent
        and a Firefox User-Agent arriving here should not take it."""
        assert browser_build(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) "
            "Gecko/20100101 Firefox/143.0"
        ) == "143.0"

    def test_a_string_with_no_build_keeps_the_old_behaviour(self):
        """The fallback is the slice this replaced, so an engine reporting
        something unexpected degrades to what it recorded before rather than to
        an empty column."""
        assert browser_build("a" * 60) == "a" * 40

    def test_nothing_is_reported_as_nothing(self):
        assert browser_build("") == ""
        assert browser_build(None) == ""


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
    """The non-Playwright shims take Playwright's `page.evaluate` argument, and
    half the probes in this repository pass a block-bodied arrow.

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

    The shims are asserted together on purpose. They were written months apart,
    and a difference between two adapters is a difference in what their columns
    mean.

    **Naming them was not enough, and that is why the last test here reads the
    source instead.** This class asserted zendriver and botasaurus and passed,
    while `seleniumbase` carried the identical unwrapping and was found the same
    evening by a run rather than by the suite - ChromeDriver answers it as
    `JavascriptException: Unexpected identifier 'safe'`, because `return
    ({ const safe = ... })` parses the block as an object literal. A test that
    lists the adapters it knows about cannot fail on the adapter nobody added
    to the list.
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
        from nmbench.engines import botasaurus, seleniumbase, zendriver

        zen, seen = self._shim(zendriver)
        zen.evaluate(script)
        yield "zendriver", seen["expression"]

        page = botasaurus._Page.__new__(botasaurus._Page)
        page.driver = type("_D", (), {"run_js": staticmethod(lambda call: call)})()
        yield "botasaurus", page.evaluate(script)

        page = seleniumbase._Page.__new__(seleniumbase._Page)
        page.driver = type("_D", (), {
            "execute_script": staticmethod(lambda call: call)})()
        yield "seleniumbase", page.evaluate(script)

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

    def test_no_adapter_splits_a_script_on_the_arrow(self):
        """The derived half, and the one that would have caught seleniumbase.

        Every adapter needing this fix needs its own driver double, so the
        tests above can only cover the ones somebody remembered to add - and
        the third instance of this bug shipped past them. Reading the source
        for the unwrapping instead costs no double at all and covers an adapter
        added tomorrow, which is the same reason
        `test_no_script_branches_on_a_provider_name` reads source rather than
        importing.
        """
        directory = pathlib.Path(engines.__file__).parent
        offenders = [path.name for path in sorted(directory.glob("*.py"))
                     if 'split("=>"' in path.read_text(encoding="utf-8")
                     or "split('=>'" in path.read_text(encoding="utf-8")]
        assert offenders == [], (
            f"{offenders} unwrap a script on the arrow. That drops everything "
            "up to the first `=>`, which is an inner arrow in most of the "
            "probes here, so the engine loses every row of every probe passing "
            "a block-bodied one. Call the arrow instead of unwrapping it.")


class TestBotasaurusDisableFeatures:
    """Chrome honours only the last `--disable-features` and drops the rest.

    botasaurus-driver ships one of its own and appends the caller's after it, so
    passing a bare `--disable-features` silently discards the vendor's - which is
    what this engine did until 2026-09-01, leaving site isolation on in every row
    it ever wrote. These tests are about the union surviving, not about the
    string, because the vendor's value is read from the vendor and may change.

    **Two of the three do not run on the gate, and that is a real gap rather
    than a formality.** `requirements-ci.txt` deliberately installs no browser
    framework, so anything reading the vendor's live value skips there. The
    alternative - adding botasaurus-driver to the gate - is refused by that
    file's own reasoning: it would make every push depend on a browser
    framework resolving from PyPI, and the CI badge is the first thing a
    stranger reads. So the gate is covered by the source-reading test below,
    which needs nothing installed and runs everywhere, and the two that read
    the vendor run on a developer machine and on the VPS. What no gate can
    check is whether the vendor's *current* value survives the merge, because
    that value only exists where the vendor is.

    This was found the expensive way: both were written and mutation-tested on
    a machine that had botasaurus-driver, pushed, and turned the gate red on
    all four cells at once.
    """

    def _vendor_names(self):
        pytest.importorskip("botasaurus_driver")
        from botasaurus_driver.core.config import Config
        return set(Config(headless=True).default_arguments)

    def test_the_merged_value_keeps_every_name_the_vendor_asked_for(self):
        from nmbench.engines.botasaurus import merged_disable_features

        vendor = {arg.split("=", 1)[1] for arg in self._vendor_names()
                  if arg.startswith("--disable-features=")}
        assert len(vendor) == 1, (
            "the vendor no longer ships exactly one --disable-features, so "
            "what this engine merges has changed and the helper should have "
            "refused rather than let this test read a different shape")
        theirs = set(next(iter(vendor)).split(","))

        merged = set(merged_disable_features("OptimizationHints")
                     .split("=", 1)[1].split(","))
        assert theirs <= merged, (
            f"{sorted(theirs - merged)} is disabled by botasaurus-driver and "
            "not by us, so our switch discards it and the browser under "
            "measurement is not the one the vendor ships")
        assert "OptimizationHints" in merged, (
            "our own name did not survive the merge, so the 24-45 MB idle "
            "download is back and no row would say so")

    def test_the_engine_passes_the_merged_value_and_not_a_bare_one(self):
        """Source, not behaviour: launching botasaurus needs a browser.

        The failure this guards against is someone writing
        `--disable-features=...` inline again, which reads as correct and
        silently drops whatever the vendor disables.
        """
        source = (pathlib.Path(engines.__file__).parent
                  / "botasaurus.py").read_text(encoding="utf-8")
        body = source.split("driver = Driver(", 1)[1].split(")", 1)[0]
        assert "merged_disable_features(" in body, (
            "the Driver call no longer merges its --disable-features with the "
            "vendor's, so whichever switch Chrome honours, one side's names "
            "are being thrown away silently")
        assert '"--disable-features=' not in body, (
            "a bare --disable-features is being passed to Driver. It lands "
            "after the vendor's and wins, discarding theirs whole. Pass it "
            "through merged_disable_features instead")

    def test_it_refuses_rather_than_guessing_when_the_vendor_shape_changes(self):
        """The guard is the point of the helper, so it is tested directly.

        A vendor that moves or splits its switch must stop the engine, not make
        it quietly emit a value that discards names nobody knows are missing.

        Skips without the vendor for the reason in the class docstring: the
        guard reads `Config.__init__`'s source, so there is no way to exercise
        it without the package that owns that source. Faking the module would
        test the fake.
        """
        pytest.importorskip("botasaurus_driver")
        from nmbench.engines import botasaurus as engine
        from nmbench.engines.base import EngineUnavailable

        original = engine._VENDOR_DISABLE_FEATURES
        engine._VENDOR_DISABLE_FEATURES = __import__("re").compile(
            r'"--this-name-is-not-in-the-vendor-source=([^"]*)"')
        try:
            with pytest.raises(EngineUnavailable) as raised:
                engine.merged_disable_features("OptimizationHints")
        finally:
            engine._VENDOR_DISABLE_FEATURES = original
        assert "merged_disable_features" in str(raised.value), (
            "the refusal should name the function to fix, since whoever hits "
            "it is reading a stack trace and not this test")


class TestTheEnterThatSubmitsTheQuery:
    """`submit_query`'s last two lines, and the only place the harness asks a
    target for an answer.

    Tested with fakes because the property is about **which keyword arguments
    reach Playwright**, and that is invisible in every column the run writes. A
    row whose Enter was waited for twice and a row whose Enter was waited for
    once are byte-identical until one of them times out, so nothing downstream
    could ever notice the flags being dropped. That is the whole reason these
    exist: measured 2026-09-05 on `probehold_20260904T214642Z`, the doubled wait
    had been in this function since it was written and surfaced as
    `ElementHandle.press: Timeout 30000ms exceeded` - our own configured 60 s
    never reached, because `press` waits for the navigation itself and was given
    no timeout of its own.
    """

    class Handle:
        def __init__(self, value=""):
            self.value = value
            self.log = []
            self.press_kwargs = None

        def input_value(self):
            return self.value

        def click(self):
            self.log.append("click")

        # The two signatures below are copied from the **sync** API and are
        # deliberately strict - no `**kwargs`. A fake looser than the thing it
        # stands for cannot fail where the real one does, and on 2026-09-04 this
        # one did exactly that: `press(self, keys, **kwargs)` swallowed a
        # `noWaitAfter=True` that the real sync `press` rejects, four tests went
        # green, the whole suite went green, and the run that followed raised
        # `TypeError: ElementHandle.press() got an unexpected keyword argument
        # 'noWaitAfter'` on every probe -`probehold_20260904T224114Z`, 25
        # attempts, 8.26 MB, 0 judged rows, all four cells stopped by the
        # breaker. `test_the_fakes_are_no_looser_than_the_library` now pins them
        # against the installed library so they cannot drift apart again.
        def type(self, text, *, delay=None, timeout=None, no_wait_after=None):
            self.value += text
            self.log.append(f"type:{text}")

        def press(self, key, *, delay=None, timeout=None, no_wait_after=None):
            self.log.append(f"press:{key}")
            if key == "Enter":
                self.press_kwargs = {"delay": delay, "timeout": timeout,
                                     "no_wait_after": no_wait_after}

    class Navigation:
        value = "response"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class Page:
        def __init__(self, handle):
            self.handle = handle
            self.navigation_kwargs = None

        def wait_for_selector(self, selector, **kwargs):
            return self.handle

        def expect_navigation(self, **kwargs):
            self.navigation_kwargs = kwargs
            return TestTheEnterThatSubmitsTheQuery.Navigation()

    class Target:
        name = "fake"
        search_box = "input#q"

    def _submit(self, query="kimchi mistakes", timeout_ms=60000):
        from nmbench.engines.base import submit_query

        handle = self.Handle()
        page = self.Page(handle)
        import random

        result = submit_query(page, self.Target(), query,
                              rng=random.Random(3), timeout_ms=timeout_ms)
        return page, handle, result

    def test_the_key_press_does_not_wait_for_the_navigation_as_well(self):
        """`no_wait_after=True` is what makes Enter a keystroke and nothing else.

        Read off the shipped driver 2026-09-05,
        `patchright/driver/package/lib/coreBundle.js`: `_press` wraps the
        keystroke in `waitForSignalsCreatedBy(progress, !options.noWaitAfter,
        ...)`, which holds a barrier until any navigation the keystroke started
        has committed. Without the flag the navigation below is waited for twice.

        The keyword is snake_case here and camelCase in that quote, and the
        difference is not cosmetic: the generated sync wrapper translates at the
        boundary, `no_wait_after` in and `noWaitAfter=no_wait_after` out to the
        async impl. Reading the impl or the wire validator - both camelCase, both
        real - and writing what they say is what broke
        `probehold_20260904T224114Z`.
        """
        _page, handle, _result = self._submit()
        assert handle.press_kwargs is not None, "Enter was never pressed"
        assert handle.press_kwargs.get("no_wait_after") is True

    def test_the_fakes_are_no_looser_than_the_library(self):
        """The fakes above may not accept a keyword the real sync API refuses.

        This is the check that was missing on 2026-09-04, and it is one line of
        `inspect` against the installed package rather than a re-reading of
        anyone's source. It fails in three separate ways, all of which have now
        happened or nearly happened: a fake that grows a `**kwargs` and stops
        being able to reject anything, a call site spelled in the impl's or the
        protocol's camelCase, and an upstream release that renames or drops the
        keyword - `no_wait_after` is deprecated, so the third is the one to
        expect.

        Skipped where patchright is absent, which is the offline CI gate by
        design - `requirements-ci.txt` installs no browser framework. So this
        guards the machine that runs the harness, and the strict signatures on
        the fakes are what guards CI.
        """
        import inspect

        sync_api = pytest.importorskip("patchright.sync_api")

        for name in ("press", "type"):
            fake = inspect.signature(getattr(self.Handle, name))
            real = inspect.signature(getattr(sync_api.ElementHandle, name))
            var_kinds = (inspect.Parameter.VAR_KEYWORD,
                         inspect.Parameter.VAR_POSITIONAL)
            assert not [p for p in fake.parameters.values()
                        if p.kind in var_kinds], (
                f"the fake ElementHandle.{name} takes *args or **kwargs, so it "
                f"accepts keywords the real one rejects and cannot fail where "
                f"the run fails")
            assert set(fake.parameters) == set(real.parameters), (
                f"the fake ElementHandle.{name} and the installed one no longer "
                f"take the same arguments: fake {sorted(fake.parameters)}, "
                f"library {sorted(real.parameters)}")

    def test_the_two_waits_carry_the_same_budget(self):
        """The failure this pins is not that the wait was too short but that
        there were two of them with different budgets, so the caller's number
        was unreachable. Asserting equality rather than a value, because the
        defect is the disagreement and not either figure."""
        page, handle, _result = self._submit(timeout_ms=12345)
        assert handle.press_kwargs.get("timeout") == 12345
        assert page.navigation_kwargs["timeout"] == 12345

    def test_the_navigation_is_still_what_the_response_comes_from(self):
        """A cheap guard on the obvious way to 'fix' the doubled wait: dropping
        `expect_navigation` and returning None would make every typed row carry
        no `status`, which reads in the report as the target answering nothing
        rather than as the harness having stopped asking."""
        page, _handle, result = self._submit()
        assert result == "response"
        assert page.navigation_kwargs["wait_until"] == "domcontentloaded"

    def test_the_query_is_typed_before_it_is_submitted(self):
        _page, handle, _result = self._submit(query="swimming technique")
        assert handle.log == ["click", "type:swimming technique", "press:Enter"]
