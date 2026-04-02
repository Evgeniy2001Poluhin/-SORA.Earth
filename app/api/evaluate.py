from typing import List

from fastapi import APIRouter
from fastapi.responses import StreamingResponse, FileResponse

from app.schemas import ProjectInput as Project, GHGInput
from app.country_benchmarks import BENCHMARKS, GLOBAL_AVG
from app import cache, external_data
from app.drift_detection import drift_detector
from app.mlflow_tracking import log_evaluation
from app.middleware import METRICS

import csv, io, time
from datetime import datetime

from fpdf import FPDF
import tempfile


router = APIRouter()


@router.post("/evaluate")
def evaluate_project(project: Project):
    from app.main import calculate_esg, get_db, COUNTRIES, _sanitize_pdf

    cache_key = cache.make_key("eval", project.model_dump())
    cached = cache.get(cache_key)
    if cached:
        return cached

    cdata = COUNTRIES.get(
        project.region or "Germany",
        {"region": "Europe", "lat": 50.0, "lon": 10.0},
    )
    region_name = cdata.get("region", "Europe")

    result = calculate_esg(project, region_name)

    # Enrich with external ESG country context
    try:
        ctx = external_data.get_country_context(project.region or region_name)
        if ctx is not None:
            result["external_context"] = ctx
    except Exception:
        pass

    lat = cdata["lat"] + (hash(project.name) % 10 - 5) * 0.3
    lon = cdata["lon"] + (hash(project.name) % 10 - 5) * 0.3

    conn = get_db()
    conn.execute(
        "INSERT INTO evaluations (name,budget,co2_reduction,social_impact,duration_months,total_score,environment_score,"
        "social_score,economic_score,success_probability,recommendation,risk_level,created_at,region,lat,lon) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            project.name,
            project.budget,
            project.co2_reduction,
            project.social_impact,
            project.duration_months,
            result["total_score"],
            result["environment_score"],
            result["social_score"],
            result["economic_score"],
            result["success_probability"],
            "; ".join([_sanitize_pdf(r) for r in result["recommendations"]]),
            result["risk_level"],
            datetime.now().isoformat(),
            region_name,
            lat,
            lon,
        ),
    )
    conn.commit()
    conn.close()

    result["region"] = region_name
    result["lat"] = lat
    result["lon"] = lon

    log_evaluation(project.name, result, result["risk_level"])
    drift_detector.add_observation(
        {
            "budget": project.budget,
            "co2_reduction": project.co2_reduction,
            "social_impact": project.social_impact,
            "duration_months": project.duration_months,
        }
    )

    country_name = project.region or "Germany"
    bench = BENCHMARKS.get(country_name, GLOBAL_AVG)
    result["country_benchmark"] = {
        "country": country_name if country_name in BENCHMARKS else "Global Average",
        "co2_per_capita": bench["co2_per_capita"],
        "renewable_share": bench["renewable_share"],
        "esg_rank": bench["esg_rank"],
        "hdi": bench["hdi"],
        "project_vs_country": {
            "esg_score_diff": round(result["total_score"] - bench["esg_rank"], 2),
            "above_average": result["total_score"] > 50,
        },
    }

    m = METRICS
    m["evaluations_total"] = m.get("evaluations_total", 0) + 1
    prev_avg = m.get("evaluations_avg_score", 0.0)
    n = m["evaluations_total"]
    m["evaluations_avg_score"] = round(
        prev_avg + (result["total_score"] - prev_avg) / n, 2
    )

    cache.set(cache_key, result, ttl=600)
    return result


@router.get("/history")
def get_history():
    from app.main import get_db

    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM evaluations ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.delete("/history/{eval_id}")
def delete_evaluation(eval_id: int):
    from app.main import get_db

    conn = get_db()
    conn.execute("DELETE FROM evaluations WHERE id=?", (eval_id,))
    conn.commit()
    conn.close()
    return {"status": "deleted"}


@router.delete("/history")
def clear_history():
    from app.main import get_db

    conn = get_db()
    conn.execute("DELETE FROM evaluations")
    conn.commit()
    conn.close()
    return {"status": "cleared"}


@router.get("/export/csv")
def export_csv():
    from app.main import get_db

    conn = get_db()
    rows = conn.execute(
        "SELECT name,budget,co2_reduction,social_impact,duration_months,total_score,environment_score,social_score,"
        "economic_score,success_probability,risk_level,region,created_at FROM evaluations ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(
        [
            "Name",
            "Budget",
            "CO2 Reduction",
            "Social Impact",
            "Duration (months)",
            "ESG Score",
            "Environment",
            "Social",
            "Economic",
            "Success Probability",
            "Risk Level",
            "Region",
            "Date",
        ]
    )
    for r in rows:
        w.writerow(
            [
                r["name"],
                r["budget"],
                r["co2_reduction"],
                r["social_impact"],
                r["duration_months"],
                r["total_score"],
                r["environment_score"],
                r["social_score"],
                r["economic_score"],
                r["success_probability"],
                r["risk_level"],
                r["region"],
                r["created_at"],
            ]
        )
    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode()),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=sora_earth_projects.csv"
        },
    )


# ===== WHAT-IF =====
@router.post("/what-if")
def what_if(project: Project):
    from app.main import COUNTRIES, calculate_esg

    cdata = COUNTRIES.get(project.region or "Germany", {"region": "Europe"})
    wi_region = cdata.get("region", "Europe")
    base = calculate_esg(project, wi_region)
    variations = {}
    deltas = {
        "budget": ("budget", 0.2, True),
        "co2_reduction": ("co2_reduction", 20, False),
        "social_impact": ("social_impact", 2, False),
        "duration_months": ("duration_months", -6, False),
    }
    for key, (field, delta, is_pct) in deltas.items():
        d = project.model_dump()
        d[field] = d[field] * (1 + delta) if is_pct else d[field] + delta
        d[field] = max(d[field], 0)
        if field == "social_impact":
            d[field] = min(d[field], 10)
        if field == "duration_months":
            d[field] = max(int(d[field]), 1)
        mod = Project(**d)
        mr = calculate_esg(mod, wi_region)
        variations[key] = {
            "new_value": round(d[field], 0),
            "new_score": mr["total_score"],
            "score_change": round(mr["total_score"] - base["total_score"], 2),
            "new_probability": mr["success_probability"],
            "prob_change": round(
                mr["success_probability"] - base["success_probability"], 2
            ),
        }
    return {"base": base, "variations": variations}


# ===== GHG =====
@router.post("/ghg-calculate")
def ghg_calculate(data: GHGInput):
    scope1 = round(
        (data.natural_gas_m3 * 2.0 + data.diesel_liters * 2.68 + data.petrol_liters * 2.31)
        / 1000,
        2,
    )
    scope2 = round((data.electricity_kwh * 0.4) / 1000, 2)
    scope3 = round(
        (data.flights_km * 0.255 + data.waste_kg * 0.5) / 1000, 2
    )
    total = round(scope1 + scope2 + scope3, 2)
    breakdown = {
        "electricity": round(data.electricity_kwh * 0.4 / 1000, 3),
        "natural_gas": round(data.natural_gas_m3 * 2.0 / 1000, 3),
        "diesel": round(data.diesel_liters * 2.68 / 1000, 3),
        "petrol": round(data.petrol_liters * 2.31 / 1000, 3),
        "flights": round(data.flights_km * 0.255 / 1000, 3),
        "waste": round(data.waste_kg * 0.5 / 1000, 3),
    }
    if total < 5:
        rating, tip = "Excellent", "Your carbon footprint is well below average."
    elif total < 15:
        rating, tip = "Good", "Consider switching to renewable energy."
    elif total < 30:
        rating, tip = "Average", "Significant improvements possible."
    else:
        rating, tip = "High", "Urgent action needed."
    return {
        "total_tons_co2": total,
        "scope1": scope1,
        "scope2": scope2,
        "scope3": scope3,
        "breakdown": breakdown,
        "rating": rating,
        "tip": tip,
    }


# ===== TRENDS / REGIONS / COUNTRIES =====
@router.get("/trends")
def trends():
    from app.main import get_db

    conn = get_db()
    rows = conn.execute(
        "SELECT total_score,success_probability,created_at FROM evaluations ORDER BY created_at ASC"
    ).fetchall()
    conn.close()
    return [
        {
            "score": r["total_score"],
            "prob": r["success_probability"],
            "date": r["created_at"][:16].replace("T", " "),
        }
        for r in rows
    ]


@router.get("/regions")
def regions():
    from app.main import REGIONAL_FACTORS

    return list(REGIONAL_FACTORS.keys())


@router.get("/countries")
def countries_list():
    from app.main import COUNTRIES

    return {k: v["region"] for k, v in COUNTRIES.items()}


# ===== PDF REPORT =====
@router.post("/report/pdf")
def generate_pdf_report(project: Project):
    from app.main import (
        calculate_esg,
        make_features,
        ensemble_model,
        best_threshold,
        _sanitize_pdf,
    )
    from app.validators import ProjectInput as ValidatorProject

    esg = calculate_esg(project, project.region)
    feats = make_features(
        ValidatorProject(
            budget=project.budget,
            co2_reduction=project.co2_reduction,
            social_impact=project.social_impact,
            duration_months=project.duration_months,
        )
    )
    prob = float(ensemble_model.predict_proba(feats)[0][1])
    prediction = int(prob >= best_threshold)
    risk = (
        "Low"
        if esg["total_score"] >= 70
        else "Medium" if esg["total_score"] >= 40 else "High"
    )

    pdf = FPDF()
    _orig_normalize = pdf.normalize_text

    def _safe_normalize(txt):
        return _orig_normalize(_sanitize_pdf(txt))

    pdf.normalize_text = _safe_normalize
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 22)
    pdf.cell(0, 15, "SORA.Earth - Project ESG Report", ln=True, align="C")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(
        0,
        8,
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        ln=True,
        align="C",
    )
    pdf.ln(10)

    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "Project Overview", ln=True)
    pdf.set_font("Helvetica", "", 11)
    info = [
        ("Name", project.name),
        ("Budget", f"${project.budget:,.0f}"),
        ("CO2 Reduction", f"{project.co2_reduction} tons/year"),
        ("Social Impact", f"{project.social_impact}/10"),
        ("Duration", f"{project.duration_months} months"),
        ("Country", project.region),
    ]
    for k, v in info:
        pdf.cell(60, 8, k + ":", 0)
        pdf.cell(0, 8, str(v), ln=True)
    pdf.ln(6)

    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "ESG Assessment", ln=True)
    pdf.set_font("Helvetica", "", 11)
    scores = [
        ("Total ESG Score", f"{esg['total_score']}/100"),
        ("Environment", f"{esg['environment_score']}/100"),
        ("Social", f"{esg['social_score']}/100"),
        ("Economic", f"{esg['economic_score']}/100"),
        ("Risk Level", risk),
        ("ML Success Probability", f"{prob * 100:.2f}%"),
        ("Prediction", "Success" if prediction else "Fail"),
    ]
    for k, v in scores:
        pdf.cell(60, 8, k + ":", 0)
        pdf.cell(0, 8, str(v), ln=True)
    pdf.ln(6)

    if esg.get("recommendations"):
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 10, "Recommendations", ln=True)
        pdf.set_font("Helvetica", "", 11)
        for i, r in enumerate(esg["recommendations"], 1):
            pdf.set_x(10)
            pdf.multi_cell(0, 7, _sanitize_pdf(f"{i}. {r}"))
    pdf.ln(6)

    pdf.set_font("Helvetica", "I", 9)
    pdf.cell(
        0,
        8,
        "This report was generated by SORA.Earth AI Platform v2.0",
        ln=True,
        align="C",
    )

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    pdf.output(tmp.name)
    return FileResponse(
        tmp.name,
        media_type="application/pdf",
        filename=f"SORA_Earth_{project.name.replace(' ', '_')}_Report.pdf",
    )
