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
    home_url = "https://www.google.com/?hl=en"
    # Google has served the box as a textarea since 2023 and as an input before
    # that, and still serves the input on some lightweight variants. Both are
    # listed rather than one being assumed, so a variant swap is a slower page
    # and not a run of `error` rows.
    search_box = "textarea[name='q'], input[name='q']"
    # Pages to open on a fresh exit before asking it anything. The claim under
    # test is that this alone moves the yield from 20% to 75%, which is a larger
    # effect than any engine difference in this repository, so it is an axis in
    # `probe_and_hold.py` rather than something the probe always does.
    #
    # Declared on the target and not in the probe for the usual reason: a probe
    # that knew a domain would be a probe that could warm one target better than
    # another. The list must not contain the front page - the probe always ends
    # on `home_url`, so that the box the query is typed into is the one on the
    # page the protocol names.
    warm_urls = ("https://www.google.com/imghp?hl=en",)
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
    # See `GoogleSerp.warm_urls`. The bestsellers page is an ordinary browse
    # page rather than a search, which is the point: it is what a visitor does
    # before searching, and it is the one thing this exit will have done before
    # the throttle decides about it.
    warm_urls = ("https://www.amazon.com/gp/bestsellers/?language=en_US",)

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


TARGETS = {t.name: t for t in (IpInfo(), GoogleSerp(), DuckDuckGoSerp(),
                               BingSerp(), AmazonSearch(), WalmartSearch())}

# Targets that build their results in the browser. A preset that blocks script
# cannot be used against these without measuring the preset instead of the
# target.
NEEDS_SCRIPT = {name for name, t in TARGETS.items()
                if getattr(t, "needs_script", False)}
