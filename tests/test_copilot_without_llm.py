"""The Co-Pilot's answers must not depend on a reachable LLM."""
import pytest

from app.services import copilot


@pytest.fixture(autouse=True)
def llm_off(monkeypatch):
    for name in ("COPILOT_LLM_ENABLED", "COPILOT_LLM_BASE_URL",
                 "COPILOT_LLM_MODEL", "COPILOT_LLM_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_health_is_ok_with_no_llm():
    """Disabled is a healthy state, not a degraded one."""
    h = copilot.health()
    assert h["ok"] is True
    assert h["llm_enabled"] is False
    assert h["explanation_mode"] == "smart_template"


def test_the_full_scenario_matrix_is_still_available():
    assert copilot.health()["scenario_matrix"]["total_scenarios"] == 18


def test_no_outbound_call_is_attempted(monkeypatch):
    """Not merely 'it works' -- that nothing is even tried. A blocked call that
    eventually times out is a slow endpoint, not a working one."""
    import socket

    def refuse(*args, **kwargs):
        raise AssertionError("the Co-Pilot attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    h = copilot.health()
    assert h["ok"] is True


def test_a_follow_up_question_is_answered_from_templates():
    answer = copilot.answer_qa(
        question="What drives this verdict?",
        context="Verdict: moderate. Probability: 0.55.",
        sources=[],
        audience="executive",
    )
    assert answer["mode"] == "template"
    assert answer["tokens_used"] == 0
    assert answer["answer"]


def test_the_prediction_contract_holds_without_an_llm():
    """These are what a caller relies on, and none of them come from a language
    model. The LLM only adds or rewrites executive_summary; if enabling it ever
    changed one of these, the explanation would stop describing the prediction.
    """
    base = copilot.explain_prediction(
        probability=0.62,
        features={"budget": 200000, "co2_reduction": 300, "social_impact": 7,
                  "duration_months": 24},
        project={"name": "t"},
        enrich=False,
    )

    for key in ("verdict", "probability", "confidence", "key_drivers",
                "model_version", "explanation_mode"):
        assert key in base, f"{key} missing from a template-only explanation"

    assert base["explanation_mode"] == "smart_template"
    assert base["probability"] == 0.62
    assert base["verdict"]["label"]
