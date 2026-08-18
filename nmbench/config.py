"""Environment configuration. Single place where credentials are read.

Values are resolved on first access rather than at import, so the parts of the
package that never touch a gateway - verdicts, the scheduler, the query list -
can be imported and tested on a machine with no `.env` at all. A missing
variable still fails loudly, but it fails when something actually needed it and
it names what to do about it.

**Credentials are keyed by provider, and the prefix is the provider's own id.**
`nodemaven` reads `NODEMAVEN_LOGIN`, `oxylabs` reads `OXYLABS_LOGIN`, and both
can sit in one `.env` at once. That is not tidiness: a matrix that interleaves
two providers needs both sets present in the same process, and interleaving is
the only way two providers are comparable at all - one at 10:00 and one at 14:00
measures the afternoon. A single shared `PROXY_LOGIN` would make the one shape
worth running the one shape the configuration cannot express.

The host and port may come from the provider definition instead, which carries
the vendor's documented gateway. The environment wins when it says anything, so
a fork pointing at a regional endpoint changes a variable rather than a file.
"""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

from . import providers

load_dotenv()


class MissingCredentials(RuntimeError):
    """Raised when a run needs a gateway and the environment cannot reach it."""


@dataclass(frozen=True)
class Credentials:
    """What one provider needs to carry a request. Never logged, never returned
    to a caller that is about to print it."""

    login: str
    password: str
    host: str
    port: int

    @property
    def gateway(self) -> str:
        return f"{self.host}:{self.port}"


def variables(provider) -> dict:
    """The environment variable names this provider reads, without their values.

    Kept as a function rather than a constant because the names are derived from
    the provider id: a definition added as a file must not also need a line here.
    """
    prefix = provider.env_prefix
    return {name: f"{prefix}_{name.upper()}"
            for name in ("login", "password", "host", "port")}


def credentials(provider=None) -> Credentials:
    provider = provider or providers.load()
    names = variables(provider)
    values = {key: os.environ.get(var) for key, var in names.items()}

    missing = [names[key] for key in ("login", "password") if not values[key]]
    if missing:
        raise MissingCredentials(
            f"{' and '.join(missing)} not set, so no request can leave through "
            f"the {provider.label} gateway. Copy .env.example to .env and fill "
            f"it in. Nothing has been sent."
        )

    host = values["host"] or provider.host
    port = values["port"] or provider.port
    if not host or not port:
        raise MissingCredentials(
            f"no gateway address for {provider.label}: {names['host']} and "
            f"{names['port']} are unset and its definition names no default. "
            f"Nothing has been sent."
        )
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise MissingCredentials(
            f"{names['port']} is {port!r}, which is not a port number, so the "
            f"connection would go nowhere. Nothing has been sent."
        ) from None

    return Credentials(login=values["login"], password=values["password"],
                       host=host, port=port)


def available(provider=None) -> bool:
    """True when a proxied run is possible. Never reveals the values."""
    try:
        credentials(provider)
    except (MissingCredentials, providers.ProviderError):
        return False
    return True


def configured() -> list:
    """Providers on disk whose credentials are present, cheapest thing a
    `--dry-run` can say about whether a matrix can start."""
    return [name for name in providers.names()
            if available(providers.load(name))]
