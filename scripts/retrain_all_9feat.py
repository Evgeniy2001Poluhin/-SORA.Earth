import pandas as pd, numpy as np, pickle, os
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier, StackingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score, f1_score
from sklearn.calibration import CalibratedClassifierCV
from datetime import datetime
import torch
import torch.nn as tnn

ROOT = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(ROOT, '..')

df = pd.read_csv(os.path.join(ROOT, 'data', 'projects.csv'))
df['budget_per_month'] = df['budget'] / df['duration_months'].clip(lower=1)
df['co2_per_dollar'] = df['co2_reduction'] / df['budget'].clip(lower=1) * 1000
df['efficiency_score'] = (df['co2_reduction'] * df['social_impact']) / df['duration_months'].clip(lower=1)
now = datetime.utcnow()
df['year'] = now.year
df['quarter'] = (now.month - 1) // 3 + 1

COLS = ['budget','co2_reduction','social_impact','duration_months',
        'budget_per_month','co2_per_dollar','efficiency_score','year','quarter']

X = df[COLS].values
y = df['success'].values

Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)

sc = StandardScaler()
Xtr_s = sc.fit_transform(Xtr)
Xte_s = sc.transform(Xte)

# --- RF ---
rf = RandomForestClassifier(n_estimators=200, max_depth=10, min_samples_leaf=2, random_state=42, n_jobs=-1)
rf.fit(Xtr_s, ytr)
auc_rf = roc_auc_score(yte, rf.predict_proba(Xte_s)[:,1])
print(f'RF  AUC: {auc_rf:.4f}')

# --- Neural net (9 features) ---
class SoraNet(tnn.Module):
    def __init__(self):
        super().__init__()
        self.net = tnn.Sequential(
            tnn.Linear(9, 64), tnn.ReLU(), tnn.BatchNorm1d(64), tnn.Dropout(0.3),
            tnn.Linear(64, 32), tnn.ReLU(), tnn.BatchNorm1d(32), tnn.Dropout(0.2),
            tnn.Linear(32, 16), tnn.ReLU(), tnn.Linear(16, 1), tnn.Sigmoid(),
        )
    def forward(self, x):
        return self.net(x)

nn_model = SoraNet()
Xt = torch.FloatTensor(Xtr_s)
yt = torch.FloatTensor(ytr).unsqueeze(1)
opt = torch.optim.Adam(nn_model.parameters(), lr=0.001)
loss_fn = tnn.BCELoss()

nn_model.train()
for epoch in range(150):
    opt.zero_grad()
    out = nn_model(Xt)
    loss = loss_fn(out, yt)
    loss.backward()
    opt.step()

nn_model.eval()
with torch.no_grad():
    preds = nn_model(torch.FloatTensor(Xte_s)).numpy().flatten()
auc_nn = roc_auc_score(yte, preds)
print(f'NN  AUC: {auc_nn:.4f}')

# --- Save ---
mdir = os.path.join(ROOT, 'models')
with open(os.path.join(mdir, 'random_forest.pkl'), 'wb') as f:
    pickle.dump(rf, f)
with open(os.path.join(mdir, 'scaler.pkl'), 'wb') as f:
    pickle.dump(sc, f)
torch.save(nn_model.state_dict(), os.path.join(mdir, 'pytorch_mlp.pth'))
print('Saved: random_forest.pkl, scaler.pkl, pytorch_mlp.pth')

# --- Stacking ensemble v2 (11 features) stays separate ---
print('Done. Run retrain for ensemble_v2 separately if needed.')
