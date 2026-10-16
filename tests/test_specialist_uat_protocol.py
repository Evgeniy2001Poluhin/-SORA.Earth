import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "docs" / "SPECIALIST_UAT_PROTOCOL.md"
APP = ROOT / "web" / "src" / "app" / "App.tsx"


def _scenario_rows(text: str) -> list[tuple[str, str]]:
    return re.findall(r"^\| (UAT-\d{2}) \| `([^`]+)` \|", text, re.MULTILINE)


def test_the_uat_protocol_is_preregistered_and_has_the_declared_sample():
    text = PROTOCOL.read_text(encoding="utf-8")

    assert "**Status:** preregistered; not yet run" in text
    assert "**Target sample:** 5–10 independent specialists" in text
    assert "**Planned workload:** 40 scenarios" in text
    assert "does not report interviews" in text


def test_the_uat_scenario_catalogue_is_complete_and_stable():
    scenarios = _scenario_rows(PROTOCOL.read_text(encoding="utf-8"))

    assert [scenario_id for scenario_id, _ in scenarios] == [
        f"UAT-{number:02d}" for number in range(1, 41)
    ]


def test_every_uat_route_exists_in_the_product_router():
    app = APP.read_text(encoding="utf-8")
    routes = set(re.findall(r'<Route path="([^"]+)"', app))
    protocol_routes = {route for _, route in _scenario_rows(PROTOCOL.read_text(encoding="utf-8"))}

    assert protocol_routes <= routes


def test_the_protocol_keeps_completion_separate_from_provenance():
    text = PROTOCOL.read_text(encoding="utf-8")

    assert "Evidence-chain success" in text
    assert "a correct-looking answer without provenance fails it" in text
    assert "Critical error rate" in text
    assert "0 |" in text
