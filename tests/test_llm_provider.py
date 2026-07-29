"""The optional LLM must be optional, and off unless somebody asks for it.

The Co-Pilot's verdict, probability, drivers and risks come from the model and
from templates. An LLM only rewrites prose. These tests pin that separation,
because the failure they prevent is quiet: a deployment that looks fine until
the provider is unreachable, and then isn't.
"""
import pytest

from app.services import llm_provider
from app.services.llm_provider import LLMConfigError, enabled, mode, settings

WORKING = {
    "COPILOT_LLM_ENABLED": "true",
    "COPILOT_LLM_BASE_URL": "https://example.invalid/v1",
    "COPILOT_LLM_MODEL": "some-model",
    "COPILOT_LLM_API_KEY": "k",
}


def test_it_is_off_unless_asked(monkeypatch):
    monkeypatch.delenv("COPILOT_LLM_ENABLED", raising=False)
    assert enabled() is False
    assert mode() == "smart_template"


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_the_switch_accepts_the_usual_spellings(monkeypatch, value):
    monkeypatch.setenv("COPILOT_LLM_ENABLED", value)
    assert enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_anything_else_is_off(monkeypatch, value):
    monkeypatch.setenv("COPILOT_LLM_ENABLED", value)
    assert enabled() is False


def test_a_stray_key_alone_does_not_turn_it_on(monkeypatch):
    """The previous gate was the presence of a key, with api.openai.com as the
    default endpoint. A forgotten variable was enough to start calling out."""
    monkeypatch.delenv("COPILOT_LLM_ENABLED", raising=False)
    monkeypatch.setenv("COPILOT_LLM_API_KEY", "leftover-from-an-old-deployment")
    monkeypatch.setenv("OPENAI_API_KEY", "also-leftover")

    assert enabled() is False
    assert mode() == "smart_template"


def test_there_is_no_default_endpoint(monkeypatch):
    """Naming where the model lives is the point: it cannot be inherited."""
    for name in ("COPILOT_LLM_BASE_URL", "COPILOT_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("COPILOT_LLM_ENABLED", "true")

    with pytest.raises(LLMConfigError, match="no default endpoint"):
        settings()
    assert mode() == "smart_template_misconfigured"


def test_a_complete_configuration_resolves(monkeypatch):
    for k, v in WORKING.items():
        monkeypatch.setenv(k, v)
    resolved = settings()
    assert resolved["base_url"] == WORKING["COPILOT_LLM_BASE_URL"]
    assert resolved["model"] == WORKING["COPILOT_LLM_MODEL"]
    assert mode() == "smart_template_with_llm_rewrite"


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "ollama"])
def test_plain_http_is_allowed_only_to_a_local_server(monkeypatch, host):
    """Self-hosted means nothing leaves the host, so TLS buys nothing there."""
    for k, v in WORKING.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("COPILOT_LLM_BASE_URL", f"http://{host}:11434/v1")
    assert settings()["base_url"].startswith("http://")


def test_plain_http_to_anywhere_else_is_refused(monkeypatch):
    """The request carries project data and the key."""
    for k, v in WORKING.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("COPILOT_LLM_BASE_URL", "http://llm.example.invalid/v1")
    with pytest.raises(LLMConfigError, match="use https"):
        settings()


def test_retired_variables_are_ignored_not_rejected():
    """Refusing to start over a forgotten variable turns a no-op into an outage
    during whatever change happened to surface it."""
    present = llm_provider.warn_about_legacy_variables(
        {"OPENAI_API_KEY": "x", "OPENAI_BASE_URL": "y"}
    )
    assert set(present) == {"OPENAI_API_KEY", "OPENAI_BASE_URL"}


def test_the_warning_never_carries_the_value(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="sora_earth"):
        llm_provider.warn_about_legacy_variables({"OPENAI_API_KEY": "sk-secret-value-here"})

    for record in caplog.records:
        assert "sk-secret-value-here" not in record.getMessage()


def test_nothing_names_a_provider_we_cannot_administer():
    """api.openai.com answers -- it returns 401. The reason it is gone is that
    the account cannot be administered from here: no key can be created,
    rotated or revoked. A hostname denylist would not express that."""
    import inspect

    source = inspect.getsource(llm_provider)
    for name in ("api.openai.com", "openrouter.ai", "gpt-4o"):
        assert name not in source.split('"""')[2], f"{name} is configured again"
