"""The pieces of `scripts/probes/marketplace_recon.py` that need no browser.

`HomePage`, `record` and the size table in `report` are pure, and all three are
new with `--entry home`. The reason to test them at all is the sibling probe:
`b2b_recon.py` grew two bugs in one hand-built row and both were invisible to
the suite, because nothing ever constructed one of its rows without a live
site. The same shape is here - a row built by hand, an arm only ever exercised
against a real storefront - so the same gap is worth closing before it is paid
for in exits rather than in tests.

One of the three tests below is a regression that was written and caught in the
same hour. The size table originally grouped on `row["entry"]`, which looks
correct and is not: on `--entry home` the front page and the search that
followed it both carry `entry="home"`, so the two documents collapsed back into
one line and the median was taken across a front page and a result page
together.
"""
import importlib.util
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from nmbench.targets import TARGETS

ROOT = Path(__file__).resolve().parent.parent


def load_script(name):
    path = ROOT / "scripts" / "probes" / f"{name}.py"
    # `marketplace_recon` imports `b2b_recon` as a top-level module, the way it
    # does when run from its own directory, so that directory has to be
    # importable here.
    probes = str(path.parent)
    if probes not in sys.path:
        sys.path.insert(0, probes)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


recon = load_script("marketplace_recon")


class Args:
    geo = "sg"
    entry = "home"
    warm = "off"


class Sink:
    def __init__(self):
        self.written = []

    def write(self, row):
        self.written.append(row)


ASKED = "https://shopee.sg/search?keyword=wireless+earbuds"


def wall(next_url="https%3A%2F%2Fshopee.sg%2F", tracking="497327640d2-fd61"):
    """Shopee's traffic check, in the shape `154738Z` recorded it.

    The `tracking_id` is a parameter and not a constant because it is
    per-request on the real site, and a fixture that left it out would let a
    summary grouping on the whole URL look correct. That is exactly what
    happened: the destination block passed its tests against hand-built rows
    and printed six lines of "moved 1x" the first time it saw a real run.
    """
    return (f"https://shopee.sg/verify/traffic/error?home_url=https%3A%2F%2F"
            f"shopee.sg&is_logged_in=false&next={next_url}"
            f"&tracking_id={tracking}&type=4")


WALL = wall()


def body(anchors=0):
    """A body carrying a given number of anchors and nothing else.

    Written with a space after the tag name because that is what the count
    looks for: `<a ` and not `<a`. Without the space the pattern also matches
    `<abbr` and `<article`, which is the sort of thing that turns a wall into a
    served page by two elements.
    """
    return "<html>" + '<a href="#">x</a>' * anchors + "</html>"


def row(target="shopee", verdict="recon", html_len=1000, status=200,
        url=ASKED, final_url=None, html=None):
    # `final_url=None` means the response stayed where it was sent, which is
    # what `fingerprint` writes: it records the page's URL either way, so a row
    # that did not move carries the requested URL rather than nothing.
    return {"target": target, "verdict": verdict, "html_len": html_len,
            "status": status, "url": url,
            "final_url": url if final_url is None else final_url,
            "_html": body() if html is None else html}


class TestTheFrontPageIsItsOwnTarget:
    def test_it_ignores_the_query(self):
        site = recon.SITES["shopee"]
        home = recon.HomePage(site)
        assert home.url("usb hub") == site.home
        assert home.url("air fryer") == site.home

    def test_its_name_does_not_collide_with_the_search_target(self):
        home = recon.HomePage(recon.SITES["shopee"])
        assert home.name == "shopee_home"
        assert home.name != recon.SITES["shopee"].name

    def test_it_scores_outside_the_verdict_vocabulary(self):
        # Same contract as `Site.judge`: a front page counted as a pass by some
        # later reader should produce a visibly wrong category, not a number.
        from nmbench.targets import VERDICTS
        judged = recon.HomePage(recon.SITES["shopee"]).judge("", "", "")
        assert judged.verdict == "recon"
        assert judged.verdict not in VERDICTS

    def test_every_site_declares_a_front_page_on_its_own_origin(self):
        # `--entry home` silently skips a site with no `home`, so a missing one
        # is a quiet loss of the arm rather than an error.
        from urllib.parse import urlparse
        for name, site in recon.SITES.items():
            assert site.home, name
            assert urlparse(site.home).netloc == urlparse(site.url("x")).netloc


class TestARungIsRefusedRatherThanShortened:
    """`warm_urls` returning `None` is what makes the refusal possible.

    An undeclared rung and a rung with no URLs are different answers, and the
    distinction is the whole point: `--warm off` is a real arm that visits
    nothing, while `--warm L3` on a site that never declared one has to stop
    the run. Collapsing them to a falsy value would produce a session labelled
    with a warm-up it did not receive, which is `AmazonSearch.warm_ladder`'s
    stated reason for refusing and `probe_and_hold`'s recorded failure.
    """

    def test_off_is_a_rung_with_no_urls_and_not_an_undeclared_one(self):
        site = recon.SITES["shopee"]
        assert recon.warm_urls(site, "off") == ()
        assert recon.warm_urls(site, "L3") is None

    def test_a_site_that_names_no_target_offers_nothing(self):
        class Bare:
            warm_target = None
        assert recon.warm_urls(Bare(), "L1") is None
        # Still not None for `off`: a site with no ladder can be run unwarmed.
        assert recon.warm_urls(Bare(), "off") == ()

    def test_the_declared_rung_has_urls(self):
        assert recon.warm_urls(recon.SITES["shopee"], "L1")

    def test_the_two_scripts_deliver_one_rung(self):
        """`--warm L1` here and `--warm on` in `probe_and_hold` are one label.

        They read the same `warm_ladder` and they are separate code, so the
        rung can mean two sequences without either script being wrong on its
        own. That is a wrong result rather than an error: rows on disk say
        `L1` and two of them received different warm-ups.

        Written after the suite caught the first half of this. Declaring
        `ShopeeSearch.warm_ladder` and nothing else made `probe_and_hold` crash
        on the target's missing `home_url`, which is what pointed at the
        appended front page being part of the rung rather than the caller's.
        """
        probe_and_hold = load_script("probe_and_hold")

        class NoOverride:
            warm_urls = None

        for name, site in recon.SITES.items():
            if not site.warm_target:
                continue
            target = TARGETS[site.warm_target]
            for rung, _ in target.warm_ladder:
                assert list(recon.warm_urls(site, rung)) == \
                    probe_and_hold.warm_sequence(target, rung, NoOverride()), \
                    f"{name} {rung}"

    def test_the_rung_ends_where_this_script_thinks_the_front_page_is(self):
        # The trailing visit is recorded as the site's front page rather than
        # as a warm page, and the substitution is by URL. Two spellings of one
        # URL in two files would leave it silently unrecorded as the front
        # page, which is the merge this arm's only number depends on.
        for name, site in recon.SITES.items():
            if site.warm_target:
                assert site.home == TARGETS[site.warm_target].home_url, name

    def test_an_l1_rung_stays_on_the_site_it_warms(self):
        # L1 means the target's own surfaces. A URL somewhere else is a
        # different rung wearing L1's label, and nothing downstream would show
        # it: the row would read `warm=L1` either way.
        from urllib.parse import urlparse
        for name, site in recon.SITES.items():
            own = urlparse(site.url("x")).netloc
            for url in (recon.warm_urls(site, "L1") or ()):
                host = urlparse(url).netloc
                assert host == own or host.endswith("." + own), name


class TestTheWarmPageIsItsOwnTarget:
    def test_it_ignores_the_query(self):
        page = recon.WarmPage(recon.SITES["shopee"], "https://help.shopee.sg/",
                              "L1")
        assert page.url("usb hub") == "https://help.shopee.sg/"

    def test_its_name_collides_with_neither_the_search_nor_the_front_page(self):
        site = recon.SITES["shopee"]
        page = recon.WarmPage(site, "https://help.shopee.sg/", "L1")
        assert page.name == "shopee_warm"
        assert page.name != site.name
        assert page.name != recon.HomePage(site).name

    def test_it_scores_outside_the_verdict_vocabulary(self):
        from nmbench.targets import VERDICTS
        judged = recon.WarmPage(recon.SITES["shopee"], "https://help.shopee.sg/",
                                "L1").judge("", "", "")
        assert judged.verdict == "recon"
        assert judged.verdict not in VERDICTS

    def test_the_rung_is_named_in_the_reason(self):
        # The reason travels with the row into the archive, and a warm-up page
        # is indistinguishable from any other side page without it.
        page = recon.WarmPage(recon.SITES["shopee"], "https://help.shopee.sg/",
                              "L1")
        assert "L1" in page.judge("", "", "").reason


class TestTheRowSaysHowItWasReached:
    def test_the_entry_is_written_on_the_default_arm_too(self):
        sink, rows, bodies = Sink(), [], []
        recon.record(row(), "url", "patchright", "shopee", Args(), sink, rows,
                     bodies)
        assert sink.written[0]["entry"] == "url"

    def test_the_body_is_taken_off_the_row_before_it_is_written(self):
        sink, rows, bodies = Sink(), [], []
        recon.record(row(), "home", "patchright", "shopee", Args(), sink, rows,
                     bodies)
        assert bodies == ["<html></html>"]
        assert "_html" not in sink.written[0]

    def test_the_front_page_body_can_be_kept_apart(self):
        # The census counts markers across the list it is handed, so the caller
        # keeps two lists. This asserts only that `record` writes where it is
        # told - the split itself is the caller's.
        sink, rows, search, home = Sink(), [], [], []
        recon.record(row("shopee_home"), "home", "patchright", "shopee",
                     Args(), sink, rows, home)
        recon.record(row("shopee"), "home", "patchright", "shopee", Args(),
                     sink, rows, search)
        assert len(home) == 1
        assert len(search) == 1
        assert len(rows) == 2


class TestTheAnchorCountIsRecorded:
    """The one measurement that separated a served page from the wall.

    Measured 2026-09-05 over `113703Z`, `154738Z`, `164652Z` and `170518Z`:
    three served front pages carry 451, 423 and 426 `<a `, and all 24 walled
    ones carry exactly 2. Status, title, wall-clock and `b2b_recon`'s own
    `blocked` marker count all fail to separate them, and `blocked` scored the
    served pages higher than 15 of the 24 walls.
    """

    def count(self, html):
        sink, rows, bodies = Sink(), [], []
        recon.record(row(html=html), "home", "patchright", "shopee", Args(),
                     sink, rows, bodies)
        return sink.written[0]["links"]

    def test_it_counts_the_anchors_in_the_body(self):
        assert self.count(body(426)) == 426

    def test_it_does_not_match_a_tag_that_merely_starts_with_a(self):
        assert self.count("<html><abbr>x</abbr><article>y</article></html>") == 0

    def test_no_body_is_not_a_body_with_no_anchors(self):
        # An attempt that never arrived and an arrival with an empty page are
        # opposite findings, and the summary prints them under one word. The
        # error row carries `None` so the median can leave it out rather than
        # average a zero into it.
        assert self.count("") is None
        assert self.count(body(0)) == 0

    def test_an_older_run_file_is_not_read_as_a_body_with_no_anchors(self):
        # Rows written before this column existed have no `links` key at all.
        # `r.get("links") or 0` would have put them in the median as zeros,
        # which reports every archived run as walled - a wrong answer wearing
        # the shape of a right one.
        sink, rows, bodies = Sink(), [], []
        recon.record(row(html=body(400)), "home", "patchright", "shopee",
                     Args(), sink, rows, bodies)
        rows.append({**rows[0]})
        del rows[1]["links"]
        info = {"rows": rows, "census": {"scan": {}, "attrs": {}}, "home": None}
        out = io.StringIO()
        with redirect_stdout(out):
            recon.report({("shopee", "patchright"): info}, ["shopee"],
                         [("patchright", "why")])
        line = next(recon.table_fields(ln) for ln in out.getvalue().splitlines()
                    if ln.startswith("shopee "))
        assert line["links"] == "400"


class TestTheTableIsReadBackByName:
    def test_a_value_with_a_space_in_it_does_not_shift_the_columns(self):
        # `{'200': 1, 'None': 1}` splits into three tokens on whitespace, so an
        # index into `line.split()` means a different column depending on how
        # many distinct statuses the run produced. The widths do not move.
        printed = recon.table_row(("shopee", "patchright", "search", "home", 2,
                                   {"200": 1, "None": 1}, "1,000", 0, 1, 1))
        fields = recon.table_fields(printed)
        assert fields["statuses"] == "{'200': 1, 'None': 1}"
        assert fields["n"] == "2"
        assert fields["moved"] == "1"
        assert fields["err"] == "1"

    def test_the_rules_are_as_wide_as_the_table(self):
        # Three literal 78s, then an 82, and every column added since had to be
        # remembered in two places. Derived now, so this only has to hold once.
        assert recon.WIDTH == len(recon.table_row(
            name for name, _, _ in recon.COLUMNS))


class TestARefusalThatAnswers200IsVisibleInTheSummary:
    """The regression this class exists for cost two runs to notice.

    On this vertical a refusal is an HTTP 200 carrying the storefront's own
    title, so the status column cannot show it and the size column barely can.
    `marketplace_recon_20260905T113703Z` and `154738Z` both printed a table of
    clean 200s while 5 of 6 and 6 of 8 responses had been redirected to
    `/verify/traffic/error`, and both were only read correctly by going back to
    the raw rows afterwards.

    What the mistake looked like from the inside: the instruction was already
    written down and the summary simply did not follow it. `b2b_recon`'s
    docstring says "the first thing to read off a run is `final_url`" - this
    probe is built on that module and prints a summary that had no such column,
    so the printed output and the documented method disagreed and the output is
    what gets read. A summary that has to be distrusted is worse than none.
    """

    def test_a_row_that_ended_elsewhere_is_counted(self):
        assert recon.moved(row(final_url=WALL))

    def test_a_row_that_stayed_is_not(self):
        assert not recon.moved(row())

    def test_an_error_row_did_not_arrive_anywhere(self):
        # No `final_url` is not a redirect. Counting it as one would report a
        # timeout as a wall, which is the opposite mistake and just as wrong.
        assert not recon.moved({"url": ASKED, "final_url": None})
        assert not recon.moved({"url": ASKED})

    def test_a_long_request_url_is_not_a_redirect(self):
        # `fingerprint` truncates `final_url` at 200 characters and leaves
        # `url` whole, so a plain `!=` reports every long URL as moved. That
        # would be a column reading 100% on a site nothing had happened to.
        long = "https://shopee.sg/search?keyword=" + "x" * 300
        assert not recon.moved({"url": long, "final_url": long[:200]})


class TestMoreQueriesThanExistIsRefused:
    """`plan()` promised 20 attempts and the run made 10.

    `words` is `site.words[:args.queries]`, so the list was never repeated and
    the extra attempts were never sent. `plan()`'s own docstring says a dry run
    the real run does not follow is worse than none because it is trusted, and
    this is that failure with the arithmetic done in a different place.
    """

    def run(self, argv):
        old = sys.argv
        sys.argv = ["marketplace_recon.py", *argv]
        try:
            out = io.StringIO()
            with redirect_stdout(out):
                recon.main()
            return out.getvalue()
        finally:
            sys.argv = old

    def test_it_exits_rather_than_truncating(self):
        with pytest.raises(SystemExit):
            self.run(["--sites", "shopee", "--queries", "20", "--dry-run"])

    def test_the_message_names_the_site_and_what_it_holds(self, capsys):
        with pytest.raises(SystemExit):
            self.run(["--sites", "shopee", "--queries", "20", "--dry-run"])
        # argparse writes its error to stderr, which `redirect_stdout` does not
        # capture, so the assertion goes through pytest's own capture.
        assert "'shopee': 10" in capsys.readouterr().err

    def test_the_whole_list_is_still_allowed(self):
        text = self.run(["--sites", "shopee", "--queries",
                         str(len(recon.PRODUCTS)), "--dry-run"])
        assert "dry run, nothing sent" in text


class ReportHarness:
    """Shared by the classes that assert on printed output.

    A base rather than a parent test class. `class TestB(TestA)` was the first
    version and pytest collected every one of A's tests again under B's name,
    so a failure in A reported twice under two subjects.
    """

    def rows(self, text):
        """The table's own lines, read back into named columns.

        Selected by position and not by substring. `next(ln for ln in lines if
        " home " in ln)` was the first version and it matched the *entry*
        column on the warm line, so a test about the front page asserted on a
        help-centre row and failed with numbers that looked like a bug in the
        code it was testing. The two columns share a vocabulary on purpose -
        `--entry home` is a real value - so nothing about that was going to get
        better by picking a longer substring.

        `startswith` also drops the destination block below the table, whose
        lines carry the same `site/arm` text and would be counted as rows.

        The fields come from `recon.table_fields`, which slices by the widths
        the report prints with, rather than from `line.split()` with a negative
        index. Two things broke that and only the first was noticed: adding the
        `links` column moved every negative index by one, and the statuses
        value is a dict whose repr contains spaces, so `split()` returns eleven
        fields on a row with two distinct statuses and nine on a row with one.
        The first failed loudly. The second would have shifted the assertions
        silently and left them green against the wrong column.
        """
        return [recon.table_fields(ln) for ln in text.splitlines()
                if ln.startswith("shopee ")]

    def line(self, text, doc):
        return next(f for f in self.rows(text) if f["doc"] == doc)

    def report(self, built, entry="home"):
        # The rows are put through `record` rather than written by hand, so
        # this fixture cannot drift from what the loop actually produces. The
        # first draft built them literally, left out `entry`, and failed with
        # a `KeyError` that said nothing about the table - the same way
        # `test_b2b_recon`'s fake session, written with `version` as a method,
        # failed for a reason that had nothing to do with its subject.
        sink, rows, bodies = Sink(), [], []
        for one in built:
            recon.record(one, entry, "patchright", "shopee", Args(), sink,
                         rows, bodies)
        info = {"rows": rows, "census": {"scan": {}, "attrs": {}},
                "home": None}
        out = io.StringIO()
        with redirect_stdout(out):
            recon.report({("shopee", "patchright"): info}, ["shopee"],
                         [("patchright", "why")])
        return out.getvalue()


class TestTheTableSeparatesTheTwoDocuments(ReportHarness):
    def test_a_front_page_and_a_search_are_not_averaged_together(self):
        text = self.report([row("shopee_home", html_len=100_000),
                            row("shopee", html_len=200)])
        assert len(self.rows(text)) == 2
        # Asserted per line and by column, not with `"100,000" in text`. The
        # status column renders as `{'200': 1}`, so a bare substring check for
        # the search median would have passed on a merged table too - it would
        # have found the 200 in the statuses. An assertion that cannot fail
        # for the reason the test is named after is not a test.
        assert self.line(text, "home")["median size"] == "100,000"
        assert self.line(text, "search")["median size"] == "200"

    def test_a_url_entry_run_still_prints_one_line(self):
        text = self.report([row("shopee"), row("shopee")], entry="url")
        assert len(self.rows(text)) == 1

    def test_the_run_that_was_read_wrong_no_longer_prints_as_clean(self):
        """`154738Z`'s shape: eight 200s, six of them walled.

        Replayed as the run produced it rather than as a minimal pair, because
        the failure was not that a redirect went uncounted - it was that the
        whole summary read as a healthy run. The assertion is on what an
        operator sees.
        """
        text = self.report([
            row("shopee_warm", html_len=97_727,
                url="https://help.shopee.sg/",
                final_url="https://help.shopee.sg/portal/4"),
            row("shopee_home", html_len=683_292, url="https://shopee.sg/"),
            row("shopee_home", html_len=573_746, url="https://shopee.sg/",
                final_url=WALL),
            row("shopee", html_len=279_832,
                final_url=wall("...keyword%3Dwireless", "7487f9e0365-955d")),
            row("shopee_home", html_len=569_074, url="https://shopee.sg/",
                final_url=wall(tracking="115e83a6de-9a81")),
            row("shopee", html_len=279_832,
                final_url=wall("...keyword%3Dphone", "734476f66f9-20f2")),
            row("shopee_home", html_len=569_262, url="https://shopee.sg/",
                final_url=wall(tracking="786498b59a0-77e1")),
            row("shopee", html_len=279_833,
                final_url=wall("...keyword%3Dlaptop", "9a0e7c1442b-31ab")),
        ])
        # Every status is 200, which is the whole difficulty.
        assert "'200'" in text and "403" not in text
        # The front page moved 3 of its 4 times and the search 3 of 3.
        assert self.line(text, "home")["moved"] == "3"
        assert self.line(text, "home")["err"] == "0"
        assert self.line(text, "search")["moved"] == "3"
        assert self.line(text, "search")["err"] == "0"
        # One destination line for the wall and one for the help centre, not
        # one per response. Six 200-character lines that differ only in a
        # `tracking_id` is the same unreadable summary in another shape.
        assert text.count("moved 6x -> shopee.sg/verify/traffic/error") == 1
        assert text.count("moved 1x -> help.shopee.sg/portal/4") == 1
        assert "moved 1x -> shopee.sg" not in text
        # With one full URL under it as evidence, because `next=` is what
        # refuted the request-path hypothesis on 2026-09-05 and it lives in the
        # query string the grouping key drops.
        assert f"e.g. {WALL}" in text
        # And no example under a destination whose key already says all of it.
        assert "e.g. https://help.shopee.sg/portal/4" not in text

    def test_a_warm_page_is_a_third_document(self):
        # The rung's own pages are not the front page and not the search, and
        # the median size of a help centre averaged in with either is a number
        # about nothing.
        text = self.report([row("shopee_warm", html_len=50_000),
                            row("shopee_home", html_len=100_000),
                            row("shopee", html_len=200)])
        assert len(self.rows(text)) == 3
        assert self.line(text, "warm")["median size"] == "50,000"


class TestThePositionInSessionIsPrinted(ReportHarness):
    """The question the table cannot answer, and the tempo run needed it.

    `164652Z` at `--pause 3` and `170518Z` at `--pause 60` both walled 19 of 19
    completed attempts on 2026-09-05, which only means something once you know
    whether the wall is earned during the session or is already there when it
    opens. `open_session` is entered once per site and arm, so the index is the
    request's place in one session; across the four Shopee runs of that day the
    first request was already walled in three of four.
    """

    def test_the_sequence_reproduces_the_shape_of_the_pause_3_run(self):
        built = []
        for i in range(20):
            walled = i not in (3, 13)
            built.append(row(
                "shopee_home" if i % 2 == 0 else "shopee",
                verdict="error" if i == 3 else "recon",
                status=None if i == 3 else 200,
                url="https://shopee.sg/" if i % 2 == 0 else ASKED,
                final_url=(None if i == 3 else
                           wall(tracking=f"t{i}") if walled else None),
                html="" if i == 3 else body(0),
            ))
        text = self.report(built)
        assert "   0 WWW!WWWWWWWWW.WWWWWW" in text

    def test_the_wall_that_answered_in_place_is_named_with_its_anchors(self):
        # Position 13 of `164652Z` answered its own search URL with no redirect
        # at all, and its body carries 0 anchors and no `<title>`. Read the
        # redirect column alone and that row counts as served, which is why the
        # anchor count is printed on the line rather than left to the median.
        text = self.report([row(final_url=wall()), row(html=body(0))])
        assert "arrived at #1: shopee, 1,000 b, 0 anchors" in text

    def test_a_served_page_is_told_apart_from_it_on_the_same_line(self):
        text = self.report([row("shopee_home", html_len=664_329,
                                url="https://shopee.sg/", html=body(426))])
        assert "arrived at #0: shopee_home, 664,329 b, 426 anchors" in text

    def test_a_row_from_before_the_column_says_so_rather_than_saying_zero(self):
        sink, rows, bodies = Sink(), [], []
        recon.record(row(), "home", "patchright", "shopee", Args(), sink, rows,
                     bodies)
        del rows[0]["links"]
        info = {"rows": rows, "census": {"scan": {}, "attrs": {}}, "home": None}
        out = io.StringIO()
        with redirect_stdout(out):
            recon.report({("shopee", "patchright"): info}, ["shopee"],
                         [("patchright", "why")])
        assert "no link count" in out.getvalue()
        assert "0 anchors" not in out.getvalue()


class Hop:
    """A relay's counters without a relay.

    `Wire` only ever calls `snapshot()`, so this is the whole of the surface it
    needs. A stand-in rather than a real `Relay` because a real one
    authenticates in `__init__` - it builds a username out of `.env`
    credentials - and a unit test that needs credentials fails on a machine
    without them for a reason that has nothing to do with its subject.
    """

    def __init__(self, exits=(), ja4=(), failures=0, tunnels=0):
        self.state = {"exits": list(exits), "ja4": list(ja4),
                      "tunnel_failures": failures, "tunnels": tunnels}

    def snapshot(self):
        return {"exits": list(self.state["exits"]),
                "ja4": list(self.state["ja4"]),
                "tunnels": self.state["tunnels"],
                "tunnel_failures": self.state["tunnel_failures"]}

    def names(self, ip):
        """The gateway naming an exit on a CONNECT reply during an attempt.

        A tunnel is counted with it, because that is where the header comes
        from - the exit is read off a CONNECT reply, so no exit can be named
        without one. Keeping the two in step is what lets a test assert the
        denominator: a change count on its own cannot tell 7 changes over 9
        tunnels from 7 over 200, and on 2026-09-05 the count was 7 to 11.
        """
        self.state["exits"].append(ip)
        self.state["tunnels"] += 1

    def opens(self, count=1):
        """Tunnels whose replies named no exit.

        The ordinary case rather than a fault: six CONNECT replies on
        2026-08-13 were all 200, with three different reason phrases, and only
        two carried the header.
        """
        self.state["tunnels"] += count


@pytest.fixture
def offline_locate(monkeypatch):
    """`gateway.locate` without the request.

    It is an HTTP call to ipinfo, so leaving it live would put a live host in a
    unit test - and on this workstation, behind the Happ gateway, it would
    answer about the gateway rather than about the exit. The stub records what
    it was asked, so a test can assert the direction of the question: `locate`
    asks *about* the address, and anything that asked *through* it would warm
    the exit this probe exists to meet cold.
    """
    asked = []

    def fake(ip, timeout=5):
        asked.append(ip)
        return {"country": "SG", "asn": "AS4657 StarHub Ltd",
                "timezone": "Asia/Singapore"}

    monkeypatch.setattr(recon.gateway, "locate", fake)
    return asked


class TestTheExitIsRecordedRatherThanAsked:
    """`--geo sg` writes the request onto the row and nothing writes the answer.

    All 51 attempts of the four Shopee runs of 2026-09-05 carry `geo: 'sg'` and
    `params: {'country': 'sg'}`, and not one of them says where the exit was, so
    those files cannot be asked whether the wall is on the exit or on the
    browser build. These tests are about the column that closes that.
    """

    def test_an_unrelayed_row_says_so_and_carries_no_exit(self):
        wire = recon.Wire(None, recon.gateway.ExitRegistry())
        got = wire.stamp(row(), wire.mark())
        assert got["relayed"] is False
        assert "exit_label" not in got

    def test_the_prefix_and_the_label_are_written(self, offline_locate):
        hop = Hop()
        wire = recon.Wire(hop, recon.gateway.ExitRegistry())
        before = wire.mark()
        hop.names("203.0.113.44")
        got = wire.stamp(row(), before)
        assert got["relayed"] is True
        assert got["exit_prefix"] == "203.0.113.0/24"
        assert got["exit_label"] == "exit_1"
        assert got["exit_country"] == "SG"
        assert got["exit_asn"] == "AS4657 StarHub Ltd"

    def test_the_full_address_never_reaches_the_row(self, offline_locate):
        # `data/runs/` is committed and these are real people's home addresses.
        # Asserted over the whole row rather than over the exit keys, because
        # the way this breaks is a helper adding a column nobody listed.
        hop = Hop()
        wire = recon.Wire(hop, recon.gateway.ExitRegistry())
        before = wire.mark()
        hop.names("203.0.113.44")
        got = wire.stamp(row(), before)
        assert "203.0.113.44" not in repr(got)

    def test_the_address_is_asked_about_and_not_asked_through(
            self, offline_locate):
        hop = Hop()
        wire = recon.Wire(hop, recon.gateway.ExitRegistry())
        before = wire.mark()
        hop.names("203.0.113.44")
        wire.stamp(row(), before)
        assert offline_locate == ["203.0.113.44"]

    def test_two_exits_inside_one_prefix_are_told_apart(self, offline_locate):
        # The /24 is what makes the column publishable and it is not an
        # identifier: two addresses on one network share it. The label is what
        # the analysis counts distinct exits with.
        registry = recon.gateway.ExitRegistry()
        hop = Hop()
        wire = recon.Wire(hop, registry)
        first = wire.mark()
        hop.names("203.0.113.44")
        one = wire.stamp(row(), first)
        second = wire.mark()
        hop.names("203.0.113.90")
        two = wire.stamp(row(), second)
        assert one["exit_prefix"] == two["exit_prefix"]
        assert (one["exit_label"], two["exit_label"]) == ("exit_1", "exit_2")

    def test_the_run_owns_the_numbering_and_not_the_session(
            self, offline_locate):
        # One registry across every session of a run, so `exit_2` means one
        # address in the whole file. A registry per session restarts at
        # `exit_1`, which puts two different addresses under one label in two
        # arms and reads afterwards as one exit reused.
        registry = recon.gateway.ExitRegistry()
        first_hop, second_hop = Hop(), Hop()
        one = recon.Wire(first_hop, registry)
        before = one.mark()
        first_hop.names("203.0.113.44")
        first_row = one.stamp(row(), before)
        two = recon.Wire(second_hop, registry)
        before = two.mark()
        second_hop.names("198.51.100.7")
        second_row = two.stamp(row(), before)
        assert first_row["exit_label"] == "exit_1"
        assert second_row["exit_label"] == "exit_2"

    def test_a_session_carries_one_ja4_on_every_row(self, offline_locate):
        # Not differenced the way the counters are. A browser sends one
        # ClientHello and then reuses the tunnel, so differencing would fill
        # this on the first row of a session and leave it empty on the rest,
        # which reads as an engine that stopped having a TLS fingerprint.
        hop = Hop(ja4=["t13d1516h2_8daaf6152771_02713d6af862"])
        wire = recon.Wire(hop, recon.gateway.ExitRegistry())
        before = wire.mark()
        hop.names("203.0.113.44")
        first = wire.stamp(row(), before)
        second = wire.stamp(row(), wire.mark())
        assert first["tls_ja4"] == second["tls_ja4"]
        assert first["tls_ja4"].startswith("t13d")


class TestASilentGatewayIsNotAReusedTunnel:
    """The exit header is on some CONNECT replies and not others.

    Six replies on 2026-08-13, all answered 200, three different reason
    phrases, the header present on two. So a row can inherit the previous
    attempt's address and `exit_prefix` alone cannot say whether this attempt
    used it. `exit_new` is the column that separates them.
    """

    def test_a_named_exit_counts_as_new(self, offline_locate):
        hop = Hop()
        wire = recon.Wire(hop, recon.gateway.ExitRegistry())
        before = wire.mark()
        hop.names("203.0.113.44")
        assert wire.stamp(row(), before)["exit_new"] == 1

    def test_an_attempt_the_gateway_said_nothing_about_counts_none(
            self, offline_locate):
        hop = Hop(exits=["203.0.113.44"])
        wire = recon.Wire(hop, recon.gateway.ExitRegistry())
        got = wire.stamp(row(), wire.mark())
        assert got["exit_new"] == 0
        # And the row still carries the last address the gateway named, which
        # is evidence about the session and not a claim about this attempt.
        assert got["exit_prefix"] == "203.0.113.0/24"

    def test_a_relayed_session_that_was_never_named_carries_no_exit(self):
        # No `offline_locate` here on purpose: a row with no address must not
        # reach `locate` at all, and the missing fixture is what fails if it
        # does.
        wire = recon.Wire(Hop(), recon.gateway.ExitRegistry())
        got = wire.stamp(row(), wire.mark())
        assert got["relayed"] is True
        assert got["exit_new"] == 0
        assert "exit_label" not in got

    def test_tunnel_failures_are_differenced(self, offline_locate):
        hop = Hop(failures=2)
        wire = recon.Wire(hop, recon.gateway.ExitRegistry())
        before = wire.mark()
        hop.state["tunnel_failures"] = 5
        assert wire.stamp(row(), before)["tunnel_failures"] == 3

    def test_the_change_count_is_written_with_its_denominator(
            self, offline_locate):
        """`exit_new` alone cannot say whether the rotation is unusual.

        Measured 2026-09-05 evening: 7 to 11 new exits inside a single page
        load. Seven changes over nine tunnels is a gateway handing out a
        different home per connection; seven over two hundred is a mostly
        stable session. The row has to carry both or the analysis picks one by
        assumption, so `tunnels` is differenced beside it.
        """
        hop = Hop()
        wire = recon.Wire(hop, recon.gateway.ExitRegistry())
        before = wire.mark()
        for ip in ("203.0.113.44", "198.51.100.7", "203.0.113.44"):
            hop.names(ip)
        hop.opens(6)
        got = wire.stamp(row(), before)
        # Three and not two: the relay appends whenever the header differs from
        # the last one stored, so a return to an address already seen is
        # another change. `exit_new` counts changes, never distinct addresses.
        assert got["exit_new"] == 3
        assert got["tunnels"] == 9

    def test_the_denominator_is_differenced_and_not_cumulative(
            self, offline_locate):
        # The same reason every other counter here is differenced: a session
        # keeps its sockets, so a cumulative count would post the first
        # attempt's tunnels to the second attempt's row.
        hop = Hop(tunnels=40)
        wire = recon.Wire(hop, recon.gateway.ExitRegistry())
        before = wire.mark()
        hop.names("203.0.113.44")
        hop.opens(3)
        assert wire.stamp(row(), before)["tunnels"] == 4


class TestTheRelayIsRefusedWhereItWouldLie:
    """Two ways to ask for a relay that cannot deliver one.

    Both are refused rather than skipped, because a skip writes `relayed=False`
    on a run the operator asked to be relayed and leaves the exit columns empty
    for a reason nobody would go looking for.
    """

    def run(self, argv):
        old = sys.argv
        sys.argv = ["marketplace_recon.py", *argv]
        try:
            out = io.StringIO()
            with redirect_stdout(out):
                recon.main()
            return out.getvalue()
        finally:
            sys.argv = old

    def test_a_relay_without_a_gateway_is_refused(self, capsys):
        with pytest.raises(SystemExit):
            self.run(["--sites", "shopee", "--arms", "patchright", "--relay",
                      "--dry-run"])
        assert "--relay without --geo" in capsys.readouterr().err

    def test_an_arm_that_cannot_take_one_is_refused(self, capsys):
        # Asked of `accepts_relay` and never of the engine's name. Every `open`
        # in the package ends in `**ignored`, so an arm that does not handle
        # the address would swallow it, dial the gateway itself, and write
        # `relayed=True` beside an exit column nothing filled.
        with pytest.raises(SystemExit):
            self.run(["--sites", "shopee", "--arms", "http", "--geo", "sg",
                      "--relay", "--dry-run"])
        assert "cannot be pointed at one" in capsys.readouterr().err

    def test_the_plan_says_the_exit_is_unrecorded_when_it_is(self):
        text = self.run(["--sites", "shopee", "--arms", "http", "--geo", "sg",
                         "--queries", "1", "--dry-run"])
        assert "the exit is asked for and never recorded" in text


class TestTheReportSaysWhereItLeftFrom(ReportHarness):
    def relayed(self, built, exits, ja4=("t13d1516h2_8daaf6152771",)):
        """`report`'s harness with the rows stamped by a `Wire` first.

        `exits` is one entry per attempt: an address the gateway named during
        it, None for an attempt it said nothing about, or a sequence for an
        attempt it named several during. The sequence is the real case and not
        the exotic one - 2026-09-05 evening measured 7 to 11 per attempt.
        """
        hop = Hop(ja4=list(ja4))
        wire = recon.Wire(hop, recon.gateway.ExitRegistry())
        stamped = []
        # `strict` because a short `exits` would silently drop attempts off the
        # end and the assertions would still pass against a smaller run.
        for one, ip in zip(built, exits, strict=True):
            before = wire.mark()
            for address in ([] if not ip else
                            [ip] if isinstance(ip, str) else list(ip)):
                hop.names(address)
            stamped.append(wire.stamp(one, before))
        return self.report(stamped)

    def test_an_archived_unrelayed_run_replays_without_the_column(self):
        # The four runs of 2026-09-05 go through this same function and predate
        # every exit key, so each read is a `.get`. A `KeyError` here would make
        # the archive unreadable in order to report a column it cannot have.
        text = self.report([row(final_url=wall()), row()])
        assert "no exit recorded, not relayed" in text

    def test_a_relayed_run_the_gateway_never_named_says_which_silence(
            self, offline_locate):
        # Two different silences and the row can tell them apart, so the
        # summary does too. Nobody watching is not the same finding as watching
        # and being told nothing.
        text = self.relayed([row(), row()], [None, None])
        assert "relayed, and the gateway named no exit" in text

    def test_the_exit_is_printed_with_its_country_and_its_traffic(
            self, offline_locate):
        text = self.relayed([row(final_url=wall()), row(), row()],
                            ["203.0.113.44", None, None])
        line = next(ln for ln in text.splitlines() if "exit_1" in ln)
        assert "203.0.113.0/24" in line
        assert "SG" in line
        assert "3 req, 1 moved, 2 arrived" in line

    def test_one_exit_is_said_not_to_separate_the_exit_from_the_build(
            self, offline_locate):
        # The point of the whole column, said plainly rather than left for the
        # reader to notice: one address is one address however many requests
        # went through it, and the table above looks like an answer either way.
        text = self.relayed([row(), row()], ["203.0.113.44", None])
        assert "cannot separate the exit from the" in text

    def test_two_exits_are_not_given_that_caveat(self, offline_locate):
        text = self.relayed([row(), row()], ["203.0.113.44", "198.51.100.7"])
        assert "cannot separate the exit from the" not in text
        assert "exit_1" in text and "exit_2" in text

    def test_the_session_ja4_is_printed_once(self, offline_locate):
        text = self.relayed([row(), row()], ["203.0.113.44", None])
        assert text.count("t13d1516h2_8daaf6152771") == 1

    def test_a_rotating_attempt_is_not_printed_as_one_exit_per_request(
            self, offline_locate):
        """The table reads as "this exit served these requests". Usually false.

        Measured over the 22 attempts of 2026-09-05 evening, the gateway named
        7 to 11 new exits inside a single page load, so `exit_prefix` is the
        last address of many and not the one that served the request. Printing
        the table without saying so is the shape of mistake that cannot be seen
        afterwards in the rows.
        """
        text = self.relayed(
            [row(), row()],
            [("203.0.113.44", "198.51.100.7", "203.0.113.9"), None])
        assert "exit changed 3x over 2 attempts and 3 tunnels" in text
        assert "1 per attempt" not in text
        assert "names the LAST exit of its attempt" in text

    def test_an_archive_with_no_tunnel_count_is_not_printed_as_zero(self):
        """The 22 rows of 2026-09-05 evening carry `exit_new` and no `tunnels`.

        Summing with a `or 0` prints "over 0 tunnels", which reads as a
        measured zero - and zero is the one value a denominator cannot have,
        since every exit came off a CONNECT reply. Absent is not zero, and the
        rows can tell them apart.
        """
        archived = {"exit_new": 8, "exit_label": "exit_1", "relayed": True,
                    "exit_prefix": "203.0.113.0/24", "exit_country": "SG"}
        text = self.report([{**row(), **archived},
                            {**row(), **archived, "exit_new": 3}])
        assert "11x over 2 attempts and tunnels not recorded" in text
        assert "0 tunnels" not in text

    def test_one_exit_per_attempt_is_not_given_that_warning(
            self, offline_locate):
        # The control for the test above: the count is still printed, because
        # zero rotation is a finding too and this run cannot claim it without
        # having asked, but the caveat that the label is the last of many is
        # not printed when it is not the last of many.
        text = self.relayed([row(), row()], ["203.0.113.44", "198.51.100.7"])
        assert "exit changed 2x over 2 attempts and 2 tunnels" in text
        assert "names the LAST exit of its attempt" not in text
