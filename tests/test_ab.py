import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

SAMPLE = {
    'budget': 75000,
    'co2_reduction': 150,
    'social_impact': 9,
    'duration_months': 18
}

def test_ab_predict_status():
    r = client.post('/ab/predict', json=SAMPLE)
    assert r.status_code == 200

def test_ab_predict_schema():
    r = client.post('/ab/predict', json=SAMPLE)
    body = r.json()
    assert 'model' in body
    assert 'probability' in body
    assert 'prediction' in body
    assert 'latency_ms' in body

def test_ab_predict_probability_calibrated():
    r = client.post('/ab/predict', json=SAMPLE)
    prob = r.json()['probability']
    assert 0.0 < prob < 0.98, 'Probability looks uncalibrated: ' + str(prob)

def test_ab_predict_prediction_values():
    r = client.post('/ab/predict', json=SAMPLE)
    assert r.json()['prediction'] in ('approved', 'rejected')

def test_ab_predict_low_budget():
    r = client.post('/ab/predict', json={
        'budget': 100,
        'co2_reduction': 0,
        'social_impact': 0,
        'duration_months': 1
    })
    assert r.status_code == 200
    assert r.json()['probability'] < 0.7

def test_ab_predict_missing_field():
    r = client.post('/ab/predict', json={'budget': 75000})
    assert r.status_code == 422

def test_ab_stats_status():
    r = client.get('/ab/stats')
    assert r.status_code == 200

def test_health_status():
    r = client.get('/health')
    assert r.status_code == 200

def test_health_all_checks():
    body = client.get('/health').json()
    assert body['status'] == 'healthy'
    checks = body.get('checks', {})
    assert checks.get('models', {}).get('status') == 'healthy'
    assert checks.get('database', {}).get('status') == 'healthy'

def test_health_models_loaded():
    body = client.get('/health').json()
    assert body['checks']['models']['loaded'] is True
