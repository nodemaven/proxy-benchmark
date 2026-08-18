"""The attribution rule in the report: was the engine ever shown a page.

Nothing here reads the network or the run directory. `report.py` is a script,
so it is loaded by path the same way the dispatcher would load it.

This file exists because of one wrong headline. The split between "the address
was refused" and "the engine failed" was read off the HTTP status alone, and
Google serves its `/sorry/` diversion with a 200 about a quarter of the time.
That single omission moved 14 of camoufox's 16 Google responses into a
denominator it had never been shown a page for, and moved 16 diversions into
patchright's numerator of failures. Both headline numbers in the report were
wrong, in opposite directions, from one missing condition.
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
