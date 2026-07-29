"""Optional LLM, behind a provider-neutral switch that defaults to off.

The Co-Pilot computes its verdict, probability, drivers and risks from the model
and from templates. An LLM only rewrites the prose of `executive_summary` and
answers follow-up questions. That distinction is the whole design: nothing a
user relies on may depend on a service being reachable.

OpenAI and OpenRouter are no longer configured. Not because their endpoints are
unreachable -- api.openai.com answers, it returns 401 -- but because the
accounts cannot be administered from here: a key cannot be created, rotated or
revoked. Holding a credential you cannot revoke is a standing risk in exchange
for nicer wording.

There is deliberately no default endpoint. Enabling the LLM means naming where
it lives, so a stray key can never turn on an outbound call by itself.

Both remaining candidates speak the OpenAI wire format, so the adapter is the
same for either:

    self-hosted (nothing leaves the host)
        COPILOT_LLM_ENABLED=true
        COPILOT_LLM_BASE_URL=http://ollama:11434/v1
        COPILOT_LLM_MODEL=qwen2.5:7b-instruct
        COPILOT_LLM_API_KEY=ollama          # not checked by a local server

    managed
        COPILOT_LLM_ENABLED=true
        COPILOT_LLM_BASE_URL=<provider's OpenAI-compatible endpoint>
        COPILOT_LLM_MODEL=<provider's model id>
        COPILOT_LLM_API_KEY=<from a secret, never a literal>
"""
import logging
import os

log = logging.getLogger("sora_earth")

_LEGACY = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "LLM_MODEL")

_TRUE = {"1", "true", "yes", "on"}


class LLMConfigError(RuntimeError):
    """Enabled but not usable. Raised at call time, never at import."""


def enabled() -> bool:
    return os.getenv("COPILOT_LLM_ENABLED", "false").strip().lower() in _TRUE


def warn_about_legacy_variables(environ=None) -> list[str]:
    """Name any leftover provider-specific variables. Never their values.

    Ignored rather than rejected. A forgotten key in an old deployment file is
    harmless once nothing reads it, and refusing to start over one would turn a
    no-op into an outage during whatever change surfaced it.
    """
    environ = os.environ if environ is None else environ
    present = [name for name in _LEGACY if environ.get(name)]
    if present:
        log.warning(
            "ignoring retired LLM configuration: %s. OpenAI and OpenRouter are no "
            "longer configured; see docs/SECRETS.md. Use COPILOT_LLM_* instead.",
            ", ".join(present),
        )
    return present


def settings(environ=None) -> dict:
    """Resolved settings, or raise if enabled and incomplete.

    Incomplete-and-enabled is a configuration mistake worth surfacing: the
    operator asked for an LLM and would otherwise get templates with no
    explanation of why.
    """
    environ = os.environ if environ is None else environ
    base_url = (environ.get("COPILOT_LLM_BASE_URL") or "").strip()
    model = (environ.get("COPILOT_LLM_MODEL") or "").strip()
    api_key = environ.get("COPILOT_LLM_API_KEY") or ""

    missing = [n for n, v in (("COPILOT_LLM_BASE_URL", base_url),
                              ("COPILOT_LLM_MODEL", model)) if not v]
    if missing:
        raise LLMConfigError(
            "COPILOT_LLM_ENABLED is set but " + " and ".join(missing) +
            " is not. There is no default endpoint: enabling the LLM means "
            "naming where it lives."
        )

    # http is allowed only for a host-local server. Anything else must be TLS:
    # the prompt carries project data, and the key travels with it.
    if base_url.startswith("http://"):
        host = base_url.split("://", 1)[1].split("/")[0].split(":")[0]
        if host not in ("localhost", "127.0.0.1", "::1", "ollama"):
            raise LLMConfigError(
                "COPILOT_LLM_BASE_URL uses http:// to a non-local host. "
                "The request carries project data and the key; use https."
            )
    return {"base_url": base_url, "model": model, "api_key": api_key}


def mode() -> str:
    """What the Co-Pilot will actually do, for the health endpoint."""
    if not enabled():
        return "smart_template"
    try:
        settings()
    except LLMConfigError:
        return "smart_template_misconfigured"
    return "smart_template_with_llm_rewrite"
