"""Refuse to start production with a secret that is missing or obviously fake.

A placeholder that reaches production is worse than a missing one. Missing
fails immediately and visibly; a placeholder starts, serves traffic, and signs
tokens anyone holding the same public example can forge.

Only production is checked. Development runs on defaults on purpose, and making
a developer set eight variables to run a test would push everyone towards
copying a real value into their shell.
"""
import os
import re

# Values that appear in .env.example, documentation and tutorials. Anything
# matching these is a template that was never filled in.
_PLACEHOLDERS = re.compile(
    r"^(|change[-_ ]?me|changeme|placeholder|your[-_ ].*|<.*>|xxx+|todo|"
    r"secret|password|admin|test|example|sample|dummy|foo|bar)$",
    re.IGNORECASE,
)

_DEV_PREFIXES = ("sora-earth-dev-",)

# name -> minimum length. A signing secret short enough to brute force is not
# meaningfully different from no secret at all.
REQUIRED_IN_PRODUCTION = {
    "SORA_JWT_SECRET": 32,
    "SORA_ADMIN_TOKEN": 24,
    "POSTGRES_PASSWORD": 16,
}


class SecretValidationError(RuntimeError):
    """Raised at import time. The process must not continue."""


def _fault(name: str, value: str, minimum: int) -> str | None:
    """Why this value is unusable, or None. Never returns the value itself."""
    if value is None or value == "":
        return "is not set"
    if _PLACEHOLDERS.match(value.strip()):
        return "is a placeholder from the example configuration"
    if any(value.startswith(prefix) for prefix in _DEV_PREFIXES):
        return "still carries the development default prefix"
    if len(value) < minimum:
        # The threshold is public -- it is in this file. The actual length is
        # not: it narrows a guess, and it ends up in whatever log captured the
        # startup failure.
        return f"is shorter than the required {minimum} characters"
    return None


def validate(environ=None, env_name: str | None = None) -> list[str]:
    """Return the faults found. Empty means the configuration is usable."""
    environ = os.environ if environ is None else environ
    env_name = environ.get("SORA_ENV", "development") if env_name is None else env_name
    if env_name != "production":
        return []

    faults = []
    for name, minimum in sorted(REQUIRED_IN_PRODUCTION.items()):
        fault = _fault(name, environ.get(name, ""), minimum)
        if fault:
            faults.append(f"{name} {fault}")
    return faults


def enforce(environ=None, env_name: str | None = None) -> None:
    """Fail closed. The message names variables, never values."""
    faults = validate(environ, env_name)
    if not faults:
        return
    raise SecretValidationError(
        "refusing to start in production with an unusable secret configuration:\n  "
        + "\n  ".join(faults)
        + "\n\nSet each variable to a value generated for this deployment. "
        "See docs/SECRETS.md for how each one is rotated."
    )
