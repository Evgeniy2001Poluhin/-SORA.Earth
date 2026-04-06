with open("app/api/predict.py", "r") as f:
    src = f.read()

# В make_features_v2 вызов использует data вместо _to_legacy(project)
old = "make_features_v2(data)"
new = "make_features_v2(_to_legacy(project), cat, reg)"

if old in src:
    src = src.replace(old, new)
    print("Fixed 'data' -> '_to_legacy(project)' OK")
else:
    # Показываем контекст вокруг make_features_v2
    idx = src.find("make_features_v2")
    print("Context:", repr(src[max(0,idx-30):idx+80]))

with open("app/api/predict.py", "w") as f:
    f.write(src)
