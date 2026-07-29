"""Production must not start on a placeholder.

A missing secret fails loudly. A placeholder does not: the process starts,
serves traffic, and signs tokens that anyone holding the same public example
can forge. That is the case these tests exist for.
"""
import pytest

from app.secret_validation import (
    REQUIRED_IN_PRODUCTION,
    SecretValidationError,
    enforce,
    validate,
)

GOOD = {
    "SORA_JWT_SECRET": "s" * 48,
    "SORA_ADMIN_TOKEN": "t" * 32,
    "POSTGRES_PASSWORD": "p" * 24,
}


def production(**overrides):
    env = dict(GOOD, SORA_ENV="production")
    env.update(overrides)
    return env


def test_a_complete_configuration_passes():
    assert validate(production(), "production") == []


def test_development_is_not_policed():
    """Requiring eight variables to run a test pushes people towards copying a
    real value into their shell."""
    assert validate({"SORA_ENV": "development"}, "development") == []


@pytest.mark.parametrize("name", sorted(REQUIRED_IN_PRODUCTION))
def test_a_missing_secret_is_refused(name):
    env = production()
    del env[name]
    faults = validate(env, "production")
    assert any(name in f and "not set" in f for f in faults)


@pytest.mark.parametrize("placeholder", [
    "changeme", "change-me", "CHANGEME", "your-secret-here", "<your-key>",
    "xxxxxx", "TODO", "password", "example", "", "   ",
])
def test_a_placeholder_is_refused(placeholder):
    faults = validate(production(SORA_JWT_SECRET=placeholder), "production")
    assert any("SORA_JWT_SECRET" in f for f in faults), placeholder


def test_the_development_default_prefix_is_refused():
    env = production(SORA_JWT_SECRET="sora-earth-dev-secret-change-in-production-2026")
    faults = validate(env, "production")
    assert any("development default" in f for f in faults)


def test_a_short_secret_is_refused():
    faults = validate(production(SORA_JWT_SECRET="short"), "production")
    assert any("characters" in f for f in faults)


def test_enforce_raises_and_names_the_variable():
    with pytest.raises(SecretValidationError) as excinfo:
        enforce(production(SORA_JWT_SECRET="changeme"), "production")
    assert "SORA_JWT_SECRET" in str(excinfo.value)


def test_the_message_never_contains_the_value():
    """A validation error that echoes the secret puts it in every log that
    captured the startup failure."""
    secret = "an-actual-looking-secret-value-9f3a2b"
    with pytest.raises(SecretValidationError) as excinfo:
        enforce(production(SORA_ADMIN_TOKEN="changeme", SORA_JWT_SECRET=secret), "production")
    message = str(excinfo.value)
    assert secret not in message
    assert "changeme" not in message


def test_every_fault_is_reported_not_just_the_first():
    """Fixing one variable and restarting to discover the next is how a short
    outage becomes a long one."""
    env = production(SORA_JWT_SECRET="", SORA_ADMIN_TOKEN="", POSTGRES_PASSWORD="")
    assert len(validate(env, "production")) == len(REQUIRED_IN_PRODUCTION)
