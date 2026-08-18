"""Query list tests.

The inputs are committed files, so these tests double as a check on the data: a
stranger who forks this repository must be able to send the same strings we did,
in the same order.
"""
import pytest

from nmbench import queries
from nmbench.targets import TARGETS


class TestLoad:
    def test_the_committed_list_holds_a_thousand(self):
        assert len(queries.load("serp_1000")) == 1000

    def test_the_committed_list_has_no_duplicates(self):
        """A repeat is a second measurement of the first one's cache state."""
        loaded = queries.load("serp_1000")
        assert len(set(loaded)) == len(loaded)

    def test_the_committed_list_has_no_blank_or_comment_lines(self):
        assert all(q.strip() and not q.startswith("#")
                   for q in queries.load("serp_1000"))

    def test_a_limit_takes_a_prefix(self):
        """So a fifty-query run is a strict subset of a thousand-query one."""
        assert queries.load("serp_1000", 50) == queries.load("serp_1000")[:50]

    def test_order_is_stable_between_calls(self):
        assert queries.load("serp_1000") == queries.load("serp_1000")

    def test_the_smoke_list_is_built_in(self):
        assert len(queries.load("smoke")) == 10

    def test_an_unknown_list_names_the_known_ones(self):
        with pytest.raises(FileNotFoundError, match="Available"):
            queries.load("does_not_exist")

    def test_asking_for_more_than_exists_is_refused(self):
        """Padding by repetition would measure the repeat, not the list."""
        with pytest.raises(ValueError, match="Repeating queries"):
            queries.load("serp_1000", 2000)

    @pytest.mark.parametrize("limit", [0, -5])
    def test_a_limit_that_sends_nothing_is_refused(self, limit):
        with pytest.raises(ValueError, match="send nothing"):
            queries.load("serp_1000", limit)


class TestEveryTargetCanBeFed:
    """A target names the list it draws from, and the runner loads that list
    without knowing which target asked. A name with no file behind it turns into
    a run that dies at the plan stage, which is cheap, but only if something
    checks - and nothing else does, because the runner never inspects the name.
    """

    @pytest.mark.parametrize("name", sorted(TARGETS))
    def test_the_declared_list_exists_and_is_not_short(self, name):
        declared = getattr(TARGETS[name], "query_list", "serp_1000")
        assert len(queries.load(declared)) == 1000

    def test_the_shop_does_not_draw_from_the_search_list(self):
        """The reason the axis exists. Asked "photosynthesis exam questions",
        Amazon answers with an empty shelf, and an empty shelf cannot be told
        apart from a soft refusal once it is a verdict in a row."""
        assert TARGETS["amazon_search"].query_list != TARGETS["google_serp"].query_list


class TestAvailable:
    def test_lists_the_committed_file_and_the_builtin(self):
        assert {"amazon_1000", "serp_1000", "smoke"} <= set(queries.available())

    def test_is_sorted(self):
        assert queries.available() == sorted(queries.available())
