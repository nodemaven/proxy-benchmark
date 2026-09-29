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

# What kind of addresses the gateway hands out. This is not a marketing label
# and it is not decoration on a chart: it decides which arms may be put in one
# column at all.
#
# An ISP pool is static addresses on carrier ranges and a residential pool is
# peers, so a gap between an ISP arm and a residential arm is a difference
# between two product classes and not between two companies. In multi-gateway
# Amazon runs the ISP arm stood well clear of every residential arm while the
# residential arms could not be ordered against each other once clustering was
# accounted for. Reading the ISP row as "this vendor is better" is the easiest
# wrong conclusion to draw from a chart that puts them in one column.
#
# `""` is the honest default and means nobody wrote it down. It is NOT read as
# residential: an unstated network puts the arm in its own group, because
# pooling it with a group it may not belong to is the failure this field exists
# to stop, and a guess that happens to be right looks identical to one that is
# not.
NETWORKS = ("residential", "isp", "datacenter", "mobile")


class ProviderError(RuntimeError):
    """Raised for a definition that cannot be used, naming what will not work."""


PARAM_KINDS = ("enum", "text")

# What is behind one legal value of one parameter. Three and not two, because
# the `notes` in these files already draw the distinction and a two-way split
# would have to throw one of the readings away.
#
#   measured   the value has been sent from here and its effect observed. That
#              is rows in data/runs/ for NodeMaven's `filter=medium`, and a
#              probe with an ASN readout for `type=mobile`. Both are the value
#              doing something visible, which is the claim; where it was seen
#              belongs in `help`.
#   accepted   the gateway was probed and answered in a way only a recognised
#              value produces, but nothing has been run through it. On these
#              gateways that is its own reading rather than a weak `measured`:
#              an unknown parameter is answered 200 and silently dropped, so
#              acceptance means nothing until it is established against a
#              control. `filter=high` is here - it answers 200 where a junk
#              value answers 407, which puts it on the whitelist and says
#              nothing whatever about what it does to the exits.
#   documented read off a vendor surface - help pages, the dashboard generator,
#              an SDK - and never probed. This is not a weaker form of the other
#              two, it is the one that has been wrong before: these same notes
#              record that `ttl` is documented in seconds and refused in
#              seconds, and that `norotate` came off the vendor's own generator
#              and does nothing on this account.
VALUE_STATUSES = ("measured", "accepted", "documented")


@dataclass(frozen=True)
class ParamValue:
    """One legal value of one parameter, carrying what is behind it."""

    value: str
    status: str = "documented"

    @property
    def measured(self) -> bool:
        return self.status == "measured"


@dataclass(frozen=True)
class ParamSpec:
    """One tunable setting of one gateway, in a form a form can be built from.

    This exists because the legal values were prose. `known_params` has always
    been machine-readable and it only ever said which NAMES the gateway knows;
    which VALUES are legal lived in the `notes` string, where a person could
    read them and nothing else could. So anything offering these settings to an
    operator - a web form, a completion, a `--help` - had either to carry a
    second copy of the vocabulary or to offer a free-text box. A second copy
    goes stale silently. A free-text box on these gateways costs an hour of
    runtime on a value that was answered 200 and dropped, which is the one
    failure that cannot be seen in the rows afterwards.

    `kind` is `enum` when the legal values are a closed set and `text` when they
    are not. A sticky TTL is `text` because `1m`, `45m` and `3h` are all legal
    and nobody will enumerate them; its `values` are then examples rather than a
    whitelist. That is a field rather than something inferred from the list
    being short, because "short list" and "closed set" are different claims and
    only one of them may be enforced.
    """

    name: str
    label: str = ""
    kind: str = "enum"
    help: str = ""
    placeholder: str = ""
    values: tuple = ()

    @property
    def measured_values(self) -> tuple:
        return tuple(v for v in self.values if v.measured)


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
    # How this gateway spells "do not pin a country". `matrix.ANY` is a keyword
    # on the harness's own country axis, not a country code, and the gateways
    # disagree about whether such a thing can be asked for at all: NodeMaven
    # takes the literal `any`, and the other three have no spelling for it - you
    # leave the parameter out. Empty here means exactly that, and it is the
    # default because it is the assumption that sends nothing.
    #
    # This field did not exist until 2026-09-14 and `any` went to every gateway
    # as written. The run of 2026-09-11 is what it cost: six of sixteen cells
    # asked four gateways for a country called `any`, drew 40
    # `ERR_TUNNEL_CONNECTION_FAILED` and 20 timeouts between them, and reached
    # Amazon zero times - so three providers were reported at 0% against a
    # question they were never asked. The warning was already written down in
    # `probes/probe_and_hold.py` and had not reached the matrix runner.
    country_any: str = ""
    session_param: str = "sid"
    param_transport: str = "username"
    host: str = ""
    port: int = 0
    exit_ip_header: str = ""
    # One of NETWORKS, or empty for "not written down". See the comment there:
    # this is the axis a comparison may be drawn along, so it is a property of
    # the definition and not something a chart infers from the id.
    network: str = ""
    status: str = "documented"
    source: str = ""
    source_read: str = ""
    notes: str = ""
    # The tunable settings, as `ParamSpec`s sorted by name. A tuple rather than
    # a dict so the ordering a form renders in is decided here and is the same
    # every time; `tunable` below is the lookup.
    #
    # Empty is the honest default and means the legal values have not been
    # written down as data yet - which was true of every definition here until
    # 2026-09-22. It is NOT read as "this gateway takes no settings": that is
    # what an empty `known_params` says, and the two are different claims. A
    # caller offering a form gets nothing to offer and says so, rather than
    # offering a free-text box it cannot validate.
    params: tuple = ()

    def tunable(self, name: str):
        """The spec for one parameter, or None."""
        for spec in self.params:
            if spec.name == name:
                return spec
        return None

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


def _build_params(raw_params, raw: dict, path: Path) -> tuple:
    """`[params.<name>]` tables into `ParamSpec`s, refusing what cannot be used.

    Every refusal here is a setting that would otherwise be offered to an
    operator and then not applied. That is the failure this whole layer exists
    to prevent, so a definition that describes a setting wrongly does not load
    at all - the alternative is a form with a control on it that spends an hour
    of runtime and changes nothing.
    """
    if not isinstance(raw_params, dict):
        raise ProviderError(
            f"{path.name} has a 'params' that is not a table of settings. Write "
            f"one [params.<name>] block per tunable parameter")

    known = raw.get("known_params") or frozenset()
    session = raw.get("session_param", "sid")
    specs = []
    for name in sorted(raw_params):
        body = raw_params[name]
        if not isinstance(body, dict):
            raise ProviderError(
                f"{path.name} writes params.{name} as {body!r} rather than a "
                f"table. Write [params.{name}] with a kind and its values")
        if name not in known:
            # Offering a name the gateway does not know produces a username
            # `build_username` refuses, so the run dies at the start of the
            # matrix instead of here - much further from the typo.
            raise ProviderError(
                f"{path.name} describes params.{name} but does not list it in "
                f"known_params, so nothing could ever send it. Add it there, or "
                f"drop the block")
        if name == "country" or (session and name == session):
            # Both are axes of the run in their own right - country is a column
            # of the matrix and the session id is drawn per identity. A form
            # offering them as free settings would let one run pin a country on
            # the country axis and a different one here, and the row would
            # record both.
            raise ProviderError(
                f"{path.name} describes params.{name}, which is not a free "
                f"setting: the harness owns that axis. 'country' is a column of "
                f"the matrix and {session!r} is drawn per identity, so a value "
                f"set here would contradict the one the run already chose")

        kind = body.get("kind", "enum")
        if kind not in PARAM_KINDS:
            raise ProviderError(
                f"{path.name} gives params.{name} kind {kind!r}. Use "
                f"{list(PARAM_KINDS)}: 'enum' is a closed set that may be "
                f"enforced, 'text' is one that may not")

        seen, values = {}, []
        for status in VALUE_STATUSES:
            listed = body.get(status, [])
            if isinstance(listed, str):
                raise ProviderError(
                    f"{path.name} gives params.{name}.{status} as a string. It "
                    f"is a list of values, and a string here would be read one "
                    f"character at a time")
            for value in listed:
                value = str(value)
                if value in seen:
                    # The whole point of the field is which evidence stands
                    # behind the value, so two answers is no answer.
                    raise ProviderError(
                        f"{path.name} lists params.{name} value {value!r} as "
                        f"both {seen[value]!r} and {status!r}. One value has one "
                        f"provenance; say which, and put the rest in 'help'")
                seen[value] = status
                values.append(ParamValue(value=value, status=status))
        if kind == "enum" and not values:
            raise ProviderError(
                f"{path.name} gives params.{name} kind 'enum' and lists no "
                f"values, so the only thing it can offer is an empty choice. "
                f"List what the gateway takes, or make it kind = \"text\"")

        specs.append(ParamSpec(
            name=name,
            label=body.get("label") or name,
            kind=kind,
            help=body.get("help", ""),
            placeholder=body.get("placeholder", ""),
            values=tuple(values)))
    return tuple(specs)


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
    if raw.get("network", "") not in ("", *NETWORKS):
        raise ProviderError(
            f"{path.name} has network {raw['network']!r}, which names no product "
            f"class this repository can group on. Use one of {list(NETWORKS)}, or "
            f"leave it out - an unstated network is grouped on its own and never "
            f"pooled with a class it might not belong to"
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
    if "params" in raw:
        raw["params"] = _build_params(raw["params"], raw, path)
    if raw.get("country_any") and "country" not in raw.get("known_params", ()):
        raise ProviderError(
            f"{path.name} spells an unpinned country as "
            f"{raw['country_any']!r} and does not list 'country' in "
            f"known_params, so that spelling can never be sent. A definition "
            f"that sells no country has the axis collapsed for it and asks for "
            f"nothing; leave country_any out"
        )
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
