with open("app/api/predict.py", "r") as f:
    lines = f.readlines()

# 1. Добавляем импорт на уровень модуля (после существующего импорта из app.main)
for i, line in enumerate(lines):
    if "from app.main import rf_model, best_threshold, make_features" in line:
        lines[i] = "from app.main import rf_model, best_threshold, make_features, make_features_v2, ensemble_model_v2\n"
        print(f"Module import patched at line {i+1}")
        break

# 2. Убираем inline `from app.main import` из тела функции
new_lines = []
for i, line in enumerate(lines):
    if "from app.main import make_features_v2, ensemble_model_v2" in line:
        print(f"Removed inline import at line {i+1}")
        continue
    new_lines.append(line)

with open("app/api/predict.py", "w") as f:
    f.writelines(new_lines)
print("Done")
