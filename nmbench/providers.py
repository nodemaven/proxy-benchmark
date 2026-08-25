"""Provider definitions: the one place a gateway's own dialect is written down.

Every provider sells the same thing and spells it differently. The settings a
run varies - country, sticky session, quality filter - are encoded inside the
proxy username, and each vendor picks its own separators, its own parameter
names and its own idea of what happens when you get one wrong. That difference
is the whole of what a provider is, from this harness's point of view.

So it is data, not code: one `.toml` file per provider under `data/providers/`,
read through `tomllib` from the standard library, which means adding a competitor
costs a file and no dependency. It sits beside `data/queries/` for the same
reason that does - a committed input a stranger gets identically, rather than
something assembled at run time. The runner never
learns a provider's name, the same rule that already governs engines and
targets - and a data file cannot branch, which is the reason these are not
Python modules. A module per provider invites one `if provider == ...` in a
place nobody reviews, and the argument this whole repository rests on is that
every arm went through the same code path.

**A gateway that recognises nothing is a definition too, and it is the common
one.** The dialect above describes a pool gateway that sells countries and sticky
sessions inside the username. A proxy somebody already owns - one endpoint, a
login and a password, bought from anyone or run on their own box - takes no
settings at all, and that is written as an empty `known_params` and an empty
`session_param` rather than as a special case in the runner. What follows from it
is not cosmetic and the harness has to say so out loud: with no session parameter
there is no way to ask for a different exit, so every attempt in a run leaves
from the same address. Exit yield, rotation and anything about the pool are not
measurable through such a gateway; engines and targets are, which is most of what
this repository measures.

**A definition carries its own provenance, and the field is not decoration.**
`status = "measured"` means rows in `data/runs/` were produced through this
gateway from this machine. `status = "documented"` means the dialect was read
off the vendor's own documentation, on the date recorded, and nothing here has
ever sent a byte through it. The distinction lives on the definition rather than
in a README because it is the first thing a reader needs and the last thing
anyone remembers to write down - and because a wrong username is invisible: the
one gateway measured here answers an unknown parameter name with 200 and the
setting silently dropped, so a mistake in one of these files does not fail, it
quietly produces rows describing settings that were never applied.
"""
import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

DEFINITIONS = Path(__file__).resolve().parent.parent / "data" / "providers"

# The provider used when nothing names one. `NMBENCH_PROVIDER` exists so a fork
# can point every script and probe at its own gateway without editing a command
# line: the axis is per cell in a matrix, but a single-provider fork should not
# have to say so on every invocation.
FALLBACK = "nodemaven"
SELECT_VAR = "NMBENCH_PROVIDER"

TRANSPORTS = ("username",)
STATUSES = ("measured", "documented")


class ProviderError(RuntimeError):
    """Raised for a definition that cannot be used, naming what will not work."""


@dataclass(frozen=True)
class Provider:
    """One gateway's dialect.

    `known_params` and `aliases` are both written in the harness's own
    vocabulary - `country`, `sid`, `filter` - and the provider's spelling is the
    value side of `aliases`. Keeping the canonical names as the ones already on
    disk means a run recorded before this layer existed still matches on
    `--resume` and its `params` column still reads the same.

    Empty `known_params` is a gateway that takes no settings in the username,
    which is what a proxy you already own looks like. It is the default here
    because it is the assumption that asks for nothing: a field left out cannot
    put a parameter on the wire, and a parameter on the wire that the gateway
    does not know is answered with 200 and dropped by at least one of them.
    """

    id: str
    label: str
    prefix: str = "{login}"
    separator: str = "-"
    pair_separator: str = "-"
    known_params: frozenset = frozenset()
    aliases: dict = field(default_factory=dict)
    session_param: str = "sid"
    param_transport: str = "username"
    host: str = ""
    port: int = 0
    exit_ip_header: str = ""
    status: str = "documented"
    source: str = ""
    source_read: str = ""
    notes: str = ""

    @property
    def env_prefix(self) -> str:
        """`nodemaven` -> `NODEMAVEN`, so credentials for several providers can
        sit in one `.env` at once. A matrix that interleaves two providers needs
        both sets present in the same process; a single shared `PROXY_LOGIN`
        would make the interleaved run - the only kind worth running - the one
        shape the configuration cannot express."""
        return self.id.upper().replace("-", "_")

    def spell(self, key: str) -> str:
        """The provider's own name for a canonical parameter."""
        return self.aliases.get(key, key)

    @property
    def measured(self) -> bool:
        return self.status == "measured"


def _read(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except OSError as exc:
        raise ProviderError(f"cannot read {path.name}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ProviderError(
            f"{path.name} is not valid TOML ({exc}), so the gateway dialect it "
            f"describes is unavailable and no request can be built for it"
        ) from exc


def _build(name: str, raw: dict, path: Path) -> Provider:
    known = {f.name for f in fields(Provider)}
    unknown = set(raw) - known
    if unknown:
        # The same reasoning as `KNOWN_PARAMS` in proxy.py: a key nobody reads
        # is indistinguishable from a key that was applied, and the failure
        # surfaces as rows claiming settings that never took effect.
        raise ProviderError(
            f"{path.name} sets {sorted(unknown)}, which nothing reads. Either it "
            f"is a typo or the field was renamed; as written those settings will "
            f"NOT be applied. Known fields: {sorted(known)}"
        )

    raw = dict(raw)
    raw.setdefault("id", name)
    if raw["id"] != name:
        raise ProviderError(
            f"{path.name} declares id {raw['id']!r} but is named {name!r}. The "
            f"file name is what `--providers` and the env prefix are derived "
            f"from, so the two must agree"
        )
    for required in ("label", "source", "source_read"):
        if not raw.get(required):
            raise ProviderError(
                f"{path.name} has no {required!r}. A definition without its "
                f"provenance cannot be re-checked when the vendor changes the "
                f"format, and this one already cannot be verified from here"
            )
    if raw.get("status", "documented") not in STATUSES:
        raise ProviderError(
            f"{path.name} has status {raw['status']!r}, which claims nothing "
            f"readable. Use one of {list(STATUSES)}: 'measured' means rows in "
            f"data/runs/ came through it, 'documented' means nobody here has run it"
        )
    transport = raw.get("param_transport", "username")
    if transport not in TRANSPORTS:
        raise ProviderError(
            f"{path.name} asks for param_transport {transport!r}, which is not "
            f"implemented. Only {list(TRANSPORTS)} can carry a setting; a gateway "
            f"that takes its parameters another way needs code, not a config "
            f"entry, and building a username for it would send settings nowhere"
        )
    if "{login}" not in raw.get("prefix", "{login}"):
        raise ProviderError(
            f"{path.name} has prefix {raw['prefix']!r}, which drops the login. "
            f"Every request built from it would authenticate as nobody"
        )
    if "known_params" in raw:
        raw["known_params"] = frozenset(raw["known_params"])
    session = raw.get("session_param", "sid")
    known = raw.get("known_params")
    if known and session and session not in known:
        raise ProviderError(
            f"{path.name} names {session!r} as its session parameter but does "
            f"not list it in known_params, so every sticky session would be "
            f"refused client-side before it was built"
        )
    if session and not known:
        # The default `session_param` is `sid`, so a file that lists no
        # parameters and says nothing else is claiming a sticky session it never
        # described. `build_username` would refuse every one of them, so the
        # definition loads and nothing built from it can run - and the operator
        # reads that refusal at the start of a matrix rather than here.
        #
        # Refused rather than inferred, because the two readings differ in what
        # a run means. A gateway with no session parameter hands every attempt
        # the same exit, so exit yield and rotation stop being measurable through
        # it, and that is a fact about the results which has to be written down
        # by the person adding the definition rather than guessed at by the
        # loader.
        raise ProviderError(
            f"{path.name} lists no known_params and still names {session!r} as "
            f"its session parameter, so every request built for it would be "
            f"refused before it was sent. If this gateway takes no settings in "
            f"the username - one endpoint, a login and a password, which is what "
            f"a proxy you already own looks like - write session_param = \"\" "
            f"and say so. Every attempt then leaves from whatever single exit "
            f"that proxy has, which is a real limit on what can be measured "
            f"through it and not a defect"
        )
    return Provider(**raw)


def load(name: str = None) -> Provider:
    """The named provider, or the one the environment selects."""
    name = name or default_name()
    path = DEFINITIONS / f"{name}.toml"
    if not path.exists():
        raise ProviderError(
            f"no provider definition {name!r}. Present: {sorted(names())}. "
            f"Adding one is a file in data/providers/ - copy _template.toml"
        )
    return _build(name, _read(path), path)


def default_name() -> str:
    return os.environ.get(SELECT_VAR) or FALLBACK


def names() -> list:
    """Definitions on disk. Files starting with `_` are templates, not gateways."""
    return sorted(p.stem for p in DEFINITIONS.glob("*.toml")
                  if not p.stem.startswith("_"))


def load_all() -> dict:
    return {name: load(name) for name in names()}
