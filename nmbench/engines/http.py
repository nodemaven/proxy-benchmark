"""Plain HTTP client. No browser, no JavaScript. The cheap baseline.

Worth keeping in the matrix even though it fails the JavaScript targets: it is
the control that says whether a refusal needed a browser to provoke. When this
engine is refused and a browser is not, the address is the tell; when the
reverse happens, the browser is.
"""
import ssl
import time
from contextlib import contextmanager

import requests
import urllib3

from .. import providers, proxy
from .base import blank_row, keep_body, record_error, record_judgement


def supported_encodings() -> str:
    """Advertise only what this installation can actually decode.

    Asking for brotli without the decoder present is worse than not asking:
    the server compresses, requests hands back raw bytes, and a content-based
    verdict reads a perfectly good page as a block. Measured on 2026-08-10,
    that misread 43 KB Google result pages as blocks across a whole run.
    """
    encodings = ["gzip", "deflate"]
    try:
        import brotli  # noqa: F401
        encodings.append("br")
    except ImportError:
        try:
            import brotlicffi  # noqa: F401
            encodings.append("br")
        except ImportError:
            pass
    return ", ".join(encodings)


# A plain client announcing python-requests is refused on the header layer before
# anything else gets a chance to matter. These headers do not disguise the TLS
# handshake, which still has a Python shape - see BROWSER_HEADERS callers.
#
# The Chrome version is a pin, not a current value, and it is 24 releases behind
# the browsers in the matrix: they report 145-151 and this says 127. Recorded and
# deliberately not bumped, on the same grounds as every other pin here - 289 rows
# in `data/runs/` were measured with this exact string, and changing it moves the
# control's identity without moving anything else, so new rows would stop being
# comparable with them for a reason no column records.
#
# It is a known confound rather than a settled choice. This repository's own
# DuckDuckGo finding is that a User-Agent substring alone sorted a table 95/95
# against 0/50, so a target that sorts on staleness would refuse this control and
# the refusal would read as the scriptless client failing.
#
# **That paragraph used to end "nothing here has measured whether any target
# does". It has now been measured, on 2026-08-28, and two do.** These exact
# headers, direct from the Windows workstation, one GET per cell, nothing
# varying but the major version inside this one string:
#
#   www.producthunt.com/search        Chrome/100..130: 403, 16 of 16
#                                     Chrome/131..149: 200, 16 of 16
#                                     The edge is sharp - 130 is 0 of 3 served,
#                                     131 is 3 of 3 - and the served body is
#                                     205 KB against the refusal's 5.9 KB.
#   www.sec.gov/cgi-bin/browse-edgar  Chrome/145: 403, 5 of 5
#                                     Chrome/146..149: 200, 14 of 14
#
# Different vendors and different floors - Cloudflare in front of ProductHunt,
# Akamai Bot Manager in front of sec.gov - so it is a floor each site sets and
# not one product's rule. Three negative controls in the same window did not
# move: opencorporates.com, capterra.com and clutch.co answered 403 at 149, at
# 160 and at an impossible 999. So whatever refuses those is not the version,
# and the effect above is not "any change to the string helps".
#
# What this costs is a reading rather than a number. On a target whose floor is
# above 127 this arm is refused for a reason that has nothing to do with being
# scriptless, and the sentence at the top of this module - "when this engine is
# refused and a browser is not, the address is the tell" - names the wrong
# layer. The Python TLS handshake passes both those sites; one token in one
# header is the whole of it.
#
# The pin still is not bumped, and now for a better reason than inertia: the
# finding is that the version is a treatment, so the answer is an axis and not a
# new constant. Bumping swaps one arbitrary value for another, loses the 289
# rows' comparability and buys nothing. If it is ever bumped, bump it in a
# commit that runs both values in one window, the way the geo axis was added.
# Note also that no row records this string. `engine_version` on this arm names
# the libraries that build the request and the handshake, and that is not the
# same thing as the pin below, so the parameter that decided those 61 attempts
# is still nowhere in `data/runs/`.
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": supported_encodings(),
    "Upgrade-Insecure-Requests": "1",
}


def looks_undecoded(text: str) -> bool:
    """True when the body is compressed bytes rather than markup.

    A verdict derived from undecoded bytes is always the fallback verdict, which
    silently looks like a block. Better to refuse to judge.

    The two constants are a discriminator and not a tuning. Compressed bytes
    decoded as text are replacement characters almost everywhere, so the real
    figure is tens of percent; ordinary markup carries none at all, and the few
    that appear come from a mis-declared charset on a single product title. 2%
    sits in the empty gap between those, not near either. The 4000-character
    sample is the head of the document, which is where the difference is already
    total - a gzip magic number is in the first two bytes - and it keeps the
    check off the megabyte of markup behind it on every attempt that is fine.
    """
    if not text:
        return False
    sample = text[:4000]
    return sample.count("�") / len(sample) > 0.02


class HttpSession:
    def __init__(self, proxies, headers, timeout, direct, params, store=None,
                 provider=None):
        self.client = requests.Session()
        self.store = store
        self.proxies = proxies
        self.headers = headers
        self.timeout = timeout
        self.direct = direct
        self.params = params
        # The provider whose dialect built this session's username, None on the
        # direct arm. On the session so the column reports what was used.
        self.provider = provider
        self.index = 0

    def fetch(self, target, query: str) -> dict:
        url = target.url(query)
        row = blank_row(
            "http-direct" if self.direct else "http",
            requests.__version__, query, url,
            target=getattr(target, "name", None),
            direct=self.direct, preset=None,
            params={} if self.direct else dict(self.params),
            provider=getattr(self.provider, "id", None),
            session_index=self.index,
        )
        self.index += 1

        started = time.perf_counter()
        try:
            resp = self.client.get(url, proxies=self.proxies,
                                   headers=self.headers, timeout=self.timeout)
            row["status"] = resp.status_code
            row["html_len"] = len(resp.text)
            row["bytes"] = len(resp.content)

            if looks_undecoded(resp.text):
                row["verdict"] = "error"
                row["verdict_reason"] = "body could not be decoded"
                row["error"] = (
                    f"body arrived as "
                    f"{resp.headers.get('Content-Encoding')!r} and could not be "
                    f"decoded, so no verdict is possible: a content check on "
                    f"these bytes would report a block for a page that may be "
                    f"fine. Install the matching decoder (pip install brotli) "
                    f"and re-run."
                )
            else:
                record_judgement(row, target, resp.url, "", resp.text)
                keep_body(self.store, row, resp.text)
        except Exception as exc:
            record_error(row, exc)
        row["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        return row

    def close(self) -> None:
        self.client.close()


class HttpEngine:
    name = "http"
    # No browser, so nothing to block, nothing to make headful, no timezone or
    # language list to align with the exit address, and no cursor to humanize.
    supports_blocking = False
    supports_headful = False
    supports_geo_align = False
    supports_geoip = False
    humanize_modes = frozenset({"off"})
    runs_script = False
    # No box to type into either. A client could post the form by hand, and that
    # would be a third entry shape rather than this one: no keystrokes, no
    # focus, no rendered page. Refused rather than approximated, because the
    # entry axis is only readable if every column in it did the same thing.
    supports_typing = False
    # Sends its own Proxy-Authorization, so a relay would add a loopback hop and
    # buy nothing. See `nmbench.relay` for what that hop costs.
    needs_relay = False
    # And this `open` does not handle a relay address either, so one passed to
    # it would be swallowed by `**ignored` and the request would go straight to
    # the gateway. False is what stops a run labelling this arm relayed and then
    # finding nothing in its exit column. See `chromium.ChromiumEngine` for why
    # "needs one" and "can take one" are two questions.
    accepts_relay = False
    # No browser at all. This arm's handshake comes from the OpenSSL the
    # interpreter was linked against, which is what `version` reports, and no
    # browser binary can move it.
    supports_chrome_binary = False

    @classmethod
    def check(cls) -> str:
        return None

    @classmethod
    def version(cls) -> str:
        """`requests`, and the two libraries under it that decide the handshake.

        `requests` does not choose a cipher list. urllib3 builds the SSL context
        and Python's `ssl` module hands it whatever OpenSSL the interpreter was
        linked against, so those two are what a JA4 on this arm is a property
        of - and until 2026-09-02 neither was written down anywhere.

        What that cost: `tls_echo_20260901T112110Z.jsonl` and
        `..113512Z.jsonl`, 14 minutes apart on one host, record this arm at 18
        ciphers and then 31, with the extension hash unchanged and this string
        reading `2.34.2` in both. 2026-09-02 reads 18 again, so the middle run
        is the outlier - but with only `requests.__version__` on the row there
        is no way to say what moved, and the question is still open in
        NOTEBOOK.md for exactly that reason. The comment on `BROWSER_HEADERS`
        above names the same gap for the pinned Chrome version.

        Two libraries and not one because they fail independently: a `pip`
        operation can move urllib3 without touching the interpreter, and a
        different interpreter can carry a different OpenSSL with urllib3
        unchanged.

        Deliberately not a shared column. On every browser engine the handshake
        comes from BoringSSL or NSS compiled into the browser, and `curl_cffi`
        links its own; a column filled from this process would name Python's
        OpenSSL on rows whose ClientHello it had nothing to do with. It is only
        true here, so it is only recorded here.

        This changes the string's format, so rows before today read `2.34.2`
        bare. That is honest: those rows really did not record the stack, and
        the discontinuity is where the column started meaning more. Every other
        engine already formats this field as slash-separated parts.
        """
        # Two tokens, so "OpenSSL 1.1.1q  5 Jul 2022" loses a build date that
        # the version already implies. Works unchanged for a LibreSSL build,
        # which formats the same way.
        stack = " ".join(ssl.OPENSSL_VERSION.split()[:2])
        return f"{requests.__version__} / urllib3 {urllib3.__version__} / {stack}"

    @contextmanager
    def open(self, *, direct: bool = False, params: dict = None,
             timeout: int = 45, headers: dict = None, store=None,
             provider=None, **ignored):
        params = params or {}
        provider = None if direct else (provider or providers.load())
        if direct:
            proxies = None
        else:
            url = proxy.proxy_url(provider=provider, **params)
            proxies = {"http": url, "https": url}
        session = HttpSession(proxies, headers or BROWSER_HEADERS, timeout,
                              direct, params, store=store, provider=provider)
        try:
            yield session
        finally:
            session.close()
