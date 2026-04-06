with open("app/api/predict.py", "r") as f:
    lines = f.readlines()

# Находим начало и конец функции predict_project
start = None
end = None
for i, line in enumerate(lines):
    if 'def predict_project' in line:
        start = i
    if start and i > start and line.startswith('@router') and i > start + 2:
        end = i
        break

print(f"Function: lines {start+1}..{end}")

clean_func = [
    '@router.post("/predict")\n',
    'def predict_project(project: Project):\n',
    '    feats = make_features(_to_legacy(project))\n',
    '    proba = float(rf_model.predict_proba(feats)[0][1])\n',
    '    prediction = int(proba >= best_threshold)\n',
    '    cat = getattr(project, "category", "Solar Energy")\n',
    '    reg = getattr(project, "region", "Europe")\n',
    '    prob_v2 = round(float(ensemble_model_v2.predict_proba(make_features_v2(_to_legacy(project), cat, reg))[0][1]) * 100, 2) if ensemble_model_v2 else round(proba*100, 2)rediction": prediction, "probability": round(proba*100,2), "probability_v2": prob_v2, "model": "RandomForest", "threshold": best_threshold}\n',
    '    log_prediction("RandomForest", project.model_dump(), prediction, round(proba*100,2))\n',
    '    METRICS["predictions_total"] = METRICS.get("predictions_total",0)+1\n',
    '    return result\n',
    '\n',
]

# Строка с module-level импортом — убираем из тела, оставляем только если уже вверху
# Находим строку с module-level импортом (не внутри функции)
new_lines = []
for i, line in enumerate(lines):
    if i == start - 1 and 'from app.main import' in line and 'make_features_v2' in line:
        # это module-level — оставляем
        new_lines.append(line)
        continue
    if start <= i < end:
        continue  # удаляем старую функцию
    new_lines.append(line)

# Вставляем чистую функцию
insert_at = staыла функция
idx = None
for i, line in enumerate(new_lines):
    if '@router.post("/predict/neural")' in line:
        idx = i
        break

new_lines = new_lines[:idx] + clean_func + new_lines[idx:]

with open("app/api/predict.py", "w") as f:
    f.writelines(new_lines)
print("Done")
print("".join(new_lines[idx-2:idx+14]))
