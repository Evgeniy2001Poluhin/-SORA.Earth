"""PDF for a persisted evaluation. No scoring, external data or model calls."""
from pathlib import Path

from fpdf import FPDF
from matplotlib import get_data_path


def render_saved_evaluation(row):
    pdf = FPDF()
    # These Unicode fonts ship with our pinned matplotlib dependency. Embed
    # them so Russian project names and recommendations survive unchanged.
    fonts = Path(get_data_path()) / "fonts" / "ttf"
    pdf.add_font("DejaVu", fname=str(fonts / "DejaVuSans.ttf"))
    pdf.add_font("DejaVu", style="B", fname=str(fonts / "DejaVuSans-Bold.ttf"))
    pdf.add_page()
    pdf.set_font("DejaVu", "B", 18)
    pdf.cell(0, 12, "SORA.Earth - Saved ESG Evaluation", ln=True)
    pdf.set_font("DejaVu", "", 11)
    pdf.multi_cell(0, 7, "Stored assessment; not recalculated. Success probability is an unvalidated estimate, not a verified outcome.", align="L")
    fields = [
        ("Evaluation ID", row.id), ("Saved at", row.created_at),
        ("Project", row.name), ("Budget (USD)", row.budget),
        ("CO2 reduction (t/year)", row.co2_reduction),
        ("Social impact", row.social_impact), ("Duration (months)", row.duration_months),
        ("Region", row.region), ("Total ESG score", row.total_score),
        ("Environment score", row.environment_score), ("Social score", row.social_score),
        ("Economic score", row.economic_score), ("Risk", row.risk_level),
        ("Success probability (%)", row.success_probability),
        ("Recommendations", row.recommendation),
    ]
    for label, value in fields:
        text = "Not recorded" if value is None else str(value)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, 7, f"{label}: {text}", align="L")
    result = pdf.output(dest="S")
    return result.encode("latin-1") if isinstance(result, str) else bytes(result)
