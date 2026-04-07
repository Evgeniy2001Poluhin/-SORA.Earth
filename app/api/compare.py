import os, pickle
import pandas as pd
from fastapi import APIRouter
from sklearn.metrics import roc_auc_score, f1_score, accuracy_score
from datetime import datetime as _dt

router = APIRouter(prefix='/model', tags=['ml-ops'])

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODELS   = os.path.join(ROOT_DIR, 'models')
DATA_CSV = os.path.join(ROOT_DIR, 'data', 'projects.csv')
SCALER_COLS = ['budget','co2_reduction','social_impact','duration_months',
               'budget_per_month','co2_per_dollar','efficiency_score','year','quarter']

def _load_model(rf_path, sc_path):
    if not os.path.exists(rf_path) or not os.path.exists(sc_path):
        return None, None
    with open(rf_path, 'rb') as f: rf = pickle.load(f)
    with open(sc_path, 'rb') as f: sc = pickle.load(f)
    return rf, sc

def _score(rf, sc):
    df = pd.read_csv(DATA_CSV)
    df['budget_per_month'] = df['budget'] / df['duration_months'].clip(lower=1)
    df['co2_per_dollar']   = df['co2_reduction'] / df['budget'].clip(lower=1) * 1000
    df['efficiency_score'] = (df['co2_reduction'] * df['social_impact']) / df['duration_months'].clip(lower=1)
    n = _dt.utcnow()
    df['year'] = n.year; df['quarter'] = (n.month - 1) // 3 + 1
    X_all = sc.transform(df[SCALER_COLS].fillna(0))
    X_rf  = X_all[:, :rf.n_features_in_]
    col   = 'success' if 'success' in df.columns else 'approved'
    y     = df[col].values
    preds = rf.predict(X_rf)
    proba = rf.predict_proba(X_rf)[:,1]
    return {
        'auc':      round(float(roc_auc_score(y, proba)), 4),
        'f1':       round(float(f1_score(y, preds)), 4),
        'accuracy': round(float(accuracy_score(y, preds)), 4),
        'n_estimators': rf.n_estimators,
        'n_features': rf.n_features_in_,
    }

@router.get('/compare')
def compare_models():
    cur_rf, cur_sc = _load_model(
        os.path.join(MODELS, 'random_forest.pkl'),
        os.path.join(MODELS, 'scaler.pkl')
    )
    bak_rf, bak_sc = _load_model(
        os.path.join(MODELS, 'random_forest.pkl.bak'),
        os.path.join(MODELS, 'scaler.pkl.bak')
    )
    result = {'current': None, 'backup': None, 'winner': None, 'delta': {}}
    if cur_rf: result['current'] = _score(cur_rf, cur_sc)
    if bak_rf: result['backup']  = _score(bak_rf, bak_sc)
    if result['current'] and result['backup']:
        for m in ['auc', 'f1', 'accuracy']:
            result['delta'][m] = round(result['current'][m] - result['backup'][m], 4)
        result['winner'] = 'current' if result['current']['auc'] >= result['backup']['auc'] else 'backup'
    elif result['current']:
        result['winner'] = 'current'
        result['backup'] = 'no_backup_found'
    return result
