"""Plain HTTP client. No browser, no JavaScript. The cheap baseline.

Worth keeping in the matrix even though it fails the JavaScript targets: it is
the control that says whether a refusal needed a browser to provoke. When this
engine is refused and a browser is not, the address is the tell; when the
reverse happens, the browser is.
"""
import time
from contextlib import contextmanager

import requests

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
# the refusal would read as the scriptless client failing. Nothing here has
# measured whether any target does. If it is ever bumped, bump it in a commit
# that runs both values in one window, the way the geo axis was added.
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
    supports_humanize = False
    runs_script = False
    # No box to type into either. A client could post the form by hand, and that
    # would be a third entry shape rather than this one: no keystrokes, no
    # focus, no rendered page. Refused rather than approximated, because the
    # entry axis is only readable if every column in it did the same thing.
    supports_typing = False
    # Sends its own Proxy-Authorization, so a relay would add a loopback hop and
    # buy nothing. See `nmbench.relay` for what that hop costs.
    needs_relay = False

    @classmethod
    def check(cls) -> str:
        return None

    @classmethod
    def version(cls) -> str:
        return requests.__version__

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
