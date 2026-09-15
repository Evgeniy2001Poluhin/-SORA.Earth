"""Which destinations this server will fetch on a user's behalf.

No network in these tests beyond DNS for the public-name case, and nothing here
contacts a metadata service, a real internal host, or the machine running them.
Addresses are asserted against, never connected to.
"""
import ipaddress
import socket

import pytest

from app.services.outbound import (
    AllowlistMisconfigured,
    OutboundRefused,
    allowed_hosts,
    check_outbound_url,
    safe_error,
)


@pytest.fixture(autouse=True)
def permit_everything_by_default(monkeypatch):
    """The tests above this fixture were written before the allowlist and are
    about the address rules. They get a permissive list so they keep testing the
    thing they were written for; the allowlist has its own tests below."""
    monkeypatch.setenv("SORA_WEBHOOK_ALLOWED_HOSTS",
                       ".example.com,example.com,.partner.example,partner.example,"
                       "anything.example.com,harmless-looking.example.com,"
                       "mixed.example.com,internal.example.com,nowhere.invalid")


@pytest.fixture
def resolves_to(monkeypatch):
    """Point every name at addresses of our choosing, so the policy is tested
    rather than the resolver."""
    def _set(*addresses):
        def fake(host, *a, **kw):
            return [
                (socket.AF_INET6 if ":" in addr else socket.AF_INET,
                 socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (addr, 0))
                for addr in addresses
            ]
        monkeypatch.setattr(socket, "getaddrinfo", fake)
    return _set


@pytest.mark.parametrize("address,why", [
    ("127.0.0.1", "loopback"),
    ("10.0.3.7", "private"),
    ("172.16.4.4", "private"),
    ("192.168.1.1", "private"),
    ("169.254.169.254", "link-local, and the cloud metadata address"),
    ("0.0.0.0", "unspecified"),
    ("224.0.0.1", "multicast"),
    ("240.0.0.1", "reserved"),
])
def test_internal_destinations_are_refused(resolves_to, address, why):
    resolves_to(address)
    with pytest.raises(OutboundRefused):
        check_outbound_url("https://anything.example.com/hook")


def test_a_public_destination_is_allowed(resolves_to):
    resolves_to("93.184.216.34")
    assert check_outbound_url("https://example.com/hook")


def test_the_name_is_not_what_is_checked(resolves_to):
    """`http://127.0.0.1/` is easy to spot. A name that resolves to it is the
    same request wearing a different hostname, and only resolution tells them
    apart."""
    resolves_to("127.0.0.1")
    with pytest.raises(OutboundRefused):
        check_outbound_url("https://harmless-looking.example.com/hook")


def test_every_resolved_address_must_pass(resolves_to):
    """One public record beside one private record would otherwise pass, and the
    connection would go to whichever the stack picked."""
    resolves_to("93.184.216.34", "10.0.0.5")
    with pytest.raises(OutboundRefused):
        check_outbound_url("https://mixed.example.com/hook")


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "gopher://example.com/",
    "ftp://example.com/",
    "http://example.com/",
])
def test_only_https_is_fetched(url):
    with pytest.raises(OutboundRefused):
        check_outbound_url(url)


def test_a_name_that_does_not_resolve_is_refused(monkeypatch):
    def boom(*a, **kw):
        raise socket.gaierror("nope")
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(OutboundRefused):
        check_outbound_url("https://nowhere.invalid/hook")


def test_service_ports_are_refused(resolves_to):
    resolves_to("93.184.216.34")
    with pytest.raises(OutboundRefused):
        check_outbound_url("https://example.com:5432/hook")


def test_the_refusal_does_not_name_the_address(resolves_to):
    """The message reaches a delivery record. `10.0.3.7 refused` is the scan
    result the policy exists to withhold."""
    resolves_to("10.0.3.7")
    with pytest.raises(OutboundRefused) as excinfo:
        check_outbound_url("https://internal.example.com/hook")
    assert "10.0.3.7" not in str(excinfo.value)
    assert "private" in str(excinfo.value)


def test_recorded_errors_do_not_carry_the_networks_words():
    """`str(e)[:500]` was stored and served. A connection error naming a host and
    port is a port scan result; five hundred characters of it is a generous one."""
    exc = ConnectionRefusedError("[Errno 111] Connection refused to 10.0.3.7:5432")
    recorded = safe_error(exc)
    assert "10.0.3.7" not in recorded
    assert "5432" not in recorded
    assert recorded == "ConnectionRefusedError"


def test_a_policy_refusal_keeps_its_reason():
    """The category is safe and worth keeping: an operator needs to know why
    their webhook was not called."""
    assert "private" in safe_error(OutboundRefused("destination is a private address"))


# --------------------------------------------------------------- the allowlist

@pytest.fixture
def allow(monkeypatch):
    def _set(*hosts):
        monkeypatch.setenv("SORA_WEBHOOK_ALLOWED_HOSTS", ",".join(hosts))
    return _set


def test_nothing_is_permitted_until_a_list_is_set(monkeypatch, resolves_to):
    """Fail closed. An empty setting refuses every destination rather than
    allowing every destination: a control that is off by default and looks on is
    the shape of defect this whole exercise keeps finding."""
    monkeypatch.delenv("SORA_WEBHOOK_ALLOWED_HOSTS", raising=False)
    resolves_to("93.184.216.34")
    with pytest.raises(OutboundRefused, match="no destinations are permitted"):
        check_outbound_url("https://example.com/hook")


def test_a_listed_host_is_permitted(allow, resolves_to):
    allow("hooks.partner.example")
    resolves_to("93.184.216.34")
    assert check_outbound_url("https://hooks.partner.example/hook")


def test_an_unlisted_host_is_refused(allow, resolves_to):
    allow("hooks.partner.example")
    resolves_to("93.184.216.34")
    with pytest.raises(OutboundRefused, match="allowlist"):
        check_outbound_url("https://elsewhere.example/hook")


def test_a_bare_entry_does_not_admit_subdomains(allow, resolves_to):
    allow("partner.example")
    resolves_to("93.184.216.34")
    with pytest.raises(OutboundRefused, match="allowlist"):
        check_outbound_url("https://sub.partner.example/hook")


def test_a_dotted_entry_admits_subdomains_only(allow, resolves_to):
    allow(".partner.example")
    resolves_to("93.184.216.34")
    assert check_outbound_url("https://a.partner.example/hook")
    with pytest.raises(OutboundRefused, match="allowlist"):
        check_outbound_url("https://partner.example/hook")


def test_the_allowlist_is_not_a_suffix_match(allow, resolves_to):
    """`notpartner.example` ends with `partner.example`. Matching on the suffix
    without requiring the dot is the classic way an allowlist leaks."""
    allow(".partner.example")
    resolves_to("93.184.216.34")
    with pytest.raises(OutboundRefused, match="allowlist"):
        check_outbound_url("https://notpartner.example/hook")


def test_plain_http_is_refused(allow, resolves_to):
    allow("hooks.partner.example")
    resolves_to("93.184.216.34")
    with pytest.raises(OutboundRefused, match="scheme"):
        check_outbound_url("http://hooks.partner.example/hook")


def test_a_listed_host_that_resolves_internally_is_still_refused(allow, resolves_to):
    """The allowlist narrows who may be named; it does not vouch for where the
    name points. Both checks have to hold."""
    allow("hooks.partner.example")
    resolves_to("127.0.0.1")
    with pytest.raises(OutboundRefused, match="loopback"):
        check_outbound_url("https://hooks.partner.example/hook")


def test_an_unlisted_host_is_refused_before_it_is_resolved(monkeypatch):
    """A name nobody approved should not cause a DNS lookup: the lookup is itself
    an outbound signal an attacker can observe."""
    monkeypatch.setenv("SORA_WEBHOOK_ALLOWED_HOSTS", "hooks.partner.example")
    looked_up = []
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda h, *a, **k: looked_up.append(h) or [])
    with pytest.raises(OutboundRefused, match="allowlist"):
        check_outbound_url("https://attacker.example/hook")
    assert looked_up == [], f"resolved {looked_up} despite refusing the host"


# ------------------------------------------------- canonicalisation and config

@pytest.mark.parametrize("written", [
    "hooks.partner.example",
    "HOOKS.PARTNER.EXAMPLE",
    "Hooks.Partner.Example",
    "hooks.partner.example.",
])
def test_one_destination_has_one_spelling(monkeypatch, resolves_to, written):
    """Case and a root dot are the same name. Three answers for one destination
    is a gap somebody eventually walks through."""
    monkeypatch.setenv("SORA_WEBHOOK_ALLOWED_HOSTS", "hooks.partner.example")
    resolves_to("93.184.216.34")
    assert check_outbound_url(f"https://{written}/hook")


def test_an_internationalised_name_is_compared_in_one_alphabet(monkeypatch, resolves_to):
    """Without IDNA a Unicode name never matches its own entry, and a lookalike
    could match one it should not."""
    monkeypatch.setenv("SORA_WEBHOOK_ALLOWED_HOSTS", "пример.example")
    resolves_to("93.184.216.34")
    assert check_outbound_url("https://пример.example/hook")
    assert check_outbound_url("https://xn--e1afmkfd.example/hook")


@pytest.mark.parametrize("url,why", [
    ("https://user@evil.example/hook", "userinfo"),
    ("https://user:pass@evil.example/hook", "userinfo with a password"),
    ("https://hooks.partner.example@evil.example/hook",
     "a permitted name as userinfo -- the allowlist already refuses this on the "
     "hostname, so rejecting userinfo is hardening against parser disagreement "
     "and credential leakage, not a bypass this closes"),
])
def test_credentials_in_the_url_are_refused(monkeypatch, resolves_to, url, why):
    monkeypatch.setenv("SORA_WEBHOOK_ALLOWED_HOSTS", "hooks.partner.example")
    resolves_to("93.184.216.34")
    with pytest.raises(OutboundRefused, match="credentials"):
        check_outbound_url(url)


@pytest.mark.parametrize("host", [
    "partner.example.evil",
    "notpartner.example",
    "partner-example",
])
def test_names_that_merely_resemble_a_permitted_one_are_refused(
        monkeypatch, resolves_to, host):
    monkeypatch.setenv("SORA_WEBHOOK_ALLOWED_HOSTS", "partner.example")
    resolves_to("93.184.216.34")
    with pytest.raises(OutboundRefused, match="allowlist"):
        check_outbound_url(f"https://{host}/hook")


def test_the_port_is_not_part_of_the_host(monkeypatch, resolves_to):
    monkeypatch.setenv("SORA_WEBHOOK_ALLOWED_HOSTS", "hooks.partner.example")
    resolves_to("93.184.216.34")
    assert check_outbound_url("https://hooks.partner.example:8443/hook")


@pytest.mark.parametrize("entry,why", [
    ("https://partner.example", "a url rather than a name"),
    ("partner.example/path", "a path"),
    ("user@partner.example", "userinfo"),
    ("partner.example:443", "a port"),
    ("*.partner.example", "a wildcard"),
])
def test_a_misconfigured_allowlist_fails_loudly(monkeypatch, entry, why):
    """A silently dropped entry is either an outage nobody can explain or a
    permission nobody granted."""
    monkeypatch.setenv("SORA_WEBHOOK_ALLOWED_HOSTS", entry)
    with pytest.raises(AllowlistMisconfigured):
        allowed_hosts()


def test_the_wildcard_message_says_why_it_is_refused(monkeypatch):
    """A permitted zone whose owner lets outsiders create names in it is an open
    destination again, and the person writing the config is the one who knows."""
    monkeypatch.setenv("SORA_WEBHOOK_ALLOWED_HOSTS", "*.partner.example")
    with pytest.raises(AllowlistMisconfigured, match="create names in it"):
        allowed_hosts()


@pytest.mark.parametrize("address,why", [
    ("100.64.0.1", "carrier-grade NAT, RFC 6598 -- not caught by any category name"),
    ("192.0.2.1", "TEST-NET-1"),
    ("198.18.0.1", "benchmarking range"),
    ("255.255.255.255", "broadcast"),
    ("2001:db8::1", "documentation prefix"),
])
def test_addresses_no_category_names_are_still_refused(monkeypatch, resolves_to, address, why):
    """`is_global` is the positive form, and it catches what an enumeration
    forgets. 100.64.0.1 is the one that found this: not loopback, not private,
    not link-local, not multicast, not reserved, not unspecified -- and not
    globally routable either."""
    monkeypatch.setenv("SORA_WEBHOOK_ALLOWED_HOSTS", "hooks.partner.example")
    resolves_to(address)
    with pytest.raises(OutboundRefused):
        check_outbound_url("https://hooks.partner.example/hook")


def test_a_special_purpose_address_that_calls_itself_global_is_refused(
        monkeypatch, resolves_to):
    """64:ff9b::1 is the NAT64 prefix. is_global reports True for it and
    is_reserved reports True, which is why the categories stay: the single flag
    alone would admit what the registry sets aside."""
    monkeypatch.setenv("SORA_WEBHOOK_ALLOWED_HOSTS", "hooks.partner.example")
    resolves_to("64:ff9b::1")
    with pytest.raises(OutboundRefused, match="reserved"):
        check_outbound_url("https://hooks.partner.example/hook")


# --------------------------------------------- ranges pinned, not flags trusted

# `ipaddress`'s is_private and is_global have shifted between Python releases.
# Naming the ranges means an interpreter upgrade that changes a flag fails here
# rather than quietly widening what this server will fetch. Production is 3.11.
SPECIAL_RANGES = [
    ("100.64.0.1", "carrier-grade NAT, RFC 6598"),
    ("100.127.255.254", "the far end of the same range"),
    ("192.0.2.1", "TEST-NET-1"),
    ("198.51.100.1", "TEST-NET-2"),
    ("203.0.113.1", "TEST-NET-3"),
    ("198.18.0.1", "benchmarking, RFC 2544"),
    ("198.19.255.254", "the far end of benchmarking"),
    ("169.254.169.254", "link-local, and the metadata address"),
    ("255.255.255.255", "broadcast"),
    ("0.0.0.0", "unspecified"),
    ("224.0.0.1", "multicast"),
    ("::1", "IPv6 loopback"),
    ("::", "IPv6 unspecified"),
    ("fe80::1", "IPv6 link-local"),
    ("2001:db8::1", "IPv6 documentation"),
    ("2001::1", "Teredo"),
    ("2002:7f00:1::1", "6to4 wrapping 127.0.0.1"),
    ("64:ff9b::1", "NAT64"),
    ("ff02::1", "IPv6 multicast"),
    ("::ffff:127.0.0.1", "IPv4-mapped loopback"),
    ("::ffff:10.0.0.1", "IPv4-mapped private"),
    ("::ffff:169.254.169.254", "IPv4-mapped metadata address"),
]


@pytest.mark.parametrize("address,why", SPECIAL_RANGES)
def test_special_purpose_ranges_are_refused(monkeypatch, resolves_to, address, why):
    monkeypatch.setenv("SORA_WEBHOOK_ALLOWED_HOSTS", "hooks.partner.example")
    resolves_to(address)
    with pytest.raises(OutboundRefused):
        check_outbound_url("https://hooks.partner.example/hook")


def test_an_address_that_cannot_be_read_is_refused_not_skipped(monkeypatch):
    """Skipping is a fail-open.

    One address that will not parse is passed over, the next is public, the check
    succeeds -- and the connection may still go to the one nobody examined. An
    address that cannot be read is an address that cannot be cleared.
    """
    import socket as s
    monkeypatch.setenv("SORA_WEBHOOK_ALLOWED_HOSTS", "hooks.partner.example")
    monkeypatch.setattr(s, "getaddrinfo", lambda *a, **k: [
        (s.AF_INET, s.SOCK_STREAM, s.IPPROTO_TCP, "", ("not-an-address", 0)),
        (s.AF_INET, s.SOCK_STREAM, s.IPPROTO_TCP, "", ("93.184.216.34", 0)),
    ])
    with pytest.raises(OutboundRefused, match="cannot be read"):
        check_outbound_url("https://hooks.partner.example/hook")


def test_an_unexpected_address_family_is_refused(monkeypatch):
    import socket as s
    monkeypatch.setenv("SORA_WEBHOOK_ALLOWED_HOSTS", "hooks.partner.example")
    monkeypatch.setattr(s, "getaddrinfo", lambda *a, **k: [
        (s.AF_UNIX, s.SOCK_STREAM, 0, "", ("/tmp/sock", 0)),
    ])
    with pytest.raises(OutboundRefused, match="address family"):
        check_outbound_url("https://hooks.partner.example/hook")


def test_the_allowlist_is_not_written_to_the_log(monkeypatch, caplog):
    """Partner domains can be commercially sensitive, and this line lands
    wherever the application logs go -- not always somewhere as private as the
    configuration itself."""
    import logging
    from app.services.outbound import validate_configuration
    monkeypatch.setenv("SORA_WEBHOOK_ALLOWED_HOSTS", "hooks.secret-partner.example")
    with caplog.at_level(logging.INFO):
        validate_configuration()
    assert "secret-partner" not in caplog.text
    assert "1 entries" in caplog.text


def test_an_empty_allowlist_says_it_is_deliberate(monkeypatch, caplog):
    import logging
    from app.services.outbound import validate_configuration
    monkeypatch.delenv("SORA_WEBHOOK_ALLOWED_HOSTS", raising=False)
    with caplog.at_level(logging.WARNING):
        validate_configuration()
    assert "safe default" in caplog.text


@pytest.mark.parametrize("proto,accepted", [
    (0, True),
    (6, True),      # IPPROTO_TCP
    (17, False),    # UDP
])
def test_an_unspecified_protocol_is_accepted_and_udp_is_not(monkeypatch, proto, accepted):
    """A resolver is entitled to return proto=0 for a record usable over TCP.

    Refusing it would be a fail-closed nobody can explain -- a webhook that works
    on one platform and not another. Measured on glibc: explicit hints yield
    proto=6, and no hints yield 0, 6 and 17. So 0 is allowed alongside TCP, and
    UDP is not.
    """
    import socket as s
    monkeypatch.setenv("SORA_WEBHOOK_ALLOWED_HOSTS", "hooks.partner.example")
    monkeypatch.setattr(s, "getaddrinfo", lambda *a, **k: [
        (s.AF_INET, s.SOCK_STREAM, proto, "", ("93.184.216.34", 0)),
    ])
    if accepted:
        assert check_outbound_url("https://hooks.partner.example/hook")
    else:
        with pytest.raises(OutboundRefused, match="not TCP"):
            check_outbound_url("https://hooks.partner.example/hook")


# ------------------------------------------------------------- TLS verification

def test_no_bundle_configured_means_the_system_trust_store(monkeypatch):
    from app.services.outbound import ca_bundle
    monkeypatch.delenv("SORA_WEBHOOK_CA_BUNDLE", raising=False)
    assert ca_bundle() is True


def test_a_configured_bundle_is_returned(monkeypatch, tmp_path):
    from app.services.outbound import ca_bundle
    pem = tmp_path / "ca.pem"
    pem.write_text("-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n")
    monkeypatch.setenv("SORA_WEBHOOK_CA_BUNDLE", str(pem))
    assert ca_bundle() == str(pem)


@pytest.mark.parametrize("content,why", [
    ("", "empty"),
    ("   \n", "whitespace only"),
    ("this is not a certificate", "no PEM block"),
    ("-----BEGIN PRIVATE KEY-----\n", "a key rather than a certificate"),
])
def test_a_bundle_without_a_certificate_fails_at_startup(monkeypatch, tmp_path, content, why):
    """A path that exists and holds no certificate is the worst of the three
    outcomes: TLS then fails against every destination while the setting looks
    configured."""
    from app.services.outbound import AllowlistMisconfigured, ca_bundle
    pem = tmp_path / "ca.pem"
    pem.write_text(content)
    monkeypatch.setenv("SORA_WEBHOOK_CA_BUNDLE", str(pem))
    with pytest.raises(AllowlistMisconfigured):
        ca_bundle()


def test_a_directory_is_not_a_bundle(monkeypatch, tmp_path):
    from app.services.outbound import AllowlistMisconfigured, ca_bundle
    monkeypatch.setenv("SORA_WEBHOOK_CA_BUNDLE", str(tmp_path))
    with pytest.raises(AllowlistMisconfigured, match="not a file"):
        ca_bundle()


def test_a_bundle_path_that_does_not_exist_fails_at_startup(monkeypatch):
    """A missing CA file must stop the deployment, not surface as a TLS error on
    the first webhook of the night."""
    from app.services.outbound import AllowlistMisconfigured, validate_configuration
    monkeypatch.setenv("SORA_WEBHOOK_ALLOWED_HOSTS", "hooks.partner.example")
    monkeypatch.setenv("SORA_WEBHOOK_CA_BUNDLE", "/no/such/ca.pem")
    with pytest.raises(AllowlistMisconfigured, match="not a file"):
        validate_configuration()


def test_there_is_no_setting_that_disables_verification(monkeypatch, tmp_path):
    """ca_bundle never returns False.

    trust_env=False switched off REQUESTS_CA_BUNDLE along with the proxy
    variables, and the tempting repair is `verify=False`. An unverified TLS
    connection to an address that passed the destination check still ends up
    wherever someone on the path decides, so the value simply does not exist.
    """
    from app.services.outbound import ca_bundle
    # These are not keywords here. Any non-empty value is a path, so each fails
    # as a file that does not exist -- which is the point: there is no spelling
    # of "off". Inventing one would be inventing the vulnerability.
    for value in ("0", "false", "no", "off", "none", "disable"):
        monkeypatch.setenv("SORA_WEBHOOK_CA_BUNDLE", value)
        with pytest.raises(Exception):
            ca_bundle()
    monkeypatch.setenv("SORA_WEBHOOK_CA_BUNDLE", "")
    assert ca_bundle() is True
