"""Targets: how to build a URL and how to judge what came back.

The verdict is decided by page content, not by HTTP status. A blocked page very
often returns 200 with a stub body, and the same `/sorry/` body has arrived as
both 429 and 200 in our own runs.

`judge` returns a verdict and the reason it was reached. The reason is stored on
every row because a bare verdict is not reviewable: "block" meant two completely
different things until 2026-08-11, and the rows could not tell them apart
without re-reading the raw markup.
"""
from typing import NamedTuple
from urllib.parse import quote_plus

VERDICTS = ("ok", "captcha", "consent", "block", "empty", "error")


class Judgement(NamedTuple):
    verdict: str
    reason: str


# Recorded with every attempt. A verdict is a claim about markup and markup
# changes without notice: on 2026-08-10 a whole run was misread because the
# bodies never decoded. Storing the evidence the verdict was based on means the
# next disagreement is settled by re-reading the rows, not by re-hitting the
# target and heating the pool again.
FINGERPRINT_MARKERS = (
    "id=\"rso\"", "id=\"search\"", "id=\"main\"", "/sorry/", "recaptcha",
    "unusual traffic", "enablejs", "noscript", "consent",
    "b_algo", "b_results", "captcha_header", "challenge/verify",
    "result__a", "anomaly", "<h3", "<cite",
    "s-search-result", "s-main-slot", "s-no-results", "validatecaptcha",
    "opfcaptcha", "api-services-support", "continue shopping",
    "item-stack", "product-title", "0 results for", "px-captcha",
    "re-captcha", "blockscript",
    # Google's redirect wrapper, which replaced the destination href on a served
    # result page somewhere between 2026-08-26 15:27 and 2026-08-27 20:11 UTC.
    # It is here to date the next such change from the rows instead of from
    # decompressing archives, which is how this one had to be dated. No verdict
    # reads it: `GoogleSerp.judge` is structural throughout and a served page is
    # served whichever form its links take.
    #
    # This exact spelling because the looser ones are not discriminating. `caes`,
    # the protobuf prefix, matches 62 of 90 archived bodies including every
    # pre-transition one; `/goto?url=` matches 28 of those 90 and 0 of the 616
    # archived bodies of every other target.
    "/goto?url=",
    # The three targets added 2026-09-04 off `marketplace_recon_20260903T234200Z`.
    # Counted on every body, not only their own, for the reason this tuple
    # exists: a marker that turns up where it was not expected is how the next
    # markup change gets dated from the rows.
    "_____tmd_____", "x5secdata", "sufei-punish",
    "role=\"feed\"", "/maps/place/", "403 forbidden",
)


def fingerprint(url: str, html: str) -> dict:
    """Title plus marker counts: enough to re-judge a response later."""
    title = ""
    if html and "<title>" in html:
        title = html.split("<title>", 1)[1].split("</title>", 1)[0].strip()[:120]
    low = (html or "").lower()
    return {
        "title": title,
        "final_url": (url or "")[:200],
        "markers": {m: low.count(m.lower()) for m in FINGERPRINT_MARKERS
                    if low.count(m.lower())},
    }


# Committed, not generated: a stranger running this repository sends the same
# inputs we did. Shared by every search target so the comparison is like-for-like.
# The long list lives in data/queries/ and is loaded by nmbench.queries; these
# ten are the smoke-test set used by the probe scripts.
SEARCH_QUERIES = [
    "residential proxy provider",
    "web scraping python tutorial",
    "playwright vs puppeteer",
    "tls fingerprint ja4",
    "anti detect browser",
    "rotating proxy api",
    "scrapy vs beautifulsoup",
    "headless chrome detection",
    "captcha solving service",
    "browser fingerprinting canvas",
]


# Amazon is a shop, not a search engine. Sending it "photosynthesis exam
# questions" measures how it answers a query with no products, which is a
# different question from whether it let us in. The long list lives in
# data/queries/amazon_1000.txt; these ten are the smoke-test set.
PRODUCT_QUERIES = [
    "wireless earbuds",
    "air fryer",
    "cast iron skillet",
    "standing desk",
    "cordless drill",
    "yoga mat",
    "dog bed",
    "camping tent",
    "usb c hub",
    "dash cam",
]


# Maps answers a place query and answers a product with a page that has no
# places on it, which is the `s-no-results` problem in a form that cannot even be
# detected: an empty Maps feed and a refused one are not distinguishable after
# the fact. Every query names its city, so none of them depends on where the exit
# is for its meaning - an exit in Brazil asked for "dentist in Austin Texas" gets
# the same places as one in Texas. The long list is data/queries/places_1000.txt;
# these ten are the smoke set and are the ten
# `scripts/probes/marketplace_recon.py` sent.
PLACE_QUERIES = [
    "coffee shop in Singapore",
    "dentist in Austin Texas",
    "plumber in Manchester",
    "hardware store in Warsaw",
    "law firm in Toronto",
    "gym in Berlin",
    "bakery in Lisbon",
    "car repair in Dublin",
    "pharmacy in Rotterdam",
    "bookshop in Edinburgh",
]


class IpInfo:
    """Not a real target: an echo service used to prove the path works."""

    name = "ipinfo"
    queries = ["json"] * 100
    # The URL ignores the query, so the list only decides how many times the
    # echo is called. It draws from the search list so a mixed run needs no
    # special case.
    query_list = "serp_1000"
    ready_selector = None
    needs_script = False

    def url(self, query: str) -> str:
        return "https://ipinfo.io/json"

    def judge(self, url: str, title: str, html: str) -> Judgement:
        if not html:
            return Judgement("empty", "no body")
        if '"ip"' in html:
            return Judgement("ok", "address echoed")
        return Judgement("block", "no address in the body")


class GoogleSerp:
    name = "google_serp"
    queries = SEARCH_QUERIES
    query_list = "serp_1000"
    # Google builds its results in the browser. A snapshot taken at
    # domcontentloaded catches the "enable JavaScript" scaffold instead, which
    # then reads as a block: measured 2026-08-10, 91 KB, no results, on an exit
    # that had not been rate limited.
    ready_selector = "#rso, #search"
    needs_script = True
    # The two attributes an approach can use instead of `url`: land on the
    # homepage and type. Every number in this repository was taken by navigating
    # straight to `/search?q=`, which is one request with a query string and no
    # keystroke behind it - a shape no human produces. Whether that is what the
    # refusals are keyed on is a question the harness could not previously ask,
    # because nothing here could reach the box. `probe_and_hold.py` is what asks
    # it, and it needs the entry page and the box named by the target rather
    # than guessed at the call site.
    #
    # `hl=en` is on the homepage for the same reason it is on the search URL:
    # the markers a verdict rests on are English strings.
    #
    # **That reason is overstated, measured 2026-08-28 against every run on
    # disk.** `judge` below has eight rules and seven are structural - the
    # `/sorry/` and `consent.` path tests, `id="rso"`, `id="search"`, `<h3`,
    # `consent.google.com` and `enablejs`. The single English literal,
    # `unusual traffic`, is the second disjunct of a line whose first disjunct
    # is `/sorry/` in the URL, and **4550 of 4550 captcha rows carried
    # `/sorry/`**, so it has never been the rule that decided anything. What
    # the mistake looked like from the inside: the sentence was written when
    # the classifier was younger and reads as though it were still describing
    # it, and nobody re-read the classifier when the structural rules were
    # added underneath it.
    #
    # This is not a licence to delete the parameter. It changes which page
    # Google serves, so every number in `data/runs/` was taken under `hl=en`
    # and a run without it is not comparable with any of them. If it is to be
    # tested it is an arm - the same reason `entry` is an arm and not a
    # replacement. The real question it raises is not the parameter but the
    # combination: `hl=en` and English queries on an exit whose geoip is
    # Brazil is an incoherent identity, and `exit_country` is the column that
    # makes that measurable rather than arguable.
    home_url = "https://www.google.com/?hl=en"
    # Google has served the box as a textarea since 2023 and as an input before
    # that, and still serves the input on some lightweight variants. Both are
    # listed rather than one being assumed, so a variant swap is a slower page
    # and not a run of `error` rows.
    search_box = "textarea[name='q'], input[name='q']"
    # Pages to open on a fresh exit before asking it anything, in rungs. This is
    # an axis in `probe_and_hold.py` rather than something the probe always does,
    # because an operator reports reaching 75% on a warmed exit and nothing says
    # which part of the warming earned it - a probe that always warmed would
    # carry the treatment in every row and be unable to price it. One rung was
    # not enough to say which part does the work, so the axis carries levels and
    # the probe runs them interleaved.
    #
    # Declared on the target and not in the probe for the usual reason: a probe
    # that knew a domain would be a probe that could warm one target better than
    # another. No list may contain the front page - the probe always ends on
    # `home_url`, so that the box the query is typed into is the one on the page
    # the protocol names.
    #
    # The rungs are cumulative as sets, so `warm_depth` rises monotonically and
    # a difference between two rungs is a difference in what was added. Each
    # ends on the same page L1 is, so whatever L1 buys is held while the rungs
    # above it vary. Third-party pages come first because that is the order a
    # session would produce: a browser is somewhere else before it is here.
    #
    # What each rung is for is written in the README, not here, because the
    # question belongs to the experiment and the URLs belong to the target.
    # Pairs rather than a dict so the attribute is immutable at class level; the
    # reader converts.
    # `news.google.com` was in L2 and L3 until 2026-08-26 and came out after the
    # first ladder run measured it. It is the one warm-up page that does not
    # arrive: 10 delivered of 24 in `probehold_20260826T152748Z`, almost all
    # `ERR_TIMED_OUT` at the full 30 s, against 24 of 24 for
    # `translate.google.com` and 12 of 12 for both third-party pages in the same
    # run and on the same exits.
    #
    # The obvious alternative explanation was checked before it was dropped. The
    # failures are not a bad window in the run - a failed visit did not predict
    # the next one failing, 0.13 against a 0.21 base rate, and the ten slices of
    # the run carried 5, 3, 6, 2, 3, 4, 1, 2, 4 and 0 of them - and they are not
    # the exit going bad, because `translate.google.com` was visited on those
    # same exits and never failed once. It is the host.
    #
    # Removing it cost L2 its third host, and `scholar.google.com` is the
    # replacement, chosen off `surfaces_20260826T220435Z` rather than guessed.
    # It arrived 3 of 3 through the pool and cost 0.13 MB median, the cheapest
    # of the twenty surveyed by a factor of three, and it is a `.google.com`
    # subdomain, so whatever the warm-up does through cookies it does on the
    # same registrable domain as the target.
    #
    # `about.google` looked cheaper than it is and was rejected on a second
    # measurement. The survey put it at 0.78 MB median, which would have made
    # it the second pick; measured again on 2026-08-27 on the direct arm it
    # cost 17.81, 17.83, 17.81 and 19.99 MB. Position in the list and headless
    # were both varied across those four and neither moved it, so the 20-fold
    # gap belongs to the arm and this repository cannot yet say why. A warm-up
    # page whose cost swings twentyfold with something we do not control is a
    # tax nobody can price, and it is a separate registrable domain besides.
    #
    # Read the survey's cost column knowing what it is: every surface in that
    # run was visited by a browser that had already visited the ones before it,
    # so a page late in a fixed list is quoted against a warm cache. That is
    # the opposite of the ladder, where a warm-up page is the first thing a
    # fresh profile fetches. The survey now shuffles for this reason among
    # others, and until a shuffled run exists these figures are a lower bound
    # on the cold cost.
    warm_ladder = (
        # One Google surface that is not the front page.
        ("L1", ("https://www.google.com/imghp?hl=en",)),
        # Google surfaces on more than one host. Separates "Google has seen this
        # exit at all" from "Google has seen it more than once".
        ("L2", ("https://translate.google.com/?hl=en",
                "https://scholar.google.com/?hl=en",
                "https://www.google.com/imghp?hl=en")),
        # The same, preceded by pages nobody would call a Google property. Each
        # was checked on 2026-08-26 for a Google-owned tag in its body -
        # `google-analytics.com`, `doubleclick.net` or `googlesyndication` - so
        # the exit is reported to Google's infrastructure without a navigation
        # to a Google host. Four other candidates were dropped: stackoverflow,
        # medium, allrecipes and tripadvisor all answered 403 to this check, and
        # espn.com carried no such tag.
        ("L3", ("https://www.theverge.com/",
                "https://www.wikihow.com/Main-Page",
                "https://translate.google.com/?hl=en",
                "https://scholar.google.com/?hl=en",
                "https://www.google.com/imghp?hl=en",
                "https://trends.google.com/trends/?hl=en")),
        # The control the ladder was missing, added 2026-08-28. L1 through L3
        # all vary depth and composition together, so a difference between any
        # two of them cannot say which moved: L3 adds two non-Google pages *and*
        # is four pages deeper than L1. This rung holds the depth at L1's and
        # changes only whose page it was.
        #
        # One page and not two, and that is the whole design of it. `N1` is
        # `[theverge, front page]` against L1's `[imghp, front page]`: the same
        # navigation count, the same profile state, the same terminal page, and
        # the single difference is whether the extra visit was Google's own.
        # Adding wikihow here would make it three deep and it would stop being a
        # control of anything - it would be a fourth point on a line that
        # already confounds the two variables.
        #
        # What it cannot answer, and the rung name should not be read as
        # claiming otherwise: this is not a Google-free arm. `entry=home`
        # navigates to `home_url` before it can type, so every arm including
        # this one contacts Google immediately before the probe. N1 asks
        # whether *the page before that* has to be Google's, not whether Google
        # has to be visited at all. A genuinely Google-free warm-up is only
        # measurable through `entry=url`, and that arm discards the typed
        # entry shape as well, so it is a different experiment.
        ("N1", ("https://www.theverge.com/",)),
        # N1's design moved to the depth where the ladder actually separates,
        # added 2026-09-01. N1 sits at L1's depth, where the arms measured so
        # far are 4 points apart and nothing will ever resolve; L3 is the one
        # rung with a resolvable effect, and it is 6 pages of which 4 are
        # Google's. "Google's infrastructure was told about this exit" and "the
        # browser lived through six navigations" are both consistent with every
        # L3 row on disk, and this rung is what separates them: the same six
        # visits in the same positions, with the four Google surfaces swapped
        # for third-party pages carrying the same tag check.
        #
        # theverge and wikihow keep positions 1 and 2 exactly as in L3, so the
        # arms differ in four URLs and in nothing else - not in depth, not in
        # ordering, not in which page is met by a cold profile first.
        #
        # The four replacements come from `warmvet_20260901T132509Z`, run on the
        # VPS because a 403 seen from behind the workstation's VPN gateway is
        # not distinguishable from a site that refuses plain clients. Both
        # controls held in that run: theverge and wikihow came back tagged, and
        # en.wikipedia.org came back clean, so the marker list was matching
        # Google-owned tags rather than prose or a header. Six of twelve
        # candidates passed and four were needed.
        #
        # Which four is a judgement and it is worth naming as one. The measured
        # fact is binary - the page carries a Google-owned tag or it does not -
        # and all six passed it. The ranking used to cut six to four is the
        # number of *distinct* Google-owned hosts in the body, on the reasoning
        # that more distinct hosts is more independent reporting paths; that
        # reasoning is not measured here. edition.cnn.com carried three
        # (doubleclick, googlesyndication, googletagservices), arstechnica,
        # wired and cnet two each, and the tie among those was broken
        # arbitrarily. www.techradar.com also carried two and www.theguardian.com
        # one; both are usable and swapping one in is not a new measurement.
        #
        # What this rung cannot answer, same as N1 and for the same reason:
        # `entry=home` navigates to `home_url` before it can type, so this arm
        # contacts Google immediately before the probe too. It asks whether the
        # six visits before that have to be Google's, not whether Google has to
        # be visited at all.
        ("N3", ("https://www.theverge.com/",
                "https://www.wikihow.com/Main-Page",
                "https://arstechnica.com/",
                "https://www.wired.com/",
                "https://edition.cnn.com/",
                "https://www.cnet.com/")),
    )
    # What to do on a warm-up page once it has loaded, per URL, for the
    # `--warm-interact` axis. Empty for every page not named here, and the arm
    # is refused rather than run when the selected rung names none of them - an
    # `on` arm with nothing to do is the `off` arm under a second label, which
    # is the failure `parse_param_sets` exists to prevent on the gateway side.
    #
    # Declared on the target for the same reason `warm_ladder` is, only more so:
    # a CSS selector is a stronger piece of target-specific knowledge than a URL.
    #
    # **Typing and scrolling are separately selectable, and that is what makes
    # more than one page safe to declare.** The ladder's own history is the
    # argument: L1 through L3 varied depth and composition together and it took
    # two extra rungs, N1 and N3, to separate them after the rows were on disk.
    # A single `on` arm that typed here *and* scrolled theverge and wikihow
    # would be one treatment made of two behaviours across four pages, and a
    # difference in it would say nothing about which part moved. `warm.SETS`
    # is what avoids repeating that: `--warm-interact off,scroll,type,on` runs
    # the decomposition in the same night rather than needing two more rungs
    # afterwards.
    #
    # Scrolling is on the two article pages and not on translate, which is a
    # form and not something anybody reads. Typing is on the surfaces that have
    # a box: translate, scholar and imghp. Two pages are deliberately left
    # inert and the reasons differ - `trends` puts its query in a custom widget
    # nothing here has seen, so a selector for it would be a guess with no way
    # to check it from this host, and `www.google.com/?hl=en` is the front page
    # the probe itself types the real query into under `entry=home`, so warming
    # it by typing would be the probe running twice.
    #
    # **No type selector here is verified, and every scroll is.** This host
    # reaches the network through a VPN gateway, so nothing here may touch a
    # live Google surface; the type candidates are written the way `search_box`
    # is - most specific first, a generic fallback last - and a page that
    # matches none of them records `type:miss` on its warm row rather than
    # failing quietly. Read that column on the first run before reading anything
    # else, and note that `InteractWatch` stops a run whose interaction never
    # lands at all. The aria-label is English because every warm URL here
    # carries `hl=en`.
    #
    # A scroll names nothing on the page, so there is no selector to be wrong
    # and `scroll:miss` cannot happen. That is why `--warm-interact scroll` is
    # the half of this axis that can be trusted before a single run has reported
    # back, and it is worth knowing which of the two a first result came from.
    #
    # Two phrases and a settle between them rather than one. A translator that
    # is opened, typed into once and abandoned is a shorter visit than the
    # inert arm's 3-8 second dwell, and the axis would then vary time on page
    # as well as interaction; `run_identity` subtracts the interaction from the
    # dwell for the same reason.
    #
    # The scroll distances are page-sized rather than drawn from the human
    # traces: how far there is to read belongs to the page, and only *how* the
    # wheel turns belongs to the person - see `pointer.scroll_plan`, which owns
    # that half. 1500-3500 px is between two and five measured flicks, against
    # the 3281 px both humans covered on the trace page.
    warm_actions = (
        ("https://www.theverge.com/", (
            ("scroll", 1500, 3500),
            ("settle", 1200, 2600),
            ("scroll", 800, 2200),
            ("settle", 1200, 2600),
        )),
        ("https://www.wikihow.com/Main-Page", (
            ("scroll", 1500, 3500),
            ("settle", 1200, 2600),
            ("scroll", 800, 2200),
            ("settle", 1200, 2600),
        )),
        # Scholar's box is an `input` and not a `textarea`, so the fallback
        # differs from translate's rather than being copied from it. `#gs_hdr_tsi`
        # is scholar's own id for it and has been stable for years; it is still
        # unverified from this host, like every other selector here.
        ("https://scholar.google.com/?hl=en", (
            ("type", "input#gs_hdr_tsi, input[name='q'], form input[type='text']",
             ("photonic crystal waveguide", "graph neural network",
              "crispr off target effects", "urban heat island",
              "transformer attention", "gut microbiome obesity",
              "quantum error correction", "protein folding prediction")),
            ("settle", 1200, 2600),
        )),
        # Image search takes the same box shape as web search, which is the one
        # selector on this list the harness has verified live - `search_box`
        # above is what every probe row on disk was typed into.
        ("https://www.google.com/imghp?hl=en", (
            ("type", "textarea[name='q'], input[name='q']",
             ("golden retriever puppy", "kitchen shelf ideas",
              "mountain sunrise", "vintage bicycle", "origami crane",
              "succulent arrangement", "lighthouse storm", "autumn forest path")),
            ("settle", 1200, 2600),
        )),
        ("https://translate.google.com/?hl=en", (
            ("type",
             "textarea[aria-label='Source text'], "
             "c-wiz textarea[jsname], textarea",
             ("weather tomorrow", "thank you very much", "where is the station",
              "how much does it cost", "good morning", "see you later",
              "what time is it", "i would like a coffee")),
            ("settle", 1200, 2600),
            ("type",
             "textarea[aria-label='Source text'], "
             "c-wiz textarea[jsname], textarea",
             ("open the window", "the train is late", "my name is anna",
              "please call me back", "it is very cold today",
              "where can i buy tickets")),
            ("settle", 1200, 2600),
        )),
    )
    # Google covers its own front page with a consent wall, and this is a cost
    # of the entry shape rather than a detail. Measured 2026-08-13 from this
    # line: `www.google.com/?hl=en` and `/imghp` both arrive with a full-screen
    # "Before you continue to Google" panel on most fresh profiles, while
    # `/search?q=` never carries it - 0 occurrences of `consent.google.com` on
    # the URL-entry body against 5 on the front page. So every row already in
    # `data/runs/` was taken by an entry shape that never met this, and the
    # typed path meets it on the first navigation.
    #
    # It is an overlay, not a redirect: the address stays `/?hl=en`, so the
    # url-matching consent rule below cannot see it, and the search box is
    # `is_visible()` underneath it while the panel intercepts every pointer
    # event. That combination cost a 30 s actionability timeout recorded as
    # `error` - our own client failing to reach a target that had not refused
    # anything.
    #
    # Reject-all first, deliberately. Both buttons clear the panel and either
    # would let the run proceed; rejecting sets the smaller cookie and is the
    # identity this harness can defend having presented from a shared company
    # account. Which one is clicked is a state difference the target can read,
    # so it is a choice recorded here and not a measurement - an accept-all arm
    # is a separate run, not a silent default.
    consent_dismiss = ("button#W0wltc", "button#L2AGLb")

    def url(self, query: str) -> str:
        return f"https://www.google.com/search?q={quote_plus(query)}&hl=en"

    def judge(self, url: str, title: str, html: str) -> Judgement:
        low_url = (url or "").lower()
        low = (html or "").lower()
        if not html:
            return Judgement("empty", "no body")

        # The rejection redirects to /sorry/ and carries the recaptcha widget.
        # Observed 2026-08-10 as HTTP 429 and 2026-08-11 as HTTP 200, same body.
        if "/sorry/" in low_url or "unusual traffic" in low:
            return Judgement("captcha", "redirected to /sorry/ with a recaptcha")
        if "consent." in low_url or "consent?" in low_url:
            return Judgement("consent", "consent interstitial")
        if 'id="rso"' in low or 'id="search"' in low or "<h3" in low:
            return Judgement("ok", "results container present")

        # The same wall served inline, which is what the front page does and
        # what `/search` never does. Placed after the result test for the reason
        # `WalmartSearch` documents at length: a served page carrying a dormant
        # panel has still been served, and a challenge test in front of the
        # result test scores a working page as an interstitial.
        #
        # Keyed on `consent.google.com`, which is the endpoint the choice is
        # posted to, rather than on "before you continue", which is prose and is
        # translated. Without this rule the panel falls through to the no-JS
        # test below - it carries `noscript` and no `<h3>` - and a wall the
        # target really did put up would be recorded as our own client coming up
        # short, which is the one direction of error this repository cannot
        # absorb. Measured 2026-08-13: 5 occurrences on every front page that
        # bumped, 0 on the `/sorry/` body and 0 on the `/search` body.
        if "consent.google.com" in low:
            return Judgement("consent", "consent wall served inline, no results "
                                        "behind it")

        # A scriptless client is handed a 92 KB "enable JavaScript" page that
        # stays on /search and carries no rejection of any kind. Counting it as
        # a block credits Google with a refusal it never made: measured
        # 2026-08-11, 14 of 14 such rows carried `enablejs` and not one carried
        # `recaptcha`. It is our client that came up short, not the target.
        if "enablejs" in low or ("noscript" in low and "<h3" not in low):
            return Judgement("empty", "served the no-JS scaffold, results need a "
                                      "browser that runs scripts")
        return Judgement("block", "no results and no known interstitial")


class DuckDuckGoSerp:
    """The no-JS endpoint. DuckDuckGo's main page is a JS application and would
    return an empty shell to a plain HTTP client, which says nothing about
    whether the request was accepted."""

    name = "ddg_serp"
    queries = SEARCH_QUERIES
    query_list = "serp_1000"
    ready_selector = ".result, .results"
    needs_script = False

    def url(self, query: str) -> str:
        return f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"

    def judge(self, url: str, title: str, html: str) -> Judgement:
        low = (html or "").lower()
        if not html:
            return Judgement("empty", "no body")
        if "anomaly" in low or "unfortunately, bots" in low or "recaptcha" in low:
            return Judgement("captcha", "anomaly interstitial")
        # Read off `low` like every other rule here. The markers are lowercase
        # literals either way, and testing one of them against the raw body -
        # which this did until 2026-08-15 - makes the pass rule the only rule in
        # the file that a change of attribute casing could silently turn into a
        # `block`. That failure would report a served page as a refusal.
        if 'class="result' in low or "result__a" in low:
            return Judgement("ok", "results container present")
        return Judgement("block", "no results and no known interstitial")


class BingSerp:
    name = "bing_serp"
    queries = SEARCH_QUERIES
    query_list = "serp_1000"
    ready_selector = "#b_results"
    needs_script = False

    def url(self, query: str) -> str:
        return f"https://www.bing.com/search?q={quote_plus(query)}"

    def judge(self, url: str, title: str, html: str) -> Judgement:
        low = (html or "").lower()
        if not html:
            return Judgement("empty", "no body")
        # Observed 2026-08-10: HTTP 200, the query in the title and an empty
        # results container, with a challenge interstitial embedded in the body.
        # Matching the bare word "captcha" is not enough - it appears in the
        # telemetry of a perfectly good page.
        if "captcha_header" in low or "challenge/verify" in low:
            return Judgement("captcha", "challenge interstitial")
        if "b_algo" in low:
            return Judgement("ok", "results container present")
        return Judgement("block", "no results and no known interstitial")


class AmazonSearch:
    """Amazon product search.

    Server rendered, so a client that cannot run scripts still receives the
    result list: `needs_script` is False and the aggressive preset is allowed.

    Two of the rules below rest on captured bodies and the rest do not, and the
    split matters because they are not equally safe to quote:

    - `ok` was read on 2026-08-11, direct from the operator's line: a plain
      `requests` client with browser headers received the full list, HTTP 200,
      919 KB, `s-search-result` x18.
    - the 503 throttle page was read on 2026-08-12 off 23 archived bodies from
      `benchmark_20260812T111353Z` and `...T112029Z`, through the pool. Every
      one was 2,316-2,317 bytes. Across those runs plus `...T112846Z` the status
      was 503 on 47 refusals and 200 on 7 - so the rule keys on the markup, and
      a status test would have missed one refusal in eight.
    - the Akamai Bot Manager interstitial was read on 2026-08-13 off 5 archived
      bodies, direct. It is the second byte cluster this docstring used to call
      unread, and it turned out not to be a refusal shape at all. See the rule.
    - the AWS WAF challenge was read on 2026-08-13 off one body, through the
      pool, on the front page. One body is a shape, not a rate.
    - the character-entry captcha and the continue-shopping gate are still
      written from public descriptions and have NEVER fired. No number that
      rests on them may be quoted until a real body is captured.

    This target serves at least four distinct answers and the small ones do not
    mean the same thing. The `ref=cs_503` throttle refuses the address: nothing
    on it to solve, and the same client is served in full from an address Amazon
    likes. The Akamai interstitial and the AWS WAF page challenge the client and
    hand it a way through, which a browser takes without ever noticing - two
    vendors, one meaning. Reading them as one number is how a target's different
    reactions become one flat rate.

    Do not carry over the old summary that "through the pool Amazon does not
    challenge the browser, it refuses the address". It was drawn from one arm
    with no engine varied in it, and the first run that varied one reversed it:
    camoufox is served 90% of the time and has met the throttle once in 127
    attempts, while the Chromium-family engines meet it on most of theirs.
    """

    name = "amazon_search"
    queries = PRODUCT_QUERIES
    query_list = "amazon_1000"
    ready_selector = "div.s-main-slot, [data-component-type='s-search-result']"
    needs_script = False
    # See `GoogleSerp` for why these exist. Amazon is the second target that can
    # be entered through its own front door, and it is the one where the entry
    # shape is most likely to matter: the throttle here is aimed at the browser
    # rather than the address, so arriving with a session, a cookie jar and a
    # keystroke is a different client from one that appears at `/s?k=` cold.
    home_url = "https://www.amazon.com/?language=en_US"
    search_box = "input#twotabsearchtextbox"
    # See `GoogleSerp.warm_ladder`. The bestsellers page is an ordinary browse
    # page rather than a search, which is the point: it is what a visitor does
    # before searching, and it is the one thing this exit will have done before
    # the throttle decides about it.
    #
    # One rung only. The rungs above L1 were designed against Google's refusal
    # and there is no measurement here saying they transfer, so declaring them
    # would be inventing an experiment for a target nobody has run it on.
    # Asking for L2 or L3 on this target is refused rather than silently
    # answered with L1.
    warm_ladder = (
        ("L1", ("https://www.amazon.com/gp/bestsellers/?language=en_US",)),
    )
    # Scrolling only, and typing deliberately absent. The box named in
    # `search_box` above is the one the probe itself types the real query into
    # under `entry=home`, so a warm-up that typed a second query into an Amazon
    # search box would be running the probe twice and calling the first one a
    # warm-up. A bestsellers page is a list, so reading down it is what a
    # visitor does there and it touches nothing the probe uses.
    #
    # This makes the target answerable under `--warm-interact scroll` and
    # refused under `type`, which is the intended shape rather than a gap:
    # `check_interact` says so in words instead of running the inert arm.
    warm_actions = (
        ("https://www.amazon.com/gp/bestsellers/?language=en_US", (
            ("scroll", 1500, 3500),
            ("settle", 1200, 2600),
            ("scroll", 800, 2200),
            ("settle", 1200, 2600),
        )),
    )

    def url(self, query: str) -> str:
        # Language is pinned for the same reason Google gets hl=en: the markers
        # a verdict rests on are English strings, and a localised page would be
        # judged by a rule that cannot match it.
        return f"https://www.amazon.com/s?k={quote_plus(query)}&language=en_US"

    def judge(self, url: str, title: str, html: str) -> Judgement:
        low_url = (url or "").lower()
        low = (html or "").lower()
        if not html:
            return Judgement("empty", "no body")
        if ("validatecaptcha" in low or "opfcaptcha" in low
                or "enter the characters you see below" in low):
            return Judgement("captcha", "character-entry captcha")
        # The measured refusal, and the only Amazon refusal shape ever captured
        # here. A 2,317 byte stub served as HTTP 200, carrying no captcha and
        # nothing to solve. `ref=cs_503` is what Amazon tags its own links on
        # that page with, so it names the throttle rather than describing the
        # symptom - the previous rule matched the alt text of an image on it by
        # coincidence and would have survived Amazon rewording the sentence.
        if "ref=cs_503" in low or "dogsofamazon" in low:
            return Judgement("block", "503 throttle page, the address is "
                                      "refused rather than the browser "
                                      "challenged")
        if "/errors/" in low_url or "something went wrong on our end" in low:
            return Judgement("block", "error page instead of a result list")
        if "s-search-result" in low or "s-main-slot" in low:
            return Judgement("ok", "result list present")

        # Akamai Bot Manager's interstitial, and the second refusal shape this
        # target serves. Read 2026-08-13 off 5 archived bodies from
        # `benchmark_20260813T120848Z`, direct: 2,308-2,369 bytes, HTTP 200, a
        # meta refresh carrying a `bm-verify=` token, an iframe holding a single
        # gif, and a `triggerInterstitialChallenge()` that POSTs to
        # `/_sec/verify?provider=interstitial`.
        #
        # This is the byte cluster the docstring above called an open question.
        # It is not the throttle: `ref=cs_503` is on every throttle body and on
        # none of these, so the shapes never overlap and Amazon is doing two
        # different things.
        #
        # `captcha` rather than `block`, on the same grounds as the
        # continue-shopping gate: there is something here to clear. A client that
        # runs scripts computes the challenge and is let through, so a scriptless
        # engine meeting this page was challenged rather than refused, and
        # scoring it as a refusal credits Amazon with turning down an address it
        # was willing to serve.
        #
        # Keyed on the endpoint and the token, which name the mechanism, rather
        # than on the body size or the gif URL, which describe this week's
        # rendering of it. Placed after the result test for the reason
        # `WalmartSearch` documents at length: a served page carrying a dormant
        # challenge inline must read as served. Neither marker appeared on any of
        # the 4 served bodies, so the order costs nothing here and insures
        # against the failure that has already happened once in this repository.
        if "/_sec/verify" in low or "bm-verify" in low:
            return Judgement("captcha", "Akamai Bot Manager interstitial, a "
                                        "scripted challenge rather than a "
                                        "refused address")

        # AWS WAF's own challenge, and the third small page Amazon serves. Read
        # 2026-08-13 off one archived body from `probehold_20260813T185327Z`,
        # 2,005 bytes: `window.gokuProps` holding an encrypted key, a
        # `challenge.js` from a `token.awswaf.com` subdomain, an empty
        # `div#challenge-container`, and a script that calls
        # `AwsWafIntegration.getToken()` and reloads the page.
        #
        # A different vendor from the Akamai interstitial above, doing the same
        # thing, so it is scored the same way: the client is challenged and
        # handed a way through, and a browser that runs scripts computes the
        # token and reloads into the real page without the operator ever seeing
        # this. Filing it as `block` would credit Amazon with refusing an
        # address it was willing to serve.
        #
        # It was found through the front page rather than `/s?k=`, which is why
        # it is only now on disk: it arrived on a row the typed entry shape
        # produced, and it was archived only because `keep_error_body` had been
        # added hours earlier. Every previous run would have recorded the same
        # event as a bare `error` with nothing behind it.
        #
        # Keyed on the two names the mechanism owns. Checked across all 296
        # archived Amazon bodies: `gokuProps` and `awswaf` are on 1 of 1 of
        # these and on none of the 67 served, 186 throttled or 21 Akamai bodies,
        # so they separate the set in both directions. One body is one body
        # though - the rule is placed after the result test like its neighbours,
        # so the worst a wrong reading can do is misname a page that was going
        # to the catch-all anyway.
        if "gokuprops" in low or "awswaf" in low:
            return Judgement("captcha", "AWS WAF challenge, a scripted "
                                        "challenge rather than a refused "
                                        "address")

        # Amazon answering "we have nothing for that" is the search working.
        # Scoring it as a refusal would turn a query with no products into
        # evidence against the framework that sent it.
        if "s-no-results" in low or "no results for" in low:
            return Judgement("ok", "search served, no products matched the query")

        # A one-button page that asks for a click before anything is shown. It
        # is a gate rather than a refusal, and it is not the character-entry
        # captcha, so it is recorded separately: an engine that clears one may
        # not clear the other.
        if "continue shopping" in low:
            return Judgement("captcha", "continue-shopping interstitial, an "
                                        "interaction gate rather than a refusal")
        return Judgement("block", "no result list and no known interstitial")


class WalmartSearch:
    """Walmart product search, fronted by PerimeterX.

    Unlike `AmazonSearch`, every rule below was read off a captured body. The
    evidence is 18 responses taken direct from the operator's line on
    2026-08-12 by `scripts/probes/walmart_recon.py`: 10 refusals, 5 result
    pages, and 3 pages for queries deliberately built to match nothing. All 18
    arrived as HTTP 200, so the status says nothing here and is not consulted.

    Two markers separate the set cleanly and in opposite directions, which is
    why both are used instead of one plus an ordering trick:

        data-testid="item-stack"   1-2 on all 9 served, 0 on all 10 refusals
        class="re-captcha"         1 on all 10 refusals, 0 on all 9 served

    The ordering still matters, and this is the finding that justified the
    recon. A passing 2.1 MB body carrying 63 products also carried the whole
    PerimeterX modal inline and hidden: the heading "Robot or human?", the
    "Activate and hold the button" text, and `id="px-captcha"` holding a live
    token. Any rule that tested for a challenge first would have scored that
    page a captcha. The same trap as the `consent` string appearing twice in a
    perfectly good Amazon result list, except here the entire challenge is
    present verbatim on a page that was served.
    """

    name = "walmart_search"
    queries = PRODUCT_QUERIES
    # The list Amazon uses, from the same generator and the same seed. Holding
    # the query list fixed is what makes the pair a comparison of two defences
    # rather than two unrelated numbers.
    query_list = "amazon_1000"
    ready_selector = "[data-testid='item-stack']"
    # Conservative rather than measured. No scriptless client has ever got past
    # the handshake here - the plain `requests` arm was challenged 5 times out
    # of 5 - so whether the markup is usable without JavaScript is unknown, and
    # a wrong `False` would let a script-blocking preset turn our own setting
    # into the target's refusal. Revisit if a scriptless client is ever served.
    needs_script = True

    def url(self, query: str) -> str:
        return f"https://www.walmart.com/search?q={quote_plus(query)}"

    def judge(self, url: str, title: str, html: str) -> Judgement:
        low = (html or "").lower()
        if not html:
            return Judgement("empty", "no body")

        # Served, tested first. See the class docstring: the challenge markup
        # lives inside pages that were served, so a refusal test placed here
        # would misread them.
        if "item-stack" in low:
            return Judgement("ok", "result stack present")
        if "data-automation-id=\"product-title\"" in low:
            return Judgement("ok", "product tiles present")

        # Nothing matched is the search working. Two of the three nonsense
        # queries came back with zero product markers and only this line, so
        # without the rule a working search would be filed as a refusal - the
        # mistake `s-no-results` exists to prevent on Amazon.
        if "0 results for" in low:
            return Judgement("ok", "search served, nothing matched the query")

        # 15-17 KB, an empty px-captcha div and the PerimeterX bootstrap. This
        # is a hold-the-button challenge, so it is an interaction gate we did
        # not clear rather than a flat refusal.
        if "class=\"re-captcha\"" in low or "blockscript" in low:
            return Judgement("captcha", "perimeterx hold-button challenge")

        return Judgement("block", "no result stack and no known interstitial")


class GoogleMapsSearch:
    """Google Maps place search: the same reputation surface as `google_serp`,
    a different renderer and a different result marker.

    Both sides of the verdict are measured, which is what separates this class
    from the two marketplaces below it. The served side is
    `marketplace_recon_20260903T234200Z`, 2 of 2 direct headful attempts on
    patchright 1.62.2 / Chromium 151.0.7922.34: HTTP 200, 451-452 KB, title
    echoing the query, and

        role="feed"        1 on both, inside aria-label="Results for <query>"
        /maps/place/       7 on both, one per result card

    The refused side is not measured here and is not guessed either: it is
    `google_serp`'s, where `/sorry/` was carried by 4550 of 4550 captcha rows.
    Maps is served from the same host under the same reputation, so the redirect
    is the same redirect. If a Maps-specific wall ever turns up that `/sorry/`
    does not catch, it lands in `block` and the archived body says so.

    **The marker is `role="feed"` and not the class names, deliberately.** The
    two bodies carry `Nv2PK` on every result card and `hfpxzc` on every result
    link, which look like better markers and are worse ones: Google rotates
    obfuscated class names without notice, and this repository has already dated
    one such rotation from the rows - see `/goto?url=` in `FINGERPRINT_MARKERS`.
    A rotation would turn every row into `block` silently, which is the one
    direction of error that cannot be absorbed. `role` and `aria-label` are
    accessibility contracts and survive a rebuild.

    **`google_serp`'s classifier cannot be reused, and the reason is in the
    numbers above.** Its served test is `id="rso"`, `id="search"` or `<h3`, and
    all three are 0 on both served Maps bodies. Its no-JS test is `noscript`
    without `<h3`, which both served bodies would satisfy - Maps ships the same
    `noscript` scaffold on a page that rendered perfectly. Pointed at Maps that
    classifier reads 2 of 2 as `empty`.

    **A served attempt cost 5 695 125 and 5 699 075 bytes over the wire**, at
    `--preset light`, for 451-452 KB of markup. That is seven times the
    `google_serp` entry in `matrix.MEASURED_BYTES` and twenty times the
    `DEFAULT_BYTES` floor this target is priced at, so a full `places_1000` run
    that mostly passes spends gigabytes on a metered exit. It is deliberately
    not entered in that table: two attempts from a recon are not a benchmark
    run, and both of them passed, so the figure would price the next run by
    assuming its pass rate - the reason `amazon_search` is absent from the table
    too. Read `--dry-run` here knowing its number is a floor and knowing by how
    much.
    """

    name = "google_maps"
    queries = PLACE_QUERIES
    query_list = "places_1000"
    # Measured present on both served bodies. Maps is an application and builds
    # the feed in the browser, so a snapshot taken before it exists is the same
    # trap `google_serp` documents at `ready_selector`.
    ready_selector = "div[role='feed']"
    needs_script = True

    def url(self, query: str) -> str:
        # The query rides in the path, not in a query string, which is the URL
        # Maps itself produces. `hl=en` for the reason `google_serp` gives: the
        # verdict must not depend on which language the page came back in.
        return f"https://www.google.com/maps/search/{quote_plus(query)}?hl=en"

    def judge(self, url: str, title: str, html: str) -> Judgement:
        low_url = (url or "").lower()
        low = (html or "").lower()
        if not html:
            return Judgement("empty", "no body")

        if "/sorry/" in low_url or "unusual traffic" in low:
            return Judgement("captcha", "redirected to /sorry/ with a recaptcha")
        if "consent." in low_url or "consent?" in low_url:
            return Judgement("consent", "consent interstitial")

        # Served, tested before every challenge rule, for the reason
        # `WalmartSearch` documents: a page that was served is served even when
        # it carries challenge scaffolding inline.
        if 'role="feed"' in low:
            return Judgement("ok", "result feed present")

        # A query that resolves to exactly one place is answered with the place
        # panel and no feed. **Not observed in the two recon bodies** - both
        # queries were plural and both got a feed - and it is here anyway,
        # because the alternative is scoring a page Maps served as a refusal.
        # Keyed on the URL rather than on panel markup, since the redirect is
        # the part that is certain: Maps only rewrites to `/maps/place/` once it
        # has resolved the query to a place.
        if "/maps/place/" in low_url:
            return Judgement("ok", "resolved to a single place panel")

        if "consent.google.com" in low:
            return Judgement("consent", "consent wall served inline, no feed "
                                        "behind it")

        # Placed after both served tests and not before them. Both served bodies
        # carry `noscript` twice, so this rule in front of them would score a
        # rendered feed as our own client coming up short.
        if "enablejs" in low or "noscript" in low:
            return Judgement("empty", "served the no-JS scaffold, the feed needs "
                                      "a browser that runs scripts")
        return Judgement("block", "no feed, no place panel and no known "
                                  "interstitial")


class LazadaSearch:
    """Lazada Singapore catalogue search, fronted by Alibaba's `sufei-punish`.

    **This target can name a refusal and cannot yet name a pass.** Everything
    below the `captcha` rule is a gap that is written down rather than filled,
    because the only Lazada bodies this repository has ever captured are
    refusals: `marketplace_recon_20260903T234200Z`, 2 of 2 direct headful
    attempts, and both were the wall.

    **That gap is closed as of 2026-09-05 and the paragraph above is kept
    because the shape of the fix is the useful part.** `probehold_
    20260905T092634Z` put patchright through the pool onto SG exits and Lazada
    served the catalogue: 2 of 2 attempts HTTP 200, redirected to
    `/tag/<slug>/?q=...&catalog_redirect_tag=true`, 1.43 and 1.27 MB across the
    wire. The served marker is `data-qa-locator="product-item"`, one per card,
    40 of them in the body that carried the grid, inside a
    `data-qa-locator="general-products"` container. Checked against the
    negative: **0 occurrences in all six archived wall bodies** - four 1 735-byte
    ones from `marketplace_recon_20260903T184142Z` and the two 10 KB ones below.
    So the marker separates the two states rather than merely appearing in one.

    **Only one of those two 200s had the grid in the DOM, and the other one is
    ours.** 00002 archived 1.78 MB with 40 cards at 8 569 ms; 00000 archived
    62 KB with an empty `<div id="root">` at 5 774 ms, same title shape, same
    redirect, more bytes across the wire. Lazada served both; the snapshot was
    taken before the grid rendered in one of them. That is what `ready_selector`
    below now exists to stop, and it is why the `ok` rule and the selector were
    added in the same edit: the rule alone would have read that run as 1 of 2
    and recorded a `block` this repository caused.

    The refusal is measured and it is unambiguous:

        HTTP status       200 on both, so the status is not consulted
        final_url         www.lazada.sg//catalog//_____tmd_____/punish?x5secdata=
        html_len          10 187 and 10 143 bytes
        title             empty on both
        _____tmd_____     5 on both        x5secdata     7 on both
        /punish           2 on both        sufei-punish  1 on both

    The body is one `<iframe>` pointing at `/catalog/_____tmd_____/punish?
    recaptcha=1&x5step=2&x5secdata=...` plus a QR-code widget, with the
    challenge bootstrap loaded from `g.alicdn.com/bsop-static/sufei-punish/`.
    Two mistakes it would be easy to make from the run summary alone: the 200 is
    not a served page, and the `recaptcha` marker that the recon scan counted 10
    times is a query parameter on the iframe's URL, not a widget on a page that
    otherwise worked.

    2 025 839 bytes crossed the wire for the first of those two 10 KB
    documents. On a metered residential exit the wall is the expensive part.

    **What was missing and what closed it.** A served catalogue page, and the
    run above is it. Until it existed, anything that was not the wall fell to
    `block` carrying a reason that said so, so a run's `ok` count was 0 by
    construction and could not be read as a pass rate. Inventing an `ok` marker
    off Lazada's public markup would have been the `norotate` mistake again - a
    vendor surface standing in for a measurement - and the cost of getting it
    wrong is worse here than a missing rule, because a wrong `ok` is a pass this
    repository would publish. The marker below is read out of an archived body
    and checked against six archived refusals, not off lazada.sg.

    It could not be taken from the workstation: the storefront is per-country
    and this host egresses through a VPN gateway, so a refusal here does not
    separate Lazada's defence from a geo mismatch. It needed an SG exit through
    the pool, which is what the run above used - MobileOne, Whiz, MyRepublic and
    Viewqwest, all SG.

    **Still not measured: a search that legitimately matches nothing.** Its body
    would carry `general-products` with no `product-item` inside it and would
    fall to `block` here. No such body has been archived, so no rule is written
    for it, and the fallback reason says to read the body.
    """

    name = "lazada_search"
    queries = PRODUCT_QUERIES
    # Amazon's and Walmart's list, from the same generator and the same seed.
    # Holding the strings fixed is what makes four shops one comparison.
    query_list = "amazon_1000"
    # The per-card attribute, and it is the same string `judge` reads, so the
    # wait and the verdict cannot disagree about what "the grid arrived" means.
    #
    # The grid container `general-products` would have served as a wait just as
    # well - checked 2026-09-05 in the empty snapshot of `probehold_
    # 20260905T092634Z`, where the whole subtree is absent and the container
    # scores 0 alongside the card, so it is not the case that the shell writes
    # the container early and fills it late. That was written here as the reason
    # for preferring the card before it was checked, and it was wrong; the real
    # reason is only that one string is cheaper to keep true than two.
    ready_selector = "[data-qa-locator='product-item']"
    needs_script = True

    def url(self, query: str) -> str:
        return f"https://www.lazada.sg/catalog/?q={quote_plus(query)}"

    def judge(self, url: str, title: str, html: str) -> Judgement:
        low_url = (url or "").lower()
        low = (html or "").lower()
        if not html:
            return Judgement("empty", "no body")

        # The URL is tested first because it is the cheaper and the more stable
        # of the two: the wall redirects, and a redirect target is not markup
        # that can be restyled. The body test is the second disjunct so that a
        # wall served inline, without a redirect, is still caught.
        if "_____tmd_____" in low_url or "/punish" in low_url:
            return Judgement("captcha", "redirected to the sufei-punish wall")
        if "sufei-punish" in low or "x5secdata" in low:
            return Judgement("captcha", "sufei-punish challenge served inline")

        # Tested after the wall and not before it, because the wall is the
        # cheaper claim to be sure of: a punish document carries no cards, so
        # the order cannot change a verdict today, and if Lazada ever serves the
        # challenge over a rendered grid the refusal is the honest reading.
        if 'data-qa-locator="product-item"' in low:
            return Judgement("ok", "catalogue grid served")

        return Judgement("block", "not the punish wall and no product cards, "
                                  "which is also what a snapshot taken before "
                                  "the grid rendered looks like: check the "
                                  "`ready` column before reading this as a "
                                  "refusal, and read the body if the search "
                                  "may simply have matched nothing")


class ShopeeSearch:
    """Shopee Singapore search. Three outcomes, none of them a pass.

    **The heading of this docstring said "Refused at the edge, before Shopee
    sees it" until 2026-09-05, and that is not what an SG exit gets.** The
    sentence was true of the run it was written from and was carried as a
    property of the target. Both readings are kept below because the pair is the
    finding: the edge refuses some addresses outright and lets others through to
    a shell that never fills.

    Later the same day a third outcome was archived and the heading moved again,
    from two to three. The order they were found in is worth keeping, because
    each one arrived by the corpus growing and not by anyone reasoning better.

    The refusal, `marketplace_recon_20260903T234200Z`, 2 of 2 direct headful
    attempts from this workstation: HTTP 403, **144 bytes**, `<title>403
    Forbidden</title>` and a body of `<center><h1>403 Forbidden</h1></center>
    <hr><center>nginx</center>`. No Shopee markup at all - no storefront, no
    challenge, no fingerprinting script. This is nginx declining the connection,
    and nothing in the response says on what grounds. So the rule below reports
    the shape and refuses to name the cause: from a workstation behind a VPN
    gateway, asking a Singapore storefront, an address-based refusal and an
    anti-bot refusal produce the identical 144 bytes.

    The other outcome, `probehold_20260905T092634Z`, 2 of 2 through the pool on
    SG exits: **HTTP 200 and the ordinary storefront shell**, 269 KB of DOM,
    `<div id="main">` whose entire visible text is "Skip to main content". The
    two bodies are 269 573 and 269 569 bytes for two different queries, so the
    document does not vary with the search at all. The `captcha`, `blocked` and
    `denied` strings a scan finds in it are the shell's own i18n bundle and
    asset manifest - `"msg_captcha_empty_error":"Captcha cannot be empty"`,
    `pcmall-captcha` pointing at `deo.shopeemobile.com` - and not a wall.

    26 272 and 26 334 bytes crossed the wire, against Lazada's 1.4 MB on the
    same run. That looks like the document arriving and no bundle following it,
    and it is **not measured**: the rows carry no per-request breakdown, and a
    269 KB document of repeated CSS and JSON could plausibly compress to 26 KB
    on its own. Do not quote the reading without taking it.

    A raw shape is on disk and is neither of the two: `marketplace_recon_
    20260903T182409Z` holds four 162 043-byte bodies fetched by the scriptless
    `http` engine, with `id="main"` present, no `<title>` and no visible text at
    all. That is the raw document before anything runs, so it belongs beside the
    269 KB rendered one rather than against it, and the pair says only that the
    browser's DOM grew by 107 KB without a grid appearing in it.

    **The third outcome is the one Shopee names itself, archived 2026-09-05 in
    `marketplace_recon_20260905T104017Z`.** Through the pool on an SG exit, 3 of
    3 queries answered 200, and 2 of the 3 were redirected: `final_url` is
    `shopee.sg/verify/traffic/error?home_url=https://shopee.sg&
    is_logged_in=false&next=<the search URL>&tracking_id=<a nonce>`, and the
    rendered document carries `<div class="BsK01h">Login Required</div>`, the
    copy "Looks like you're not logged in yet", buttons "Log In" and "Back to
    Home Page", and an `ID:` line repeating the `tracking_id`. Whole visible
    text: 246 characters.

    The route is `/verify/traffic/`, which is the reading that matters: this is
    a traffic check wearing login copy, not an authentication requirement. That
    much is measured from the URL. **What is not measured is whether an
    unflagged visitor would be asked to log in at all** - no request has ever
    reached a Shopee result grid from here, so "the wall is conditional on the
    traffic" is inference from a route name and is written here as one.

    The third query of that run, `laptop stand`, was **not** redirected -
    `final_url` stayed `shopee.sg/search?keyword=laptop+stand` - and after a
    25-second settle its visible text is 65 characters, "Skip to main content".
    So one address in one window produced two walls and one shell, which is the
    first evidence that the two outcomes alternate on a single exit rather than
    belonging to different addresses.

    The request counters on that run say the shell is the arm that did *less*,
    not more: the two walls allowed 158 and 149 requests for 2.56 and 2.37 MB,
    the shell allowed **33 for 658 KB**. A wall is a rendered application and
    costs a full page load; the shell stops a third of the way in. The reading
    that suggests itself - the app booted and its search API was refused - does
    not fit, because that would cost about as much as the wall does. Nothing
    here says what does fit, and the rows carry no per-request breakdown to
    settle it.

    That 658 KB also sits badly beside the 26 KB quoted three paragraphs up, and
    the two are taken by different instruments - a relay counting at a CONNECT
    proxy against Playwright's route counter in the page - so neither refutes
    the other and both should be re-taken on one instrument before either is
    used.

    **The direct-arm 403 has an independent confirmation and it is the user's
    own browser**, 2026-09-05: the same search URL opened by hand with no proxy
    answered the stock nginx `403 Forbidden` page. That is a fourth client
    getting what `http-direct` and `patchright-direct` got on 2026-09-03, so the
    403 is a property of the address and not of the client. The control is not
    clean and the confound should be named: that connection is neither Singapore
    nor a residential pool exit, so country and network type move together and
    neither is isolated.

    **So this target still has no measured pass, and one cannot be invented from
    these bodies.** Across all 18 archived Shopee bodies the strings a result
    grid would carry - `data-sqe`, `shopee-search-item-result` - score zero.
    A selector taken off shopee.sg would be the `norotate` mistake. What closes
    it is a body with a grid in it. What the run above shows is that a long
    settle is not the missing ingredient: 25 seconds produced a wall twice and
    an unfilled shell once, so the next thing to vary is the request path -
    reaching the results through the site's own search box rather than by
    navigating straight to `/search?keyword=`. If a grid never arrives across a
    run, that is itself the result and this `block` stops being a placeholder.

    **The front page was tried as that request path and it did not help, but it
    did produce the first Shopee body that has ever rendered here.**
    `marketplace_recon_20260905T113703Z`, `--entry home --settle 25`, one SG
    session, three queries, each preceded by a visit to `shopee.sg/`: six
    attempts, all HTTP 200, and **5 of 6 ended at `/verify/traffic/error`** -
    including 2 of the 3 front pages. The corpus is now 24 bodies: 6 nginx
    403s, 9 walls, 3 shells, 5 scriptless documents, and one rendered page.

    That one, position 00002, is the front page at 659 029 bytes with 12 787
    visible characters and a real storefront in it, and it is the only body of
    the 24 whose final URL stayed on the address it asked for. It is what the
    search-bar discriminator in `judge` is calibrated on, and it is what showed
    the shell rule had been mislabelling a served page.

    **The one honest test of the idea returned a negative, at n=1.** The arm
    ran three times but only once did the front page actually serve, and the
    search immediately after that served front page was still sent to the
    wall. The other two searches followed front pages that were themselves
    walled, so those trials never established the session state the arm exists
    to create. A three-attempt arm that tested its own hypothesis once is worth
    saying out loud, because the run output reads as three trials.

    What that arm did **not** vary is the referrer: `page.goto` sends none
    either way, so it moved the cookie jar and the session's history with the
    origin and held "arrived from nowhere" fixed. Typing into the site's own
    search box is a different request - an in-page navigation carrying a
    referrer and the app's own state - and it is now buildable for the first
    time, because the served front page contains
    `<form class="shopee-searchbar" action="/search">` wrapping
    `input.shopee-searchbar-input__input`. That selector is derived from an
    archived body, which is the only way it may be taken.

    **The paragraph above recommends a run that should not be spent, and the
    evidence refuting it was in the same file the whole time.** The wall's own
    query string carries `next`, the address it intends to return you to, and
    on the two walled front pages of `113703Z` that value is
    `https://shopee.sg/` - not a search URL. So the gate fires on the bare
    front page, against a client that had asked for nothing but `/`. It is not
    keyed to `/search`, and no request path avoids it, because every path
    starts at a page the gate also guards. Typing into the search box requires
    a served front page first, and that is the step the gate takes 2 times in
    3.

    That also settles the reading the wall's copy invites. "Login Required"
    with `is_logged_in=false` looks like an auth policy, and it is not one:
    body 00002 rendered the full storefront for a client that was equally
    logged out, on the same session and the same exit as the two that were
    walled. Logged-out is not what is being refused. What varies between the
    served request and the walled ones is not in these rows.

    What the mistake looked like from the inside: the request-path idea was
    formed when only `/search` had been seen to fail, so "vary the path" was
    the obvious next move and it stayed obvious after `--entry home` gave the
    front page its own rows. The refutation needed no new run - only reading
    `next` off a URL that had already been written to disk. Two runs were
    spent before anyone parsed the wall's own parameters.

    **What the refutation does not touch is the warm-up**, and the distinction
    is worth keeping straight because it would be easy to retire both at once.
    The `next` finding is about which URL is requested. A warm-up changes what
    the session did before it requested anything, which is a different axis and
    is untested here. It is also the strongest lever measured anywhere in this
    repository: on Google, `probehold_20260904T000605Z` moved 3/12 to 8/11
    (p=0.039) with the warm-up alone, and adding interaction on top of it moved
    8/11 to 8/11 (p=1.000). That measurement is Google's and nothing says it
    transfers, which is exactly why the rung below is one rung.
    """

    # One rung, and the reason is `AmazonSearch.warm_ladder`'s: the rungs above
    # L1 were designed against Google's refusal and no measurement here says
    # they transfer. Asking for L2 or L3 on this target is refused rather than
    # silently answered with L1.
    #
    # **L3 is refused for a second and harder reason: it has no carrier.** The
    # rung means "third-party pages that report the exit to the target's
    # infrastructure without a navigation to it", and for Google each page was
    # checked on 2026-08-26 for a `google-analytics.com`, `doubleclick.net` or
    # `googlesyndication` tag. Shopee's stack is first-party: counted over the
    # served body of `marketplace_recon_20260905T113703Z`, the hosts it
    # references are `deo.shopeemobile.com` 891 times, `shopee.sg` 122,
    # `susercontent.com` 14, and the only third-party tag on it is
    # `googletagmanager.com`, which reports to Google and not to Shopee.
    #
    # State that as what it is. Measured: Shopee's own page loads almost
    # entirely first-party infrastructure. Inferred, and not measured: that no
    # third-party page therefore carries a Shopee tag. The check that would
    # settle it is the reverse one and it needs a live fetch of candidate
    # pages, which cannot be taken from this workstation.
    #
    # `help.shopee.sg` is a subdomain of the target's own registrable domain,
    # which is the `scholar.google.com` reasoning in L2: whatever the warm-up
    # does through cookies, it does on the same domain the target is on. It is
    # unvisited from here - whether Shopee's gate guards it too is one of the
    # things the rung is for, and it gets its own row so a warmed session that
    # never got its warm-up is visible rather than assumed.
    warm_ladder = (
        ("L1", ("https://help.shopee.sg/",)),
    )
    # Declaring the ladder above is what makes this necessary, and the suite
    # said so before any run did. `probe_and_hold.warm_sequence` appends
    # `home_url` to every rung so its two callers cannot disagree about what a
    # warm-up is, and `test_every_rung_ends_on_the_front_page` walks every
    # target that declares a ladder. Adding `warm_ladder` and nothing else
    # turned that script's clean refusal - "target declares no L1 in its
    # warm_ladder" - into an `AttributeError` on a target it had been correctly
    # rejecting, which is the crash already recorded at `probe_and_hold.py:1668`
    # on 2026-09-04 and this is the second way into it.
    #
    # `search_box` stays absent, so `check_entry` there still refuses this
    # target under `--entry home`. That is not an oversight: a grid has never
    # been served here, so a selector would be read off live markup rather than
    # out of an archived body, and a wrong one is recorded as the target
    # refusing rather than as a typo.
    home_url = "https://shopee.sg/"

    name = "shopee_search"
    queries = PRODUCT_QUERIES
    query_list = "amazon_1000"
    # Still None, and now for a narrower reason than `LazadaSearch`'s former
    # one. A grid has been served here exactly zero times, so any selector would
    # be read off Shopee's live markup rather than out of an archived body. The
    # cost of leaving it None is known and bounded: the snapshot is taken when
    # navigation settles, which on 2026-09-05 was 2 878 and 4 286 ms and caught
    # the shell both times.
    ready_selector = None
    needs_script = True

    def url(self, query: str) -> str:
        return f"https://shopee.sg/search?keyword={quote_plus(query)}"

    def judge(self, url: str, title: str, html: str) -> Judgement:
        low = (html or "").lower()
        if not html:
            return Judgement("empty", "no body")

        # Both markers, not one. `nginx` alone would match any page that
        # mentions it and `403 forbidden` alone would match a storefront's own
        # error styling; together they are the stock nginx error document.
        if "403 forbidden" in low and "nginx" in low:
            return Judgement("block", "nginx 403 with no storefront markup, "
                                      "refused before Shopee saw the request")

        # Shopee's traffic check, and it must be tested before the shell below.
        # The wall renders inside the same shell, so `skip to main content` is
        # present in it too and the shell rule would swallow it: of the 18
        # archived Shopee bodies that string scores in 8, and 5 of those 8 are
        # walls. Read on the archived bodies rather than on the URL because a
        # replay over `data/artifacts/` has no URL to read - which is how both
        # of this target's rules were calibrated - and `verify/traffic` appears
        # nowhere in the markup, 0 of 18.
        #
        # `login required` and `back to home page` have the identical
        # distribution, 5 of 18 and exactly the walls. One is enough; two
        # strings would be two things to keep true for no extra separation.
        #
        # The limit, and it is the same one the reason below states: none of the
        # 13 non-wall bodies is a served grid, so this marker is calibrated
        # against every known refusal and against no pass at all. If Shopee's
        # own i18n bundle ever carries this heading on a served page, this rule
        # turns a pass into a block and nothing here would notice.
        if "login required" in low:
            return Judgement("block", "shopee's own traffic check, served at "
                                      "/verify/traffic/error with login copy "
                                      "and a tracking id: the edge let the "
                                      "request through and shopee refused it")

        # One verdict, four reasons. The shell, the wall and the 403 are the
        # same `block` because none is a result grid, but the 403 and the other
        # two are opposite answers about whether the edge let us through, and a
        # report that merges them cannot show the SG exits doing better than the
        # gateway.
        #
        # **This comment said until later on 2026-09-05 that `skip to main
        # content` scores "0 in all ten other archived Shopee bodies".** That
        # was true of the twelve bodies that existed when it was written and it
        # is false now: six more were archived the same day, five of them walls,
        # and all six carry the string. The rule was calibrated against every
        # body in the corpus and was still wrong, because the state it fails to
        # separate had not been archived yet. A calibration is worth exactly the
        # states its corpus contains, and saying "checked against all N bodies"
        # does not raise that.
        #
        # It is written by the page's own scripts, not served. The four
        # 162 043-byte bodies the scriptless `http` engine took in
        # `marketplace_recon_20260903T182409Z` carry `id="main"` and not this
        # string, so they fall to the last reason and not to this one. That is
        # the right split - it says the document arrived and nothing ran - but
        # it means this rule reports on a browser and the reason below covers
        # both "no browser" and "something genuinely new", which is a merge to
        # undo the first time a body lands there from an engine that renders.
        #
        # **The second condition is a correction, and the paragraph above it
        # predicted the failure without preventing it.** Until
        # `marketplace_recon_20260905T113703Z` this rule was `skip to main
        # content` alone, and that run archived the first Shopee body that has
        # ever rendered - the front page at position 00002, 659 029 bytes,
        # 12 787 visible characters, the only one of the six that stayed on
        # `/` instead of being sent to `/verify/traffic/error`. Replaying this
        # method over it returned `block` with the reason "served and never
        # filled", about a page that was served and had filled. The rule was
        # calibrated against every body in the corpus twice, and both times
        # the state it could not separate was simply missing from the corpus.
        #
        # The discriminator is the site's own search bar, which its scripts
        # write only when they render the chrome. Counted over the 24 archived
        # Shopee bodies on 2026-09-05: `shopee-searchbar-input__input` scores
        # **1 of 24**, and that one is the rendered body. It scores 0 in all 6
        # nginx 403s, all 9 walls, all 3 shells and all 5 scriptless
        # documents.
        #
        # Two limits, both load-bearing. It is calibrated on a single served
        # body, so it is the thinnest rule on this target. And that body is a
        # **front page, not a result page** - the search bar is global chrome
        # and a served result page will almost certainly carry it, but
        # "almost certainly" is the `norotate` mistake with a CSS class in it,
        # and no served Shopee result page exists here to check it against.
        # A rendered page therefore falls through to the reason below, which
        # says to read the body, rather than being given a confident wrong
        # one here.
        if ("skip to main content" in low
                and "shopee-searchbar-input__input" not in low):
            return Judgement("block", "storefront shell served and never "
                                      "filled, so the edge let the request "
                                      "through and no grid rendered")

        return Judgement("block", "not the nginx 403, the traffic check or the "
                                  "empty shell, and no served marker exists for "
                                  "this target yet: no Shopee result grid has "
                                  "ever been archived here, so read the body "
                                  "and not this verdict")


TARGETS = {t.name: t for t in (IpInfo(), GoogleSerp(), DuckDuckGoSerp(),
                               BingSerp(), AmazonSearch(), WalmartSearch(),
                               GoogleMapsSearch(), LazadaSearch(),
                               ShopeeSearch())}

# Targets that build their results in the browser. A preset that blocks script
# cannot be used against these without measuring the preset instead of the
# target.
NEEDS_SCRIPT = {name for name, t in TARGETS.items()
                if getattr(t, "needs_script", False)}
