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
