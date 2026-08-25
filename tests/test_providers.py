"""Provider definitions: that the dialect is data, and that a wrong file is
refused before it can produce rows.

Two things make this file worth more than its size.

**A mistake in a definition is invisible.** The one gateway measured here answers
an unrecognised parameter name with 200 and the setting silently dropped, so a
misspelled parameter does not fail - the connection succeeds, the run completes,
and every row claims a setting that was never applied. Nothing the gateway
replies can catch it, which means the loader's refusals are the only check that
exists and each one is load-bearing.

**The shipped definition cannot exercise the DSL.** NodeMaven spells both
separators `-`, so a bug that used `separator` where `pair_separator` was meant
produces a byte-identical username and no test against `nodemaven.toml` can see
it. The second dialect here is therefore synthetic and deliberately spells every
field differently. It is not a competitor: a test asserting a real vendor's
username format would pin a transcription nobody here can verify, which is
exactly the claim `status = "documented"` exists to keep out of the code.
"""
import json

import pytest

from nmbench import config, providers, proxy

BASE = {
    "label": "Test Provider",
    "status": "documented",
    "source": "https://example.invalid/docs",
    "source_read": "2026-08-18",
}

# Every field spelled differently from the shipped definition, so a hardcoded
# NodeMaven habit anywhere in proxy.py shows up as a wrong username rather than
# as a coincidence.
DIALECT = {
    "prefix": "customer-{login}",
    "separator": "_",
    "pair_separator": ":",
    "known_params": ["country", "sid", "ttl"],
    "aliases": {"country": "cc", "sid": "sessid"},
    "session_param": "sid",
    "host": "gw.example.invalid",
    "port": 7000,
}

# The same dialect with the session parameter under a different canonical name.
# `DIALECT` only aliases it, which leaves the canonical key `sid` and is the case
# a hardcoded literal survives; this is the case it does not.
RENAMED_SESSION = {**DIALECT,
                   "known_params": ["country", "session", "ttl"],
                   "aliases": {"country": "cc", "session": "sessid"},
                   "session_param": "session"}

# A proxy somebody already owns: one endpoint, a login and a password, and
# nothing encoded in the username. This is the shape most proxies have, and the
# harness has to be able to run through it without a line of code, so it is
# tested as a first-class dialect rather than as a degenerate case.
PLAIN = {"prefix": "{login}", "known_params": [], "session_param": "",
         "host": "gw.example.invalid", "port": 8000}


def _value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_value(v) for v in value) + "]"
    return json.dumps(str(value))


def define(directory, name, **fields):
    """Write a definition and return its path. `None` removes a base field."""
    body = {**BASE, **fields}
    aliases = body.pop("aliases", None)
    lines = [f"{key} = {_value(v)}" for key, v in body.items() if v is not None]
    if aliases:
        lines.append("[aliases]")
        lines += [f"{key} = {_value(v)}" for key, v in aliases.items()]
    path = directory / f"{name}.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def definitions(tmp_path, monkeypatch):
    """An empty definitions directory, so a test cannot be answered by a file
    that happens to be shipped."""
    directory = tmp_path / "providers"
    directory.mkdir()
    monkeypatch.setattr(providers, "DEFINITIONS", directory)
    return directory


class TestTheDialectIsData:
    """The whole reason these are files: a second gateway is a different username
    format and nothing else. If any part of the format is in the code, adding a
    competitor edits the runner, and then no two arms went through one path."""

    def test_a_second_dialect_needs_no_code(self, definitions):
        define(definitions, "synth", **DIALECT)
        provider = providers.load("synth")
        assert proxy.build_username("acct", provider=provider, country="us",
                                    sid="abc") == "customer-acct_cc:us_sessid:abc"

    def test_the_two_separators_are_not_the_same_field(self, definitions):
        """NodeMaven spells both `-`, so swapping them is invisible on the only
        definition shipped. This is the test that would catch it."""
        define(definitions, "synth", **DIALECT)
        built = proxy.build_username("acct", provider=providers.load("synth"),
                                     country="us")
        assert built.count("_") == 1 and built.count(":") == 1

    def test_the_prefix_carries_the_login(self, definitions):
        define(definitions, "synth", **DIALECT)
        assert proxy.build_username(
            "acct", provider=providers.load("synth")) == "customer-acct"

    def test_parameters_are_named_in_our_vocabulary_and_sent_in_theirs(
            self, definitions):
        """`aliases` is one-directional on purpose. The canonical names are the
        ones already written into every row and every cell key on disk, so a
        provider renaming `country` to `cc` must not rename the column."""
        define(definitions, "synth", **DIALECT)
        provider = providers.load("synth")
        assert provider.spell("country") == "cc"
        assert provider.spell("ttl") == "ttl"

    def test_the_order_asked_for_is_the_order_sent(self, definitions):
        """The sticky key is the whole recognised parameter set on the gateway
        measured here, so the string is the identity and its shape matters."""
        define(definitions, "synth", **DIALECT)
        provider = providers.load("synth")
        assert proxy.build_username("a", provider=provider, sid="x",
                                    country="us") == "customer-a_sessid:x_cc:us"

    def test_a_parameter_one_gateway_knows_and_another_does_not_is_refused(
            self, definitions):
        """Why `--param` is validated against every provider in the matrix at
        preflight: `filter` is measured on NodeMaven and absent here, and finding
        that out mid-run means the earlier providers' cells have already spent
        traffic."""
        define(definitions, "synth", **DIALECT)
        with pytest.raises(proxy.ParamError, match="NOT be applied"):
            proxy.build_username("a", provider=providers.load("synth"),
                                 filter="medium")


class TestTheSessionKeyComesFromTheProvider:
    """`sid` is the canonical name and a definition may pick another one.

    `aliases` covers a gateway that only spells it differently on the wire, so
    the canonical key stays `sid` there and a literal would have worked. It is a
    definition naming the parameter something else canonically that the literal
    breaks, and eleven call sites wrote the literal. These pin the plumbing that
    replaced them.
    """

    def test_the_key_is_the_providers_canonical_name(self, definitions):
        define(definitions, "synth", **RENAMED_SESSION)
        assert proxy.session_params("held1", provider=providers.load("synth"),
                                    country="us") == {"country": "us",
                                                      "session": "held1"}

    def test_the_alias_still_applies_on_the_wire(self, definitions):
        """Two renames in one path: canonical `session`, sent as `sessid`."""
        define(definitions, "synth", **RENAMED_SESSION)
        provider = providers.load("synth")
        params = proxy.session_params("held1", provider=provider, country="us")
        assert proxy.build_username("acct", provider=provider, **params) == \
            "customer-acct_cc:us_sessid:held1"

    def test_the_literal_this_replaced_would_be_refused(self, definitions):
        """Under `strict` the old spelling is loud. The health probe passes
        `strict=False` on purpose, and there the same name reaches the gateway,
        is answered with 200 and dropped, and every attempt draws a fresh exit
        while the rows claim one held session."""
        define(definitions, "synth", **RENAMED_SESSION)
        provider = providers.load("synth")
        with pytest.raises(proxy.ParamError, match="NOT be applied"):
            proxy.build_username("acct", provider=provider, sid="held1")
        assert proxy.build_username("acct", provider=provider, strict=False,
                                    sid="held1") == "customer-acct_sid:held1"

    def test_the_shipped_definition_is_unaffected(self):
        """The rows on disk carry `sid`, and this must not have moved them."""
        assert proxy.session_params("bm1", provider=providers.load("nodemaven"),
                                   country="us") == {"country": "us",
                                                     "sid": "bm1"}


class TestAProxyYouAlreadyOwn:
    """The gateway that recognises nothing, which is what almost every proxy
    outside this industry looks like.

    It is not a lesser provider and it is not a stub: the engine and target axes
    - which are most of what this repository measures - run through it exactly as
    they run through a pool. What it cannot do is hand out a second exit, and the
    whole of that limit has to be visible in the plumbing rather than argued
    about in a README, because a run through it that quietly carried a session
    parameter would record one held identity per row while every attempt drew
    whatever the single exit was.
    """

    def test_it_needs_no_parameters_to_be_a_definition(self, definitions):
        define(definitions, "mine", **PLAIN)
        assert proxy.build_username(
            "acct", provider=providers.load("mine")) == "acct"

    def test_no_session_is_asked_for(self, definitions):
        """Not `sid` under a guessed name, and not a refusal either. There is no
        session to ask for: the connection is the session."""
        define(definitions, "mine", **PLAIN)
        assert proxy.session_params("held1", provider=providers.load("mine")) \
            == {}

    def test_the_url_carries_the_login_and_nothing_else(self, definitions,
                                                        monkeypatch):
        """The end of the path an operator's own proxy actually takes. Anything
        appended here would be sent to a gateway with no parser for it."""
        define(definitions, "mine", **PLAIN)
        monkeypatch.setenv("MINE_LOGIN", "login")
        monkeypatch.setenv("MINE_PASSWORD", "secret")
        assert proxy.proxy_url(provider=providers.load("mine")) == \
            "http://login:secret@gw.example.invalid:8000"

    def test_a_setting_it_does_not_sell_is_refused_before_a_request_exists(
            self, definitions):
        """The one refusal that matters to this shape. `--countries us` against
        a plain proxy is a setting nobody can apply, and a gateway that ignores
        it silently would let the run complete and label every row `us`."""
        define(definitions, "mine", **PLAIN)
        with pytest.raises(proxy.ParamError, match="NOT be applied"):
            proxy.build_username("acct", provider=providers.load("mine"),
                                 country="us")

    def test_a_definition_that_lists_nothing_and_still_names_a_session(
            self, definitions):
        """`session_param` defaults to `sid`, so a file that lists no parameters
        and says nothing else is claiming a rotation it never described. Every
        request built from it would be refused, so the refusal is moved to the
        loader where it can say which line to write instead."""
        define(definitions, "mine", **{**PLAIN, "session_param": None})
        with pytest.raises(providers.ProviderError, match="session_param"):
            providers.load("mine")

    def test_the_shipped_definition_is_this_shape(self):
        """`custom.toml` is what a reader copies, and the four environment
        variables in its header are the whole of what it asks for. A field that
        drifted back into it would put a parameter on somebody else's wire."""
        provider = providers.load("custom")
        assert provider.known_params == frozenset()
        assert provider.session_param == ""
        assert not provider.measured


class TestRefusals:
    """Each of these is a file that would otherwise produce rows describing
    settings that were never applied."""

    def test_an_unknown_field_says_the_setting_will_not_apply(self, definitions):
        define(definitions, "synth", **DIALECT, seperator="-")
        with pytest.raises(providers.ProviderError, match="NOT be applied"):
            providers.load("synth")

    def test_an_id_disagreeing_with_the_filename_is_refused(self, definitions):
        """The filename is what `--providers` selects and what the credential
        variables are derived from, so a definition claiming another id would
        read another account's credentials."""
        define(definitions, "synth", id="other", **DIALECT)
        with pytest.raises(providers.ProviderError, match="named 'synth'"):
            providers.load("synth")

    @pytest.mark.parametrize("field", ["label", "source", "source_read"])
    def test_a_definition_without_provenance_is_refused(self, definitions, field):
        define(definitions, "synth", **{**DIALECT, field: None})
        with pytest.raises(providers.ProviderError, match=field):
            providers.load("synth")

    def test_a_status_outside_the_two_is_refused(self, definitions):
        """`measured` and `documented` are the only two that mean anything to a
        reader, and the difference is whether any byte has left through it."""
        define(definitions, "synth", **DIALECT, status="verified")
        with pytest.raises(providers.ProviderError, match="measured"):
            providers.load("synth")

    def test_an_unimplemented_transport_is_refused_rather_than_ignored(
            self, definitions):
        """A gateway taking its settings by port or by control API needs code. A
        username built for it would carry the settings nowhere and the run would
        complete."""
        define(definitions, "synth", **DIALECT, param_transport="port")
        with pytest.raises(providers.ProviderError, match="not implemented"):
            providers.load("synth")

    def test_a_prefix_that_drops_the_login_is_refused(self, definitions):
        define(definitions, "synth", **{**DIALECT, "prefix": "customer-acct"})
        with pytest.raises(providers.ProviderError, match="authenticate as nobody"):
            providers.load("synth")

    def test_a_session_parameter_outside_known_params_is_refused(self, definitions):
        """Every probe here holds one exit across a series, so a provider whose
        session parameter is unlistable cannot run the method at all."""
        define(definitions, "synth", **{**DIALECT, "session_param": "sessid"})
        with pytest.raises(providers.ProviderError, match="sticky session"):
            providers.load("synth")

    def test_invalid_toml_names_the_file_and_the_consequence(self, definitions):
        (definitions / "synth.toml").write_text("label = \n", encoding="utf-8")
        with pytest.raises(providers.ProviderError, match=r"synth\.toml"):
            providers.load("synth")

    def test_an_absent_definition_says_what_is_present(self, definitions):
        define(definitions, "synth", **DIALECT)
        with pytest.raises(providers.ProviderError, match="synth"):
            providers.load("nope")


class TestSelection:
    def test_files_starting_with_an_underscore_are_templates(self, definitions):
        define(definitions, "synth", **DIALECT)
        define(definitions, "_template", **DIALECT)
        assert providers.names() == ["synth"]

    def test_the_default_is_the_fallback_when_nothing_selects_one(self):
        assert providers.default_name() == providers.FALLBACK

    def test_the_environment_can_point_a_fork_at_its_own_gateway(
            self, definitions, monkeypatch):
        """A single-provider fork should not have to name it on every command
        line. The axis is still per cell in a matrix."""
        define(definitions, "synth", **DIALECT)
        monkeypatch.setenv(providers.SELECT_VAR, "synth")
        assert providers.load().id == "synth"

    def test_load_all_reads_every_definition(self, definitions):
        define(definitions, "synth", **DIALECT)
        define(definitions, "other", **DIALECT)
        assert sorted(providers.load_all()) == ["other", "synth"]


class TestCredentialsArePerProvider:
    """A matrix that interleaves two providers holds both credential sets in one
    process, and interleaving is the only shape in which two providers are
    comparable: one at 10:00 and one at 14:00 measures the afternoon. A shared
    `PROXY_LOGIN` would make that shape the one the configuration cannot
    express."""

    def test_the_prefix_is_the_provider_id(self, definitions):
        define(definitions, "synth", **DIALECT)
        assert config.variables(providers.load("synth")) == {
            "login": "SYNTH_LOGIN", "password": "SYNTH_PASSWORD",
            "host": "SYNTH_HOST", "port": "SYNTH_PORT"}

    def test_a_hyphenated_id_becomes_an_underscore(self, definitions):
        define(definitions, "two-words", **DIALECT)
        assert providers.load("two-words").env_prefix == "TWO_WORDS"

    def test_one_providers_credentials_do_not_satisfy_another(
            self, definitions, monkeypatch):
        define(definitions, "alpha", **DIALECT)
        define(definitions, "beta", **DIALECT)
        monkeypatch.setenv("ALPHA_LOGIN", "login")
        monkeypatch.setenv("ALPHA_PASSWORD", "secret")
        assert config.available(providers.load("alpha"))
        assert not config.available(providers.load("beta"))

    def test_the_definition_carries_the_gateway_and_the_environment_wins(
            self, definitions, monkeypatch):
        """The host in the file is documentation and a default, so a fork
        pointing at a regional endpoint changes a variable rather than a file."""
        define(definitions, "synth", **DIALECT)
        monkeypatch.setenv("SYNTH_LOGIN", "login")
        monkeypatch.setenv("SYNTH_PASSWORD", "secret")
        provider = providers.load("synth")
        assert config.credentials(provider).gateway == "gw.example.invalid:7000"
        monkeypatch.setenv("SYNTH_HOST", "eu.example.invalid")
        assert config.credentials(provider).gateway == "eu.example.invalid:7000"

    def test_a_missing_credential_names_the_variable_and_the_provider(
            self, definitions):
        define(definitions, "synth", **DIALECT)
        with pytest.raises(config.MissingCredentials,
                           match="SYNTH_LOGIN and SYNTH_PASSWORD"):
            config.credentials(providers.load("synth"))

    def test_a_definition_with_no_gateway_says_so_rather_than_connecting_nowhere(
            self, definitions, monkeypatch):
        define(definitions, "synth", **{**DIALECT, "host": "", "port": 0})
        monkeypatch.setenv("SYNTH_LOGIN", "login")
        monkeypatch.setenv("SYNTH_PASSWORD", "secret")
        with pytest.raises(config.MissingCredentials, match="no gateway address"):
            config.credentials(providers.load("synth"))

    def test_configured_lists_only_providers_that_can_run(
            self, definitions, monkeypatch):
        """The cheapest thing a --dry-run can say about whether a matrix can
        start, and it never reveals a value."""
        define(definitions, "alpha", **DIALECT)
        define(definitions, "beta", **DIALECT)
        monkeypatch.setenv("BETA_LOGIN", "login")
        monkeypatch.setenv("BETA_PASSWORD", "secret")
        assert config.configured() == ["beta"]


class TestTheShippedDefinitions:
    """Read from the real directory. A broken file here is a run that cannot
    start, and the template is what every future definition is copied from."""

    @pytest.mark.parametrize("name", providers.names())
    def test_it_loads(self, name):
        assert providers.load(name).id == name

    @pytest.mark.parametrize("name", providers.names())
    def test_it_declares_its_provenance(self, name):
        provider = providers.load(name)
        assert provider.status in providers.STATUSES
        assert provider.source and provider.source_read

    @pytest.mark.parametrize("name", providers.names())
    def test_a_measured_definition_names_the_gateway_it_was_measured_through(
            self, name):
        """`measured` means rows in data/runs/ came through it, which is not
        possible without an address."""
        provider = providers.load(name)
        if provider.measured:
            assert provider.host and provider.port

    def test_the_template_is_itself_loadable(self):
        """It is excluded from `names()`, so nothing else would ever read it -
        and a template that does not load is found by whoever copies it."""
        assert providers.load("_template").param_transport == "username"

    def test_the_default_provider_is_present(self):
        """Every script and probe resolves it when nothing names one, so its
        absence is not a missing file, it is a repository that cannot run."""
        assert providers.FALLBACK in providers.names()

    def test_the_measured_dsl_is_pinned(self):
        """The nine parameters and the separators were read off the gateway on
        2026-08-12 by raw CONNECT probes, and the gateway answers a tenth name
        with 200 and the setting dropped. Pinned so the set cannot be widened
        from a vendor page without a run behind it."""
        provider = providers.load("nodemaven")
        assert provider.measured
        assert provider.known_params == frozenset(
            {"country", "region", "city", "isp", "sid", "ttl", "filter",
             "ipv4", "speed"})
        assert (provider.prefix, provider.separator, provider.pair_separator) \
            == ("{login}", "-", "-")
        assert provider.aliases == {}
