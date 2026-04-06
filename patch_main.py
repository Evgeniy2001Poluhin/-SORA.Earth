with open("app/main.py", "r") as f:
    src = f.read()

# Патч 1: загрузка v2 после ENS_PATH блока
old1 = 'ENS_PATH = os.path.join(ROOT_DIR, "models", "ensemble_model.pkl")\nensemble_model = None'
new1 = old1 + '''

# v2 stacking model
try:
    with open(os.path.join(ROOT_DIR, "models", "cat_encodings.json")) as _f:
        cat_encodings = json.load(_f)
    with open(os.path.join(ROOT_DIR, "models", "scaler_v2.pkl"), "rb") as _f:
        scaler_v2 = pickle.load(_f)
    with open(os.path.join(ROOT_DIR, "models", "ensemble_model_v2.pkl"), "rb") as _f:
        ensemble_model_v2 = pickle.load(_f)
    FEATURE_COLS_V2 = ["budget","co2_reduction","social_impact","duration_months",
                       "budget_per_month","co2_per_dollar","efficiency_score",
                       "impact_ratio","budget_efficiency","category_enc","region_enc"]
    logger.info("ensemble_model_v2 loaded OK (CV AUC=0.82)")
except Exception as _e:
    ensemble_model_v2 = None;caler_v2 = None; FEATURE_COLS_V2 = []
    logger.warning(f"ensemble_model_v2 not loaded: {_e}")'''

if old1 in src:
    src = src.replace(old1, new1)
    print("Patch 1 OK")
else:
    print("Patch 1 FAILED - block not found")

# Патч 2: добавляем make_features_v2 после существующей make_features
insert_after = "    return pd.DataFrame(scaler.transform(df), columns=FEATURE_COLS)"
addon = '''

def make_features_v2(data, category: str = "Solar Energy", region: str = "Europe"):
    if scaler_v2 is None:
        return make_features(data)
    bpm = data.budget / max(data.duration_months, 1)
    c2d = data.co2_reduction / max(data.budget, 1) * 1000
    eff = (data.co2_reduction * data.social_impact) / max(data.duration_months, 1)
    ir  = data.social_impact / max(data.co2_reduction, 1)
    be  = data.co2_reduction / max(bpm, 1)
    c_enc = cat_encodings.get("category", {}).get(category, 0.5)
    r_enc = cat_encodings.get("region", {}).get(region, 0.5)
    row = [[data.budget, data.cct, data.duration_months,
            bpm, c2d, eff, ir, be, c_enc, r_enc]]
    df = pd.DataFrame(row, columns=FEATURE_COLS_V2)
    return pd.DataFrame(scaler_v2.transform(df), columns=FEATURE_COLS_V2)'''

if insert_after in src:
    src = src.replace(insert_after, insert_after + addon)
    print("Patch 2 OK")
else:
    print("Patch 2 FAILED - anchor not found")

with open("app/main.py", "w") as f:
    f.write(src)
print("app/main.py saved")
