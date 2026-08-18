"""A scriptless client wearing Chrome's handshake. The control for the control.

This is not an anti-detect browser and it is not trying to be one. It exists to
split a question the matrix currently cannot split.

`http` and the browser engines differ in two ways at once - one has no browser
and one has a Python-shaped ClientHello - so every result that separates them is
attributable to either. Amazon is where that bites: a plain `requests` client is
served 48% of the time through the pool while the Chromium-family engines sit at
9-15%, and `tls_echo.py` has already shown the handshake is not what sorts the
engines. Both readings survive, and they say opposite things about what to buy.

This engine holds the browser absent and moves the handshake to Chrome's.
Against `http` it changes only the ClientHello; against `cloak` it changes only
the browser, because cloakbrowser ships Chromium 146 and the default profile
here is `chrome146`. Two edges of one triangle, and the target's answer names
which of them it is reading.

**The headers are not ours and must not be.** `http` sets `BROWSER_HEADERS`
because a client announcing `python-requests` is refused on the header layer
before anything else matters. Here the impersonation profile brings its own
header set, ordered and cased the way the real browser sends it, and the profile
is chosen so that the headers and the ClientHello come from the same browser.
Overriding them would emit a Chrome handshake carrying our header list - a
combination no real Chrome produces, and a mismatch that is itself a stronger
signal than either half. Nothing in this repository passes headers to this
engine; the parameter exists so a probe asking a header question can, and what
it gets is a merge over the profile's set rather than a replacement of it.

**The profile is pinned and recorded.** `impersonate="chrome"` follows whatever
curl_cffi considers current, so an upgrade would silently move the handshake
under rows that all read `curlcffi` and could not be told apart afterwards. The
version is in the row's engine version string for the same reason the Chromium
channel is in the engine label.

What this cannot do: no JavaScript, so it fails every target whose markup is
assembled in the browser, and it will read `empty` on those rather than `block`.
That is the same limit `http` has and it is the point - a refusal that needs a
browser to provoke is a finding, and a scaffold this client cannot render is
not.
"""
import time
from contextlib import contextmanager

from .. import providers, proxy
from .base import blank_row, keep_body, record_error, record_judgement
from .http import looks_undecoded

# Pinned rather than "chrome". Paired with cloakbrowser's Chromium 146 so the
# two engines differ in exactly one thing: whether a browser is present.
IMPERSONATE = "chrome146"


class CurlCffiSession:
    def __init__(self, client, *, proxies, timeout, direct, params,
                 extra_headers=None, version, store=None, provider=None):
        self.client = client
        self.store = store
        self.proxies = proxies
        self.timeout = timeout
        self.direct = direct
        self.params = params
        # The provider whose dialect built this session's username, None on the
        # direct arm. On the session so the column reports what was used.
        self.provider = provider
        self.extra_headers = extra_headers
        self.version = version
        self.index = 0

    def fetch(self, target, query: str) -> dict:
        url = target.url(query)
        row = blank_row(
            "curlcffi-direct" if self.direct else "curlcffi",
            self.version, query, url,
            target=getattr(target, "name", None),
            direct=self.direct, preset=None,
            params={} if self.direct else dict(self.params),
            provider=getattr(self.provider, "id", None),
            headless=True, humanize=False,
            session_index=self.index,
        )
        self.index += 1

        started = time.perf_counter()
        try:
            resp = self.client.get(url, proxies=self.proxies,
                                   timeout=self.timeout,
                                   headers=self.extra_headers or None)
            row["status"] = resp.status_code
            text = resp.text
            row["html_len"] = len(text)
            row["bytes"] = len(resp.content)

            if looks_undecoded(text):
                row["verdict"] = "error"
                row["verdict_reason"] = "body could not be decoded"
                row["error"] = (
                    f"body arrived as "
                    f"{resp.headers.get('Content-Encoding')!r} and could not be "
                    f"decoded, so no verdict is possible: a content check on "
                    f"these bytes would report a block for a page that may be "
                    f"fine."
                )
            else:
                record_judgement(row, target, str(resp.url), "", text)
                keep_body(self.store, row, text)
        except Exception as exc:
            record_error(row, exc)
        row["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        return row

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:
            pass


class CurlCffiEngine:
    name = "curlcffi"
    # No browser: nothing to block, no window, no timezone to align, no cursor.
    supports_blocking = False
    supports_headful = False
    supports_geo_align = False
    supports_humanize = False
    runs_script = False
    # No box to type into. See `HttpEngine` for why posting the form by hand
    # would be a third entry shape rather than this one.
    supports_typing = False
    # Sends its own Proxy-Authorization, so a relay would add a loopback hop and
    # buy nothing. See `nmbench.relay` for what that hop costs.
    needs_relay = False

    @classmethod
    def check(cls) -> str:
        try:
            import curl_cffi  # noqa: F401
        except ImportError:
            return ("curl_cffi is not installed, so this engine cannot run: "
                    "pip install curl_cffi")
        # A profile this build does not carry is the failure that matters. It
        # raises at request time, one attempt at a time, and every row would
        # read `error` against a target that was never asked.
        try:
            import typing

            from curl_cffi.requests.impersonate import BrowserTypeLiteral
            known = set(typing.get_args(BrowserTypeLiteral))
        except Exception:
            return None
        if known and IMPERSONATE not in known:
            return (f"curl_cffi {cls._version()} does not carry the "
                    f"{IMPERSONATE!r} profile, so this engine would emit a "
                    f"handshake nobody chose. Pick a profile it does carry and "
                    f"change IMPERSONATE, so the rows say which one they used.")
        return None

    @staticmethod
    def _version() -> str:
        import curl_cffi
        return getattr(curl_cffi, "__version__", "unknown")

    @classmethod
    def version(cls) -> str:
        return f"curl_cffi {cls._version()} / {IMPERSONATE}"

    @contextmanager
    def open(self, *, direct: bool = False, params: dict = None,
             timeout: int = 45, headers: dict = None, store=None,
             provider=None, **ignored):
        unavailable = self.check()
        if unavailable:
            from .base import EngineUnavailable
            raise EngineUnavailable(unavailable)

        from curl_cffi import requests as curl_requests

        params = params or {}
        provider = None if direct else (provider or providers.load())
        if direct:
            proxies = None
        else:
            url = proxy.proxy_url(provider=provider, **params)
            proxies = {"http": url, "https": url}

        client = curl_requests.Session(impersonate=IMPERSONATE)
        session = CurlCffiSession(
            client, proxies=proxies, timeout=timeout, direct=direct,
            params=params, extra_headers=headers,
            version=self.version(), store=store, provider=provider)
        try:
            yield session
        finally:
            session.close()
