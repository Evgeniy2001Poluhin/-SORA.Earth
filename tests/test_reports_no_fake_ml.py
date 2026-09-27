"""Test that compliance PDFs do not contain invented ML analysis."""
from fastapi.testclient import TestClient
import app.api.reports
import base64
import re
import zlib

# Import app late to avoid issues with TestClient
from app import main
client = TestClient(main.app)

PAYLOAD = {
    "name": "Solar Project Alpha",
    "budget": 300000,
    "co2_reduction": 500,
    "social_impact": 8,
    "duration_months": 24,
    "category": "Solar",
    "region": "ES"
}


def _decoded_streams(data: bytes):
    for m in re.finditer(rb"stream\r?\n", data):
        start = m.end(); end = data.find(b"endstream", start)
        if end < 0:
            continue
        body = data[start:end].strip()
        try:
            if body.endswith(b"~>"):
                body = base64.a85decode(body[:-2])
            yield zlib.decompress(body)
        except Exception:
            continue

def pdf_text(data: bytes) -> str:
    parts = []
    for s in _decoded_streams(data):
        for raw in re.findall(rb"\(((?:\\.|[^\\)])*)\)\s*Tj", s):
            parts.append(raw.replace(rb"\(", b"(").replace(rb"\)", b")").replace(rb"\\\\", b"\\").decode("latin-1"))
    return " ".join(parts)


def test_single_pdf_no_invented_ml():
    """Single PDF must not contain invented ML analysis in the actual PDF text."""
    r = client.post("/api/v1/reports/compliance.pdf?lang=en", json=PAYLOAD)
    assert r.status_code == 200

    text = pdf_text(r.content)

    # Control: verify extraction worked and PDF contains expected content
    assert len(text) > 0, "PDF text extraction returned empty string"
    assert "Solar Project Alpha" in text, "PDF does not contain project name"
    assert "Compliance Report" in text, "PDF does not contain expected heading"

    # Assert absence of invented ML terms
    forbidden = ["AI Model Analysis", "ML Predicted", "SHAP", "Confidence Interval"]
    for term in forbidden:
        assert term not in text, (
            f"Single PDF contains '{term}' - the invented ML analysis is still present"
        )


def test_batch_pdf_no_invented_ml():
    """Batch PDF must not contain invented ML analysis in the actual PDF text."""
    payload = [
        {
            "name": "Solar A",
            "budget": 100000,
            "co2_reduction": 300,
            "social_impact": 7,
            "duration_months": 12,
            "category": "Solar",
            "region": "ES"
        },
        {
            "name": "Wind B",
            "budget": 500000,
            "co2_reduction": 800,
            "social_impact": 9,
            "duration_months": 36,
            "category": "Wind",
            "region": "DE"
        },
    ]

    r = client.post("/api/v1/reports/compliance-batch.pdf?lang=en", json=payload)
    assert r.status_code == 200

    text = pdf_text(r.content)

    # Control: verify extraction worked and PDF contains expected content
    assert len(text) > 0, "PDF text extraction returned empty string"
    assert "Solar A" in text, "PDF does not contain first project name"
    assert "Wind B" in text, "PDF does not contain second project name"
    assert "Compliance Report" in text, "PDF does not contain expected heading"

    # Assert absence of invented ML terms
    forbidden = ["AI Model Analysis", "ML Predicted", "SHAP", "Confidence Interval"]
    for term in forbidden:
        assert term not in text, (
            f"Batch PDF contains '{term}' - the invented ML analysis is still present"
        )


def test_i18n_ml_keys_removed():
    """ML-related translation keys should be removed from I18N.

    This is a quick sanity check that complements the behavior tests above.
    If the I18N keys are gone, the removed section cannot be re-added without
    re-introducing them.
    """
    from app.api.reports import I18N

    forbidden_keys = [
        "ml_section", "ml_score", "ml_ci", "ml_verdict",
        "shap_section", "shap_feat", "shap_dir", "shap_impact",
        "verdict_strong", "verdict_moderate", "verdict_risk", "verdict_mis"
    ]

    for lang in ["en", "ru"]:
        for key in forbidden_keys:
            assert key not in I18N[lang], (
                f"I18N[{lang}]['{key}'] still exists - "
                "ML-related translation keys were not removed"
            )
