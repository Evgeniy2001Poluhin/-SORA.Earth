with open("app/api/predict.py", "r") as f:
    src = f.read()

old = '    result = {"prediction": prediction, "probability": round(proba*100,2), "model": "RandomForest", "threshold": best_threshold}'
new = ('    from app.main import make_features_v2, ensemble_model_v2\n'
       '    cat = getattr(project, "category", "Solar Energy")\n'
       '    reg = getattr(project, "region", "Europe")\n'
       '    prob_v2 = round(float(ensemble_model_v2.predict_proba(make_features_v2(_to_legacy(project), cat, reg))[0][1]) * 100, 2) if ensemble_model_v2 else round(proba*100, 2)\n'
       '    result = {"prediction": prediction, "probability": round(proba*100,2), "probability_v2": prob_v2, "model": "RandomForest", "threshold": best_threshold}')

if old in src:
    src = src.replace(old, new)
    print("Patch OK")
else:
    idx = src.find("RandomForest")
    print("Not found, context:", repr(src[max(0,idx-100):idx+80]))

with open("app/api/predict.py", "w") as f:
    f.write(src)
