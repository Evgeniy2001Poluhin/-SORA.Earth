with open("app/main.py", "r") as f:
    src = f.read()

old = "    features_scaled = make_features(project)\n    success_prob = round(float(rf_model.predict_proba(features_scaled)[0][1]) * 100, 2)"

new = """    features_scaled = make_features(project)
    success_prob = round(float(rf_model.predict_proba(features_scaled)[0][1]) * 100, 2)

    category = getattr(project, "category", "Solar Energy")
    region   = getattr(project, "region",   "Europe")
    if ensemble_model_v2 is not None:
        feats_v2 = make_features_v2(project, category, region)
        success_prob_v2 = round(float(ensemble_model_v2.predict_proba(feats_v2)[0][1]) * 100, 2)
    else:
        success_prob_v2 = success_prob"""

if old in src:
    src = src.replace(old, new)
    print("Patch 1 OK")
else:
    idx = src.find("make_features(project)")
    print("Anchor not found, context:", repr(src[max(0,idx-30):idx+120]))

# Добавляем success_prob_v2 в response dict
old2 = '"success_probabicess_prob'
new2 = '"success_probability": success_prob,\n        "success_probability_v2": success_prob_v2'

if old2 in src:
    src = src.replace(old2, new2)
    print("Patch 2 OK")
else:
    idx = src.find("success_prob")
    print("Response anchor not found, context:", repr(src[max(0,idx-30):idx+100]))

with open("app/main.py", "w") as f:
    f.write(src)
