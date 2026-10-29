from io import BytesIO
from types import SimpleNamespace

from pypdf import PdfReader
from app.services.evaluation_report import render_saved_evaluation


def test_report_keeps_zeroes_and_does_not_invent_missing_values():
    row = SimpleNamespace(id=42,created_at=None,name="Zero budget",budget=0,
        co2_reduction=None,social_impact=0,duration_months=0,region=None,
        total_score=0,environment_score=0,social_score=0,economic_score=0,
        risk_level=None,success_probability=0,recommendation=None)
    document=render_saved_evaluation(row)
    assert document.startswith(b"%PDF")
    text="\n".join(page.extract_text() for page in PdfReader(BytesIO(document)).pages)
    assert "Budget (USD): 0" in text
    assert "Success probability (%): 0" in text
    assert "Risk: Not recorded" in text
    assert "CO2 reduction (t/year): Not recorded" in text
    assert "Prediction: Success" not in text


def test_long_unicode_input_is_rendered_as_data_across_pages():
    row = SimpleNamespace(id=7,created_at="2026-10-29",name="Солнечный проект Ω / <script>",budget=10,
        co2_reduction=10,social_impact=7,duration_months=24,region="Europe",
        total_score=60,environment_score=60,social_score=60,economic_score=60,
        risk_level="Medium",success_probability=50,recommendation="Stored recommendation. "*500)
    reader=PdfReader(BytesIO(render_saved_evaluation(row)))
    assert row.name in "\n".join(page.extract_text() for page in reader.pages)
    assert len(reader.pages)>1
    assert "Stored recommendation." in "\n".join(page.extract_text() for page in reader.pages)
