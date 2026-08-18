"""Verdict tests.

These are the most important tests in the repository. Every number the benchmark
produces is a count of verdicts, so a verdict function that is wrong does not
produce a wrong number, it produces a confident wrong number. The Google cases
below are built from bodies actually observed on 2026-08-10 and 2026-08-11.
"""
import pytest

from nmbench.targets import NEEDS_SCRIPT, TARGETS, VERDICTS, fingerprint

GOOGLE = TARGETS["google_serp"]
BING = TARGETS["bing_serp"]
DDG = TARGETS["ddg_serp"]
IPINFO = TARGETS["ipinfo"]
AMAZON = TARGETS["amazon_search"]
WALMART = TARGETS["walmart_search"]

# Trimmed from a body captured direct on 2026-08-12: 2.1 MB, HTTP 200, the right
# title, 63 products - and the entire PerimeterX modal sitting inside it, hidden.
# This fixture exists to fail loudly if anyone reorders the Walmart rules so a
# challenge test runs before the result test.
WALMART_SERVED_WITH_DORMANT_CHALLENGE = """<!doctype html><html>
<head><title>air fryer - Walmart.com</title></head><body>
<div data-testid="item-stack"><div data-automation-id="product-title">Air fryer
</div></div>
<div><h2>Robot or human?</h2><p>Activate and hold the button to confirm that
you're human. Thank You!</p>
<div id="px-captcha" class="flex justify-center" style="display: block;">
<iframe style="display: none;" token="4fc1787eb1af014447b6864dd849"></iframe>
</div></div></body></html>"""

# The refusal, 15 KB, also HTTP 200. Note that nothing about the status tells the
# two apart: both fixtures above and below arrived as 200.
WALMART_CHALLENGE = """<!doctype html><html>
<head><title>Robot or human?</title></head><body>
<h1 class="sign-in-widget">Robot or human?</h1>
<div class="sign-in-widget"><div class="re-captcha">
<p class="bot-message" id=message>Activate and hold the button to confirm that
you're human. Thank You!</p>
<div id="px-captcha" style="margin:16px;"></div></div></div>
<script>window._pxAppId = 'PXu6b0qd2S';</script>
<script id="blockScript"></script></body></html>"""

# Trimmed from one of 20 distinct bodies archived on 2026-08-12 through the pool,
# runs benchmark_20260812T111353Z and ...T112029Z. Every one arrived as HTTP 200
# at 2,317 bytes, so neither the status nor the size separates it from a served
# page. There is no captcha here and nothing to solve: Amazon refused the
# address, it did not challenge the browser.
AMAZON_THROTTLE = """<!doctype html><html>
<head><title>Sorry! Something went wrong!</title></head><body>
<a href="/ref=cs_503_logo"><img id="b" src="https://images-na.ssl-images-amazon.com/images/G/01/error/logo._TTD_.png" alt="Amazon.com"></a>
<form id="a" action="/s" method="GET" role="search">
<input id="e" name="field-keywords" placeholder="Search">
<input name="ref" type="hidden" value="cs_503_search"></form>
<div id="g"><div><a href="/ref=cs_503_link"><img src="https://images-na.ssl-images-amazon.com/images/G/01/error/500_503.png" alt="Sorry! Something went wrong on our end. Please go back and try again or go to Amazon's home page."></a></div>
<a href="/dogsofamazon/ref=cs_503_d" target="_blank"><img id="d" alt="Dogs of Amazon"></a>
</div></body></html>"""

# Akamai Bot Manager's interstitial, transcribed from
# `data/artifacts/benchmark_20260813T120848Z/http-direct__amazon_search__block__00005.html.gz`
# with the token truncated. HTTP 200, 2,330 bytes on the wire. Note what is not
# here: no captcha image, no character entry, no "continue shopping" button, and
# no `ref=cs_503`. The whole challenge is the script, which is why a scriptless
# client can never clear it and a browser usually does not notice it happened.
AMAZON_AKAMAI_INTERSTITIAL = """<!DOCTYPE html><html><head> <meta charset="utf-8">
<meta http-equiv="refresh" content="5; URL='/s?k=pet+carrier+large&amp;language=en_US&bm-verify=AAQAAAAN_____478lCWF5XyjiV3o15CUDKDDaoH8MuEKYK'" />
<title>&nbsp;</title><script> var i = 1786622965; var j = i + Number("9598" + "22629"); </script>
</head><body> <iframe style="border: none; width: 100vw; height: 100vh;"
src="https://m.media-amazon.com/images/S/sash/6Uh4bsAwUkB3vJb.gif"> </iframe>
<script> function triggerInterstitialChallenge() {var xhr = new XMLHttpRequest();
xhr.open("POST", "/_sec/verify?provider=interstitial", false); }
triggerInterstitialChallenge(); </script></body></html>"""

# AWS WAF's challenge, transcribed from
# `data/artifacts/probehold_20260813T185327Z/camoufox-none__amazon_search__error__00025.html.gz`
# with the key and context truncated. 2,005 bytes, through the pool, and served
# on the front page rather than on /s - which is why it took the typed entry
# shape to find it and `keep_error_body` to keep it. A third vendor doing what
# Akamai does above: no image, no character entry, nothing for a person to
# solve, and a token the page computes for itself before reloading.
AMAZON_AWS_WAF = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title></title><script type="text/javascript">
window.awsWafCookieDomainList = [];
window.gokuProps = {"key":"AQIDAHjcYu","iv":"D57WyAD7jAAAH57u","context":"nnt267E"};
</script><script src="https://1c5c1ecf7303.us-east-1.token.awswaf.com/challenge.js">
</script></head><body><div id="challenge-container"></div>
<script type="text/javascript">AwsWafIntegration.saveReferrer();
AwsWafIntegration.getToken().then(() => { window.location.reload(true); });</script>
<noscript><h1>JavaScript is disabled</h1>In order to continue, we need to verify
that you are not a robot.</noscript></body></html>"""

# The no-JS scaffold: stays on /search, ~92 KB, tells you to enable JavaScript,
# and contains no rejection of any kind. Google did not refuse this request.
SCAFFOLD = """<!doctype html><html><head><title>tls fingerprint ja4 - Google Search
</title></head><body><noscript><div id="gbar"></div>
<div>Please click <a href="/httpservice/retry/enablejs?sei=abc">here</a> if you
are not redirected within a few seconds.</div></noscript></body></html>"""

# The refusal: redirected to /sorry/, carries the recaptcha widget and posts the
# solved challenge back to /sorry/index.
SORRY = """<!doctype html><html><head><title>https://www.google.com/search?q=x
</title></head><body>Our systems have detected unusual traffic from your
computer network. <script src="https://www.google.com/recaptcha/api.js"></script>
<form action="/sorry/index" method="post"><input name="continue"></form>
</body></html>"""

RESULTS = """<!doctype html><html><head><title>x - Google Search</title></head>
<body><div id="search"><div id="rso"><h3>A result</h3><cite>example.com</cite>
</div></div></body></html>"""

# The consent wall as Google actually serves it: an overlay on the front page,
# HTTP 200, address still `/?hl=en`, with the search box present underneath it.
# Trimmed from a body read on 2026-08-13 - 332 KB, `consent.google.com` five
# times, `noscript` present and no `<h3>` anywhere. The url-matching consent rule
# cannot see this one, and without a rule of its own it falls through to the
# no-JS test and is recorded as our own client coming up short.
CONSENT_BUMP = """<!doctype html><html><head><title>Google</title></head>
<body><noscript>Please enable JavaScript</noscript>
<textarea name="q" aria-label="Search"></textarea>
<div id="xe7COe" role="dialog" aria-modal="true">
<h1>Before you continue to Google</h1>
<p>We use cookies and data, including IP addresses, to</p>
<form action="https://consent.google.com/save?continue=x" method="POST">
<button id="W0wltc">Reject all</button><button id="L2AGLb">Accept all</button>
</form></div></body></html>"""


class TestGoogle:
    def test_results_page_is_ok(self):
        assert GOOGLE.judge("https://www.google.com/search?q=x", "", RESULTS
                            ).verdict == "ok"

    def test_sorry_page_is_captcha(self):
        judgement = GOOGLE.judge("https://www.google.com/sorry/index?continue=x",
                                 "", SORRY)
        assert judgement.verdict == "captcha"
        assert "sorry" in judgement.reason

    def test_unusual_traffic_is_captcha_even_without_the_redirect(self):
        """The body has arrived on /search as well as on /sorry/."""
        assert GOOGLE.judge("https://www.google.com/search?q=x", "", SORRY
                            ).verdict == "captcha"

    def test_scaffold_is_not_a_block(self):
        """The whole point of the 2026-08-11 fix.

        Scoring this as a block credits Google with a refusal it never made and
        makes the plain HTTP engine look rejected when it was merely outmatched.
        """
        judgement = GOOGLE.judge("https://www.google.com/search?q=x", "", SCAFFOLD)
        assert judgement.verdict == "empty"
        assert "no-JS scaffold" in judgement.reason

    def test_scaffold_and_refusal_are_distinguishable(self):
        scaffold = GOOGLE.judge("https://www.google.com/search?q=x", "", SCAFFOLD)
        refusal = GOOGLE.judge("https://www.google.com/sorry/index", "", SORRY)
        assert scaffold.verdict != refusal.verdict

    def test_consent_interstitial(self):
        assert GOOGLE.judge("https://consent.google.com/m?continue=x", "",
                            "<html>agree</html>").verdict == "consent"

    def test_the_inline_consent_wall_is_consent_and_not_a_short_client(self):
        """The wall the typed entry shape meets, and the URL rule cannot see it.

        It arrives as 200 on `/?hl=en`, so the address says nothing. Before this
        rule it fell through to the no-JS test - the body carries `noscript` and
        no `<h3>` - and a wall Google really did put up was recorded as `empty`,
        which in this repository means our own client fell short. That is the
        one direction of error the verdicts cannot absorb.
        """
        judgement = GOOGLE.judge("https://www.google.com/?hl=en", "Google",
                                 CONSENT_BUMP)
        assert judgement.verdict == "consent"

    def test_the_consent_wall_is_named_rather_than_matched_by_prose(self):
        """Keyed on the endpoint the choice is posted to, not on the heading.

        `Before you continue to Google` is translated per country and reworded
        without notice, and this harness runs against exits in several of them.
        A rule resting on it would quietly stop firing and every wall would
        reappear as a no-JS scaffold.
        """
        reworded = CONSENT_BUMP.replace("Before you continue to Google",
                                        "Bevor Sie zu Google weitergehen")
        assert GOOGLE.judge("https://www.google.com/?hl=de", "Google",
                            reworded).verdict == "consent"

    def test_a_served_page_carrying_a_consent_link_is_still_ok(self):
        """Rule order, the same trap Walmart documents at length.

        A result page that happens to link `consent.google.com` in its footer
        has been served, and a challenge test placed before the result test
        would score it as a wall. `test_results_page_is_ok` passes either way,
        so the ordering needs a case that only fails when it is wrong.
        """
        served = RESULTS.replace("</body>",
                                 '<a href="https://consent.google.com/">Privacy'
                                 "</a></body>")
        assert GOOGLE.judge("https://www.google.com/search?q=x", "", served
                            ).verdict == "ok"

    def test_empty_body(self):
        assert GOOGLE.judge("https://www.google.com/search?q=x", "", ""
                            ).verdict == "empty"

    def test_unknown_body_is_a_block(self):
        """Anything with no results and no known interstitial stays a block.

        The fallback must not widen: if a new Google refusal appears, we want it
        reported as a block and investigated, not absorbed into `empty`.
        """
        assert GOOGLE.judge("https://www.google.com/search?q=x", "",
                            "<html><body>nothing here</body></html>"
                            ).verdict == "block"


class TestOtherTargets:
    def test_bing_results(self):
        assert BING.judge("", "", '<li class="b_algo"><h2>x</h2></li>'
                          ).verdict == "ok"

    def test_bing_challenge(self):
        assert BING.judge("", "", '<div id="captcha_header">verify</div>'
                          ).verdict == "captcha"

    def test_bing_ignores_the_bare_word_captcha(self):
        """It appears in the telemetry of a perfectly good page."""
        assert BING.judge("", "", '<li class="b_algo">captcha solving service</li>'
                          ).verdict == "ok"

    def test_ddg_results(self):
        assert DDG.judge("", "", '<a class="result__a" href="x">t</a>'
                         ).verdict == "ok"

    def test_ddg_anomaly(self):
        assert DDG.judge("", "", "<p>anomaly detected</p>").verdict == "captcha"

    def test_ipinfo_echo(self):
        assert IPINFO.judge("", "", '{"ip": "1.2.3.4"}').verdict == "ok"


class TestAmazon:
    """Two of these rest on captured bodies and the rest do not.

    The `ok` case and the 503 throttle case carry measurement dates because
    their markers were read off real responses. The captcha and
    continue-shopping cases still pin the logic without confirming the markers -
    they have never fired, and no number resting on them may be published."""

    def test_the_503_throttle_page_is_a_block(self):
        """Read off 20 distinct archived bodies on 2026-08-12, every one HTTP
        200 and 2,317 bytes. This is the only Amazon refusal shape ever
        captured here, and through the pool it was the dominant verdict."""
        judgement = AMAZON.judge(
            "https://www.amazon.com/s?k=air+fryer&language=en_US",
            "Sorry! Something went wrong!", AMAZON_THROTTLE)
        assert judgement.verdict == "block"
        assert "503" in judgement.reason

    def test_the_throttle_is_named_rather_than_matched_by_prose(self):
        """The rule that caught these before was a coincidence: it matched the
        alt text of an image on the page. Amazon rewording one sentence would
        have moved every one of these into the catch-all fallback, and the
        column would have kept counting without anyone noticing."""
        stripped = AMAZON_THROTTLE.replace(
            "Sorry! Something went wrong on our end.", "We are sorry.")
        assert AMAZON.judge("https://www.amazon.com/s?k=x", "",
                            stripped).verdict == "block"

    def test_the_throttle_is_not_scored_as_a_challenge(self):
        """It carries no captcha and nothing to solve. Filing it as one would
        claim Amazon challenged the browser when it refused the address, which
        is the opposite conclusion and the one the report turns on."""
        assert AMAZON.judge("https://www.amazon.com/s?k=x", "",
                            AMAZON_THROTTLE).verdict != "captcha"

    def test_result_list_is_ok(self):
        assert AMAZON.judge(
            "https://www.amazon.com/s?k=air+fryer", "",
            '<div class="s-main-slot"><div data-component-type="s-search-result">'
            "</div></div>").verdict == "ok"

    def test_the_akamai_interstitial_is_a_challenge_and_not_a_throttle(self):
        """Read off 5 archived bodies on 2026-08-13, direct arm, HTTP 200,
        2,308-2,369 bytes. This is the second byte cluster the target docstring
        called an open question, and it is a different mechanism from the 503
        throttle: `ref=cs_503` is on every throttle body and on none of these.

        The verdict has to be `captcha`. There is a challenge on the page and a
        client that runs scripts clears it, so a scriptless engine that met this
        was challenged rather than refused. Filing it as a block would count
        Amazon as having turned down an address it was willing to serve, which
        is the same error the throttle test above guards in the other
        direction."""
        judgement = AMAZON.judge(
            "https://www.amazon.com/s?k=x", "",
            AMAZON_AKAMAI_INTERSTITIAL)
        assert judgement.verdict == "captcha"
        assert "akamai" in judgement.reason.lower()

    def test_the_interstitial_is_not_absorbed_by_the_catch_all(self):
        """Where it landed until 2026-08-13. The catch-all reason is "no result
        list and no known interstitial", which was literally true and made five
        Akamai challenges indistinguishable from any other empty page. A
        fallback that quietly grows is how a target's behaviour stops being
        measured while the column keeps counting."""
        judgement = AMAZON.judge("https://www.amazon.com/s?k=x", "",
                                 AMAZON_AKAMAI_INTERSTITIAL)
        assert "no known interstitial" not in judgement.reason

    def test_the_aws_waf_challenge_is_a_challenge_and_not_a_refusal(self):
        """The fourth answer this target serves, read 2026-08-13 off one body
        through the pool. Same reasoning as the Akamai rule above and a
        different vendor: the page hands the client a token to compute and
        reloads itself, so a client that runs scripts is let through and one
        that does not is stuck. Amazon challenged it, Amazon did not refuse it.

        It is the first Amazon shape found through the front page rather than
        `/s?k=`, which is the entry axis paying for itself: eight days of runs
        entering at the search URL never met it."""
        judgement = AMAZON.judge("https://www.amazon.com/?language=en_US", "",
                                 AMAZON_AWS_WAF)
        assert judgement.verdict == "captcha"
        assert "waf" in judgement.reason.lower()

    def test_the_aws_waf_challenge_is_not_absorbed_by_the_catch_all(self):
        """Where it landed until 2026-08-13, and where it would land again the
        day someone reorders these rules. The catch-all cannot tell a scripted
        challenge from an empty page, and those are opposite findings."""
        judgement = AMAZON.judge("https://www.amazon.com/?language=en_US", "",
                                 AMAZON_AWS_WAF)
        assert "no known interstitial" not in judgement.reason

    def test_the_two_vendors_stay_distinguishable(self):
        """Akamai and AWS WAF mean the same thing and are not the same thing.
        One number covering both would hide Amazon moving between them, which
        is a change in the target and the sort of thing this repository exists
        to notice."""
        akamai = AMAZON.judge("https://www.amazon.com/s?k=x", "",
                              AMAZON_AKAMAI_INTERSTITIAL).reason
        waf = AMAZON.judge("https://www.amazon.com/", "", AMAZON_AWS_WAF).reason
        assert akamai != waf

    def test_a_served_page_carrying_the_aws_waf_marker_is_still_ok(self):
        """The ordering, for the second vendor. No served Amazon body has
        carried these markers, and the cheapest time to fix an ordering is
        before one does."""
        assert AMAZON.judge(
            "https://www.amazon.com/s?k=x", "",
            '<div class="s-main-slot"><div data-component-type="s-search-result">'
            '</div></div><script src="https://x.token.awswaf.com/challenge.js">'
            "</script>").verdict == "ok"

    def test_a_served_page_carrying_the_challenge_is_still_ok(self):
        """The Walmart lesson applied before it costs anything here. There, a
        served 2.1 MB page carried the entire PerimeterX modal inline and
        hidden, so a challenge test placed before the result test scored good
        pages as captchas. No served Amazon body has been seen carrying these
        markers, which is exactly when the ordering is cheap to get right."""
        assert AMAZON.judge(
            "https://www.amazon.com/s?k=x", "",
            '<div class="s-main-slot"><div data-component-type="s-search-result">'
            '</div></div><script src="/_sec/verify?provider=interstitial">'
            "</script>").verdict == "ok"

    def test_character_captcha(self):
        judgement = AMAZON.judge(
            "https://www.amazon.com/errors/validateCaptcha", "",
            "<p>Enter the characters you see below</p>")
        assert judgement.verdict == "captcha"
        assert "character" in judgement.reason

    def test_captcha_wins_over_the_error_url(self):
        """The captcha is served from /errors/validateCaptcha, so a URL test
        alone would file every captcha as a block and the two would stop being
        countable separately."""
        assert AMAZON.judge("https://www.amazon.com/errors/validateCaptcha", "",
                            "<form action=/errors/validateCaptcha>"
                            "opfcaptcha</form>").verdict == "captcha"

    def test_error_page_is_a_block(self):
        assert AMAZON.judge("https://www.amazon.com/s?k=x", "",
                            "<p>Sorry! Something went wrong on our end.</p>"
                            ).verdict == "block"

    def test_no_products_is_still_a_served_search(self):
        """The engine got in. Counting an empty shelf against it would make the
        query list part of the pass rate."""
        judgement = AMAZON.judge(
            "https://www.amazon.com/s?k=asdfgh", "",
            '<span class="s-no-results">No results for asdfgh</span>')
        assert judgement.verdict == "ok"
        assert "no products" in judgement.reason

    def test_continue_shopping_gate(self):
        judgement = AMAZON.judge("https://www.amazon.com/", "",
                                 "<button>Continue shopping</button>")
        assert judgement.verdict == "captcha"
        assert "continue-shopping" in judgement.reason

    def test_empty_body(self):
        assert AMAZON.judge("https://www.amazon.com/s?k=x", "", ""
                            ).verdict == "empty"

    def test_unknown_body_is_a_block(self):
        assert AMAZON.judge("https://www.amazon.com/s?k=x", "",
                            "<html><body>nothing here</body></html>"
                            ).verdict == "block"

    def test_language_is_pinned_so_the_markers_can_match(self):
        """Every marker above is an English string. An exit that flips the store
        to another language would be judged by a rule that cannot match it."""
        assert "language=en_US" in AMAZON.url("air fryer")


class TestWalmart:
    """Read off 18 bodies captured direct on 2026-08-12: 10 refusals, 5 result
    pages and 3 pages for queries built to match nothing. Every one arrived as
    HTTP 200, which is why no rule here consults the status."""

    def test_result_stack_is_ok(self):
        assert WALMART.judge("https://www.walmart.com/search?q=air+fryer", "",
                             '<div data-testid="item-stack"></div>'
                             ).verdict == "ok"

    def test_product_tiles_alone_are_ok(self):
        assert WALMART.judge("https://www.walmart.com/search?q=x", "",
                             '<span data-automation-id="product-title">x</span>'
                             ).verdict == "ok"

    def test_the_challenge_is_a_captcha(self):
        judgement = WALMART.judge("https://www.walmart.com/search?q=x", "",
                                  WALMART_CHALLENGE)
        assert judgement.verdict == "captcha"
        assert "perimeterx" in judgement.reason

    def test_a_served_page_carrying_the_dormant_challenge_is_still_ok(self):
        """The finding that justified the recon, and the one rule here most
        likely to be broken by a well-meaning edit.

        Walmart ships the whole PerimeterX modal inside pages it served -
        heading, prompt text and `id="px-captcha"` with a live token, hidden.
        A challenge test placed before the result test scores a 2.1 MB page
        holding 63 products as a captcha, and the refusal rate for every engine
        would then be a measurement of nothing.
        """
        judgement = WALMART.judge("https://www.walmart.com/search?q=air+fryer",
                                  "", WALMART_SERVED_WITH_DORMANT_CHALLENGE)
        assert judgement.verdict == "ok"

    def test_zero_results_is_a_served_search(self):
        """Two of the three nonsense queries came back with no product markers
        at all and only this line. Without the rule, a search that worked would
        be filed as a refusal and the query list would become part of the pass
        rate - the mistake `s-no-results` prevents on Amazon."""
        judgement = WALMART.judge("https://www.walmart.com/search?q=qzxwvv", "",
                                  '<div>0 results for "qzxwvv".</div>')
        assert judgement.verdict == "ok"
        assert "nothing matched" in judgement.reason

    def test_status_is_never_consulted(self):
        """Both fixtures arrived as 200. `judge` is not given the status at all,
        so this pins the shape of the contract rather than a branch."""
        assert WALMART.judge("https://www.walmart.com/search?q=x", "",
                             WALMART_CHALLENGE).verdict == "captcha"
        assert WALMART.judge("https://www.walmart.com/search?q=x", "",
                             WALMART_SERVED_WITH_DORMANT_CHALLENGE
                             ).verdict == "ok"

    def test_empty_body(self):
        assert WALMART.judge("https://www.walmart.com/search?q=x", ""
                             , "").verdict == "empty"

    def test_unknown_body_is_a_block(self):
        """The fallback stays narrow. A new refusal shape should surface as a
        block nobody can explain, not be absorbed into the captcha count."""
        assert WALMART.judge("https://www.walmart.com/search?q=x", "",
                             "<html><body>nothing here</body></html>"
                             ).verdict == "block"

    def test_it_shares_the_amazon_query_list(self):
        """The whole argument for this target is that it holds the vertical and
        the inputs fixed and changes only the defence in front of them."""
        assert WALMART.query_list == AMAZON.query_list


class TestContract:
    @pytest.mark.parametrize("name", sorted(TARGETS))
    def test_every_verdict_is_in_the_enum(self, name):
        target = TARGETS[name]
        bodies = ["", RESULTS, SORRY, SCAFFOLD, "<html>unknown</html>"]
        for body in bodies:
            assert target.judge("https://example.com/", "", body).verdict in VERDICTS

    @pytest.mark.parametrize("name", sorted(TARGETS))
    def test_every_judgement_carries_a_reason(self, name):
        judgement = TARGETS[name].judge("https://example.com/", "", "<html></html>")
        assert judgement.reason

    @pytest.mark.parametrize("name", sorted(TARGETS))
    def test_url_encodes_the_query(self, name):
        url = TARGETS[name].url("tls fingerprint ja4")
        assert " " not in url
        assert url.startswith("https://")

    def test_needs_script_is_the_declared_set(self):
        """Google is here because it was measured: a scriptless client gets the
        no-JS scaffold. Walmart is here because it was not measured - the plain
        client was challenged 5 times out of 5 on 2026-08-12, so it never
        reached a page, and whether the markup survives without JavaScript is
        unknown. The conservative side of that unknown is the one where our own
        preset cannot be mistaken for the target's refusal."""
        assert NEEDS_SCRIPT == {"google_serp", "walmart_search"}


class TestFingerprint:
    def test_records_the_markers_the_verdict_rested_on(self):
        marks = fingerprint("https://www.google.com/sorry/index", SORRY)["markers"]
        assert marks["/sorry/"] >= 1
        assert marks["recaptcha"] >= 1
        assert marks["unusual traffic"] == 1

    def test_records_the_url_the_verdict_rested_on(self):
        """Google's refusal is identified by the redirect, and the marker counts
        only cover the body, so the URL has to be stored to re-judge the row."""
        assert fingerprint("https://www.google.com/sorry/index", SORRY
                           )["final_url"] == "https://www.google.com/sorry/index"

    def test_scaffold_carries_enablejs_and_no_recaptcha(self):
        """The discriminator, checked against the stored evidence format."""
        marks = fingerprint("https://www.google.com/search?q=x", SCAFFOLD)["markers"]
        assert marks["enablejs"] == 1
        assert "recaptcha" not in marks

    def test_absent_markers_are_omitted_not_zeroed(self):
        marks = fingerprint("", "<html></html>")["markers"]
        assert marks == {}

    def test_title_is_extracted_and_bounded(self):
        assert fingerprint("", "<title>" + "x" * 500 + "</title>")["title"] == "x" * 120

    def test_survives_a_missing_body(self):
        assert fingerprint(None, None) == {"title": "", "final_url": "",
                                           "markers": {}}
