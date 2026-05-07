import pandas as pd, numpy as np, pickle, os, json
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier, StackingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score
from sklearn.calibration import CalibratedClassifierCV
from datetime import datetime

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
df = pd.read_csv(os.path.join(ROOT, 'data', 'projects.csv'))

df['budget_per_month'] = df['budget'] / df['duration_months'].clip(lower=1)
df['co2_per_dollar'] = df['co2_reduction'] / df['budget'].clip(lower=1) * 1000
df['efficiency_score'] = (df['co2_reduction'] * df['social_impact']) / df['duration_months'].clip(lower=1)
df['impact_ratio'] = df['co2_reduction'] / (df['social_impact'] + 1)
df['budget_efficiency'] = df['co2_reduction'] / (df['budget'] + 1)
df['category_enc'] = pd.factorize(df['category'])[0]
df['region_enc'] = pd.factorize(df['region'])[0]

# Save encodings
cats = dict(zip(df['category'], df['category_enc']))
regs = dict(zip(df['region'], df['region_enc']))
with open(os.path.join(ROOT, 'models', 'cat_encodings.json'), 'w') as f:
    json.dump({'category': cats, 'region': regs}, f)

COLS = ['budget','co2_reduction','social_impact','duration_months',
        'budget_per_month','co2_per_dollar','efficiency_score',
        'impact_ratio','budget_efficiency','category_enc','region_enc']

X = df[COLS]
y = df['success'].values

Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)

sc = StandardScaler()
Xtr_s = pd.DataFrame(sc.fit_transform(Xtr), columns=COLS)
Xte_s = pd.DataFrame(sc.transform(Xte), columns=COLS)

stack = StackingClassifier(
    estimators=[
        ('rf', RandomForestClassifier(n_estimators=200, max_depth=10, random_state=42, n_jobs=-1)),
        ('gb', GradientBoostingClassifier(n_estimators=150, max_depth=5, random_state=42)),
    ],
    final_estimator=LogisticRegression(max_iter=1000),
    cv=5, passthrough=False
)
stack.fit(Xtr_s, ytr)
auc = roc_auc_score(yte, stack.predict_proba(Xte_s)[:,1])
print(f'Ensemble v2 AUC: {auc:.4f}')

# Calibrate
cal = CalibratedClassifierCV(estimator=stack, method='sigmoid', cv='prefit')
cal.fit(Xte_s, yte)
probs = cal.predict_proba(Xte_s)[:,1]
from sklearn.metrics import brier_score_loss
print(f'Brier: {brier_score_loss(yte, probs):.4f}  mean_prob: {probs.mean():.3f}')

mdir = os.path.join(ROOT, 'models')
with open(os.path.join(mdir, 'ensemble_model_v2.pkl'), 'wb') as f:
    pickle.dump(stack, f)
with open(os.path.join(mdir, 'ensemble_model_v2_cal.pkl'), 'wb') as f:
    pickle.dump(cal, f)
with open(os.path.join(mdir, 'scaler_v2.pkl'), 'wb') as f:
    pickle.dump(sc, f)
print('Saved: ensemble_model_v2.pkl, ensemble_model_v2_cal.pkl, scaler_v2.pkl')
