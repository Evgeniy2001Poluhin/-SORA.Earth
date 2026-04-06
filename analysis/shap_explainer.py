import shap, joblib, os, pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
os.makedirs('reports', exist_ok=True)
model = joblib.load('models/xgb_model.pkl')
X = pd.read_csv('data/projects_100.csv').select_dtypes(include=['number'])
explainer = shap.TreeExplainer(model)
sv = explainer.shap_values(X)
shap.summary_plot(sv, X, plot_type='bar', show=False)
plt.tight_layout()
plt.savefig('reports/shap_importance.png', dpi=150)
plt.close()
shap.summary_plot(sv, X, show=False)
plt.tight_layout()
plt.savefig('reports/shap_beeswarm.png', dpi=150)
plt.close()
print('Done')