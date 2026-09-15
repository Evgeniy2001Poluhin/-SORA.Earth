"""API keys are never literals in the source, and production has none unless configured (Security C).

`admin-key-2026` sat in `_build_api_keys` unconditionally, so it was a published
admin credential: sent as `X-API-Key`, it satisfied `require_admin_apikey`. Keys
now come only from the environment, with two clearly-named dev keys added outside
production. In production the store is empty unless `ADMIN_API_KEY` / `API_KEY`
are set — fail closed, not fail open on a known string.
"""
import importlib

import app.auth as auth


def test_the_published_literal_keys_are_gone():
    for literal in ("admin-key-2026", "demo-key-2026"):
        assert literal not in auth.API_KEYS, (
            "%s is still a valid API key; it is in the public source" % literal
        )


def test_production_keys_come_only_from_the_environment(monkeypatch):
    monkeypatch.setattr(auth, "SORA_ENV", "production")
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    assert auth._build_api_keys() == {}, "production accepted a key nobody configured"

    monkeypatch.setenv("ADMIN_API_KEY", "rotated-secret-value")
    built = auth._build_api_keys()
    assert "rotated-secret-value" in built and built["rotated-secret-value"]["role"] == "admin"
    assert "admin-key-2026" not in built


def test_dev_keys_exist_only_outside_production(monkeypatch):
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)

    monkeypatch.setattr(auth, "SORA_ENV", "development")
    assert "dev-admin-key" in auth._build_api_keys()

    monkeypatch.setattr(auth, "SORA_ENV", "production")
    assert "dev-admin-key" not in auth._build_api_keys()
