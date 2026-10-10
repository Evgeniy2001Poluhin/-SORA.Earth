"""Keep the deployable Compose environment documented in ``.env.example``.

Compose interpolation is the contract for values supplied by an operator.  Read
that contract from the production file instead of maintaining a second,
eventually-stale list here.  Interpolations with a ``:-`` default remain
optional by Compose's own semantics and do not need an example entry.
"""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.prod.yml"
ENV_EXAMPLE = ROOT / ".env.example"
INTERPOLATION = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P<default>:-[^}]*)?\}")
ENV_KEY = re.compile(r"\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)=")


def _compose_inputs_without_defaults() -> set[str]:
    return {
        match.group("name")
        for match in INTERPOLATION.finditer(COMPOSE.read_text(encoding="utf-8"))
        if match.group("default") is None
    }


def _example_keys() -> set[str]:
    return {
        match.group("name")
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if (match := ENV_KEY.match(line))
    }


def test_env_example_declares_every_required_production_compose_input():
    missing = _compose_inputs_without_defaults() - _example_keys()
    assert not missing, (
        ".env.example omits required docker-compose.prod.yml inputs: "
        + ", ".join(sorted(missing))
    )
