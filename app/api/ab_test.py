import logging
import os, random, time
from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from collections import defaultdict

from app.schemas import ABPredictOk, ABPredictUnavailable, ABSplitOk

logger = logging.getLogger(__name__)


router = APIRouter(prefix='/ab', tags=['a/b-testing'])
_stats = defaultdict(lambda: {'requests': 0, 'total_prob': 0.0})
_traffic_split = {'model_a': 0.5}


def _clip_prob(p: float) -> float:
    return min(max(float(p), 0.0001), 0.9799)


class ABRequest(BaseModel):
    budget: float
    co2_reduction: float
    social_impact: float
    duration_months: float
    team_size: float = 5.0
    region_risk: float = 3.0
    technology_readiness: float = 7.0
    category: str = 'Solar Energy'
    region: str = 'Europe'


@router.post(
    '/predict',
    response_model=ABPredictOk,
    responses={503: {"model": ABPredictUnavailable,
                     "description": "Neither arm could produce a prediction."}},
)
def ab_predict(data: ABRequest):
    from app.main import rf_model, ensemble_model_v2, make_features, make_features_v2
    use_a = random.random() < _traffic_split['model_a']
    t0 = time.time()
    try:
        if use_a or ensemble_model_v2 is None:
            feats = make_features(data)
            prob = float(rf_model.predict_proba(feats)[0][1])
            model_used = 'model_a_rf'
        else:
            feats = make_features_v2(data, data.category, data.region)
            prob = float(ensemble_model_v2.predict_proba(feats)[0][1])
            model_used = 'model_b_ensemble_v2'
    except Exception as e:
        # 503, not 200: a body with no `probability` is not a prediction, and
        # at 200 a consumer reading `.probability` finds nothing where a number
        # belongs. The exception text stays in the log -- it names internals and
        # the caller can do nothing with it (#247 removed the same echo from
        # the MLflow path).
        logger.exception("A/B prediction failed: %s", e)
        return JSONResponse(
            status_code=503,
            content=ABPredictUnavailable(
                reason_code="model_unavailable",
                detail="No model could produce a prediction for this request.",
            ).model_dump(),
        )

    prob = round(_clip_prob(prob), 4)
    latency = round((time.time() - t0) * 1000, 2)
    _stats[model_used]['requests'] += 1
    _stats[model_used]['total_prob'] += prob

    return {
        'model': model_used,
        'probability': prob,
        'prediction': 'approved' if prob >= 0.5 else 'rejected',
        'latency_ms': latency,
        'traffic_split': dict(_traffic_split),
    }


@router.get('/stats')
def ab_stats():
    result = {}
    for model, s in _stats.items():
        result[model] = {
            'requests': s['requests'],
            'avg_probability': round(s['total_prob'] / s['requests'], 4) if s['requests'] else 0,
        }
    result['traffic_split'] = dict(_traffic_split)
    return result


@router.post('/split', response_model=ABSplitOk)
def set_split(model_a_pct: float = Body(..., embed=True, ge=0.0, le=1.0)):
    """Set the share of traffic going to arm A.

    **An out-of-range value is a 422, and the split does not move.** This used
    to answer 200 with `{"error": "must be 0.0-1.0"}` and change nothing, so a
    caller that sent 5.0 was told the request succeeded and went on believing
    the split was 5.0. A write that silently did not happen is worse than one
    that fails.

    The range is a constraint on the parameter rather than an `if` in the body:
    FastAPI refuses it before this function runs, and the rule appears in
    `/openapi.json` where a client can see it.
    """
    _traffic_split['model_a'] = model_a_pct
    return ABSplitOk(status="ok", traffic_split=dict(_traffic_split))
