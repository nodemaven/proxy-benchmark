"""Username DSL tests.

Every rule here exists because the gateway's own reaction to the mistake is
useless or actively misleading: an unknown parameter returns 200 with the setting
never applied, an empty value hangs for twenty seconds with no reply at all.
Client-side refusal is the only thing that turns those into a message.
"""
import pytest

from nmbench import config, providers, proxy


class TestBuildUsername:
    def test_parameters_become_hyphenated_pairs(self):
        assert proxy.build_username("acct", country="us", sid="abc") == \
            "acct-country-us-sid-abc"

    def test_order_is_preserved(self):
        """The gateway keys sticky sessions on the parameter set, so the string
        must be reproducible from the same call."""
        assert proxy.build_username("acct", sid="abc", country="us") == \
            "acct-sid-abc-country-us"

    def test_no_parameters_is_the_bare_login(self):
        assert proxy.build_username("acct") == "acct"

    def test_unknown_parameter_is_refused(self):
        with pytest.raises(proxy.ParamError, match="unknown parameter"):
            proxy.build_username("acct", contry="us")

    def test_unknown_parameter_message_says_the_setting_is_lost(self):
        with pytest.raises(proxy.ParamError) as exc:
            proxy.build_username("acct", session="abc")
        assert "NOT be applied" in str(exc.value)

    @pytest.mark.parametrize("value", ["", None])
    def test_empty_value_is_refused(self, value):
        with pytest.raises(proxy.ParamError, match="hangs"):
            proxy.build_username("acct", country=value)

    def test_bool_becomes_the_gateway_spelling(self):
        assert proxy.build_username("acct", ipv4=True).endswith("ipv4-true")
        assert proxy.build_username("acct", ipv4=False).endswith("ipv4-false")

    def test_zero_is_not_treated_as_empty(self):
        """`0 == ""` is False in Python but the check is easy to write wrongly."""
        assert proxy.build_username("acct", ttl=0).endswith("ttl-0")

    def test_strict_off_allows_an_unknown_parameter(self):
        """Only for probes that are deliberately testing the gateway's reaction."""
        assert proxy.build_username("acct", strict=False, nonsense="x") == \
            "acct-nonsense-x"

    def test_known_params_are_the_measured_set(self):
        """Every name here was confirmed against the gateway, not assumed.

        `speed` joined on 2026-08-12. It was found by reading the provider's own
        proxy generator - the dashboard builds three of its four "IP filter
        modes" out of `filter` and `speed` together - and then confirmed the
        only way this can be confirmed: `speed=zzzz` answers 407, so the gateway
        parses the value, while `speed=fast` and `speed=slow` connect. A name
        the gateway does not recognise would have answered 200 with the setting
        silently dropped, which is indistinguishable from success.

        `type` joined on 2026-08-26 the same way and with the same control.
        Probed from the VPS: `type` with a junk value answers 407, while the
        negative control `zzqqx` - a name nobody has implemented - answers 200
        for anything, so a 407 is a name the gateway parses. It selects a
        carrier pool rather than filtering one: 5 echo requests an arm, fresh
        sid, `country=us`, `type=mobile` drew T-Mobile three times plus Cellco
        and AS7018, while `type=residential` and the unset arm drew wireline
        ASNs only.

        `norotate` was probed in the same session and is deliberately NOT here.
        It answered 200 with a junk value, exactly like the unknown name, and
        rotated 6 exits of 6 with no sid and 3 of 3 under a fixed sid - which is
        what this gateway does with a name it does not know. It is in the
        vendor's own proxy generator, and that is provenance rather than a
        measurement.

        The set moved into the provider definition when the DSL became data. It
        is still asserted here, spelled out rather than read from the file,
        because a test that loads the same file it is checking would follow an
        edit instead of catching it.
        """
        assert providers.load("nodemaven").known_params == {
            "country", "region", "city", "isp", "sid", "ttl", "filter",
            "ipv4", "speed", "type"}


class TestParseParams:
    """The one place `KEY=VALUE` is turned into gateway settings.

    It was three places until they were merged: `benchmark.py`,
    `gateway_health.py` and `probe_and_hold.py` each split the string themselves
    and then called `build_username` on the result to validate it. Three copies
    of a rule whose whole purpose is that the gateway cannot tell you when it is
    broken - an unknown name answers 200 with the setting dropped - is three
    chances for one of them to stop validating without a run ever looking wrong.
    """

    def test_it_splits_and_strips(self):
        assert proxy.parse_params(["filter=medium", " ttl = 10m "]) == {
            "filter": "medium", "ttl": "10m"}

    def test_nothing_in_is_nothing_out(self):
        """`--param` is optional on every script that takes it, and an empty
        list has to mean the gateway defaults rather than an error."""
        assert proxy.parse_params([]) == {}
        assert proxy.parse_params(None) == {}

    def test_a_value_may_hold_an_equals_sign(self):
        assert proxy.parse_params(["sid=a=b"]) == {"sid": "a=b"}

    def test_a_bare_word_is_refused(self):
        with pytest.raises(proxy.ParamError, match="KEY=VALUE"):
            proxy.parse_params(["filter"])

    def test_the_message_names_the_flag_it_was_given(self):
        """So an option spelled differently does not send the operator looking
        for a flag they never typed."""
        with pytest.raises(proxy.ParamError, match="--extra"):
            proxy.parse_params(["filter"], flag="--extra")

    def test_it_validates_and_does_not_merely_split(self):
        """The half that cannot be checked against the gateway at all.

        An unknown parameter name is answered with 200 and the setting silently
        dropped, so a run configured this way completes, writes rows, and
        measures the default pool while every row claims otherwise. If this
        assertion ever fails, the splitting still works and the validation is
        gone - which is the failure that leaves no trace anywhere else.
        """
        with pytest.raises(proxy.ParamError, match="unknown parameter"):
            proxy.parse_params(["nosuchname=1"])

    def test_an_empty_value_is_refused(self):
        """The gateway does not answer this one at all - it hangs about 20 s -
        so a run that sent it would read as a dead exit rather than as our own
        malformed username."""
        with pytest.raises(proxy.ParamError, match="hangs"):
            proxy.parse_params(["country="])


class TestTheValueIsCheckedAndNotOnlyTheName:
    """The name check above asks whether the gateway knows the NAME. This is the
    same question one level down, and it is the one that has been missing.

    It matters more than it looks, because a passing name check reads as
    validation. NodeMaven answers a bad `filter` value with 407, which sends the
    operator to check credentials that are correct, and answers an unrecognised
    `speed` value with 200 and the setting dropped, which cannot be seen in the
    rows afterwards at all.
    """

    def test_a_value_outside_a_closed_set_is_refused(self):
        with pytest.raises(proxy.ParamError, match="not a value"):
            proxy.parse_params(["filter=ultra"],
                               provider=providers.load("nodemaven"))

    def test_every_value_the_definition_lists_is_accepted(self):
        """Including the ones that are `accepted` rather than `measured`. The
        provenance is there to be shown to whoever picks a value, not to narrow
        what may be sent - refusing `high` would make the filter comparison this
        exists for impossible to run."""
        nodemaven = providers.load("nodemaven")
        for value in ("medium", "low", "high"):
            assert proxy.parse_params([f"filter={value}"],
                                      provider=nodemaven) == {"filter": value}

    def test_the_refusal_says_which_evidence_stands_behind_each_value(self):
        """An operator choosing between `medium` and `high` is choosing between
        a value 1240 rows were measured through and one that is on a whitelist,
        and the difference decides whether the result is a comparison or a first
        measurement."""
        with pytest.raises(proxy.ParamError, match=r"medium \(measured\)"):
            proxy.parse_params(["filter=ultra"],
                               provider=providers.load("nodemaven"))

    def test_a_free_form_parameter_takes_what_it_is_given(self):
        """`ttl` is `text`, and its listed values are examples with evidence
        attached rather than a whitelist. `45m` is legal and nobody here has
        sent it; refusing it would refuse the legal values this harness has not
        happened to use yet."""
        assert proxy.parse_params(["ttl=45m"],
                                  provider=providers.load("nodemaven")) == {
            "ttl": "45m"}

    def test_a_parameter_with_no_vocabulary_written_down_is_not_guessed_at(self):
        """`speed` is recognised by the gateway and its legal values were never
        recorded, so this cannot refuse and must not pretend to. The protection
        is the definition's own help text saying so, and the honest behaviour
        here is to let it through rather than to invent a set to check against.
        """
        assert proxy.parse_params(["speed=fast"],
                                  provider=providers.load("nodemaven")) == {
            "speed": "fast"}

    def test_a_definition_with_no_vocabulary_at_all_still_works(self):
        """Every provider file had empty `params` until 2026-09-22 and three of
        the five still describe only some of their names. An unlisted name falls
        through to the name check alone, which is what the whole harness did
        before this existed."""
        assert proxy.parse_params(["country=us"],
                                  provider=providers.load("oxylabs")) == {
            "country": "us"}


class TestProxyStrings:
    def test_url_percent_encodes_the_credentials(self, fake_credentials):
        url = proxy.proxy_url(country="us")
        assert "p%40ss%3Aword%2F1" in url
        assert "@gate.example.invalid:8080" in url
        # One `@`, so no client can parse a different host out of the password.
        assert url.count("@") == 1

    def test_dict_keeps_the_password_unencoded(self, fake_credentials):
        """Playwright takes the fields apart, so encoding them would double it."""
        assert proxy.proxy_dict(country="us") == {
            "server": "http://gate.example.invalid:8080",
            "username": "testlogin-country-us",
            "password": "p@ss:word/1",
        }

    def test_missing_credentials_name_the_fix(self):
        with pytest.raises(config.MissingCredentials, match="Copy .env.example"):
            proxy.proxy_url(country="us")

    def test_missing_credentials_say_nothing_was_sent(self):
        with pytest.raises(config.MissingCredentials, match="Nothing has been sent"):
            proxy.proxy_dict()


class TestConfig:
    def test_available_is_false_without_credentials(self):
        assert config.available() is False

    def test_available_is_true_with_them(self, fake_credentials):
        assert config.available() is True

    def test_port_is_an_integer(self, fake_credentials):
        """The environment hands back strings, and a socket needs a number."""
        assert config.credentials().port == 8080

    def test_a_non_numeric_port_names_the_consequence(self, monkeypatch,
                                                     fake_credentials):
        monkeypatch.setenv("NODEMAVEN_PORT", "8080/tcp")
        with pytest.raises(config.MissingCredentials, match="not a port number"):
            config.credentials()

    def test_the_port_may_come_from_the_definition(self, monkeypatch,
                                                  fake_credentials):
        """A `.env` carrying only a login and a password still reaches the
        vendor's documented gateway, so adding a provider is credentials only."""
        monkeypatch.delenv("NODEMAVEN_HOST", raising=False)
        monkeypatch.delenv("NODEMAVEN_PORT", raising=False)
        creds = config.credentials()
        provider = providers.load("nodemaven")
        assert (creds.host, creds.port) == (provider.host, provider.port)
