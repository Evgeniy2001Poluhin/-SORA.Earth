import logging
from typing import Optional
from typing import List

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.encoders import jsonable_encoder
from fastapi import Response
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.auth import UserInfo, optional_auth, require_admin, require_auth
from app import project_audit
from app.project_access import (FORBIDDEN_BODY_KEYS, PUBLIC, SAVING_ROLES, is_service_read,
                                project_read_predicate, shared_data_predicate)
from app.security.spreadsheet import neutralize_cell
from app.services.evaluation_store import save_evaluation

from app.schemas import (
    EvaluationHistoryPage,
    EvaluationView,
    HistoryCleared,
    HistoryDeleted,
    ProjectInput as Project,
    GHGInput,
    MonteCarloHistogram,
    MonteCarloOk,
    MonteCarloUnavailable,
)
from app.country_benchmarks import BENCHMARKS, GLOBAL_AVG, SOURCES
from app import external_data
from app.mlflow_tracking import log_simulation
from app.database import Evaluation
from app.map_position import marker_offset

import csv, io, time, math
from datetime import datetime

from fpdf import FPDF
import tempfile


router = APIRouter()

logger = logging.getLogger(__name__)


def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, float(x)))


def _macro_esg_from_payload(payload: dict):
    co2 = float(payload.get("co2_per_capita", 7.5) or 7.5)
    renewable = float(payload.get("renewable_share", 35.0) or 35.0)
    gdp = float(payload.get("gdp_per_capita", 20000) or 20000)
    forest = float(payload.get("forest_area", 30.0) or 30.0)
    industrial = float(payload.get("industrial_share", 25.0) or 25.0)
    energy_intensity = float(payload.get("energy_intensity", 3.0) or 3.0)
    political = float(payload.get("political_stability", 0.5) or 0.5)
    rule = float(payload.get("rule_of_law", 1.0) or 1.0)

    env = (
        35
        + max(0, 20 - co2) * 1.2
        + renewable * 0.45
        + forest * 0.20
        + max(0, 8 - energy_intensity) * 2.5
        - industrial * 0.10
    )
    social = (
        40
        + ((political + 2.5) / 5.0) * 30
        + ((rule + 2.5) / 5.0) * 30
    )
    economic = (
        30
        + 18 * math.log10(max(gdp, 1000) / 5000)  # log diminishing returns: 0@5k, +15@50k, +18@100k
        - industrial * 0.15
        + renewable * 0.10
    )

    env = round(_clamp(env), 2)
    social = round(_clamp(social), 2)
    economic = round(_clamp(economic), 2)
    total = round(0.4 * env + 0.3 * social + 0.3 * economic, 2)

    if total >= 70:
        risk = "Low"
    elif total >= 45:
        risk = "Medium"
    else:
        risk = "High"

    success_probability = round(_clamp(total * 1.15 + 20), 2)
    success_probability_v2 = round(_clamp(total * 1.10 + 18), 2)

    recs = []
    if co2 > 8:
        recs.append(f"Reduce CO2 intensity from {co2:.1f} t/cap toward < 8.0 to improve environmental performance")
    if renewable < 40:
        recs.append(f"Increase renewable energy share from {renewable:.1f}% toward 40%+")
    if rule < 1.0 or political < 0.5:
        recs.append("Strengthen governance stability and rule of law factors to improve social resilience")
    if gdp < 15000:
        recs.append("Low GDP per capita reduces economic readiness; strengthen financing and productivity assumptions")
    if not recs:
        recs.append("Current macro profile is balanced; focus on execution quality and maintaining governance strength")

    return {
        "total_score": total,
        "environment_score": env,
        "social_score": social,
        "economic_score": economic,
        "success_probability": success_probability,
        "success_probability_v2": success_probability_v2,
        "recommendations": recs[:3],
        "risk_level": risk,
        "esg_weights": {"environment": 0.4, "social": 0.3, "economic": 0.3},
    }


#: Every response that carries a caller's own evaluation, saved or not, and
#: every read of project rows. A shared proxy or the browser's disk cache must
#: not hand one account's projects to the next person on the machine.
NO_STORE = {"Cache-Control": "private, no-store"}


@router.post("/evaluate", summary="Evaluate a project; with ?save=true, save it as yours")
async def evaluate_project(
    request: Request,
    project: Project,
    save: bool = Query(False, description="Save the evaluation under the signed-in account. "
                                          "Specialist and admin only; 401 without a Bearer, "
                                          "403 for analyst and viewer."),
    caller: Optional[UserInfo] = Depends(optional_auth),
):
    """Compute an evaluation, and save it only when asked to by its owner.

    Two operations on one route, chosen by `save`:

        POST /evaluate             computes and writes nothing. 200, saved=false.
        POST /evaluate?save=true   computes, then writes the evaluation and its
                                   predictions_log row in one transaction under
                                   the caller's account. 201, saved=true, with
                                   the evaluation_id -- only after the commit.

    Anonymous calls used to write both rows with no owner, into the corpus that
    trends, drift and forecasting read. A calculation is now just a
    calculation; a saved one is private (`data_scope = 'pilot'`) and belongs to
    whoever saved it. The owner comes from the verified token. A body naming
    `owner_user_id`, `user_id` or `data_scope` is refused, not ignored.

    No result cache. The process-local LRU this route had returned a cached
    response *before* either write, so a repeated payload was never saved, and
    a cached saved response would hand back somebody else's evaluation_id.

    Nothing from this route feeds the shared channels any more -- the MLflow
    evaluation experiment, the Redis drift observations and the
    evaluations_avg_score counter. Every call is either an unsaved calculation
    or a private save, and neither belongs in a platform-wide figure.
    """
    from app.main import calculate_esg, COUNTRIES, _sanitize_pdf

    # Before any computation: who is asking decides whether this can save.
    if save:
        if caller is None:
            raise HTTPException(status_code=401, detail="Sign in to save an evaluation",
                                headers={"WWW-Authenticate": "Bearer"})
        if caller.role not in SAVING_ROLES:
            raise HTTPException(status_code=403,
                                detail="This role cannot save evaluations")

    raw_payload = await request.json()
    payload = dict(raw_payload) if isinstance(raw_payload, dict) else project.model_dump()
    forbidden = sorted(FORBIDDEN_BODY_KEYS.intersection(payload))
    if forbidden:
        raise HTTPException(status_code=422, detail={
            "reason_code": "forbidden_control_fields",
            "fields": forbidden,
            "message": "The owner and scope of a saved evaluation are set by the server.",
        })

    start = time.perf_counter()
    cdata = COUNTRIES.get(
        project.region or "Germany",
        {"region": "Europe", "lat": 50.0, "lon": 10.0},
    )
    region_name = cdata.get("region", "Europe")

    macro_keys = {
        "co2_per_capita", "renewable_share", "gdp_per_capita", "population_density",
        "forest_area", "industrial_share", "energy_intensity",
        "political_stability", "rule_of_law"
    }

    if any(k in payload and payload.get(k) is not None for k in macro_keys):
        result = _macro_esg_from_payload(payload)
        # No model computed this probability: it is a formula over the payload.
        model_version = "formula:macro"
    else:
        provenance = []
        result = calculate_esg(project, region_name, provenance_out=provenance)
        model_version = provenance[0] if provenance else "unknown"

    latency_ms = round((time.perf_counter() - start) * 1000, 2)

    try:
        ctx = external_data.get_country_context(project.region or region_name)
        if ctx is not None:
            result["external_context"] = ctx
    except Exception:
        pass

    offset = marker_offset(project.name)
    lat = cdata["lat"] + offset
    lon = cdata["lon"] + offset

    evaluation_id = None
    if save:
        # The predictions_log row is part of the save, not telemetry beside it:
        # app/services/evaluation_store.py writes both in one transaction. It
        # used to be `app.main.log_prediction`, a separate commit that
        # swallowed its own failure.
        from app.main import best_threshold
        try:
            evaluation_id = save_evaluation(
                owner_user_id=uuid.UUID(caller.user_id),
                project=project,
                result=result,
                recommendation="; ".join(
                    [_sanitize_pdf(r) for r in result.get("recommendations", [])]),
                region_name=region_name,
                lat=lat,
                lon=lon,
                latency_ms=latency_ms,
                model_version=model_version,
                threshold=best_threshold,
            )
        except SQLAlchemyError as exc:
            logger.error("evaluation could not be saved: %s", type(exc).__name__)
            raise HTTPException(status_code=503, detail="The evaluation could not be saved",
                                headers={"Retry-After": "5", **NO_STORE}) from None

    result["region"] = region_name
    result["lat"] = lat
    result["lon"] = lon

    country_name = project.region or "Germany"
    bench = BENCHMARKS.get(country_name, GLOBAL_AVG)
    result["country_benchmark"] = {
        "country": country_name if country_name in BENCHMARKS else "Global Average",
        "co2_per_capita": bench["co2_per_capita"],
        "renewable_share": bench["renewable_share"],
        "esg_rank": bench["esg_rank"],
        "hdi": bench["hdi"],
    }

    result["saved"] = evaluation_id is not None
    result["evaluation_id"] = evaluation_id
    return JSONResponse(status_code=201 if save else 200,
                        content=jsonable_encoder(result), headers=NO_STORE)


_EVALUATION_FIELDS = tuple(f for f in EvaluationView.model_fields
                           if f not in ("owner_user_id", "data_scope"))


def _evaluation_view(row, *, service: bool) -> EvaluationView:
    """A row as the API shows it. Ownership only on the administrator's view."""
    fields = {f: getattr(row, f) for f in _EVALUATION_FIELDS}
    if service:
        fields["owner_user_id"] = str(row.owner_user_id) if row.owner_user_id else None
        fields["data_scope"] = row.data_scope
    return EvaluationView(**fields)


def _newest_first(query):
    # Ties on created_at are broken by id, so a page boundary is not a matter
    # of whichever order the database happened to return equal timestamps in.
    return query.order_by(Evaluation.created_at.desc(), Evaluation.id.desc())


@router.get("/history", response_model=EvaluationHistoryPage,
            response_model_exclude_unset=True)
def get_history(
    request: Request,
    response: Response,
    region: Optional[str] = Query(None),
    risk_level: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    min_score: Optional[float] = Query(None, ge=0, le=100),
    max_score: Optional[float] = Query(None, ge=0, le=100),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    actor: UserInfo = Depends(require_auth),
):
    """The evaluations the caller may read (app/project_access.py).

    A specialist sees their own saved evaluations; analyst and viewer see the
    shared corpus; an administrator sees everything, and every such read is
    recorded before it is returned. The access rule is part of the query, so
    `total` and the page are computed over the same permitted rows.
    """
    from app.main import get_db_sync
    response.headers.update(NO_STORE)
    service = is_service_read(actor)
    db = get_db_sync()
    try:
        q = db.query(Evaluation).filter(project_read_predicate(Evaluation, actor))
        if region:
            q = q.filter(Evaluation.region.ilike(f"%{region}%"))
        if risk_level:
            q = q.filter(Evaluation.risk_level == risk_level.upper())
        if date_from:
            q = q.filter(Evaluation.created_at >= datetime.fromisoformat(date_from))
        if date_to:
            q = q.filter(Evaluation.created_at <= datetime.fromisoformat(date_to))
        if min_score is not None:
            q = q.filter(Evaluation.total_score >= min_score)
        if max_score is not None:
            q = q.filter(Evaluation.total_score <= max_score)
        total = q.count()
        rows = _newest_first(q).offset(offset).limit(limit).all()
        items = [_evaluation_view(r, service=service) for r in rows]
        if service:
            project_audit.record(
                db, actor=actor, request=request, action="list", status_code=200,
                targets=[project_audit.target(project_audit.EVALUATION, r, effect="returned")
                         for r in rows],
                filters={k: v for k, v in {
                    "region": region, "risk_level": risk_level, "date_from": date_from,
                    "date_to": date_to, "min_score": min_score, "max_score": max_score,
                    "limit": limit, "offset": offset}.items() if v is not None},
                returned_count=len(rows), matched_count=total)
            project_audit.commit_or_refuse(db)
        return EvaluationHistoryPage(items=items, total=total, limit=limit, offset=offset)
    finally:
        db.close()


@router.get("/history/{eval_id}", response_model=EvaluationView,
            response_model_exclude_unset=True)
def get_evaluation_by_id(eval_id: int, request: Request, response: Response,
                         actor: UserInfo = Depends(require_auth)):
    """One evaluation, if the caller may read it; otherwise 404.

    Somebody else's id answers exactly like an id that does not exist. The
    lookup carries the access rule, so there is no step at which the row was
    found and then refused -- and nothing to tell the two cases apart by.
    """
    from app.main import get_db_sync
    response.headers.update(NO_STORE)
    service = is_service_read(actor)
    db = get_db_sync()
    try:
        row = db.query(Evaluation).filter(
            Evaluation.id == eval_id,
            project_read_predicate(Evaluation, actor),
        ).first()
        if service:
            found = row is not None
            project_audit.record(
                db, actor=actor, request=request, action="detail",
                status_code=200 if found else 404,
                outcome="ok" if found else "not_found",
                targets=[project_audit.target(project_audit.EVALUATION, row, effect="returned")
                         if found else
                         project_audit.target(project_audit.EVALUATION, resource_id=eval_id,
                                              effect="not_found")],
                returned_count=1 if found else 0, matched_count=1 if found else 0)
            project_audit.commit_or_refuse(db)
        if row is None:
            raise HTTPException(status_code=404, detail="Evaluation not found", headers=NO_STORE)
        return _evaluation_view(row, service=service)
    finally:
        db.close()


@router.get("/history/{eval_id}/report.pdf", response_class=Response,
            responses={200: {"content": {"application/pdf": {}}}})
def saved_evaluation_pdf(eval_id: int, request: Request, response: Response,
                         actor: UserInfo = Depends(require_auth)):
    # Reuse the SQL ownership predicate, indistinguishable 404 and mandatory
    # administrator read audit of the detail endpoint before rendering bytes.
    row = get_evaluation_by_id(eval_id, request, response, actor)
    from app.services.evaluation_report import render_saved_evaluation
    return Response(render_saved_evaluation(row), media_type="application/pdf",
                    headers={**NO_STORE,
                             "Content-Disposition": f'attachment; filename="SORA_Evaluation_{eval_id}.pdf"'})


@router.delete("/history/{eval_id}", response_model=HistoryDeleted)
def delete_evaluation(eval_id: int, request: Request, response: Response,
                      actor: UserInfo = Depends(require_admin)):
    """Delete one evaluation, recorded in the same transaction as the DELETE.

    Admin only, and refused before any lookup for everyone else, so a 403 says
    nothing about whether the id exists. The evaluation's predictions_log row is
    left alone: nothing links the two reliably, and deleting log rows that
    merely look alike would delete somebody else's.
    """
    from app.main import get_db_sync
    response.headers.update(NO_STORE)
    db = get_db_sync()
    try:
        deleted = project_audit.delete_evaluation(db, actor=actor, request=request,
                                                  eval_id=eval_id)
        project_audit.commit_or_refuse(db)
    finally:
        db.close()
    if deleted is None:
        raise HTTPException(status_code=404, detail="Evaluation not found", headers=NO_STORE)
    return HistoryDeleted(status="deleted", id=eval_id)


@router.delete("/history", response_model=HistoryCleared)
def clear_history(request: Request, response: Response,
                  actor: UserInfo = Depends(require_admin)):
    """Clear the shared corpus. Saved pilot evaluations are not touched.

    This used to delete every row in the table. With ownership that would
    remove every specialist's work in one call, so the scope is fixed to
    `public` and stated in the response. A pilot evaluation is deleted by id.
    """
    from app.main import get_db_sync
    response.headers.update(NO_STORE)
    db = get_db_sync()
    try:
        count = project_audit.clear_public(db, actor=actor, request=request)
        project_audit.commit_or_refuse(db)
    finally:
        db.close()
    return HistoryCleared(status="cleared", scope=PUBLIC, deleted_count=count)


@router.get("/export/csv")
def export_csv(request: Request, actor: UserInfo = Depends(require_auth)):
    """Every evaluation the caller may read, as CSV -- the same rule as /history.

    The whole permitted history, not the current page's filters. An
    administrator's export names every exported id in the audit, committed
    before the first byte is sent.
    """
    from app.main import get_db_sync

    service = is_service_read(actor)
    db = get_db_sync()
    try:
        rows = _newest_first(
            db.query(Evaluation).filter(project_read_predicate(Evaluation, actor))).all()

        output = io.StringIO()
        w = csv.writer(output)
        w.writerow([
            "ID", "Name", "Budget", "CO2 Reduction", "Social Impact", "Duration (months)",
            "ESG Score", "Environment", "Social", "Economic",
            "Success Probability", "Risk Level", "Region", "Date",
        ])
        for r in rows:
            w.writerow([
                r.id, neutralize_cell(r.name or ""), r.budget, r.co2_reduction, r.social_impact,
                r.duration_months, r.total_score, r.environment_score,
                r.social_score, r.economic_score, r.success_probability,
                neutralize_cell(r.risk_level or ""), neutralize_cell(r.region or ""), r.created_at,
            ])
        body = output.getvalue().encode()

        if service:
            project_audit.record(
                db, actor=actor, request=request, action="export", status_code=200,
                targets=[project_audit.target(project_audit.EVALUATION, r, effect="returned")
                         for r in rows],
                returned_count=len(rows), matched_count=len(rows))
            project_audit.commit_or_refuse(db)
    finally:
        db.close()

    return StreamingResponse(
        io.BytesIO(body),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=sora_earth_projects.csv",
                 **NO_STORE},
    )


def _shared_telemetry(caller) -> bool:
    """Whether a simulation's summary may go to the shared MLflow experiment.

    Only for an anonymous call. A signed-in caller's what-if, Monte Carlo or
    ranking is a run over their own project, and its baseline score and spread
    are results of that project -- the experiment is shared by the whole
    platform. isinstance, so a handler called directly in a test (where the
    parameter holds the Depends marker) behaves as anonymous.
    """
    return not isinstance(caller, UserInfo)


@router.post("/what-if")
def what_if(project: Project, caller: Optional[UserInfo] = Depends(optional_auth)):
    import uuid

    from app.main import COUNTRIES, calculate_esg

    started = time.perf_counter()
    event_id = uuid.uuid4().hex[:12]
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
    # One event for the sweep (#258). This used to write five evaluations --
    # the baseline and four variants -- because `calculate_esg` logged each
    # call itself. The aggregate is what a reader of the experiment wants
    # anyway: where the baseline sat and how far the variants moved it.
    changes = [v["score_change"] for v in variations.values()]
    if _shared_telemetry(caller):
        log_simulation(
            "what_if",
            metrics={
                "variants": len(variations),
                "baseline_score": base["total_score"],
                "min_score_change": round(min(changes), 2) if changes else None,
                "max_score_change": round(max(changes), 2) if changes else None,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
            tags={"event_id": event_id, "region": wi_region,
                  "model_version": _model_version()},
        )
    return {"base": base, "variations": variations}


@router.post("/ghg-calculate")
def ghg_calculate(data: GHGInput):
    scope1 = round(
        (data.natural_gas_m3 * 2.0 + data.diesel_liters * 2.68 + data.petrol_liters * 2.31)
        / 1000, 2,
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
        "total_tons_co2": total, "scope1": scope1, "scope2": scope2, "scope3": scope3,
        "breakdown": breakdown, "rating": rating, "tip": tip,
    }


@router.get("/trends")
def trends():
    """One point per evaluation in the shared corpus.

    Per evaluation, not an aggregate, so a saved pilot evaluation would appear
    here as its own score, probability and date. Public rows only, for every
    caller: this route takes no account into consideration at all.
    """
    from app.main import get_db_sync

    db = get_db_sync()
    try:
        rows = (db.query(Evaluation).filter(shared_data_predicate(Evaluation))
                .order_by(Evaluation.created_at.asc(), Evaluation.id.asc()).all())
    finally:
        db.close()
    return [
        {
            "score": r.total_score,
            "prob": r.success_probability,
            "date": str(r.created_at)[:16].replace("T", " "),
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


@router.post("/report/pdf")
def generate_pdf_report(project: Project):
    from app.main import (
        calculate_esg, make_features, ensemble_model, best_threshold,
        _sanitize_pdf, COUNTRIES,
    )
    from app.validators import ProjectInput as ValidatorProject

    cdata = COUNTRIES.get(project.region or "Germany", {"region": "Europe"})
    pdf_region = cdata.get("region", "Europe")
    esg = calculate_esg(project, pdf_region)
    legacy = ValidatorProject(
        budget=project.budget,
        co2_reduction=project.co2_reduction,
        social_impact=project.social_impact,
        duration_months=project.duration_months,
    )
    feats_9 = make_features(legacy)
    from app.main import rf_model, xgb_model, nn_model, make_features_xgb

    feats_7 = make_features_xgb(legacy)
    from app.api.predict import _base_probabilities, _blend

    # Only the models that are loaded (#320): this probability is printed in the
    # PDF the interface downloads, and a third of it used to be a random network.
    prob = _blend(_base_probabilities(rf_model, xgb_model, nn_model, feats_9, feats_7))
    prediction = int(prob >= best_threshold)
    risk = "Low" if esg["total_score"] >= 70 else "Medium" if esg["total_score"] >= 40 else "High"

    pdf = FPDF()
    _orig_normalize = pdf.normalize_text

    def _safe_normalize(txt):
        return _orig_normalize(_sanitize_pdf(txt))

    pdf.normalize_text = _safe_normalize
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 22)
    pdf.cell(0, 15, "SORA.Earth - Project ESG Report", ln=True, align="C")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 8, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", ln=True, align="C")
    pdf.ln(10)

    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "Project Overview", ln=True)
    pdf.set_font("Helvetica", "", 11)
    info = [
        ("Name", project.name), ("Budget", f"${project.budget:,.0f}"),
        ("CO2 Reduction", f"{project.co2_reduction} tons/year"),
        ("Social Impact", f"{project.social_impact}/10"),
        ("Duration", f"{project.duration_months} months"), ("Country", project.region),
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

    # Data provenance (built from SOURCES, not hard-coded)
    co2_src = SOURCES["co2_per_capita"]
    gdp_src = SOURCES["gdp_per_capita"]
    hdi_src = SOURCES["hdi"]
    ren_src = SOURCES["renewable_share"]
    provenance = (
        f"Country data: CO2 per capita - {co2_src['source']}, {co2_src['year']}; "
        f"GDP per capita - {gdp_src['source']}, {gdp_src['year']}; "
        f"HDI - {hdi_src['source']}, {hdi_src['year']}; "
        f"renewable share of electricity - {ren_src['source']}, year varies by country ({ren_src['years']})."
    )
    pdf.set_font("Helvetica", "I", 9)
    pdf.multi_cell(0, 6, provenance)
    pdf.ln(4)

    if esg.get("recommendations"):
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 10, "Recommendations", ln=True)
        pdf.set_font("Helvetica", "", 11)
        for i, r in enumerate(esg["recommendations"], 1):
            pdf.set_x(10)
            pdf.multi_cell(0, 7, _sanitize_pdf(f"{i}. {r}"))
    pdf.ln(6)

    pdf.set_font("Helvetica", "I", 9)
    pdf.cell(0, 8, "This report was generated by SORA.Earth AI Platform v2.0", ln=True, align="C")

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    pdf.output(tmp.name)
    return FileResponse(
        tmp.name, media_type="application/pdf",
        filename=f"SORA_Earth_{project.name.replace(' ', '_')}_Report.pdf",
    )


@router.post("/evaluate/ranking")
async def evaluate_ranking(project: Project, caller: Optional[UserInfo] = Depends(optional_auth)):
    import uuid

    from app.main import calculate_esg, COUNTRIES

    started = time.perf_counter()
    event_id = uuid.uuid4().hex[:12]
    out = []
    for country, cdata in COUNTRIES.items():
        try:
            mod = project.model_copy(update={"region": country})
        except Exception:
            mod = project.copy(update={"region": country})
        try:
            r = calculate_esg(mod, cdata.get("region", "Europe"))
            out.append({
                "country": country,
                "region": cdata.get("region"),
                "total_score": r["total_score"],
                "environment_score": r["environment_score"],
                "social_score": r["social_score"],
                "economic_score": r["economic_score"],
                "success_probability": r["success_probability"],
                "risk_level": r["risk_level"],
            })
        except Exception as e:
            out.append({"country": country, "error": str(e)})
    out.sort(key=lambda x: x.get("total_score", -1), reverse=True)

    # The third sweep, and the one the issue did not name (#258): twenty-seven
    # countries, so twenty-seven evaluations logged for one request. Found by
    # enumerating `calculate_esg`'s callers rather than by working from the
    # list in the issue.
    scored = [row["total_score"] for row in out if "total_score" in row]
    if _shared_telemetry(caller):
        log_simulation(
            "country_ranking",
            metrics={
                "countries": len(out),
                "succeeded": len(scored),
                "failed": len(out) - len(scored),
                "max_score": round(max(scored), 2) if scored else None,
                "min_score": round(min(scored), 2) if scored else None,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
            tags={
                "event_id": event_id,
                "model_version": _model_version(),
                "partial_sample": "true" if len(scored) != len(out) else "false",
            },
        )
    return {"count": len(out), "ranking": out}


from pydantic import BaseModel as _BM_MC
class _MCRequest(_BM_MC):
    project_name: str = "Project"
    region: str = "Sweden"
    budget_usd: float
    co2_reduction_tons_per_year: float
    social_impact_score: float
    project_duration_months: int
    n: int = 500
    noise: float = 0.15

def _model_version() -> str:
    """The serving model's identity, or `None` when it declares none.

    A seam, like `_region_name`: it hides an `app.main` import that would
    otherwise happen inside the request. `retrained_at` is what
    `app/model_loader.py` already treats as the version -- nothing new is
    invented here, and a model that carries no such field yields `None` rather
    than a placeholder, because "unknown" written as a string reads like a
    version somebody chose.
    """
    try:
        from app.main import model_meta
        return (model_meta or {}).get("retrained_at")
    except Exception:
        return None


def _region_name(region: str) -> str:
    """The continent a country belongs to, or Europe.

    A seam, like `_simulate_once` below: it hides an `app.main` import that
    otherwise happens inside the request.
    """
    from app.main import COUNTRIES

    meta = COUNTRIES.get(region, {"region": "Europe"})
    return meta.get("region", "Europe") if isinstance(meta, dict) else "Europe"


def _clamped_sample(req: "_MCRequest", jitter) -> dict:
    """One jittered sample, held inside the range `calculate_esg` accepts.

    Pure, and imports nothing: the clamps are the part worth testing on their
    own, and a test of them should not have to stand up the application. They
    are also the reason invalid input is not a 422 here -- a negative budget or
    a social score of 400 is clamped rather than rejected, so it never becomes
    one exception per sample.

    Unchanged from the original handler; only moved.
    """
    return {
        "project_name": req.project_name,
        "region": req.region,
        "budget_usd": max(1000.0, jitter(req.budget_usd)),
        "co2_reduction_tons_per_year": max(1.0, jitter(req.co2_reduction_tons_per_year)),
        "social_impact_score": max(1.0, min(10.0, jitter(req.social_impact_score))),
        "project_duration_months": max(1, int(round(jitter(req.project_duration_months)))),
    }


def _simulate_once(req: "_MCRequest", region_name: str, jitter) -> float:
    """Score one jittered sample of the request.

    A seam: extracted so a test can replace the computation without importing
    `app.main`, which pulls in the whole application and, through
    `app.mlflow_tracking`, a network call with no timeout (issue 243). Before
    this, every test of this endpoint paid that import -- a targeted run of
    eight cases took four minutes and told us nothing about the contract.
    """
    from app.main import Project as _P, calculate_esg

    return calculate_esg(_P(**_clamped_sample(req, jitter)), region_name)["total_score"]


@router.post(
    "/evaluate/monte-carlo",
    response_model=MonteCarloOk,
    responses={503: {"model": MonteCarloUnavailable, "description": "No simulation produced a score."}},
    summary="Distribution of the ESG score under input noise",
)
async def evaluate_monte_carlo(req: _MCRequest,
                              caller: Optional[UserInfo] = Depends(optional_auth)):
    """Run N simulations with triangular noise on inputs, return distribution stats.

    Two things this contract fixes (docs/API_CONTRACT_ROADMAP.md). "Every
    simulation raised" used to be a 200 carrying `{"error": ...}`, so a caller
    reading `mean` got `undefined`; it is now a 500. And `n` counted successful
    runs while reading as the number requested -- `requested` is stated beside
    it, so a distribution built from a fraction of the sample says so.
    """
    import random, statistics, uuid
    started = time.perf_counter()
    event_id = uuid.uuid4().hex[:12]
    rname = _region_name(req.region)
    n = max(50, min(int(req.n), 5000))
    noise = max(0.01, min(float(req.noise), 0.5))
    scores = []
    _failed = 0
    _first_failure = None
    rng = random.Random(42)
    for _ in range(n):
        def jit(v):
            lo = v * (1 - noise); hi = v * (1 + noise)
            return rng.triangular(lo, hi, v)
        try:
            scores.append(_simulate_once(req, rname, jit))
        except Exception as exc:  # noqa: PERF203 - one bad sample must not end the run
            if _first_failure is None:
                _first_failure = exc
            _failed += 1
            continue
    if not scores:
        # Logged rather than returned: the caller gets a machine-readable
        # reason, and whatever the ESG calculation raised stays server-side.
        # One aggregated line, not one per sample: a thousand failed
        # simulations must not write a thousand tracebacks. The type is enough
        # to find it; the text stays out of the response.
        logger.warning(
            "monte-carlo %s: all %d simulations raised; first failure was %s",
            event_id, n, type(_first_failure).__name__,
        )
        # One event for the failed sweep too. The alternative the issue names
        # -- an explicit counter and no event -- would leave the experiment
        # unable to distinguish "nobody asked" from "everything raised".
        if _shared_telemetry(caller):
            log_simulation(
                "monte_carlo",
                metrics={
                    "requested": n, "succeeded": 0, "failed": _failed,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
                tags={
                    "event_id": event_id, "region": rname, "outcome": "no_successful_runs",
                    "model_version": _model_version(), "partial_sample": "true",
                },
            )
        return JSONResponse(
            status_code=503,
            content=MonteCarloUnavailable(
                status="unavailable", requested=n, n=0, failed=_failed,
                reason_code="no_successful_runs",
            ).model_dump(),
        )
    scores.sort()
    def pct(p):
        if not scores: return 0.0
        k = (len(scores) - 1) * p / 100
        f = int(k); c = min(f + 1, len(scores) - 1)
        return round(scores[f] + (scores[c] - scores[f]) * (k - f), 2)
    lo, hi = min(scores), max(scores)
    nbins = 20
    width = max((hi - lo) / nbins, 0.01)
    bins = [0] * nbins
    edges = [round(lo + i * width, 2) for i in range(nbins + 1)]
    for s in scores:
        idx = min(int((s - lo) / width), nbins - 1)
        bins[idx] += 1
    if _failed:
        logger.warning(
            "monte-carlo %s: %d of %d simulations raised; first failure was %s",
            event_id, _failed, n, type(_first_failure).__name__,
        )

    # **One** event for the whole sweep (#258). Each sample used to log its own
    # evaluation through `calculate_esg`, so the default `n = 500` wrote five
    # hundred MLflow runs for one request -- measured, and 444 of them landed
    # on production from two probe calls on 2026-09-06. None of them was an
    # evaluation anyone asked for.
    #
    # `event_id` also appears in the warnings above, so a log line and its run
    # can be joined. No part of the request payload is included: an aggregate
    # is a summary, and the project name is the caller's text.
    if _shared_telemetry(caller):
        log_simulation(
            "monte_carlo",
            metrics={
                "requested": n,
                "succeeded": len(scores),
                "failed": _failed,
                "mean": round(statistics.mean(scores), 2),
                "stdev": round(statistics.pstdev(scores), 2),
                "p05": pct(5),
                "p50": pct(50),
                "p95": pct(95),
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
            tags={
                "event_id": event_id,
                "region": rname,
                "model_version": _model_version(),
                "partial_sample": "true" if _failed else "false",
            },
        )
    return MonteCarloOk(
        status="ok",
        requested=n,
        n=len(scores),
        failed=_failed,
        mean=round(statistics.mean(scores), 2),
        stdev=round(statistics.pstdev(scores), 2),
        min=round(lo, 2),
        max=round(hi, 2),
        p10=pct(10),
        p50=pct(50),
        p90=pct(90),
        histogram=MonteCarloHistogram(edges=edges, counts=bins),
        reason_code="partial_sample" if _failed else None,
    )
