"""Deciding whether the server may make a request to a caller-supplied URL.

A URL that a user stores and the server later fetches is a request the server
makes on the user's behalf, from inside the network, with whatever reachability
the server has and the user does not. Authorisation on the endpoint that stores
the URL narrows who can ask; it does not make any particular URL safe to fetch.
So the destination is checked here, separately from who supplied it.

What this refuses, and why each one:

  loopback        127.0.0.0/8, ::1 -- the application's own admin surfaces,
                  and anything else bound to localhost in the container
  private         10/8, 172.16/12, 192.168/16 and the IPv6 equivalents -- the
                  database, Redis, MLflow, Prometheus, Grafana
  link-local      169.254/16, fe80::/10 -- the cloud metadata service lives at
                  169.254.169.254
  reserved        multicast, broadcast, unspecified, and everything IANA has
                  set aside; none of it is a legitimate webhook destination

Checked after resolution rather than on the string. `http://127.0.0.1/` is easy
to spot; `http://spoofed.example.com/` that resolves to 127.0.0.1 is the same
request wearing a different name, and only resolution tells them apart.

Re-resolved at request time as well as at registration, which narrows the window
and does NOT close it. Precisely what this is:

    resolve #1 -> validate            (at registration)
    resolve #2 -> validate            (at request time)
    requests.post(hostname) -> resolve #3 -> connect

The third lookup decides where the connection goes, and nothing here ties it to
the two that were checked. That is a time-of-check to time-of-use gap, and DNS
rebinding lives in exactly it. Saying otherwise in a comment would be worse than
saying nothing, because the next reader would stop looking.

Closing it needs the connection pinned to the address that was validated -- a
transport that dials the checked IP and carries the original Host header -- and
that is not implemented here. Until it is, what actually bounds this sits outside
this function: an allowlist of destination domains, HTTPS only, `trust_env=False`
so an inherited proxy cannot redirect the connection, redirects refused, and an
egress policy at the network layer.

Redirects are refused rather than followed. A permitted destination that answers
with a redirect to a forbidden one would otherwise reach it, and following the
hop puts the decision back in the hands of the thing being validated.
"""
import ipaddress
import logging
import os
import socket

logger = logging.getLogger(__name__)
from urllib.parse import urlparse

ALLOWED_SCHEMES = frozenset({"https"})

# Fail closed. An empty list refuses every destination rather than allowing every
# destination, because the alternative is a control that is off by default and
# looks on -- which is the shape of defect this whole exercise keeps finding.
#
#   SORA_WEBHOOK_ALLOWED_HOSTS=hooks.partner.example,.internal-tools.example
#
# A bare name matches exactly. A leading dot matches that domain's subdomains and
# not the domain itself, so `.example.com` admits `a.example.com` and refuses
# both `example.com` and `notexample.com` -- suffix matching without the dot
# would admit the last of those, which is the classic way an allowlist leaks.
ALLOWED_HOSTS_ENV = "SORA_WEBHOOK_ALLOWED_HOSTS"

# Where to find the certificate authority for webhook destinations, when they are
# not signed by one the system already trusts.
#
# This exists because `trust_env = False` -- set on the dispatch session so an
# inherited HTTP_PROXY cannot reroute the connection -- also switches off
# REQUESTS_CA_BUNDLE and CURL_CA_BUNDLE. The two settings share one flag, so
# turning off the proxy variables turns off the CA variables with them. A private
# PKI therefore needs to be named explicitly rather than exported.
#
# Unset means the system trust store, which is the right default. What it must
# never mean is verification off: an unverified TLS connection to an address that
# passed a destination check still ends up wherever an attacker on the path
# decides.
#
# BUNDLE, not certificate, and the distinction is the one that bites. Setting
# `session.verify` to a path REPLACES the system trust store rather than adding
# to it, so a file holding only a private root makes every destination with an
# ordinary public certificate fail -- and it fails at the partner's endpoint,
# where the cause is least visible. Whoever sets this must supply a complete
# bundle: the private roots they need AND the public roots they still rely on,
# concatenated. `cat /etc/ssl/certs/ca-certificates.crt private-ca.pem` is the
# usual way to build one.
CA_BUNDLE_ENV = "SORA_WEBHOOK_CA_BUNDLE"


class AllowlistMisconfigured(ValueError):
    """The allowlist itself is wrong. Loud, because a silently dropped entry is
    either an outage nobody can explain or a permission nobody granted."""


def canonical_host(host: str) -> str:
    """One spelling for a name, so comparison means what it looks like it means.

    `EXAMPLE.COM`, `example.com.` and an internationalised form are the same
    destination and must not be three different answers. Lowercased, the root
    dot removed, and encoded through IDNA so a Unicode name is compared in the
    same alphabet as the list it is checked against -- without that,
    `пример.example` never matches its own entry and, worse, a lookalike could
    match one it should not.
    """
    host = (host or "").strip().rstrip(".").lower()
    if not host:
        raise OutboundRefused("no host in url")
    try:
        # Already-ASCII names pass through unchanged; Unicode becomes xn--.
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise OutboundRefused("host is not a usable name") from exc
    return host


def _validate_entry(entry: str) -> str:
    """An allowlist entry, canonicalised, or a loud failure."""
    entry = entry.strip().lower()
    subdomain_form = entry.startswith(".")
    body = entry[1:] if subdomain_form else entry
    if not body or "/" in entry or "@" in entry or ":" in entry:
        raise AllowlistMisconfigured(
            f"{ALLOWED_HOSTS_ENV} entry {entry!r} is not a hostname; "
            "entries are bare names, optionally prefixed with a dot for subdomains")
    if "*" in entry:
        raise AllowlistMisconfigured(
            f"{ALLOWED_HOSTS_ENV} entry {entry!r} uses a wildcard. Write "
            "'.example.com' for subdomains -- and consider whether the zone lets "
            "outsiders create names in it, because then a permitted zone is an "
            "open destination again")
    try:
        body = canonical_host(body)
    except OutboundRefused as exc:
        raise AllowlistMisconfigured(
            f"{ALLOWED_HOSTS_ENV} entry {entry!r}: {exc}") from exc
    return ("." + body) if subdomain_form else body


def allowed_hosts() -> list:
    """The configured destinations, canonicalised.

    Semantics, stated because the difference is one character:

        example.com     matches example.com and nothing else
        .example.com    matches its subdomains and NOT example.com itself
    """
    raw = os.getenv(ALLOWED_HOSTS_ENV, "")
    return [_validate_entry(part) for part in raw.split(",") if part.strip()]


def _host_is_allowed(host: str, allowed: list) -> bool:
    for entry in allowed:
        if entry.startswith("."):
            if host.endswith(entry):
                return True
        elif host == entry:
            return True
    return False

# Ports that are not a webhook receiver but are something else worth reaching.
# Not a security boundary on its own -- the address rules are -- but it removes
# the obvious ones from a scan.
BLOCKED_PORTS = frozenset({22, 23, 25, 445, 3306, 5432, 6379, 9090, 11211, 27017})


class OutboundRefused(ValueError):
    """The destination is not one this server will fetch."""


def _addresses(host: str):
    """Every address the name resolves to, not just the first.

    A name with several records where one is public and one is private would
    otherwise pass the check and be fetched at whichever address the connection
    happens to pick.
    """
    try:
        # Families pinned: anything the resolver offers that is not IPv4 or IPv6
        # is not something to connect to, and asking for the two we understand is
        # clearer than filtering afterwards.
        infos = socket.getaddrinfo(host, None, family=socket.AF_UNSPEC,
                                   type=socket.SOCK_STREAM,
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise OutboundRefused(f"cannot resolve {host}") from exc

    seen = []
    for entry in infos:
        # The shape first. A resolver result that is not the five-tuple this
        # expects is not something to reach into and hope: reading sockaddr[0]
        # from an unexpected structure is how a check ends up examining one thing
        # and the connection using another.
        if not isinstance(entry, (tuple, list)) or len(entry) != 5:
            raise OutboundRefused("resolver returned something unexpected")
        family, socktype, proto, _canon, sockaddr = entry
        if family not in (socket.AF_INET, socket.AF_INET6):
            raise OutboundRefused("resolved to an address family this does not handle")
        # SOCK_STREAM is asserted; proto is allowed to be unspecified.
        #
        # The call above passes explicit hints, and on glibc that yields proto=6
        # every time -- measured, alongside proto in [0, 6, 17] when no hints are
        # given. But other resolver implementations are entitled to return 0 for
        # a record that is perfectly usable over TCP, and refusing it would be a
        # fail-closed that nobody can explain: a legitimate webhook that stops
        # working on one platform and not another. 0 with SOCK_STREAM means
        # "unspecified stream", which is TCP in practice.
        if socktype != socket.SOCK_STREAM:
            raise OutboundRefused("resolved to something that is not a stream socket")
        if proto not in (0, socket.IPPROTO_TCP):
            raise OutboundRefused("resolved to a protocol that is not TCP")
        if not isinstance(sockaddr, (tuple, list)) or not sockaddr:
            raise OutboundRefused("resolver returned an address in an unexpected form")
        raw = sockaddr[0]
        try:
            seen.append(ipaddress.ip_address(raw))
        except ValueError as exc:
            # Refused rather than skipped. Skipping is a fail-open: one address
            # that will not parse is passed over, the next one is public, the
            # check succeeds, and the connection may still go to the one nobody
            # examined. An address that cannot be read is an address that cannot
            # be cleared.
            raise OutboundRefused("resolved to an address that cannot be read") from exc
    if not seen:
        raise OutboundRefused(f"{host} resolved to nothing usable")
    # Deduplicated after parsing, so `93.184.216.34` and `::ffff:93.184.216.34`
    # are not checked twice under two spellings -- and so the count in a refusal
    # reflects distinct destinations rather than resolver bookkeeping.
    unique = []
    for address in seen:
        if address not in unique:
            unique.append(address)
    return unique


def _refuse_if_internal(address: ipaddress._BaseAddress) -> None:
    """Both tests, because neither subsumes the other.

    `is_global` is the positive form and catches what an enumeration forgets:
    100.64.0.1 -- carrier-grade NAT, RFC 6598 -- is not loopback, private,
    link-local, multicast, reserved or unspecified, so listing categories let it
    through. Measured, which is how it was found.

    The enumeration is still needed, because `is_global` is true for addresses
    that are nonetheless special-purpose: 64:ff9b::1, the NAT64 prefix, reports
    is_global True and is_reserved True. Dropping the categories in favour of the
    single flag would open what the flag calls global and the registry does not.
    """
    # Named categories first, the catch-all last: "destination is a loopback
    # address" tells an operator what happened, and "not globally routable" only
    # tells them it did.
    for name, bad in (
        ("loopback", address.is_loopback),
        ("private", address.is_private),
        ("link-local", address.is_link_local),
        ("multicast", address.is_multicast),
        ("reserved", address.is_reserved),
        ("unspecified", address.is_unspecified),
        ("non-routable", not address.is_global),
    ):
        if bad:
            # The category, never the address. This message reaches a publicly
            # readable delivery record, and "10.0.3.7 refused connection" is the
            # scan result the whole exercise is meant to withhold.
            raise OutboundRefused(f"destination is a {name} address")


def ca_bundle():
    """The verification setting for an outbound webhook request.

    Returns a path for `session.verify` when one is configured, or True for the
    system trust store. Never False -- there is no configuration that turns
    verification off, because a caller who wants that has misdiagnosed something.
    """
    path = os.getenv(CA_BUNDLE_ENV, "").strip()
    if not path:
        return True

    # Any non-empty value is a path. There is no `false`, no `off`, no `0`:
    # treating them as words would be inventing a way to disable verification,
    # and as paths they fail here like any other name that is not a file.
    if not os.path.isfile(path):
        raise AllowlistMisconfigured(
            f"{CA_BUNDLE_ENV} points at {path!r}, which is not a file")
    if not os.access(path, os.R_OK):
        # Read as the user the container actually runs as, which is not root.
        raise AllowlistMisconfigured(
            f"{CA_BUNDLE_ENV} at {path!r} is not readable by uid {os.getuid()}")
    try:
        content = open(path, "rb").read()
    except OSError as exc:
        raise AllowlistMisconfigured(f"{CA_BUNDLE_ENV} at {path!r}: {exc}") from exc
    if not content.strip():
        raise AllowlistMisconfigured(f"{CA_BUNDLE_ENV} at {path!r} is empty")
    if b"-----BEGIN CERTIFICATE-----" not in content:
        # A path that exists and holds no certificate is the worst of the three
        # outcomes: TLS then fails against every destination, and the setting
        # looks configured.
        raise AllowlistMisconfigured(
            f"{CA_BUNDLE_ENV} at {path!r} contains no PEM certificate")
    return path


def validate_configuration() -> list:
    """Read the allowlist once and fail loudly if it is wrong.

    Called at startup so a typo stops the process there rather than at three in
    the morning when a drift event finally fires the dispatcher. Returns the
    parsed list so a caller can log what was accepted -- an operator who cannot
    see which destinations are permitted has a configuration they are trusting
    rather than one they have checked.
    """
    hosts = allowed_hosts()
    bundle = ca_bundle()
    if bundle is not True:
        logger.info("webhook TLS verification uses a configured bundle")
    if not hosts:
        logger.warning(
            "%s is empty: webhook registration and delivery are both refused. "
            "This is the safe default, not a fault -- set it to enable webhooks.",
            ALLOWED_HOSTS_ENV)
    else:
        # The count alone. A plain digest of the sorted domains looked like a
        # safe summary and is not: the space of plausible partner hostnames is
        # small enough to enumerate against it, so the hash discloses roughly
        # what printing the list would. Anything more informative than a count
        # needs a keyed digest or a configuration version, and neither is worth
        # inventing here.
        logger.info("webhook destinations permitted: %d entries", len(hosts))
    return hosts


def check_outbound_url(url: str) -> str:
    """Return the url if this server may fetch it, or raise OutboundRefused.

    Narrows the destination; does not pin the connection. See the module
    docstring on why re-resolution is not a rebinding defence.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise OutboundRefused(f"scheme {parsed.scheme!r} is not allowed")
    if parsed.username or parsed.password:
        # `https://allowed.example@evil.example/` has a hostname of evil.example,
        # which the allowlist would catch -- but parsers disagree about userinfo
        # often enough that the safe answer is not to accept it at all.
        raise OutboundRefused("credentials in the url are not accepted")
    if not parsed.hostname:
        raise OutboundRefused("no host in url")
    host = canonical_host(parsed.hostname)
    if parsed.port is not None and parsed.port in BLOCKED_PORTS:
        raise OutboundRefused(f"port {parsed.port} is not a webhook receiver")

    # The allowlist is checked before resolution: a name nobody approved should
    # not cause a DNS lookup, which is itself an outbound signal an attacker can
    # observe.
    allowed = allowed_hosts()
    if not allowed:
        raise OutboundRefused(
            f"no destinations are permitted; set {ALLOWED_HOSTS_ENV} to enable webhooks")
    if not _host_is_allowed(host, allowed):
        raise OutboundRefused("destination host is not on the allowlist")
    for address in _addresses(host):
        _refuse_if_internal(address)
    return url


def safe_error(exc: BaseException) -> str:
    """What may be recorded about a failed delivery.

    The previous code stored `str(e)[:500]` in a row served by an unauthenticated
    endpoint. A connection error naming a host and port is a port scan result;
    five hundred characters of it is a generous one. Only the shape of the
    failure is kept.
    """
    if isinstance(exc, OutboundRefused):
        return str(exc)
    return type(exc).__name__
