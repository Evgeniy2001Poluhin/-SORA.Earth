with open("app/api/predict.py", "r") as f:
    lines = f.readlines()

# Удаляем строки 31-35 (индексы 30-34) и заменяем чистым блоком
clean = [
    '    from app.main import make_features_v2, ensemble_model_v2\n',
    '    cat = getattr(project, "category", "Solar Energy")\n',
    '    reg = getattr(project, "region", "Europe")\n',
    '    prob_v2 = round(float(ensemble_model_v2.predict_proba(make_features_v2(_to_legacy(project), cat, reg))[0][1]) * 100, 2) if ensemble_model_v2 else round(proba*100, 2)\n',
    '    result = {"prediction": prediction, "probability": round(proba*100,2), "probability_v2": prob_v2, "model": "RandomForest", "threshold": best_threshold}\n',
]

# lines[30] = первый мусор (prob_v2 без cat)
# lines[31] = from app.main import ...
# lines[32] = cat = ...
# lines[33] = reg = ...
# lines[34] = prob_v2 = ... (дубль)
# lines[35] = result = ...
new_lines = lines[:30] + clean + lines[36:]
(new_lines)

print("Done, lines 31-36 replaced")
print("".join(new_lines[27:40]))
