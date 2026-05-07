"""
PDF report generator for CSRD/ESRS compliance.
Uses reportlab (pure Python, no system deps).
"""
from io import BytesIO
from datetime import datetime, timezone
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, KeepTogether,
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER

# SORA earth-tone palette
SORA_GREEN  = colors.HexColor("#15B887")
SORA_DARK   = colors.HexColor("#0a1f17")
SORA_MUTED  = colors.HexColor("#6b7670")
SORA_BG     = colors.HexColor("#f4f7f5")
SORA_BORDER = colors.HexColor("#d0d8d3")

STATUS_COLOR = {
    "ready":   colors.HexColor("#16a34a"),
    "partial": colors.HexColor("#ca8a04"),
    "gap":     colors.HexColor("#dc2626"),
}


def _styles():
    ss = getSampleStyleSheet()
    return {
        "h1":     ParagraphStyle("h1", parent=ss["Heading1"], fontSize=22, textColor=SORA_DARK, spaceAfter=4),
        "sub":    ParagraphStyle("sub", parent=ss["Normal"], fontSize=10, textColor=SORA_MUTED, spaceAfter=14),
        "h2":     ParagraphStyle("h2", parent=ss["Heading2"], fontSize=14, textColor=SORA_DARK, spaceBefore=12, spaceAfter=6),
        "body":   ParagraphStyle("body", parent=ss["Normal"], fontSize=10, leading=14, textColor=SORA_DARK),
        "muted":  ParagraphStyle("muted", parent=ss["Normal"], fontSize=9, textColor=SORA_MUTED),
        "score":  ParagraphStyle("score", parent=ss["Normal"], fontSize=42, textColor=SORA_GREEN, alignment=TA_LEFT, leading=46),
        "footer": ParagraphStyle("footer", parent=ss["Normal"], fontSize=8, textColor=SORA_MUTED, alignment=TA_CENTER),
    }


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(SORA_MUTED)
    txt = f"SORA.earth, auto-generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}, page {canvas.getPageNumber()}"
    canvas.drawCentredString(A4[0] / 2, 12 * mm, txt)
    canvas.restoreState()


def render_compliance_pdf(project: dict, result: dict) -> bytes:
    """
    project: {project_name, country, category, budget_usd, ...}
    result:  {score, status, framework, audit_ready, categories: [...], recommended_actions: [...]}
    """
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=20 * mm, bottomMargin=22 * mm,
        title=f"CSRD/ESRS Report - {project.get('project_name', 'Project')}",
        author="SORA.earth",
    )
    s = _styles()
    story = []

    # ---- Header
    story.append(Paragraph("SORA.earth - CSRD/ESRS Compliance Report", s["h1"]))
    story.append(Paragraph(
        f"Framework: {result.get('framework', 'Post-Omnibus I (2026)')}, "
        f"generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        s["sub"],
    ))

    # ---- Score box
    score = result.get("score", 0)
    status = result.get("status", "gap")
    audit_ready = "yes" if result.get("audit_ready") else "no"
    score_color = STATUS_COLOR.get(status, SORA_MUTED)

    score_table = Table([
        [
            Paragraph(f"<font color='{score_color.hexval()}'>{score}</font><font size='14' color='#6b7670'>/100</font>", s["score"]),
            [
                Paragraph(f"<b>Project:</b> {project.get('project_name', '')}", s["body"]),
                Paragraph(f"<b>Country:</b> {project.get('country', '')}", s["body"]),
                Paragraph(f"<b>Category:</b> {project.get('category', '')}", s["body"]),
                Paragraph(f"<b>Status:</b> <font color='{score_color.hexval()}'>{status.upper()}</font>", s["body"]),
                Paragraph(f"<b>Audit-ready:</b> {audit_ready}", s["body"]),
            ],
        ]
    ], colWidths=[60 * mm, 110 * mm])
    score_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SORA_BG),
        ("BOX", (0, 0), (-1, -1), 0.5, SORA_BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 14),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
    ]))
    story.append(score_table)
    story.append(Spacer(1, 6 * mm))

    # ---- Project parameters
    story.append(Paragraph("Project parameters", s["h2"]))
    params = [
        ["Budget (USD)", f"{project.get('budget_usd', 0):,}"],
        ["CO2 reduction (t/y)", f"{project.get('co2_reduction_tons_per_year', 0)}"],
        ["Social impact (1-10)", f"{project.get('social_impact_score', 0)}"],
        ["Duration (months)", f"{project.get('project_duration_months', 0)}"],
    ]
    pt = Table(params, colWidths=[60 * mm, 110 * mm])
    pt.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (0, -1), SORA_MUTED),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, SORA_BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(pt)

    # ---- ESRS categories
    story.append(Paragraph("ESRS categories", s["h2"]))
    cat_rows = [["Category", "Score", "Status", "Notes"]]
    for c in result.get("categories", []):
        notes = "; ".join((c.get("findings") or [])[:2]) or "-"
        if len(notes) > 90:
            notes = notes[:87] + "..."
        cat_rows.append([
            c.get("name", ""),
            str(c.get("score", "-")),
            (c.get("status") or "").upper(),
            notes,
        ])
    ct = Table(cat_rows, colWidths=[40 * mm, 18 * mm, 22 * mm, 90 * mm])
    ct.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), SORA_DARK),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 1), (2, -1), "CENTER"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SORA_BG]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, SORA_BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(ct)

    # ---- Recommended actions
    actions = result.get("recommended_actions", [])
    if actions:
        story.append(Paragraph("Recommended actions", s["h2"]))
        for i, a in enumerate(actions[:10], 1):
            text = a if isinstance(a, str) else a.get("description") or a.get("action") or str(a)
            story.append(Paragraph(f"{i}. {text}", s["body"]))
            story.append(Spacer(1, 2 * mm))

    # ---- Disclaimer
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph(
        "This report is automatically generated by SORA.earth ML platform based on the project parameters submitted. "
        "It is intended as a pre-audit gap analysis and does not constitute a formal audit opinion under CSRD.",
        s["muted"],
    ))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
