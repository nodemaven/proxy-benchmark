"""Verdict tests.

These are the most important tests in the repository. Every number the benchmark
produces is a count of verdicts, so a verdict function that is wrong does not
produce a wrong number, it produces a confident wrong number. The Google cases
below are built from bodies actually observed on 2026-08-10 and 2026-08-11.
"""
import pytest

from nmbench import warm
from nmbench.targets import NEEDS_SCRIPT, TARGETS, VERDICTS, fingerprint

GOOGLE = TARGETS["google_serp"]
BING = TARGETS["bing_serp"]
DDG = TARGETS["ddg_serp"]
IPINFO = TARGETS["ipinfo"]
AMAZON = TARGETS["amazon_search"]
WALMART = TARGETS["walmart_search"]
MAPS = TARGETS["google_maps"]
LAZADA = TARGETS["lazada_search"]
SHOPEE = TARGETS["shopee_search"]

# Trimmed from `marketplace_recon_20260903T234200Z`, 2 of 2 served Maps bodies,
# 451-452 KB each. The `noscript` pair is kept verbatim because it is the whole
# point of the fixture: `google_serp`'s classifier would read this rendered feed
# as `empty`, and `GoogleMapsSearch` must not.
MAPS_FEED = """<!doctype html><html><head>
<title>dentist in Austin Texas - Google Maps</title></head><body>
<noscript>Google Maps can't load properly because JavaScript is turned off.
</noscript><noscript><style>.gb_P{display:none}</style></noscript>
<div class="m6QErb WNBkOb XiKgde" role="main">
<div class="m6QErb DxyBCb kA9KIf dS8AEf XiKgde ecceSd"
     aria-label="Results for dentist in Austin Texas" role="feed" tabindex="-1">
<div class="Nv2PK"><a class="hfpxzc" href="https://www.google.com/maps/place/
Austin+Dental/data=!3m1!4b1"></a></div>
</div></div></body></html>"""

# The `sufei-punish` wall, trimmed from both Lazada bodies. 10 187 and 10 143
# bytes, HTTP **200**, empty title, and nothing in it but the iframe.
LAZADA_PUNISH = """<html><head><meta charset="utf-8">
<script charset="utf-8" async=""
 src="//g.alicdn.com/bsop-static/sufei-punish/0.1.127/build/htmltocanvas.min.js"
 crossorigin=""></script></head><body>
<iframe src="//www.lazada.sg:443/catalog/_____tmd_____/punish?recaptcha=1&amp;
iframe=1&amp;x5step=2&amp;x5secdata=xgec2b2723daa5851d" style="border:none;"
 width="100%" height="100%"></iframe>
<div id="bx-pu-qrcode-wrap"><div id="qrcode"></div></div></body></html>"""
LAZADA_PUNISH_URL = ("https://www.lazada.sg//catalog//_____tmd_____/punish"
                     "?x5secdata=xfwRR3EaQYaIAJusu40KluK54h-Q5pSfh3M3O9z7KuRn")

# Trimmed from the first two cards of the 1.78 MB body archived on 2026-09-05 by
# `probehold_20260905T092634Z`, SG exit, HTTP 200. 40 cards were in it. The
# container attribute is kept because it is the near miss: it is present on a
# served page and must not be what the rule reads, or a grid that renders its
# frame and no cards would score as a pass.
LAZADA_SERVED = """<html><head><title>Accurate Spirit Level Tools for Home &amp;
Construction | Lazada Singapore</title></head><body><div id="root">
<div class="_17mcb" data-qa-locator="general-products" data-spm="list">
<div class="Bm3ON" data-qa-locator="product-item" data-tracking="product-card"
 data-item-id="1973401010"><a href="//www.lazada.sg/products/pdp-i1973401010.html"
 >INGCO Spirit Level With Powerful Magnets</a><span>$12.90</span></div>
<div class="Bm3ON" data-qa-locator="product-item" data-tracking="product-card"
 data-item-id="1973401011"><a href="//www.lazada.sg/products/pdp-i1973401011.html"
 >Stanley 600mm Box Beam Level</a><span>$41.00</span></div>
</div></div></body></html>"""
LAZADA_SERVED_URL = ("https://www.lazada.sg/tag/spirit-level/?q=spirit+level"
                     "&catalog_redirect_tag=true")

# The same page snapshotted before the grid rendered: same redirect, same title
# shape, `#root` empty. 62 KB of header and footer, 1.43 MB across the wire,
# 5 774 ms. Lazada served this; the harness read it early.
LAZADA_UNHYDRATED = ("<html><head><title>Shop Mechanical Keyboard Compact at "
                     "Better Price Online | Lazada Singapore</title></head>"
                     "<body><div id=\"root\"></div><div>CUSTOMER CARE Lazada "
                     "Help Center Track my order</div></body></html>")

# All 144 bytes of it, both attempts, byte-identical. There is no Shopee in it.
SHOPEE_403 = ("<html><head><title>403 Forbidden</title></head>\n<body>\n"
              "<center><h1>403 Forbidden</h1></center>\n"
              "<hr><center>nginx</center>\n\n\n\n\n\n\n\n\n</body></html>")

# The other thing Shopee does, and the reason the docstring's old heading was
# wrong: 269 KB of ordinary shell over HTTP 200, whose entire visible text is
# "Skip to main content". `probehold_20260905T092634Z`, 2 of 2 SG exits, the two
# bodies 269 573 and 269 569 bytes for two different queries.
SHOPEE_SHELL = ("<html><head><title>Shopee Singapore | Cheaper, Faster On "
                "Shopee</title></head><body><div id=\"main\">"
                "<a href=\"#main\">Skip to main content</a>"
                "<style id=\"nebula-style\">:root{--nc-primary:#ee4d2d}</style>"
                "<script>window.__ASSETS__={\"pcmall-captcha\":\"https://"
                "deo.shopeemobile.com/shopee/stm-sg-live/51944/a.json\","
                "\"msg_captcha_empty_error\":\"Captcha cannot be empty\"};"
                "</script></div></body></html>")

# Shopee's traffic check, trimmed from `marketplace_recon_20260905T104017Z`,
# 2 of 3 queries on one SG exit. It renders *inside* the same shell, so the
# "Skip to main content" line is here too - which is the whole reason the wall
# rule has to be tested before the shell rule, and the reason this fixture keeps
# that line rather than trimming it away as noise.
SHOPEE_TRAFFIC_WALL = SHOPEE_SHELL.replace(
    "</div></body></html>",
    "<div class=\"BsK01h\">Login Required</div>"
    "<div class=\"ldaYrn\">Looks like you’re not logged in yet. Log in to "
    "continue or head back to the homepage.</div>"
    "<div><button class=\"O1y698\">Log In</button>"
    "<button class=\"a6sTP7\">Back to Home Page</button></div>"
    "<div>ID: 799afa71194-3de8-451d-ba01-b21c4909b64c</div>"
    "</div></body></html>")
SHOPEE_TRAFFIC_WALL_URL = ("https://shopee.sg/verify/traffic/error?home_url="
                           "https%3A%2F%2Fshopee.sg&is_logged_in=false&next="
                           "https%3A%2F%2Fshopee.sg%2Fsearch%3Fkeyword%3D"
                           "wireless%2Bearbuds&tracking_id=799afa71194")

# The first Shopee page that has ever rendered here, trimmed from
# `marketplace_recon_20260905T113703Z` position 00002: the front page, 659 029
# bytes, 12 787 visible characters, and the only one of that run's six that
# stayed on `/` rather than being sent to `/verify/traffic/error`. It is built
# off `SHOPEE_SHELL` for the same reason the wall is - it carries "Skip to main
# content" too, and that is exactly what made the shell rule call it a shell.
#
# The `<form>` and the `<input>` are copied from the archived body rather than
# written to taste, because the same class is what `ShopeeSearch.judge` now
# reads and what a future `search_box` on this target would use. A fixture that
# paraphrases the markup it is calibrating would let the rule pass here and
# fail on the site.
SHOPEE_RENDERED = SHOPEE_SHELL.replace(
    "</div></body></html>",
    "<form role=\"search\" autocomplete=\"off\" class=\"shopee-searchbar\" "
    "action=\"/search\">"
    "<input aria-label=\"Register now &amp; get $12 off voucher!\" "
    "type=\"search\" class=\"shopee-searchbar-input__input\" maxlength=\"128\" "
    "placeholder=\"Register now &amp; get $12 off voucher!\" "
    "autocomplete=\"off\" role=\"combobox\"></form>"
    "</div></body></html>")

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

    def test_the_goto_redirect_changes_no_verdict(self):
        """Google wrapped every result destination in `/goto?url=<protobuf>`
        between 2026-08-26 15:27 and 2026-08-27 20:11 UTC, measured off our own
        archived bodies. `judge` reads nine structural strings and not one of
        them is a destination href, so a served page is served whichever form
        its links take - but the marker was added to `FINGERPRINT_MARKERS` in
        the same commit, and a marker is one careless edit away from becoming a
        rule. This pins the separation rather than the current rule list.
        """
        wrapped = RESULTS.replace("</body>",
                                  '<a href="/goto?url=CAESjgEB6zswFfY0Zi8">x</a>'
                                  "</body>")
        assert GOOGLE.judge("https://www.google.com/search?q=x", "", wrapped
                            ).verdict == "ok"
        assert fingerprint("https://www.google.com/search?q=x",
                           wrapped)["markers"]["/goto?url="] == 1

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


class TestGoogleMaps:
    """Both sides measured: the served side from
    `marketplace_recon_20260903T234200Z` (2 of 2, HTTP 200, 451-452 KB), the
    refused side inherited from `google_serp`, where 4550 of 4550 captcha rows
    carried `/sorry/`."""

    def test_the_feed_is_ok(self):
        assert MAPS.judge("https://www.google.com/maps/search/x", "",
                          MAPS_FEED).verdict == "ok"

    def test_the_google_serp_classifier_would_have_got_this_wrong(self):
        """The reason this target exists as its own class rather than as a URL
        change on `google_serp`. Both served bodies carry `noscript` twice and
        carry `id="rso"`, `id="search"` and `<h3>` zero times, so the older
        classifier reads a rendered feed as our own client coming up short."""
        assert GOOGLE.judge("https://www.google.com/maps/search/x", "",
                            MAPS_FEED).verdict == "empty"
        assert MAPS.judge("https://www.google.com/maps/search/x", "",
                          MAPS_FEED).verdict == "ok"

    def test_the_scaffold_rule_runs_after_the_feed_rule(self):
        """Same ordering trap as Walmart's dormant modal, arrived at from the
        other direction: here it is the *served* page that carries the
        challenge-looking markup, and it carries it on every single row."""
        assert "noscript" in MAPS_FEED
        assert MAPS.judge("https://www.google.com/maps/search/x", "",
                          MAPS_FEED).verdict == "ok"

    def test_the_scaffold_without_a_feed_is_still_empty(self):
        judgement = MAPS.judge("https://www.google.com/maps/search/x", "",
                               "<html><body><noscript>turn on JavaScript"
                               "</noscript></body></html>")
        assert judgement.verdict == "empty"

    def test_sorry_is_a_captcha(self):
        assert MAPS.judge("https://www.google.com/sorry/index?continue=maps",
                          "", SORRY).verdict == "captcha"

    def test_a_single_place_panel_is_ok(self):
        """Not observed in either recon body - both queries were plural and both
        got a feed. The rule is here because the failure it prevents is the one
        this repository cannot absorb: scoring a page Maps served as a refusal.
        Keyed on the redirect, which is the certain part."""
        judgement = MAPS.judge(
            "https://www.google.com/maps/place/Austin+Dental/@30.2,-97.7,17z",
            "", "<html><body>place panel</body></html>")
        assert judgement.verdict == "ok"

    def test_class_names_are_not_what_the_verdict_rests_on(self):
        """`Nv2PK` and `hfpxzc` are obfuscated and Google rotates them - this
        repository has already had to date one such rotation from the rows. A
        feed that survives a rotation must still read as `ok`."""
        rotated = MAPS_FEED.replace("Nv2PK", "aB3xQ").replace("hfpxzc", "zZ9kL")
        assert MAPS.judge("https://www.google.com/maps/search/x", "",
                          rotated).verdict == "ok"

    def test_empty_body(self):
        assert MAPS.judge("https://www.google.com/maps/search/x", "",
                          "").verdict == "empty"

    def test_it_asks_for_places_and_not_products(self):
        """A product query returns a feed with nothing in it, and an empty feed
        cannot be told from a refused one after the fact. Amazon at least says
        `s-no-results`; Maps says nothing."""
        assert MAPS.query_list == "places_1000"
        assert all(" in " in q for q in MAPS.queries)


class TestLazada:
    """Calibrated on both sides as of 2026-09-05. It was refusals only until
    then - 2 of 2 direct attempts on 2026-09-03 were the `sufei-punish` wall -
    and `probehold_20260905T092634Z` supplied the served side from SG exits."""

    def test_the_wall_arrives_as_200_and_the_status_is_not_consulted(self):
        """The finding that makes this target worth having. Both refusals were
        HTTP 200, so a harness that scored on status would have recorded two
        passes."""
        judgement = LAZADA.judge(LAZADA_PUNISH_URL, "", LAZADA_PUNISH)
        assert judgement.verdict == "captcha"
        assert "punish" in judgement.reason

    def test_the_redirect_alone_is_enough(self):
        """The URL test is first because a redirect target is not markup that
        can be restyled."""
        assert LAZADA.judge(LAZADA_PUNISH_URL, "",
                            "<html><body></body></html>").verdict == "captcha"

    def test_the_wall_served_inline_is_still_a_captcha(self):
        """The second disjunct, for a wall that does not redirect. Never
        observed; it is the cheap half of the rule."""
        assert LAZADA.judge("https://www.lazada.sg/catalog/?q=x", "",
                            LAZADA_PUNISH).verdict == "captcha"

    def test_the_catalogue_grid_is_a_pass(self):
        """The rule the 2026-09-05 run bought. The marker is the per-card
        `data-qa-locator="product-item"`, read out of an archived body."""
        judgement = LAZADA.judge(LAZADA_SERVED_URL, "", LAZADA_SERVED)
        assert judgement.verdict == "ok"

    def test_the_container_alone_is_not_a_pass(self):
        """The near miss, and the reason the rule reads the card rather than the
        grid frame: a page that renders `general-products` and no cards inside
        it has not served a result."""
        frame_only = LAZADA_SERVED[:LAZADA_SERVED.index("Bm3ON")] + "</div></body>"
        assert 'data-qa-locator="general-products"' in frame_only
        assert LAZADA.judge(LAZADA_SERVED_URL, "", frame_only).verdict == "block"

    def test_the_wall_wins_over_the_grid(self):
        """Rule order, pinned. A punish document carries no cards, so today the
        order cannot change a verdict; if Lazada ever serves the challenge over
        a rendered grid, the refusal is the honest reading and this test is what
        stops a reorder from turning it into a pass."""
        both = LAZADA_SERVED + LAZADA_PUNISH
        assert LAZADA.judge(LAZADA_SERVED_URL, "", both).verdict == "captcha"

    def test_an_early_snapshot_is_not_reported_as_a_refusal(self):
        """The confound the same run exposed: 2 of 2 attempts were served and
        only 1 of 2 had the grid in the DOM. The verdict is still `block`,
        because the body genuinely has no result in it, but the reason must send
        the reader to the `ready` column instead of letting the row be counted
        as a Lazada refusal."""
        judgement = LAZADA.judge(LAZADA_SERVED_URL, "", LAZADA_UNHYDRATED)
        assert judgement.verdict == "block"
        assert "ready" in judgement.reason

    def test_it_shares_the_shop_query_list(self):
        assert LAZADA.query_list == AMAZON.query_list == WALMART.query_list

    def test_the_selector_and_the_rule_read_the_same_string(self):
        """They were added in one edit for one reason: a wait that succeeds on
        markup the judge does not read, or the other way round, is a target that
        reports its own timing as the site's behaviour."""
        assert LAZADA.ready_selector == "[data-qa-locator='product-item']"
        assert LAZADA.judge(LAZADA_SERVED_URL, "", LAZADA_SERVED).verdict == "ok"


class TestShopee:
    """Still no measured pass, and now three measured non-passes. Two of them
    are opposite answers about the edge: 144 bytes of stock nginx from this
    workstation, and 269 KB of ordinary shell from SG exits on 2026-09-05. The
    third is Shopee refusing in its own words at `/verify/traffic/error`, found
    later the same day and archived 5 times.

    The wall renders inside the shell, so the two share their only visible
    line. Ordering is therefore load-bearing here in the same way it is for
    Walmart's dormant challenge, and it is asserted rather than assumed."""

    def test_the_nginx_403_is_a_block(self):
        judgement = SHOPEE.judge("https://shopee.sg/search?keyword=x", "",
                                 SHOPEE_403)
        assert judgement.verdict == "block"
        assert "nginx" in judgement.reason

    def test_the_cause_is_not_named(self):
        """From a host behind a VPN gateway asking a Singapore storefront, an
        address refusal and a bot refusal produce the identical 144 bytes.
        Calling this Shopee's bot defence would be a claim the body cannot
        support."""
        reason = SHOPEE.judge("https://shopee.sg/search?keyword=x", "",
                              SHOPEE_403).reason
        assert "captcha" not in reason and "bot" not in reason

    def test_both_markers_are_required(self):
        """`nginx` alone matches anything that mentions it and `403 forbidden`
        alone matches a storefront's own error styling. Together they are the
        stock error document."""
        judgement = SHOPEE.judge("https://shopee.sg/search?keyword=x", "",
                                 "<html><body>403 Forbidden</body></html>")
        assert "no served marker" in judgement.reason

    def test_the_shell_and_the_403_are_told_apart(self):
        """One verdict, two reasons, and the split is the point. Both are
        `block` because neither carries a result, but the 403 is the edge
        refusing and the shell is the edge letting us through - a report that
        merged them could not show the SG exits doing better than this host."""
        shell = SHOPEE.judge("https://shopee.sg/search?keyword=x", "",
                             SHOPEE_SHELL)
        wall = SHOPEE.judge("https://shopee.sg/search?keyword=x", "",
                            SHOPEE_403)
        assert shell.verdict == wall.verdict == "block"
        assert shell.reason != wall.reason
        assert "through" in shell.reason and "nginx" in wall.reason

    def test_the_shells_own_i18n_strings_are_not_read_as_a_challenge(self):
        """`captcha`, `blocked` and `denied` all occur in the shell, in its
        asset manifest and its message bundle. A rule that grepped for them
        would call an ordinary storefront a challenge."""
        assert "captcha" in SHOPEE_SHELL.lower()
        assert SHOPEE.judge("https://shopee.sg/search?keyword=x", "",
                            SHOPEE_SHELL).verdict != "captcha"

    def test_the_traffic_check_is_named(self):
        """The one refusal Shopee states itself, so it is the one that must not
        be reported as "read the body"."""
        judgement = SHOPEE.judge(SHOPEE_TRAFFIC_WALL_URL, "",
                                 SHOPEE_TRAFFIC_WALL)
        assert judgement.verdict == "block"
        assert "traffic check" in judgement.reason

    def test_the_wall_is_not_swallowed_by_the_shell_rule(self):
        """The wall renders inside the shell and carries "Skip to main
        content" too - 5 of the 8 archived bodies holding that string are
        walls. Reversing the two rules loses the wall silently, which is why
        this asserts on the fixture as well as on the verdict."""
        assert "skip to main content" in SHOPEE_TRAFFIC_WALL.lower()
        wall = SHOPEE.judge(SHOPEE_TRAFFIC_WALL_URL, "", SHOPEE_TRAFFIC_WALL)
        shell = SHOPEE.judge("https://shopee.sg/search?keyword=x", "",
                             SHOPEE_SHELL)
        assert wall.reason != shell.reason
        assert "shell" in shell.reason and "shell" not in wall.reason

    def test_the_wall_is_read_off_the_body_and_not_the_url(self):
        """Both rules here were calibrated by replaying `judge` over
        `data/artifacts/`, where there is no URL to read. A URL-only rule would
        pass its own tests and score nothing on that replay."""
        judgement = SHOPEE.judge("", "", SHOPEE_TRAFFIC_WALL)
        assert "traffic check" in judgement.reason

    def test_the_login_copy_is_not_read_as_a_pass_or_a_consent(self):
        """It offers a login button, which is neither a result nor a cookie
        banner. The verdict has to stay a refusal."""
        assert SHOPEE.judge(SHOPEE_TRAFFIC_WALL_URL, "",
                            SHOPEE_TRAFFIC_WALL).verdict == "block"

    def test_a_rendered_page_is_not_called_an_unfilled_shell(self):
        """The regression from `marketplace_recon_20260905T113703Z`.

        The shell rule was `skip to main content` alone, and the first Shopee
        body that ever rendered carries that string, so replaying `judge` over
        it returned "served and never filled" about a page that had filled.
        The fixture asserts the trap is still in it: without the first line
        this test would pass against a rule that had simply stopped matching
        the shell.
        """
        assert "skip to main content" in SHOPEE_RENDERED.lower()
        judgement = SHOPEE.judge("https://shopee.sg/", "", SHOPEE_RENDERED)
        assert "never filled" not in judgement.reason
        assert "read the body" in judgement.reason

    def test_the_shell_rule_still_fires_without_the_search_bar(self):
        """The other half of the same rule. A discriminator that stopped the
        false positive by never matching anything would pass the test above."""
        judgement = SHOPEE.judge("https://shopee.sg/search?keyword=x", "",
                                 SHOPEE_SHELL)
        assert "never filled" in judgement.reason

    def test_a_rendered_page_is_still_not_a_pass(self):
        """One served body, and it is a front page rather than a result page.
        That is not enough to write an `ok` marker, so a rendered page has to
        keep landing on the reason that says to read the body."""
        assert SHOPEE.judge("https://shopee.sg/", "",
                            SHOPEE_RENDERED).verdict == "block"

    def test_a_served_grid_has_no_rule_and_the_reason_says_so(self):
        """The pinned gap, and unlike Lazada's it is still open: no Shopee
        result grid has been archived, so no `ok` marker can be written without
        reading it off shopee.sg. Delete when a grid is archived."""
        judgement = SHOPEE.judge("https://shopee.sg/search?keyword=air+fryer",
                                 "", "<html><body>a real grid</body></html>")
        assert judgement.verdict == "block"
        assert "no served marker" in judgement.reason

    def test_no_selector_is_invented(self):
        """A guessed selector makes every attempt wait out its timeout and then
        record False for a page that was fine. Lazada's was taken out of an
        archived body on 2026-09-05; Shopee has no such body."""
        assert SHOPEE.ready_selector is None


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
        preset cannot be mistaken for the target's refusal.

        Maps is measured: it is an application and builds the feed in the
        browser. Lazada and Shopee are the Walmart case again - no scriptless
        client has ever been served by either, so the unknown is resolved the
        conservative way."""
        assert NEEDS_SCRIPT == {"google_serp", "walmart_search", "google_maps",
                                "lazada_search", "shopee_search"}


class TestWarmActions:
    """What a target declares for `--warm-interact on`.

    Here rather than beside the probe's tests because the declaration is the
    target's: the probe must not know a domain, and a CSS selector is a stronger
    form of that knowledge than a URL is. What the probe checks is that the arm
    can run at all; what is checked here is that the declaration is about pages
    the target actually visits.
    """

    @pytest.mark.parametrize("name", sorted(TARGETS))
    def test_the_shape_is_one_the_arm_can_run(self, name):
        """`warm.problems` is what preflight refuses on, so a target that would
        be refused should fail here and not on the night of the run."""
        assert warm.problems(TARGETS[name]) == []

    @pytest.mark.parametrize("name", sorted(TARGETS))
    def test_every_declared_page_is_one_some_rung_visits(self, name):
        """A URL declared here and in no rung is silently no interaction.

        This is the failure this repository has recorded four times in another
        costume - an arm labelled `on` that behaves exactly like `off`, with
        nothing in the output saying so. One character wrong in a query string
        is enough: `?hl=en` against `?hl=en&` would leave preflight's
        rung-intersection check to catch it, and preflight only sees the rungs
        that were actually selected, so a run of a different rung would pass and
        do nothing.
        """
        target = TARGETS[name]
        declared = warm.pages_with_actions(target)
        if not declared:
            return
        visited = set()
        for _, pages in getattr(target, "warm_ladder", ()):
            visited.update(pages)
        visited.add(getattr(target, "home_url", None))
        assert declared <= visited, (
            f"{name} declares warm_actions for {sorted(declared - visited)}, "
            f"which no rung of its own ladder visits")


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
