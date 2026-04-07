import pickle, numpy as np, pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from sklearn.metrics import roc_curve, auc, confusion_matrix, ConfusionMatrixDisplay, brier_score_loss
from sklearn.calibration import calibration_curve

df = pd.read_csv('/app/data/projects.csv')
rf = pickle.load(open('/app/models/random_forest.pkl','rb'))
rf_cal = pickle.load(open('/app/models/rf_model_cal.pkl','rb'))
ens_v2 = pickle.load(open('/app/models/ensemble_model_v2.pkl','rb'))
scaler = pickle.load(open('/app/models/scaler.pkl','rb'))
scaler_v2 = pickle.load(open('/app/models/scaler_v2.pkl','rb'))
threshold = pickle.load(open('/app/models/best_threshold.pkl','rb'))

feat4 = ['budget','co2_reduction','social_impact','duration_months']
eng = ['budget_per_month','co2_per_dollar','efficiency_score']
X = scaler.transform(df[feat4].values)
y = df['success'].values

cat_d = pd.get_dummies(df['category'], prefix='cat')
reg_d = pd.get_dummies(df['region'], prefix='reg')
Xv2_raw = pd.concat([df[feat4 + eng], cat_d, reg_d], axis=1)
has_v2 = False
try:
    v2c = ens_v2.feature_names_in_
    for c in v2c:
        if c not in Xv2_raw.columns:
            Xv2_raw[c] = 0
    Xv2 = scaler_v2.transform(Xv2_raw[v2c].values)
    pv2 = ens_v2.predict_proba(Xv2)[:, 1]
    has_v2 = True
except Exception as e:
    print('V2 skip:', e)

prf = rf.predict_proba(X)[:, 1]
pcal = rf_cal.predict_proba(X)[:, 1]

models_list = [('RF v1', prf, 'blue', '-'), ('RF v1 Cal', pcal, 'green', '--')]
if has_v2:
    models_list.append(('Stacking v2', pv2, 'red', '-.'))

fig, ax = plt.subplots(figsize=(8, 7))
for name, p, c, ls in models_list:
    fpr, tpr, _ = roc_curve(y, p)
    ax.plot(fpr, tpr, color=c, ls=ls, lw=2, label='{} (AUC={:.3f})'.format(name, auc(fpr, tpr)))
ax.plot([0,1],[0,1],'k--',lw=1,alpha=0.5)
ax.set_xlabel('False Positive Rate', fontsize=13)
ax.set_ylabel('True Positive Rate', fontsize=13)
ax.set_title('ROC Curves', fontsize=15, fontweight='bold')
ax.legend(fontsize=12, loc='lower right')
ax.grid(True, alpha=0.3)
plt.tight_layout()
plt.savefig('/app/data/roc_curves.png', dpi=150)
plt.close()
print('1/5 ROC done')

fig, axes = plt.subplots(1, 2, figsize=(14, 6))
cm1 = confusion_matrix(y, (prf >= 0.5).astype(int))
cm2 = confusion_matrix(y, (prf >= threshold).astype(int))
ConfusionMatrixDisplay(cm1, display_labels=['Fail','Success']).plot(ax=axes[0], cmap='Blues', colorbar=False)
axes[0].set_title('Threshold = 0.50', fontsize=13, fontweight='bold')
ConfusionMatrixDisplay(cm2, display_labels=['Fail','Success']).plot(ax=axes[1], cmap='Greens', colorbar=False)
axes[1].set_title('Threshold = {:.2f} (optimized)'.format(threshold), fontsize=13, fontweight='bold')
plt.suptitle('Confusion Matrix - RF v1', fontsize=15, fontweight='bold')
plt.tight_layout()
plt.savefig('/app/data/confusion_matrix.png', dpi=150)
plt.close()
print('2/5 Confusion matrix done')

fig, ax = plt.subplots(figsize=(8, 7))
for name, p, c, ls in models_list:
    pt, pp = calibration_curve(y, p, n_bins=10, strategy='uniform')
    bs = brier_score_loss(y, p)
    ax.plot(pp, pt, color=c, ls=ls, lw=2, marker='o', label='{} (Brier={:.4f})'.format(name, bs))
ax.plot([0,1],[0,1],'k--',lw=1,alpha=0.5, label='Perfect')
ax.set_xlabel('Mean Predicted Probability', fontsize=13)
ax.set_ylabel('Fraction of Positives', fontsize=13)
ax.set_title('Reliability Diagram', fontsize=15, fontweight='bold')
ax.legend(fontsize=11)
ax.grid(True, alpha=0.3)
plt.tight_layout()
plt.savefig('/app/data/reliability_diagram_v2.png', dpi=150)
plt.close()
print('3/5 Reliability done')

try:
    import shap
    explainer = pickle.load(open('/app/models/shap_explainer.pkl','rb'))
    sv = explainer(X[:100])
    fig = plt.figure(figsize=(10, 7))
    shap.plots.beeswarm(sv, show=False, max_display=15)
    plt.title('SHAP Feature Importance', fontsize=15, fontweight='bold')
    plt.tight_layout()
    plt.savefig('/app/data/shap_beeswarm_v2.png', dpi=150, bbox_inches='tight')
    plt.close()
    print('4/5 SHAP done')
except Exception as e:
    print('4/5 SHAP skip:', e)

fig, ax = plt.subplots(figsize=(8, 5))
imp = rf.feature_importances_
idx = np.argsort(imp)
ax.barh([feat4[i] for i in idx], imp[idx], color='#1168bd')
ax.set_xlabel('Importance', fontsize=13)
ax.set_title('Feature Importance - RF v1', fontsize=15, fontweight='bold')
plt.tight_layout()
plt.savefig('/app/data/feature_importance.png', dpi=150)
plt.close()
print('5/5 Feature importance done')
print('=== ALL CHARTS GENERATED ===')
