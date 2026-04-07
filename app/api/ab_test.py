import os, random, time
from fastapi import APIRouter, Body
from pydantic import BaseModel
from collections import defaultdict

router = APIRouter(prefix='/ab', tags=['a/b-testing'])
_stats = defaultdict(lambda: {'requests': 0, 'total_prob': 0.0})
_traffic_split = {'model_a': 0.5}

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

@router.post('/predict')
def ab_predict(data: ABRequest):
    from app.main import rf_model, ensemble_model_v2, make_features, make_features_v2
    use_a = random.random() < _traffic_split['model_a']
    t0 = time.time()
    try:
        if use_a or ensemble_model_v2 is None:
            feats = make_features(data)
            prob = round(float(rf_model.predict_proba(feats)[0][1]), 4)
            model_used = 'model_a_rf'
        else:
            feats = make_features_v2(data, data.category, data.region)
            prob = round(float(ensemble_model_v2.predict_proba(feats)[0][1]), 4)
            model_used = 'model_b_ensemble_v2'
    except Exception as e:
        return {'error': str(e)}
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

@router.post('/split')
def set_split(model_a_pct: float = Body(..., embed=True)):
    if not 0.0 <= model_a_pct <= 1.0:
        return {'error': 'must be 0.0-1.0'}
    _traffic_split['model_a'] = model_a_pct
    return {'status': 'ok', 'traffic_split': dict(_traffic_split)}
