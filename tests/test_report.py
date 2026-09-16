"""The attribution rules in the report: who a failure belongs to.

Nothing here reads the network or the run directory. `report.py` is a script,
so it is loaded by path the same way the dispatcher would load it.

This file exists because of one wrong headline. The split between "the address
was refused" and "the engine failed" was read off the HTTP status alone, and
Google serves its `/sorry/` diversion with a 200 about a quarter of the time.
That single omission moved 14 of camoufox's 16 Google responses into a
denominator it had never been shown a page for, and moved 16 diversions into
patchright's numerator of failures. Both headline numbers in the report were
wrong, in opposite directions, from one missing condition.

The three classes below are three versions of that same mistake, and the two
added on 2026-09-14 come from a run that was published rather than from one
that was caught in review:

  was the engine shown a page     the original. A diversion served as 200 is not
                                  an observation of the engine
  which question was this row     `--countries any` is a keyword, and three of
  answering                       the four gateways have no wire spelling for
                                  it, so their rows carry no country. Reading
                                  the wire value alone pools three different
                                  gateways' unpinned behaviour into one arm
  was the target ever reached     a batch is one browser on one sticky exit. A
                                  cell whose every session died below the
                                  application layer has no pass rate at all, and
                                  on 2026-09-11 six of sixteen were published at
                                  0% on exactly that
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "nmbench_report", ROOT / "scripts" / "analysis" / "report.py")
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)


SEARCH = "https://www.google.com/search?q=kimchi+mistakes&hl=en"
SORRY = ("https://www.google.com/sorry/index?continue=https://www.google.com/"
         "search%3Fq%3Dkimchi&hl=en&q=EhAqAREvRAG")
AMAZON = "https://www.amazon.com/s?k=usb+c+cable"


class TestWasServed:
    def test_a_page_on_the_requested_path_was_served(self):
        assert report.was_served(
            {"status": 200, "url": SEARCH, "final_url": SEARCH})

    def test_a_diversion_served_as_200_was_not(self):
        """The case that cost the headline.

        Status 200, a real body, and the engine never saw the page it asked
        for. Counting this as a live exit blames the framework for a refusal
        aimed at the address.
        """
        assert not report.was_served(
            {"status": 200, "url": SEARCH, "final_url": SORRY})

    def test_the_same_diversion_served_as_429_was_not_either(self):
        """Google answers the identical page both ways, so neither status may
        decide it on its own."""
        assert not report.was_served(
            {"status": 429, "url": SEARCH, "final_url": SORRY})

    def test_a_refusal_on_the_requested_path_counts_as_served(self):
        """Amazon and Walmart refuse inline, without diverting.

        That is deliberate and it is why those targets are excluded from the
        split rather than shown wrong: the exchange cannot tell their refusal
        from their result page, so the report says so instead of guessing.
        """
        assert report.was_served(
            {"status": 200, "url": AMAZON, "final_url": AMAZON})

    def test_a_throttle_status_was_not_served(self):
        assert not report.was_served(
            {"status": 503, "url": AMAZON, "final_url": AMAZON})

    def test_an_attempt_that_never_completed_was_not_served(self):
        assert not report.was_served(
            {"status": None, "url": SEARCH, "final_url": None})

    def test_a_query_string_alone_does_not_count_as_a_diversion(self):
        """Search engines add and drop query parameters on the way through.

        Only the host and the path decide, because a rule that keyed on the
        full URL would file every served page as a diversion the first time a
        target appended a tracking parameter.
        """
        assert report.was_served({
            "status": 200, "url": SEARCH,
            "final_url": SEARCH + "&source=hp&ei=abc123"})

    def test_a_row_with_no_final_url_is_trusted_to_its_status(self):
        """Runs recorded before `final_url` existed still have to read.

        The alternative is a rule that silently reports every historical row as
        unserved, which would rewrite the past into a much worse result than it
        was.
        """
        assert report.was_served({"status": 200, "url": SEARCH})


class TestLiveResponses:
    def test_it_counts_only_the_pages_that_arrived(self):
        rows = [
            {"status": 200, "url": SEARCH, "final_url": SEARCH},
            {"status": 200, "url": SEARCH, "final_url": SORRY},
            {"status": 429, "url": SEARCH, "final_url": SORRY},
            {"status": None, "url": SEARCH, "final_url": None},
        ]
        assert report.live_responses(rows) == 1

    def test_a_cell_of_pure_diversions_holds_no_observation(self):
        """The condition the pass rate table prints as `unmeasured`.

        A cell can reach the breaker on ten burned exits without the target
        ever answering it, and 0% there is a statement about the pool wearing
        the framework's name.
        """
        rows = [{"status": 200, "url": SEARCH, "final_url": SORRY}
                for _ in range(10)]
        assert report.live_responses(rows) == 0


class TestWhichCountryTheRowWasAnsweringFor:
    """The axis value and the wire value are different columns now.

    `--countries any` is this harness's keyword for "do not pin one". NodeMaven
    has a wire spelling for it and the other three gateways have none, so on
    2026-09-14 those three stopped being sent a country parameter at all. Their
    rows therefore carry no country in `params`, and a report that reads only
    `params` labels all three `?` and pools them into one arm - three separate
    answers to "what does this gateway give you unpinned", averaged into a
    number belonging to none of them.
    """
    def test_the_axis_value_is_preferred_over_the_wire_value(self):
        assert report.asked_country(
            {"country": "any", "params": {"country": "any"}}) == "any"

    def test_a_gateway_sent_no_country_still_reports_the_axis(self):
        """The case the whole helper is for: the three competitors unpinned."""
        assert report.asked_country({"country": "any", "params": {}}) == "any"

    def test_a_row_written_before_the_column_falls_back_to_the_wire(self):
        """130 such rows are on disk and they all came through one gateway that
        does spell it, so the fallback is exact for them and not a guess."""
        assert report.asked_country({"params": {"country": "us"}}) == "us"

    def test_a_row_with_neither_reports_nothing_rather_than_guessing(self):
        assert report.asked_country({"params": {}}) == ""
        assert report.asked_country({}) == ""

    def test_an_unpinned_arm_is_not_labelled_unknown(self):
        """What the pooling looked like, at the point it would have happened.

        Before the axis column existed, a row with no country in `params` was
        labelled `?`, and every gateway with no spelling for unpinned landed in
        that one bucket alongside any row whose parameters were not recorded.
        """
        countries = {"us", "any"}
        oxylabs = {"engine": "chromium/x", "country": "any", "params": {}}
        assert report.engine_label(oxylabs, countries) == "chromium/x any"
        assert report.engine_label(
            {"engine": "chromium/x", "params": {}}, countries) == "chromium/x ?"


class TestWhichGatewayAnsweredTheRow:
    """Found by running the script on synthetic data shaped like the next run.

    Every committed run came through one provider, so leaving the gateway out of
    the label cost nothing and was invisible. Fed two gateways, one of them with
    a dead tunnel on one arm, the report put all of them in one row per engine:
    the dead arm's attempts were all `error`, so they left the pass-rate
    denominator entirely, and the row they were pooled into printed 100%. A
    provider's total failure disappearing into another provider's success is the
    worst attribution error this report could make, and the whole cost of
    finding it was running it once on data of the right shape.
    """
    # Two, because a single country is not named either and a fixture holding
    # one would have hidden the country half of every expectation below.
    COUNTRIES = {"us", "any"}
    TWO = {"nodemaven", "oxylabs"}

    def row(self, provider, **extra):
        return dict({"engine": "chromium/none", "provider": provider,
                     "country": "us", "params": {"country": "us"}}, **extra)

    def test_two_gateways_get_two_labels(self):
        labels = {report.engine_label(self.row(p), self.COUNTRIES,
                                      gateways=self.TWO) for p in self.TWO}
        assert labels == {"chromium/none nodemaven us",
                          "chromium/none oxylabs us"}

    def test_one_gateway_is_not_named_at_all(self):
        """The common case, and every run on disk. Naming the only gateway in
        the run would widen every label for no information."""
        assert report.engine_label(self.row("nodemaven"), self.COUNTRIES,
                                   gateways={"nodemaven"}) \
            == "chromium/none us"

    def test_a_direct_row_belongs_to_no_gateway(self):
        assert report.engine_label(
            self.row(None, direct=True), self.COUNTRIES,
            gateways=self.TWO) == "chromium/none"

    def test_a_proxied_row_with_no_provider_recorded_says_so(self):
        """Rather than silently joining whichever gateway sorts first."""
        assert report.engine_label(self.row(None), self.COUNTRIES,
                                   gateways=self.TWO) \
            == "chromium/none ? us"


class TestTheSessionSection:
    """A batch is one browser on one sticky exit, so ten failed attempts inside
    one are a single burnt exit and not ten observations."""
    def closed(self, cell, reason, prefix=None, index=0):
        return {"cell": cell, "verdict": "session_closed",
                "verdict_reason": reason, "batch_index": index,
                "exit_prefix": prefix}

    def test_a_run_without_session_rows_says_so(self, capsys):
        """Every run recorded before 2026-09-14. An empty table there would read
        as a clean run, which is the opposite of what it is."""
        report.sessions([{"cell": "a", "verdict": "cell_stopped"}])
        out = capsys.readouterr().out
        assert "predates" in out
        assert "Absent, not zero" in out

    def test_a_cell_the_target_never_answered_is_named(self, capsys):
        rows = [self.closed("c1", "unreachable", "203.0.113", i)
                for i in range(3)]
        report.sessions(rows)
        out = capsys.readouterr().out
        assert "never answered by their target" in out
        assert "unmeasured, not zero" in out
        assert "c1" in out

    def test_a_cell_that_was_refused_is_not_named_as_unmeasured(self, capsys):
        """`dead` is an answer. The target was reached and said no, which is a
        real 0% and must not be softened into `unmeasured`."""
        rows = [self.closed("c1", "dead", "203.0.113", i) for i in range(3)]
        report.sessions(rows)
        assert "never answered by their target" not in capsys.readouterr().out

    def test_the_exit_yield_counts_sessions_that_served_something(self, capsys):
        rows = ([self.closed("c1", "alive", "203.0.113", 0)]
                + [self.closed("c1", "dead", "198.51.100", 1)]
                + [self.closed("c1", "unreachable", "192.0.2", 2)])
        report.sessions(rows)
        assert "1 of 3 sessions served at least one page (33%)" \
            in capsys.readouterr().out

    def test_a_batch_that_sent_nothing_is_not_charged_to_the_gateway(self,
                                                                    capsys):
        """`abandoned` is our own launcher failing to start a browser. Counting
        it in the yield would publish an engine defect as a pool defect."""
        rows = [self.closed("c1", "alive", "203.0.113", 0),
                self.closed("c1", "abandoned", None, 1)]
        report.sessions(rows)
        out = capsys.readouterr().out
        assert "1 of 1 sessions served at least one page (100%)" in out
        assert "1 sessions sent nothing at all" in out

    def test_distinct_exits_are_counted_and_not_sessions(self, capsys):
        """Two sessions that drew the same address are one exit, and a cell
        stopped on three of those has less evidence than its count suggests."""
        rows = [self.closed("c1", "dead", "203.0.113", 0),
                self.closed("c1", "dead", "203.0.113", 1),
                self.closed("c1", "dead", "198.51.100", 2)]
        report.sessions(rows)
        line = [ln for ln in capsys.readouterr().out.splitlines()
                if ln.strip().startswith("c1")][0]
        assert line.split() == ["c1", "0", "3", "0", "0", "2"]
