"""Proxy string construction, in whichever dialect the provider speaks.

Gateways take their settings inside the proxy username, and every vendor spells
them differently - its own separators, its own parameter names, its own
punctuation. All of that lives in `data/providers/*.toml` and is read by
`nmbench.providers`; this module is the one place that assembles a username out
of it, so nothing downstream has to know which provider it is talking to.

    <prefix><separator><name><pair_separator><value>...
    acct-country-us-sid-abc123-ttl-10m

Client-side validation is not politeness, it is the only check available. The one
gateway measured here answers an unrecognised parameter name with 200 and the
setting silently dropped: the connection succeeds, the run completes, and every
row claims a setting that was never applied. Nothing the gateway replies can tell
you the name was wrong, so a name outside the provider's `known_params` is
refused before a request exists.
"""
from urllib.parse import quote

from . import config, providers


class ParamError(ValueError):
    """Raised for input the gateway would silently ignore or hang on."""


def build_username(login: str, strict: bool = True, provider=None,
                   **params) -> str:
    provider = provider or providers.load()
    parts = [provider.prefix.format(login=login)]
    for key, value in params.items():
        if strict and key not in provider.known_params:
            raise ParamError(
                f"unknown parameter {key!r} for {provider.label}: the gateway "
                f"ignores it silently, your settings will NOT be applied. "
                f"Known: {sorted(provider.known_params)}"
            )
        if value is None or value == "":
            raise ParamError(f"empty value for {key!r}: the gateway hangs on this")
        if isinstance(value, bool):
            value = "true" if value else "false"
        parts.append(f"{provider.spell(key)}{provider.pair_separator}{value}")
    return provider.separator.join(parts)


def parse_params(items, flag: str = "--param", provider=None) -> dict:
    """`["filter=medium", "ttl=10m"]` as a validated dict.

    Both halves in one call, because separating them is what produced three
    byte-identical copies of the split - in `benchmark.py`, `gateway_health.py`
    and `probe_and_hold.py` - each followed by its own throwaway
    `build_username` to validate what it had just split. The two steps are one
    operation: a key that survives the split and not the validation is a setting
    the operator believes is applied and is not.

    Validation is done by building a username and discarding it, which is the
    only honest check available. The gateway answers an unknown parameter name
    with 200 and the setting silently dropped, so nothing it replies can tell you
    the name was wrong - the check has to happen here or not at all.

    `flag` names the option in the message rather than being hardcoded, so this
    can serve an option called something else without telling the operator to
    look at a flag they did not use.
    """
    params = {}
    for item in items or ():
        key, sep, value = item.partition("=")
        if not sep:
            raise ParamError(
                f"{flag} {item!r} is not KEY=VALUE, so nothing was set. The "
                f"gateway would have accepted the connection anyway and run it "
                f"on whatever the default is")
        params[key.strip()] = value.strip()
    if params:
        # The login is irrelevant to what is being checked and is never sent.
        build_username("check", provider=provider, **params)
    return params


def session_params(session_id: str, provider=None, **params) -> dict:
    """Gateway parameters for one sticky session, keyed by the provider's own
    name for it.

    Here rather than spelled out at each call site because eleven of them wrote
    `"sid"` directly. That is the canonical name a definition will usually pick,
    and `aliases` already covers a gateway that merely spells it differently on
    the wire - so the literal is right far more often than not, which is why it
    survived eleven copies.

    It is wrong for a definition that gives the parameter another canonical name,
    and the two failure modes are not equally visible. Under `strict` the literal
    is refused by `build_username` before anything is sent, so the run stops and
    says why - loud, and merely a harness that will not run. With `strict=False`,
    which the health probe uses deliberately so it can send names the gateway may
    not know, it goes out and is answered with 200 and the setting dropped: every
    query then draws a fresh exit while each row records one held session. That
    is the invisible one, and holding an exit is the whole of the probe-and-hold
    method.

    A provider that declares no session parameter gets none, and the session id
    is dropped rather than sent under a guessed name. That is the shape of a
    proxy somebody already owns: one endpoint with one exit behind it, where the
    session is the connection and there is nothing to ask for. Sending `sid` to
    it anyway would either be refused - loud, and a harness that will not run for
    no reason - or accepted and ignored, which is the failure this module exists
    to prevent. The honest consequence is that every attempt through such a
    gateway leaves from the same address, and the row says so by carrying no
    session at all.
    """
    provider = provider or providers.load()
    if not provider.session_param:
        return dict(params)
    return {**params, provider.session_param: session_id}


def proxy_dict(provider=None, strict: bool = True, **params) -> dict:
    """Playwright-style proxy config."""
    provider = provider or providers.load()
    creds = config.credentials(provider)
    return {
        "server": f"http://{creds.gateway}",
        "username": build_username(creds.login, strict=strict, provider=provider,
                                   **params),
        "password": creds.password,
    }


def proxy_url(provider=None, strict: bool = True, **params) -> str:
    """requests / curl style proxy URL.

    Credentials are percent-encoded. A password containing `@` or `:` produces a
    URL that some clients parse into a different host entirely, and at least one
    of the engines we drive answers a proxy URL it cannot parse by connecting
    directly without saying so - which would put the operator's own address into
    a row labelled as a pool exit.
    """
    provider = provider or providers.load()
    creds = config.credentials(provider)
    username = build_username(creds.login, strict=strict, provider=provider,
                              **params)
    return (f"http://{quote(username, safe='')}:{quote(creds.password, safe='')}"
            f"@{creds.gateway}")
