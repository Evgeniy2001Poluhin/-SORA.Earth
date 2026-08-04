"""The embed widget's framing policy.

It previously sent `X-Frame-Options: ALLOWALL`, which is not a value that header
has: the specification defines DENY and SAMEORIGIN, and browsers ignore anything
else. The widget therefore had no frame policy while carrying a header that read
as a decision about framing -- the same shape of defect as a middleware that
forwards every request while the documentation states its limits.
"""
import importlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client(monkeypatch, ancestors=None):
    if ancestors is None:
        monkeypatch.delenv("SORA_EMBED_FRAME_ANCESTORS", raising=False)
    else:
        monkeypatch.setenv("SORA_EMBED_FRAME_ANCESTORS", ancestors)
    module = importlib.import_module("app.api.embed.api")
    app = FastAPI()
    app.include_router(module.router)
    return TestClient(app)


def test_the_widget_declares_a_frame_policy(monkeypatch):
    r = _client(monkeypatch).get("/embed")
    assert r.status_code == 200
    assert r.headers["Content-Security-Policy"] == "frame-ancestors *"


def test_the_invalid_header_is_gone(monkeypatch):
    """ALLOWALL is not an X-Frame-Options value; sending it protected nothing and
    suggested otherwise."""
    r = _client(monkeypatch).get("/embed")
    # Absent, not merely different.
    #
    # `!= "ALLOWALL"` passes on any other value, including DENY or SAMEORIGIN --
    # either of which would stop the widget being framed at all and break the
    # one thing it exists for, while this test reported the removal as done.
    assert "X-Frame-Options" not in r.headers, r.headers.get("X-Frame-Options")


def test_the_policy_can_be_narrowed_without_a_code_change(monkeypatch):
    r = _client(monkeypatch, "https://partner.example https://other.example").get("/embed")
    assert r.headers["Content-Security-Policy"] == \
        "frame-ancestors https://partner.example https://other.example"


def test_an_empty_setting_does_not_produce_a_meaningless_policy(monkeypatch):
    """`frame-ancestors` with nothing after it is a syntax error, and a browser
    that cannot parse the directive falls back to no policy at all -- so a blank
    environment variable would silently undo the header rather than tighten it."""
    r = _client(monkeypatch, "   ").get("/embed")
    assert r.headers["Content-Security-Policy"] == "frame-ancestors *"


def test_the_snippet_still_hands_out_an_iframe(monkeypatch):
    """Framing is the purpose. A policy that broke this would be the wrong fix."""
    r = _client(monkeypatch).get("/embed/snippet")
    assert "<iframe" in r.text and "/api/v1/embed" in r.text


# --- the escape hatch must not rebuild the defect ---------------------------
#
# The header this change removes was ignored by browsers because its value was
# not one they implement. An unvalidated SORA_EMBED_FRAME_ANCESTORS produces the
# same outcome by another route: a malformed directive is ignored, the widget is
# framable by anyone, and the configuration says otherwise.

import pytest

from app.api.embed.api import (
    FrameAncestorsError,
    _frame_ancestors,
    validate_frame_ancestors_config,
)


@pytest.mark.parametrize("value,expected", [
    (None, "*"),
    ("", "*"),
    ("   ", "*"),
    ("*", "*"),
    ("'self'", "'self'"),
    ("'none'", "'none'"),
    ("https://example.com", "https://example.com"),
    ("https://a.example https://b.example", "https://a.example https://b.example"),
    ("https://*.example.com", "https://*.example.com"),
    ("example.com:8443", "example.com:8443"),
    # Forms the first validator rejected, which mattered once a rejection became
    # a refusal to boot: a too-narrow check would have stopped the process on a
    # correct configuration.
    ("https:", "https:"),                        # scheme-source: any https origin
    ("wss:", "wss:"),
    ("https://partner.example:*", "https://partner.example:*"),   # any port
    ("https://*.a.example:* https://b.example", "https://*.a.example:* https://b.example"),
])
def test_usable_values_are_passed_through(monkeypatch, value, expected):
    """Unset is not the same as wrong: absent means no restriction was asked
    for, which is the documented default for a widget meant to be embedded."""
    if value is None:
        monkeypatch.delenv("SORA_EMBED_FRAME_ANCESTORS", raising=False)
    else:
        monkeypatch.setenv("SORA_EMBED_FRAME_ANCESTORS", value)
    assert _frame_ancestors() == expected


@pytest.mark.parametrize("value", [
    "javascript:alert(1)",
    "https://a.example; script-src *",       # directive injection
    "https://a.example, https://b.example",  # commas are not CSP list syntax
    '"quoted"',
    "* https://a.example",                   # * with anything else is meaningless
    "'none' https://a.example",              # 'none' with anything else likewise
    "<script>",
    "https://a.example:99999x",   # not a port
])
def test_an_unusable_value_is_refused_not_widened(monkeypatch, value):
    """Refused, not fallen back to "*".

    An earlier version warned and used the open default. That is fail-open on a
    security control: nobody sets this variable except to restrict, so serving a
    wider policy than the one asked for is the failure that matters -- and a
    warning in a log nobody reads is not consent.

    Enumerated rather than sampled, because this value reaches a security header
    and a guard written against the one example that prompted it is how ALLOWALL
    survived in the first place.
    """
    monkeypatch.setenv("SORA_EMBED_FRAME_ANCESTORS", value)
    with pytest.raises(FrameAncestorsError):
        _frame_ancestors()


def test_the_refusal_names_the_offending_value(monkeypatch):
    monkeypatch.setenv("SORA_EMBED_FRAME_ANCESTORS", "javascript:alert(1)")
    with pytest.raises(FrameAncestorsError, match="javascript"):
        _frame_ancestors()


def test_startup_validation_refuses_a_bad_policy(monkeypatch):
    """The process must not boot with this misconfigured.

    Discovered on the first embed request instead, the deployment would look
    healthy -- every other route working, health green -- while the one control
    this module provides was unusable.
    """
    monkeypatch.setenv("SORA_EMBED_FRAME_ANCESTORS", "https://a; script-src *")
    with pytest.raises(FrameAncestorsError):
        validate_frame_ancestors_config()


def test_startup_validation_accepts_an_unset_policy(monkeypatch):
    monkeypatch.delenv("SORA_EMBED_FRAME_ANCESTORS", raising=False)
    assert validate_frame_ancestors_config() == "*"


def test_a_path_is_refused_as_our_limitation_not_as_a_malformed_value(monkeypatch):
    """CSP permits a path here; this setting does not, and says which is which.

    CSP3 defines host-source with an optional path-part and frame-ancestors uses
    host-source, so `https://partner.example/embed/` is legitimate. An earlier
    version of this validator asserted the opposite -- that a path in
    frame-ancestors is meaningless -- and under fail-closed a wrong assertion in
    a validator refuses a correct configuration and stops the process.

    Rather than approximate the normative grammar in a regular expression, the
    supported format is narrower and the refusal names that. Telling an operator
    their correct CSP is "unusable" would send them hunting a typo that is not
    there.
    """
    monkeypatch.setenv("SORA_EMBED_FRAME_ANCESTORS", "https://partner.example/embed/")
    with pytest.raises(FrameAncestorsError, match="origin-form sources only"):
        _frame_ancestors()


def test_a_malformed_value_is_not_reported_as_a_path_limitation(monkeypatch):
    """The two refusals must stay distinguishable: one is the operator's
    mistake, the other is ours."""
    monkeypatch.setenv("SORA_EMBED_FRAME_ANCESTORS", "https://a.example; script-src *")
    with pytest.raises(FrameAncestorsError, match="unusable source"):
        _frame_ancestors()
