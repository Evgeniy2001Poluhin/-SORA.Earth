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
    assert r.headers.get("X-Frame-Options") != "ALLOWALL"


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
