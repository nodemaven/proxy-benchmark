"""Scheduler tests.

Two properties here are not conveniences and are tested as such. Interleaving is
what makes two engines comparable, because a matrix that finished one engine
before starting the next would have measured the afternoon. Resume is what keeps
an interrupted run from re-sending queries the targets have already answered,
which is the pool-heating pattern the circuit breaker exists to prevent.
"""
import json
from pathlib import Path

import pytest

from nmbench import matrix, providers

QUERIES = [f"q{i}" for i in range(10)]


def cells(engines=("camoufox", "obscura"), targets=("bing_serp",), **kwargs):
    options = {"preset": "light", "countries": ["us"], **kwargs}
    return matrix.build_cells(list(engines), list(targets), **options)


def picked(*names, sells=("country", "sid")):
    """A `chosen` mapping of synthetic definitions, one per name.

    Synthetic rather than loaded from disk, because these tests are about what
    the scheduler does with a definition and not about what any vendor sells. A
    shipped file that stopped listing `country` would otherwise quietly rewrite
    the country tests into tests of something else, and they would still pass.
    """
    return {name: providers.Provider(id=name, label=name,
                                     known_params=frozenset(sells))
            for name in names}


class TestCell:
    def test_key_names_every_axis(self):
        cell = cells(("camoufox",))[0]
        assert cell.key == "benchmark/bing_serp/camoufox-light/us"

    def test_direct_replaces_the_country(self):
        cell = cells(("camoufox",), direct=True)[0]
        assert cell.key.endswith("/direct")

    def test_extra_parameters_reach_the_key(self):
        """They change the sticky session, so two runs that differ only by an
        extra parameter are different cells and must not share a resume record."""
        cell = cells(("camoufox",), extra={"filter": "medium"})[0]
        assert cell.key == "benchmark/bing_serp/camoufox-light/us/filter-medium"

    def test_headful_reaches_the_key(self):
        assert cells(("camoufox",), headful=True)[0].key.endswith("/headful")

    def test_params_carry_country_and_extras_but_no_sid(self):
        cell = cells(("camoufox",), extra={"filter": "medium"})[0]
        assert cell.params_for(picked("mine")["mine"]) == {
            "country": "us", "filter": "medium"}

    def test_direct_sends_no_gateway_parameters(self):
        assert cells(("camoufox",), direct=True)[0].params_for() == {}

    def test_cells_are_hashable_and_the_key_is_stable(self):
        first, second = cells(("camoufox",))[0], cells(("camoufox",))[0]
        assert first == second
        assert len({first, second}) == 1

    def test_extra_order_does_not_change_the_key(self):
        a = matrix.build_cells(["camoufox"], ["bing_serp"], preset="light",
                               countries=["us"], extra={"filter": "medium", "ttl": "5m"})
        b = matrix.build_cells(["camoufox"], ["bing_serp"], preset="light",
                               countries=["us"], extra={"ttl": "5m", "filter": "medium"})
        assert a[0].key == b[0].key


class TestBuildCells:
    def test_is_the_cross_product(self):
        built = cells(("camoufox", "obscura"), ("bing_serp", "ddg_serp"))
        assert len(built) == 4
        assert len({c.key for c in built}) == 4


class TestCountryAxis:
    """The browser reports the host timezone and language list whatever address
    it leaves from, so an exit in the machine's own country is a consistent
    identity and an exit anywhere else is not. Both belong in one time window."""

    def test_every_country_gets_its_own_cell(self):
        built = cells(("camoufox",), countries=["ru", "us"])
        assert [c.country for c in built] == ["ru", "us"]

    def test_the_two_cells_do_not_collide(self):
        built = cells(("camoufox",), countries=["ru", "us"])
        assert len({c.key for c in built}) == 2

    def test_a_direct_cell_has_no_country_and_is_not_duplicated(self):
        """Direct leaves from the machine's own address, so the country axis
        collapses. Two identical keys would make the second look like a resume
        of the first and silently halve the control."""
        built = cells(("chromium:direct",), countries=["ru", "us"])
        assert len(built) == 1
        assert built[0].key.endswith("/direct")

    def test_the_axis_multiplies_the_matrix(self):
        built = cells(("camoufox", "chromium"), ("bing_serp", "ddg_serp"),
                      countries=["ru", "us"])
        assert len({c.key for c in built}) == 8


class TestGeoAlignment:
    def test_it_is_off_unless_asked_for(self):
        assert cells(("camoufox",))[0].geo == "off"

    def test_off_leaves_the_key_alone(self):
        """Runs recorded before the axis existed must still match --resume."""
        assert cells(("camoufox",))[0].key == \
            "benchmark/bing_serp/camoufox-light/us"

    def test_alignment_reaches_the_key(self):
        built = cells(("camoufox",), geo="align")
        assert built[0].key.endswith("/geo-align")

    def test_an_aligned_cell_is_not_the_same_measurement(self):
        plain = cells(("camoufox",))[0]
        aligned = cells(("camoufox",), geo="align")[0]
        assert plain.key != aligned.key


class TestProviderAxis:
    """Two providers are only comparable interleaved in one time window, so the
    provider is a cell and not a run-level setting.

    The tests that matter here are the ones about the key. There are 132 run
    files in `data/runs/` whose keys were written before this axis existed, and
    `--resume` matches on the key: an axis that appended a segment
    unconditionally would make every one of them unresumable, and a run restarted
    against them would re-send queries the targets have already answered. That is
    the pool-heating pattern the circuit breaker exists to prevent, arrived at
    through the scheduler instead.
    """

    def test_the_default_provider_leaves_the_key_alone(self):
        built = cells(("camoufox",), chosen=picked(providers.default_name()))
        assert built[0].key == "benchmark/bing_serp/camoufox-light/us"

    def test_naming_the_default_and_omitting_the_axis_are_one_cell(self):
        """The interleaved multi-provider run is the only shape whose keys
        change, which is what makes the axis free to add."""
        omitted = cells(("camoufox",))[0]
        named = cells(("camoufox",), chosen=picked(providers.default_name()))[0]
        assert omitted.key == named.key
        assert omitted == named

    def test_a_second_provider_reaches_the_key(self):
        built = cells(("camoufox",), chosen=picked("synth"))
        assert built[0].key.endswith("/provider-synth")

    def test_the_two_do_not_collide(self):
        """Averaging two gateways into one cell is the failure this axis exists
        to prevent, and a shared key is how it would happen."""
        built = cells(("camoufox",),
                      chosen=picked(providers.default_name(), "synth"))
        assert len({c.key for c in built}) == 2

    def test_the_axis_multiplies_the_matrix(self):
        built = cells(("camoufox", "chromium"), ("bing_serp", "ddg_serp"),
                      countries=["ru", "us"],
                      chosen=picked(providers.default_name(), "synth"))
        assert len({c.key for c in built}) == 16

    def test_a_direct_cell_is_not_run_once_per_provider(self):
        """A request that never reaches a gateway cannot be attributed to one.
        Leaving the name on would run one identical direct experiment twice and
        present the copies as a provider comparison."""
        built = cells(("chromium:direct",),
                      chosen=picked(providers.default_name(), "synth"))
        assert len(built) == 1
        assert built[0].key.endswith("/direct")
        assert built[0].provider == ""

    def test_a_direct_cell_sends_no_gateway_parameters_under_any_provider(self):
        built = cells(("chromium:direct",), chosen=picked("synth"),
                      extra={"filter": "medium"})
        assert built[0].params_for(picked("synth")["synth"]) == {}

    def test_the_provider_segment_comes_last(self):
        """So the segments already on disk keep their positions and a key can
        still be read left to right by eye."""
        built = cells(("camoufox",), countries=["us"], headful=True,
                      geo="align", extra={"filter": "medium"},
                      chosen=picked("synth"))
        assert built[0].key == ("benchmark/bing_serp/camoufox-light/us/"
                               "filter-medium/headful/geo-align/provider-synth")

    def test_the_default_cell_still_names_its_gateway_for_a_row(self):
        """The key omits the default and a row must not.

        These are two different jobs sharing one field, and they were sharing it
        silently: `provider` is a resume identity, empty for the default so the
        keys of every earlier run stay valid, and it was also being written into
        the bookkeeping rows as the gateway's name. On
        `benchmark_20260915T070057Z` that put `provider: ""` on 28
        `session_closed` rows whose own attempt rows said `nodemaven`.
        """
        built = cells(("camoufox",), chosen=picked(providers.default_name()))
        assert built[0].provider == ""
        assert built[0].provider_id == providers.default_name()

    def test_a_named_gateway_reads_the_same_either_way(self):
        built = cells(("camoufox",), chosen=picked("synth"))
        assert built[0].provider == "synth" == built[0].provider_id

    def test_a_direct_cell_names_no_gateway(self):
        """There is no gateway to name, and the `direct` column already says so.
        Resolving to the default here would attribute this machine's own line to
        a provider."""
        built = cells(("chromium:direct",), chosen=picked("synth"))
        assert built[0].provider_id == ""


class TestAGatewayThatSellsNoCountry:
    """A proxy somebody already owns is one endpoint with one exit behind it,
    and that is the shape most proxies actually have. It is a definition with an
    empty `known_params`, not a special case in the runner, so what has to hold
    is that the country axis collapses for it the way it already collapses for a
    direct cell - otherwise `--countries us,de` produces two cells with one key,
    and the second reads as a resume of the first.

    Both halves are asserted, because the collapse is asked of the definition
    rather than of the provider's name: a mixed matrix - your own proxy against a
    pool, in one window, which is the comparison worth running at all - has to
    keep the axis for the gateway that has it.
    """

    def test_the_countries_asked_for_collapse_to_one_cell(self):
        built = cells(("camoufox",), countries=["ru", "us"],
                      chosen=picked("mine", sells=()))
        assert len(built) == 1

    def test_the_key_says_where_it_left_from(self):
        """`gateway` rather than an empty segment: these rows left from a real
        address, and a key ending in a bare slash would read as a country that
        failed to arrive."""
        built = cells(("camoufox",), chosen=picked("mine", sells=()))
        assert built[0].key == ("benchmark/bing_serp/camoufox-light/gateway/"
                                "provider-mine")

    def test_no_country_is_put_on_the_wire(self):
        """The gateway measured here hangs about 20 s on an empty value and
        answers an unknown name with 200 and the setting dropped. Neither is
        visible in a row, so the parameter is left out rather than sent empty."""
        built = cells(("camoufox",), countries=["us"],
                      extra={"filter": "medium"},
                      chosen=picked("mine", sells=()))
        assert built[0].params_for(
            picked("mine", sells=())["mine"]) == {"filter": "medium"}

    def test_a_country_gateway_in_the_same_matrix_keeps_its_axis(self):
        built = cells(("camoufox",), countries=["ru", "us"],
                      chosen={**picked("pool"), **picked("mine", sells=())})
        assert sorted(c.country for c in built) == ["", "ru", "us"]
        assert len({c.key for c in built}) == 3


class TestAnUnpinnedCountryIsAKeywordAndNotACountryCode:
    """`any` is this harness's word for "do not pin one" and every gateway
    spells it differently, including three that have no spelling at all.

    It is tested because the keyword started life as NodeMaven's own wire value
    and reached the runner without anyone marking it as one, so `--countries
    any` sent the literal string to four gateways. The 2026-09-11 run is the
    cost: six of sixteen cells drew 40 `ERR_TUNNEL_CONNECTION_FAILED` and 20
    timeouts, reached the target zero times, and three providers were published
    at 0% against a question they were never asked.

    A synthetic definition and not the shipped TOML, for the reason `picked`
    gives: if `nodemaven.toml` stopped saying `any` these tests would quietly
    become tests of something else and still pass. The shipped files are pinned
    separately, in `test_providers.py`.
    """

    def unpinned(self, provider):
        return cells(("camoufox",), countries=[matrix.ANY])[0].params_for(
            provider)

    def test_a_gateway_with_a_wire_value_for_it_is_sent_that_value(self):
        spells_it = providers.Provider(id="pool", label="pool",
                                       known_params=frozenset({"country"}),
                                       country_any="whatever")
        assert self.unpinned(spells_it) == {"country": "whatever"}

    def test_a_gateway_with_no_spelling_is_sent_no_country_at_all(self):
        """Not the keyword, and not an empty value either. Bright Data answers
        a country it does not recognise with 407 and Oxylabs and Decodo with
        400, so the tunnel never opens and the row carries an error about a
        target that was never reached."""
        assert self.unpinned(picked("pool")["pool"]) == {}

    def test_the_axis_is_still_recorded_even_where_nothing_is_sent(self):
        """Sending nothing is not the same as having no axis. A cell that asked
        for an unpinned country and a gateway that sells no country produce the
        same empty parameter set and must not produce the same key, or the two
        are one line in the summary and one identity on `--resume`."""
        built = cells(("camoufox",), countries=[matrix.ANY])[0]
        assert built.country == matrix.ANY
        assert built.key.endswith("/any")

    def test_asking_for_it_with_no_provider_raises_instead_of_dropping_it(self):
        """The failure this whole class is about is a setting that was not
        applied and left no trace. A caller that reaches this method without a
        dialect cannot be given a silent empty dict, because that is
        indistinguishable from a gateway that sells no country."""
        with pytest.raises(ValueError, match="keyword"):
            cells(("camoufox",), countries=[matrix.ANY])[0].params_for()

    def test_a_direct_cell_needs_no_provider_to_ask_for_nothing(self):
        """There is no gateway on the direct arm, so there is no dialect to
        want and the guard above must not fire."""
        built = cells(("camoufox",), countries=[matrix.ANY], direct=True)
        assert built[0].params_for() == {}


class TestTheRuleHasOneHome:
    """The translation is a function so that the matrix is not the only caller.

    `probes/gateway_health.py` opens tunnels too, and it is the instrument an
    operator reaches for *first* when a gateway looks wrong. Until 2026-09-14 it
    had its own idea of what `--country any` meant - namely nothing, it passed
    the string through - so pointing it at four gateways to check the fix would
    have reproduced the bug and reported three dead providers.

    That is the defect class `NodeMaven\\CLAUDE.md` logs between the Python and
    Rust SDKs: one rule, several implementations, a correction that reaches one
    of them. These tests are here so a change to the rule breaks every caller
    that stopped using it.
    """

    def test_an_ordinary_country_passes_through_untouched(self):
        assert matrix.wire_country("us", picked("pool")["pool"]) == "us"

    def test_a_gateway_with_no_spelling_is_told_to_send_nothing(self):
        assert matrix.wire_country(matrix.ANY, picked("pool")["pool"]) == ""

    def test_a_gateway_with_a_spelling_is_told_to_send_it(self):
        spells_it = providers.Provider(id="pool", label="pool",
                                       known_params=frozenset({"country"}),
                                       country_any="whatever")
        assert matrix.wire_country(matrix.ANY, spells_it) == "whatever"

    def test_no_provider_is_an_error_and_not_a_default(self):
        """Either default is a wrong answer delivered quietly: passing the
        keyword through is the 2026-09-11 behaviour, and dropping it changes
        what the one gateway that spells it gets asked."""
        with pytest.raises(ValueError, match="keyword"):
            matrix.wire_country(matrix.ANY)

    def test_the_caller_is_named_in_the_error(self):
        """The probe and the matrix raise the same exception from different
        places, and an operator reading it needs to know which one was asking."""
        with pytest.raises(ValueError, match=r"--country any"):
            matrix.wire_country(matrix.ANY, where="--country any")

    def test_the_probe_translates_rather_than_passing_the_keyword_through(self):
        """Pinned against the probe's own source, because the failure it guards
        is not a wrong value - it is a caller that stopped calling. A behavioural
        test would need a live gateway, and this repository's rule is that
        nothing needing one runs from the workstation."""
        source = (Path(__file__).resolve().parent.parent / "scripts" / "probes"
                  / "gateway_health.py").read_text(encoding="utf-8")
        assert "matrix.wire_country(" in source
        assert '{"country": args.country' not in source


class TestTheHostingHintDoesNotCryWolf:
    """The exit-operator warning in `probes/gateway_health.py`.

    It is a hint on a name and can never be a verdict - the ASN registration
    would settle it and the probe has none. What it must not do is fire on a
    consumer ISP, because the line's whole value is that somebody believes it.

    On 2026-09-14 it fired for both Oxylabs and Decodo on a 20-attempt unpinned
    arm. The cause was `colo` matching inside `Colombia`, and because the line
    printed no names there was nothing in the output to catch it with.
    """

    def health(self):
        import importlib.util
        path = (Path(__file__).resolve().parent.parent / "scripts" / "probes"
                / "gateway_health.py")
        spec = importlib.util.spec_from_file_location("nmbench_health", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_names_that_produced_the_false_alarm_do_not_fire(self):
        reads_as_hosting = self.health().reads_as_hosting
        for org in ("AS10620 Telmex Colombia S.A.",
                    "AS13489 UNE EPM TELECOMUNICACIONES S.A.",
                    "AS12252 America Movil Peru S.A.C.",
                    "AS7922 Comcast Cable Communications, LLC",
                    "AS213541 WS Telecom Inc"):
            assert not reads_as_hosting(org), org

    def test_it_still_fires_on_the_names_it_is_for(self):
        """The other half of the correction. Narrowing a filter until it matches
        nothing is the same failure as widening it until it matches everything,
        and only this arm can tell the two apart."""
        reads_as_hosting = self.health().reads_as_hosting
        for org in ("AS60068 Datacamp Limited",
                    "AS16276 OVH SAS",
                    "AS396356 Latitude.sh",
                    "AS63949 Linode, LLC",
                    "AS24940 Hetzner Online GmbH",
                    "AS14061 DigitalOcean, LLC",
                    "AS8100 QuadraNet Enterprises LLC data center"):
            assert reads_as_hosting(org), org


class TestEngineSpec:
    """`chromium,chromium:direct` puts the control on both sides of the gateway
    inside one time window. Two separate runs would measure the hour as well."""

    def test_a_plain_name_is_proxied(self):
        assert matrix.parse_engine("chromium") == ("chromium", False)

    def test_the_suffix_bypasses_the_gateway(self):
        assert matrix.parse_engine("chromium:direct") == ("chromium", True)

    def test_surrounding_space_is_tolerated(self):
        assert matrix.parse_engine("  chromium:direct ") == ("chromium", True)

    def test_an_unknown_mode_says_what_exists(self):
        with pytest.raises(ValueError, match="direct"):
            matrix.parse_engine("chromium:proxied")

    def test_both_sides_can_coexist(self):
        built = matrix.build_cells(["chromium", "chromium:direct"],
                                   ["bing_serp"], preset="light", countries=["us"])
        assert [c.direct for c in built] == [False, True]

    def test_the_two_cells_do_not_collide(self):
        """They must stay separable in the output, or the direct control is
        averaged into the proxied number and stops being a control."""
        built = matrix.build_cells(["chromium", "chromium:direct"],
                                   ["bing_serp"], preset="light", countries=["us"])
        assert len({c.key for c in built}) == 2

    def test_a_direct_cell_sends_no_gateway_parameters(self):
        built = matrix.build_cells(["chromium:direct"], ["bing_serp"],
                                   preset="light", countries=["us"],
                                   extra={"filter": "medium"})
        assert built[0].params_for() == {}

    def test_the_global_flag_still_wins(self):
        """--direct means nothing leaves through the gateway. A spec that
        omitted the suffix must not partly undo it."""
        built = matrix.build_cells(["chromium", "camoufox:direct"],
                                   ["bing_serp"], preset="light", countries=["us"],
                                   direct=True)
        assert all(c.direct for c in built)


class TestPlan:
    def test_batches_are_interleaved_across_cells(self):
        batches = matrix.plan(cells(), QUERIES, batch_size=5)
        engines = [b.cell.engine for b in batches]
        assert engines == ["camoufox", "obscura", "camoufox", "obscura"]

    def test_every_query_is_scheduled_exactly_once_per_cell(self):
        batches = matrix.plan(cells(), QUERIES, batch_size=3)
        for engine in ("camoufox", "obscura"):
            scheduled = [q for b in batches if b.cell.engine == engine
                         for q in b.queries]
            assert scheduled == QUERIES

    def test_a_short_tail_is_kept(self):
        batches = matrix.plan(cells(("camoufox",)), QUERIES, batch_size=4)
        assert [len(b.queries) for b in batches] == [4, 4, 2]

    def test_batch_index_identifies_the_session(self):
        batches = matrix.plan(cells(("camoufox",)), QUERIES, batch_size=5)
        assert [b.index for b in batches] == [0, 1]

    def test_completed_pairs_are_dropped(self):
        cell = cells(("camoufox",))[0]
        done = {(cell.key, "q0"), (cell.key, "q1")}
        batches = matrix.plan([cell], QUERIES, batch_size=4, done=done)
        assert [q for b in batches for q in b.queries] == QUERIES[2:]

    def test_resume_is_per_cell_not_per_query(self):
        """A query answered by Bing says nothing about DuckDuckGo."""
        built = cells(("camoufox",), ("bing_serp", "ddg_serp"))
        done = {(built[0].key, q) for q in QUERIES}
        batches = matrix.plan(built, QUERIES, batch_size=5, done=done)
        assert {b.cell.target for b in batches} == {"ddg_serp"}

    def test_a_fully_completed_plan_is_empty(self):
        built = cells(("camoufox",))
        done = {(built[0].key, q) for q in QUERIES}
        assert matrix.plan(built, QUERIES, batch_size=5, done=done) == []

    def test_uneven_cells_do_not_lose_work(self):
        """One cell resumed further than another must not truncate the other."""
        built = cells(("camoufox", "obscura"))
        done = {(built[0].key, q) for q in QUERIES[:8]}
        batches = matrix.plan(built, QUERIES, batch_size=2, done=done)
        counted = {}
        for batch in batches:
            counted.setdefault(batch.cell.engine, []).extend(batch.queries)
        assert counted["camoufox"] == QUERIES[8:]
        assert counted["obscura"] == QUERIES

    @pytest.mark.parametrize("size", [0, -1])
    def test_a_useless_batch_size_is_refused(self, size):
        with pytest.raises(ValueError, match="at least 1"):
            matrix.plan(cells(), QUERIES, batch_size=size)


class TestPerTargetQueries:
    """A shop and a search engine have to run in one time window to be
    comparable, and they cannot be sent the same strings. The mapping is how
    both hold at once."""

    def test_each_target_gets_its_own_list(self):
        built = cells(("camoufox",), ("bing_serp", "ddg_serp"))
        batches = matrix.plan(built, {"bing_serp": ["a", "b"],
                                      "ddg_serp": ["c"]}, batch_size=5)
        got = {b.cell.target: b.queries for b in batches}
        assert got == {"bing_serp": ["a", "b"], "ddg_serp": ["c"]}

    def test_lists_of_different_lengths_do_not_truncate_each_other(self):
        built = cells(("camoufox",), ("bing_serp", "ddg_serp"))
        batches = matrix.plan(built, {"bing_serp": QUERIES,
                                      "ddg_serp": ["c"]}, batch_size=4)
        counted = {}
        for batch in batches:
            counted.setdefault(batch.cell.target, []).extend(batch.queries)
        assert counted == {"bing_serp": QUERIES, "ddg_serp": ["c"]}

    def test_resume_still_matches_on_the_cell_and_the_query(self):
        built = cells(("camoufox",), ("bing_serp",))
        done = {(built[0].key, "a")}
        batches = matrix.plan(built, {"bing_serp": ["a", "b"]}, batch_size=5,
                              done=done)
        assert [q for b in batches for q in b.queries] == ["b"]


class TestLoadCompleted:
    def write(self, tmp_path, rows):
        path = tmp_path / "run.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                        encoding="utf-8")
        return path

    def test_reads_judged_pairs(self, tmp_path):
        path = self.write(tmp_path, [{"cell": "c", "query": "q0", "verdict": "ok"},
                                     {"cell": "c", "query": "q1",
                                      "verdict": "captcha"}])
        assert matrix.load_completed([path]) == {("c", "q0"), ("c", "q1")}

    def test_errors_are_retried(self, tmp_path):
        """An error is this harness failing, not the target answering."""
        path = self.write(tmp_path, [{"cell": "c", "query": "q0",
                                      "verdict": "error"}])
        assert matrix.load_completed([path]) == set()

    def test_rows_without_a_query_are_ignored(self, tmp_path):
        """Cell-level bookkeeping rows, such as a tripped breaker."""
        path = self.write(tmp_path, [{"cell": "c", "verdict": "cell_stopped"}])
        assert matrix.load_completed([path]) == set()

    def test_a_truncated_last_line_does_not_lose_the_file(self, tmp_path):
        """Interruption is the normal way a long run ends."""
        path = tmp_path / "run.jsonl"
        path.write_text(json.dumps({"cell": "c", "query": "q0", "verdict": "ok"})
                        + '\n{"cell": "c", "que', encoding="utf-8")
        assert matrix.load_completed([path]) == {("c", "q0")}

    def test_a_missing_file_is_not_an_error(self, tmp_path):
        assert matrix.load_completed([tmp_path / "nope.jsonl"]) == set()

    def test_several_files_are_unioned(self, tmp_path):
        first = self.write(tmp_path, [{"cell": "c", "query": "q0", "verdict": "ok"}])
        second = tmp_path / "second.jsonl"
        second.write_text(json.dumps({"cell": "c", "query": "q1", "verdict": "ok"}),
                          encoding="utf-8")
        assert matrix.load_completed([first, second]) == {("c", "q0"), ("c", "q1")}


class TestWhichGatewayTheRowsWereMeasuredThrough:
    """Resume is the one door into a run that `build_cells` never sees.

    A cell on the default provider carries no provider segment in its key, so two
    gateways produce byte-identical keys and the resume cannot tell them apart on
    the key alone. Read off the rows instead, and let the runner refuse.
    """

    def write(self, tmp_path, rows):
        path = tmp_path / "run.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                        encoding="utf-8")
        return path

    def test_a_proxied_row_names_its_gateway(self, tmp_path):
        path = self.write(tmp_path, [{"provider": "oxylabs", "direct": False}])
        assert matrix.providers_named([path]) == {"oxylabs"}

    def test_rows_predating_the_column_name_nothing(self, tmp_path):
        # 2271 of the 2285 rows committed here are in this state. Reading an
        # absent provider as a conflict would refuse every resume on disk.
        path = self.write(tmp_path, [{"cell": "c", "query": "q", "verdict": "ok"}])
        assert matrix.providers_named([path]) == set()

    def test_a_direct_row_names_no_gateway(self, tmp_path):
        # The row still carries the provider it would have used, and it used
        # none: nothing it did went through a gateway, so it is not evidence
        # about which one answered.
        path = self.write(tmp_path, [{"provider": "nodemaven", "direct": True}])
        assert matrix.providers_named([path]) == set()

    def test_a_mixed_file_names_both(self, tmp_path):
        path = self.write(tmp_path, [{"provider": "a", "direct": False},
                                     {"provider": "b", "direct": False},
                                     {"provider": "a", "direct": False}])
        assert matrix.providers_named([path]) == {"a", "b"}


class TestEstimate:
    def test_counts_the_plan_before_anything_is_sent(self):
        batches = matrix.plan(cells(), QUERIES, batch_size=5)
        estimate = matrix.estimate(batches)
        assert estimate["attempts"] == 20
        assert estimate["sessions"] == 4
        assert estimate["cells"] == 2
        assert estimate["megabytes"] > 0
        assert estimate["hours"] > 0

    def test_an_empty_plan_costs_nothing(self):
        assert matrix.estimate([]) == {"cells": 0, "sessions": 0, "attempts": 0,
                                       "megabytes": 0.0, "hours": 0.0,
                                       "basis": {}}

    def test_bytes_come_from_the_target_and_not_from_one_constant(self):
        """The error that made this per-target: one global figure predicted
        3.9 MB for a 2026-08-12 Walmart run that cost 30.2 MB, while being
        roughly right for Google in the same afternoon. A plan of the same size
        against two targets must not cost the same."""
        google = matrix.plan(cells(targets=("google_serp",)), QUERIES,
                             batch_size=5)
        walmart = matrix.plan(cells(targets=("walmart_search",)), QUERIES,
                              batch_size=5)
        assert (matrix.estimate(walmart)["megabytes"]
                > 2 * matrix.estimate(google)["megabytes"])

    def test_an_unmeasured_target_is_priced_from_the_floor(self):
        batches = matrix.plan(cells(targets=("bing_serp",)), QUERIES,
                              batch_size=5)
        estimate = matrix.estimate(batches)
        assert estimate["basis"] == {"bing_serp": "default"}
        expected = 20 * matrix.DEFAULT_BYTES / 1024 / 1024
        assert estimate["megabytes"] == round(expected, 1)

    def test_the_basis_says_which_targets_were_measured(self):
        """A mixed matrix prices half of itself from a floor. The caller prints
        this, so an estimate cannot imply a precision it does not have."""
        batches = matrix.plan(cells(targets=("google_serp", "bing_serp")),
                              QUERIES, batch_size=5)
        assert matrix.estimate(batches)["basis"] == {
            "google_serp": "measured", "bing_serp": "default"}

    def test_an_override_replaces_the_table_everywhere(self):
        batches = matrix.plan(cells(targets=("google_serp", "bing_serp")),
                              QUERIES, batch_size=5)
        estimate = matrix.estimate(batches, bytes_per_attempt=1_000_000)
        assert estimate["megabytes"] == round(40 * 1_000_000 / 1024 / 1024, 1)
        assert set(estimate["basis"].values()) == {"override"}

    def test_measured_targets_carry_the_run_they_came_from(self):
        """Pinned so a number cannot be edited without editing the provenance
        beside it. Both were read by calibrate.py on 2026-08-12."""
        assert matrix.MEASURED_BYTES == {"google_serp": 776_000,
                                         "walmart_search": 2_109_000}
