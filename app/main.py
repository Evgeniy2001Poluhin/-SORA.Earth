from functools import lru_cache
from fastapi import FastAPI
from app.middleware import MetricsMiddleware, METRICS
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from typing import List, Optional
import pickle
import numpy as np
import math
import os
import io
import csv
import json
import logging
import shap
import pandas as pd
from xgboost import XGBClassifier
import sqlite3
from datetime import datetime

from app.schemas import ProjectInput as Project, GHGInput

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sora")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
DB_PATH = os.path.join(ROOT_DIR, "data", "history.db")
META_PATH = os.path.join(ROOT_DIR, "models", "meta.json")
MODEL_PATH = os.path.join(ROOT_DIR, "models", "model.pkl")
SCALER_PATH = os.path.join(ROOT_DIR, "models", "scaler.pkl")
XGB_PATH = os.path.join(ROOT_DIR, "models", "xgb_model.pkl")
METRICS_PATH = os.path.join(ROOT_DIR, "models", "metrics.json")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS evaluations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT, budget REAL, co2_reduction REAL, social_impact REAL,
        duration_months INTEGER, total_score REAL, environment_score REAL,
        social_score REAL, economic_score REAL, success_probability REAL,
        recommendation TEXT, risk_level TEXT, created_at TEXT,
        region TEXT DEFAULT 'Europe', lat REAL DEFAULT 50.0, lon REAL DEFAULT 10.0)""")
    conn.commit()
    conn.close()


import csv, datetime
PRED_LOG = os.path.join(ROOT_DIR, "data", "predictions_log.csv")
def log_prediction(endpoint, input_data, result):
    file_exists = os.path.exists(PRED_LOG)
    with open(PRED_LOG, "a", newline="") as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow(["timestamp","endpoint","budget","co2_reduction","social_impact","duration_months","prediction","probability"])
        w.writerow([datetime.datetime.now().isoformat(), endpoint,
                    input_data.budget, input_data.co2_reduction, input_data.social_impact,
                    input_data.duration_months, result.get("prediction",""), result.get("probability","")])


import csv, datetime
PRED_LOG = os.path.join(ROOT_DIR, "data", "predictions_log.csv")
def log_prediction(endpoint, input_data, result):
    file_exists = os.path.exists(PRED_LOG)
    with open(PRED_LOG, "a", newline="") as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow(["timestamp","endpoint","budget","co2_reduction","social_impact","duration_months","prediction","probability"])
        w.writerow([datetime.datetime.now().isoformat(), endpoint,
                    input_data.budget, input_data.co2_reduction, input_data.social_impact,
                    input_data.duration_months, result.get("prediction",""), result.get("probability","")])

app = FastAPI(title="SORA.Earth AI Platform", version="2.0.0")
app.add_middleware(MetricsMiddleware)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

with open(SCALER_PATH, "rb") as f:
    scaler = pickle.load(f)
with open(MODEL_PATH, "rb") as f:
    rf_model = pickle.load(f)
with open(META_PATH, "r") as f:
    model_meta = json.load(f)
with open(XGB_PATH, "rb") as f:
    xgb_model = pickle.load(f)
with open(METRICS_PATH, "r") as f:
    model_metrics = json.load(f)

init_db()
logger.info("SORA.Earth AI Platform started")
explainer_shap = shap.TreeExplainer(rf_model)

COUNTRIES = {
    "Afghanistan":{"lat":33.9,"lon":67.7,"region":"Asia"},
    "Albania":{"lat":41.2,"lon":20.2,"region":"Europe"},
    "Algeria":{"lat":28.0,"lon":1.7,"region":"Africa"},
    "Argentina":{"lat":-38.4,"lon":-63.6,"region":"South America"},
    "Armenia":{"lat":40.1,"lon":45.0,"region":"Asia"},
    "Australia":{"lat":-25.3,"lon":133.8,"region":"Oceania"},
    "Austria":{"lat":47.5,"lon":14.6,"region":"Europe"},
    "Azerbaijan":{"lat":40.1,"lon":47.6,"region":"Asia"},
    "Bangladesh":{"lat":23.7,"lon":90.4,"region":"Asia"},
    "Belarus":{"lat":53.7,"lon":27.9,"region":"Europe"},
    "Belgium":{"lat":50.8,"lon":4.5,"region":"Europe"},
    "Bolivia":{"lat":-16.3,"lon":-63.6,"region":"South America"},
    "Bosnia and Herzegovina":{"lat":43.9,"lon":17.7,"region":"Europe"},
    "Brazil":{"lat":-14.2,"lon":-51.9,"region":"South America"},
    "Bulgaria":{"lat":42.7,"lon":25.5,"region":"Europe"},
    "Cambodia":{"lat":12.6,"lon":105.0,"region":"Asia"},
    "Cameroon":{"lat":7.4,"lon":12.4,"region":"Africa"},
    "Canada":{"lat":56.1,"lon":-106.3,"region":"North America"},
    "Chile":{"lat":-35.7,"lon":-71.5,"region":"South America"},
    "China":{"lat":35.9,"lon":104.2,"region":"Asia"},
    "Colombia":{"lat":4.6,"lon":-74.3,"region":"South America"},
    "Congo":{"lat":-4.0,"lon":21.8,"region":"Africa"},
    "Costa Rica":{"lat":9.7,"lon":-83.8,"region":"North America"},
    "Croatia":{"lat":45.1,"lon":15.2,"region":"Europe"},
    "Cuba":{"lat":21.5,"lon":-77.8,"region":"North America"},
    "Czech Republic":{"lat":49.8,"lon":15.5,"region":"Europe"},
    "Denmark":{"lat":56.3,"lon":9.5,"region":"Europe"},
    "Ecuador":{"lat":-1.8,"lon":-78.2,"region":"South America"},
    "Egypt":{"lat":26.8,"lon":30.8,"region":"Africa"},
    "Estonia":{"lat":58.6,"lon":25.0,"region":"Europe"},
    "Ethiopia":{"lat":9.1,"lon":40.5,"region":"Africa"},
    "Finland":{"lat":61.9,"lon":25.7,"region":"Europe"},
    "France":{"lat":46.2,"lon":2.2,"region":"Europe"},
    "Georgia":{"lat":42.3,"lon":43.4,"region":"Asia"},
    "Germany":{"lat":51.2,"lon":10.5,"region":"Europe"},
    "Ghana":{"lat":7.9,"lon":-1.0,"region":"Africa"},
    "Greece":{"lat":39.1,"lon":21.8,"region":"Europe"},
    "Guatemala":{"lat":15.8,"lon":-90.2,"region":"North America"},
    "Hungary":{"lat":47.2,"lon":19.5,"region":"Europe"},
    "Iceland":{"lat":64.9,"lon":-19.0,"region":"Europe"},
    "India":{"lat":20.6,"lon":79.0,"region":"Asia"},
    "Indonesia":{"lat":-0.8,"lon":113.9,"region":"Asia"},
    "Iran":{"lat":32.4,"lon":53.7,"region":"Middle East"},
    "Iraq":{"lat":33.2,"lon":43.7,"region":"Middle East"},
    "Ireland":{"lat":53.4,"lon":-8.2,"region":"Europe"},
    "Israel":{"lat":31.0,"lon":34.9,"region":"Middle East"},
    "Italy":{"lat":41.9,"lon":12.6,"region":"Europe"},
    "Jamaica":{"lat":18.1,"lon":-77.3,"region":"North America"},
    "Japan":{"lat":36.2,"lon":138.3,"region":"Asia"},
    "Jordan":{"lat":30.6,"lon":36.2,"region":"Middle East"},
    "Kazakhstan":{"lat":48.0,"lon":68.0,"region":"Asia"},
    "Kenya":{"lat":-0.0,"lon":37.9,"region":"Africa"},
    "Kuwait":{"lat":29.3,"lon":47.5,"region":"Middle East"},
    "Kyrgyzstan":{"lat":41.2,"lon":74.8,"region":"Asia"},
    "Latvia":{"lat":56.9,"lon":24.1,"region":"Europe"},
    "Lebanon":{"lat":33.9,"lon":35.9,"region":"Middle East"},
    "Libya":{"lat":26.3,"lon":17.2,"region":"Africa"},
    "Lithuania":{"lat":55.2,"lon":23.9,"region":"Europe"},
    "Luxembourg":{"lat":49.8,"lon":6.1,"region":"Europe"},
    "Malaysia":{"lat":4.2,"lon":101.9,"region":"Asia"},
    "Mexico":{"lat":23.6,"lon":-102.6,"region":"North America"},
    "Moldova":{"lat":47.4,"lon":28.4,"region":"Europe"},
    "Mongolia":{"lat":46.9,"lon":103.8,"region":"Asia"},
    "Montenegro":{"lat":42.7,"lon":19.4,"region":"Europe"},
    "Morocco":{"lat":31.8,"lon":-7.1,"region":"Africa"},
    "Mozambique":{"lat":-18.7,"lon":35.5,"region":"Africa"},
    "Myanmar":{"lat":21.9,"lon":96.0,"region":"Asia"},
    "Nepal":{"lat":28.4,"lon":84.1,"region":"Asia"},
    "Netherlands":{"lat":52.1,"lon":5.3,"region":"Europe"},
    "New Zealand":{"lat":-40.9,"lon":174.9,"region":"Oceania"},
    "Nigeria":{"lat":9.1,"lon":8.7,"region":"Africa"},
    "North Macedonia":{"lat":41.5,"lon":21.7,"region":"Europe"},
    "Norway":{"lat":60.5,"lon":8.5,"region":"Europe"},
    "Oman":{"lat":21.5,"lon":55.9,"region":"Middle East"},
    "Pakistan":{"lat":30.4,"lon":69.3,"region":"Asia"},
    "Panama":{"lat":8.5,"lon":-80.8,"region":"North America"},
    "Paraguay":{"lat":-23.4,"lon":-58.4,"region":"South America"},
    "Peru":{"lat":-9.2,"lon":-75.0,"region":"South America"},
    "Philippines":{"lat":12.9,"lon":121.8,"region":"Asia"},
    "Poland":{"lat":51.9,"lon":19.1,"region":"Europe"},
    "Portugal":{"lat":39.4,"lon":-8.2,"region":"Europe"},
    "Qatar":{"lat":25.4,"lon":51.2,"region":"Middle East"},
    "Romania":{"lat":45.9,"lon":24.9,"region":"Europe"},
    "Russia":{"lat":61.5,"lon":105.3,"region":"Europe"},
    "Saudi Arabia":{"lat":23.9,"lon":45.1,"region":"Middle East"},
    "Senegal":{"lat":14.5,"lon":-14.5,"region":"Africa"},
    "Serbia":{"lat":44.0,"lon":21.0,"region":"Europe"},
    "Singapore":{"lat":1.4,"lon":103.8,"region":"Asia"},
    "Slovakia":{"lat":48.7,"lon":19.7,"region":"Europe"},
    "Slovenia":{"lat":46.2,"lon":14.9,"region":"Europe"},
    "South Africa":{"lat":-30.6,"lon":22.9,"region":"Africa"},
    "South Korea":{"lat":35.9,"lon":128.0,"region":"Asia"},
    "Spain":{"lat":40.5,"lon":-3.7,"region":"Europe"},
    "Sri Lanka":{"lat":7.9,"lon":80.8,"region":"Asia"},
    "Sudan":{"lat":12.9,"lon":30.2,"region":"Africa"},
    "Sweden":{"lat":60.1,"lon":18.6,"region":"Europe"},
    "Switzerland":{"lat":46.8,"lon":8.2,"region":"Europe"},
    "Syria":{"lat":34.8,"lon":39.0,"region":"Middle East"},
    "Taiwan":{"lat":23.7,"lon":121.0,"region":"Asia"},
    "Tanzania":{"lat":-6.4,"lon":34.9,"region":"Africa"},
    "Thailand":{"lat":15.9,"lon":100.9,"region":"Asia"},
    "Tunisia":{"lat":33.9,"lon":9.5,"region":"Africa"},
    "Turkey":{"lat":39.0,"lon":35.2,"region":"Asia"},
    "UAE":{"lat":23.4,"lon":53.8,"region":"Middle East"},
    "Uganda":{"lat":1.4,"lon":32.3,"region":"Africa"},
    "United Kingdom":{"lat":55.4,"lon":-3.4,"region":"Europe"},
    "United States":{"lat":37.1,"lon":-95.7,"region":"North America"},
    "Uruguay":{"lat":-32.5,"lon":-55.8,"region":"South America"},
    "Uzbekistan":{"lat":41.4,"lon":64.6,"region":"Asia"},
    "Venezuela":{"lat":6.4,"lon":-66.6,"region":"South America"},
    "Vietnam":{"lat":14.1,"lon":108.3,"region":"Asia"},
    "Yemen":{"lat":15.6,"lon":48.5,"region":"Middle East"},
    "Zambia":{"lat":-13.1,"lon":28.3,"region":"Africa"},
    "Zimbabwe":{"lat":-19.0,"lon":29.2,"region":"Africa"},
}

REGIONS = {}
for c, v in COUNTRIES.items():
    r = v["region"]
    if r not in REGIONS:
        REGIONS[r] = {"lat": v["lat"], "lon": v["lon"]}

REGIONAL_FACTORS = {
    "Europe": {"env_mult": 1.1, "soc_mult": 1.05, "eco_mult": 1.0, "renewable_bonus": 0.05},
    "North America": {"env_mult": 1.0, "soc_mult": 1.0, "eco_mult": 1.1, "renewable_bonus": 0.03},
    "South America": {"env_mult": 1.15, "soc_mult": 0.9, "eco_mult": 0.85, "renewable_bonus": 0.08},
    "Asia": {"env_mult": 0.9, "soc_mult": 0.95, "eco_mult": 1.05, "renewable_bonus": 0.02},
    "Africa": {"env_mult": 1.2, "soc_mult": 0.85, "eco_mult": 0.75, "renewable_bonus": 0.1},
    "Oceania": {"env_mult": 1.05, "soc_mult": 1.1, "eco_mult": 1.0, "renewable_bonus": 0.06},
    "Middle East": {"env_mult": 0.85, "soc_mult": 0.9, "eco_mult": 1.15, "renewable_bonus": 0.01},
}

def calculate_esg(project, region_name='Europe'):
    rf = REGIONAL_FACTORS.get(region_name, REGIONAL_FACTORS["Europe"])
    score_env = min(project.co2_reduction / 100.0 * rf["env_mult"] + rf["renewable_bonus"], 1.0)
    score_soc = min(project.social_impact / 10.0 * rf["soc_mult"], 1.0)
    score_eco = min(1.0 / (1.0 + math.exp(-0.00005 * (project.budget - 50000))) * rf["eco_mult"], 1.0)
    duration_factor = 0.9 if project.duration_months > 48 else (0.95 if project.duration_months > 36 else 1.0)
    total = round((score_env * 0.4 + score_soc * 0.3 + score_eco * 0.3) * duration_factor * 100, 2)
    total = min(total, 100.0)
    budget_per_month = project.budget / max(project.duration_months, 1)
    co2_per_dollar = project.co2_reduction / max(project.budget, 1) * 1000
    efficiency_score = (project.co2_reduction * project.social_impact) / max(project.duration_months, 1)
    features = pd.DataFrame([[project.budget, project.co2_reduction, project.social_impact, project.duration_months, budget_per_month, co2_per_dollar, efficiency_score]], columns=["budget","co2_reduction","social_impact","duration_months","budget_per_month","co2_per_dollar","efficiency_score"])
    features_scaled = scaler.transform(features)
    success_prob = round(float(rf_model.predict_proba(features_scaled)[0][1]) * 100, 2)
    env_pct = round(score_env * 100, 1)
    soc_pct = round(score_soc * 100, 1)
    eco_pct = round(score_eco * 100, 1)
    recommendations = []
    if score_env < 0.5:
        recommendations.append("Significantly increase CO2 reduction targets")
    elif score_env < 0.8:
        recommendations.append("Improve environmental impact metrics")
    if score_soc < 0.5:
        recommendations.append("Strengthen community engagement and social programs")
    elif score_soc < 0.8:
        recommendations.append("Expand social impact reach")
    if score_eco < 0.5:
        recommendations.append("Consider increasing budget for better outcomes")
    elif score_eco < 0.8:
        recommendations.append("Moderate budget - explore cost optimization")
    if project.duration_months > 36:
        recommendations.append("Long timeline may reduce investor confidence")
    if project.duration_months < 6:
        recommendations.append("Very short timeline - ensure feasibility")
    if not recommendations:
        recommendations.append("Strong project across all ESG dimensions")
    if total >= 75 and success_prob >= 70:
        risk_level = "Low"
    elif total >= 50 and success_prob >= 40:
        risk_level = "Medium"
    else:
        risk_level = "High"
    logger.info(f"ESG: {project.name} -> {total} (risk={risk_level}, prob={success_prob}%)")
    return {
        "total_score": total, "environment_score": env_pct,
        "social_score": soc_pct, "economic_score": eco_pct,
        "success_probability": success_prob, "recommendations": recommendations,
        "risk_level": risk_level,
        "esg_weights": {"environment": 0.4, "social": 0.3, "economic": 0.3}
    }

@app.get("/")
def read_root():
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))

@app.post("/evaluate")
def evaluate_project(project: Project):
    country = project.region or "Germany"
    cdata_tmp = COUNTRIES.get(country, {"region": "Europe"})
    region_for_calc = cdata_tmp.get("region", "Europe")
    result = calculate_esg(project, region_for_calc)
    cdata = COUNTRIES.get(country, {"lat": 50.0, "lon": 10.0, "region": "Europe"})
    lat = cdata["lat"] + (hash(project.name) % 10 - 5) * 0.3
    lon = cdata["lon"] + (hash(project.name) % 10 - 5) * 0.3
    conn = get_db()
    conn.execute(
        "INSERT INTO evaluations (name,budget,co2_reduction,social_impact,duration_months,total_score,environment_score,social_score,economic_score,success_probability,recommendation,risk_level,created_at,region,lat,lon) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (project.name, project.budget, project.co2_reduction, project.social_impact,
         project.duration_months, result["total_score"], result["environment_score"],
         result["social_score"], result["economic_score"], result["success_probability"],
         "; ".join(result["recommendations"]), result["risk_level"], __import__("datetime").datetime.now().isoformat(),
         region_for_calc, lat, lon))
    conn.commit()
    conn.close()
    result["region"] = region_for_calc
    result["lat"] = lat
    result["lon"] = lon
    return result

@app.get("/history")
def get_history():
    conn = get_db()
    rows = conn.execute("SELECT * FROM evaluations ORDER BY created_at DESC").fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        if "lat" not in d: d["lat"] = 50.0
        if "lon" not in d: d["lon"] = 10.0
        if "region" not in d: d["region"] = "Europe"
        result.append(d)
    return result

@app.delete("/history/{eval_id}")
def delete_evaluation(eval_id: int):
    conn = get_db()
    conn.execute("DELETE FROM evaluations WHERE id = ?", (eval_id,))
    conn.commit()
    conn.close()
    return {"status": "deleted"}

@app.delete("/history")
def clear_history():
    conn = get_db()
    conn.execute("DELETE FROM evaluations")
    conn.commit()
    conn.close()
    return {"status": "cleared"}

@app.get("/export/csv")
def export_csv():
    conn = get_db()
    rows = conn.execute("SELECT * FROM evaluations ORDER BY created_at DESC").fetchall()
    conn.close()
    import io, csv
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id","name","budget","co2_reduction","social_impact","duration_months",
                     "total_score","environment_score","social_score","economic_score",
                     "success_probability","recommendation","risk_level","created_at","region"])
    for r in rows:
        writer.writerow(list(r))
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv",
                            headers={"Content-Disposition": "attachment; filename=sora_earth_report.csv"})

@app.get("/model-info")
def model_info():
    return model_meta

@app.get("/model-metrics")
def get_model_metrics():
    return model_metrics

@app.post("/evaluate-compare")
def evaluate_compare(project: Project):
    budget_per_month = project.budget / max(project.duration_months, 1)
    co2_per_dollar = project.co2_reduction / max(project.budget, 1) * 1000
    efficiency_score = (project.co2_reduction * project.social_impact) / max(project.duration_months, 1)
    features = pd.DataFrame([[project.budget, project.co2_reduction, project.social_impact, project.duration_months, budget_per_month, co2_per_dollar, efficiency_score]], columns=["budget","co2_reduction","social_impact","duration_months","budget_per_month","co2_per_dollar","efficiency_score"])
    features_scaled = scaler.transform(features)
    rf_prob = round(float(rf_model.predict_proba(features_scaled)[0][1]) * 100, 2)
    xgb_prob = round(float(xgb_model.predict_proba(features_scaled)[0][1]) * 100, 2)
    return {
        "RandomForest": {"probability": rf_prob},
        "XGBoost": {"probability": xgb_prob},
        "agreement": abs(rf_prob - xgb_prob) < 15,
        "metrics": model_metrics
    }

@app.post("/shap")
def shap_explain(project: Project):
    import numpy as _np
    budget_per_month = project.budget / max(project.duration_months, 1)
    co2_per_dollar = project.co2_reduction / max(project.budget, 1) * 1000
    efficiency_score = (project.co2_reduction * project.social_impact) / max(project.duration_months, 1)
    features = pd.DataFrame([[project.budget, project.co2_reduction,
                              project.social_impact, project.duration_months,
                              budget_per_month, co2_per_dollar, efficiency_score]],
                             columns=["budget","co2_reduction","social_impact","duration_months","budget_per_month","co2_per_dollar","efficiency_score"])
    features_scaled = scaler.transform(features)
    shap_values = explainer_shap.shap_values(features_scaled)
    if isinstance(shap_values, list):
        sv = shap_values[1][0]
    elif shap_values.ndim == 3:
        sv = shap_values[0, :, 1]
    else:
        sv = shap_values[0]
    feature_names = ["Budget", "CO2 Reduction", "Social Impact", "Duration", "Budget/Month", "CO2/Dollar", "Efficiency"]
    return {
        "features": feature_names,
        "shap_values": [round(float(v.item() if hasattr(v, "item") else v), 4) for v in sv],
        "base_value": round(float(
            explainer_shap.expected_value[1]
            if isinstance(explainer_shap.expected_value, (list, _np.ndarray))
            else explainer_shap.expected_value
        ), 4)
    }

@app.post("/what-if")
def what_if(project: Project):
    cdata_wi = COUNTRIES.get(project.region or "Germany", {"region": "Europe"})
    wi_region = cdata_wi.get("region", "Europe")
    base = calculate_esg(project, wi_region)
    variations = {}
    deltas = {"budget": ("budget", 0.2, True), "co2_reduction": ("co2_reduction", 20, False),
              "social_impact": ("social_impact", 2, False), "duration_months": ("duration_months", -6, False)}
    for key, (field, delta, is_pct) in deltas.items():
        d = project.model_dump()
        if is_pct:
            d[field] = d[field] * (1 + delta)
        else:
            d[field] = d[field] + delta
        d[field] = max(d[field], 0)
        if field == "social_impact":
            d[field] = min(d[field], 10)
        if field == "duration_months":
            d[field] = max(int(d[field]), 1)
        mod_project = Project(**d)
        mod_result = calculate_esg(mod_project, wi_region)
        variations[key] = {
            "new_value": round(d[field], 0),
            "new_score": mod_result["total_score"],
            "score_change": round(mod_result["total_score"] - base["total_score"], 2),
            "new_probability": mod_result["success_probability"],
            "prob_change": round(mod_result["success_probability"] - base["success_probability"], 2)
        }
    return {"base": base, "variations": variations}

@app.post("/ghg-calculate")
def ghg_calculate(data: GHGInput):
    scope1 = round((data.natural_gas_m3*2.0 + data.diesel_liters*2.68 + data.petrol_liters*2.31)/1000, 2)
    scope2 = round((data.electricity_kwh*0.4)/1000, 2)
    scope3 = round((data.flights_km*0.255 + data.waste_kg*0.5)/1000, 2)
    total = round(scope1 + scope2 + scope3, 2)
    breakdown = {
        "electricity": round(data.electricity_kwh*0.4/1000, 3),
        "natural_gas": round(data.natural_gas_m3*2.0/1000, 3),
        "diesel": round(data.diesel_liters*2.68/1000, 3),
        "petrol": round(data.petrol_liters*2.31/1000, 3),
        "flights": round(data.flights_km*0.255/1000, 3),
        "waste": round(data.waste_kg*0.5/1000, 3),
    }
    if total < 5: rating,tip = "Excellent","Your carbon footprint is well below average. Keep it up!"
    elif total < 15: rating,tip = "Good","Consider switching to renewable energy and reducing air travel."
    elif total < 30: rating,tip = "Average","Significant improvements possible in energy and transport."
    else: rating,tip = "High","Urgent action needed. Focus on energy efficiency and transport alternatives."
    return {"total_tons_co2": total, "scope1": scope1, "scope2": scope2, "scope3": scope3,
            "breakdown": breakdown, "rating": rating, "tip": tip}

@app.get("/trends")
def trends():
    conn = get_db()
    rows = conn.execute("SELECT total_score, success_probability, created_at FROM evaluations ORDER BY created_at ASC").fetchall()
    conn.close()
    return [{"score": r["total_score"], "prob": r["success_probability"],
             "date": r["created_at"][:16].replace("T", " ")} for r in rows]

@app.get("/regions")
def regions():
    return list(REGIONS.keys())

@app.get("/countries")
def countries():
    return {k: v["region"] for k, v in COUNTRIES.items()}


# PyTorch Neural Network
import torch
import torch.nn as tnn

class SoraNet(tnn.Module):
    def __init__(self):
        super().__init__()
        self.net=tnn.Sequential(tnn.Linear(7,64),tnn.ReLU(),tnn.BatchNorm1d(64),tnn.Dropout(0.3),
            tnn.Linear(64,32),tnn.ReLU(),tnn.BatchNorm1d(32),tnn.Dropout(0.2),
            tnn.Linear(32,16),tnn.ReLU(),tnn.Linear(16,1),tnn.Sigmoid())
    def forward(self,x): return self.net(x)

nn_model=SoraNet()
NN_PATH=os.path.join(ROOT_DIR,"models","pytorch_mlp.pth")
if os.path.exists(NN_PATH):
    nn_model.load_state_dict(torch.load(NN_PATH,map_location="cpu"))
    nn_model.eval()

ENS_PATH=os.path.join(ROOT_DIR,"models","ensemble_model.pkl")
ensemble_model=None
if os.path.exists(ENS_PATH):
    with open(ENS_PATH,"rb") as f: ensemble_model=pickle.load(f)


# Stacking
with open('models/stacking_meta.pkl','rb') as f:
    stacking_meta = pickle.load(f)
with open('models/best_threshold.pkl','rb') as f:
    best_threshold = pickle.load(f)['threshold']


# Stacking
with open('models/stacking_meta.pkl','rb') as f:
    stacking_meta = pickle.load(f)
with open('models/best_threshold.pkl','rb') as f:
    best_threshold = pickle.load(f)['threshold']

@app.post("/predict-nn")
def predict_nn(project: Project):
    budget_per_month = project.budget / max(project.duration_months, 1)
    co2_per_dollar = project.co2_reduction / max(project.budget, 1) * 1000
    efficiency_score = (project.co2_reduction * project.social_impact) / max(project.duration_months, 1)
    features = pd.DataFrame([[project.budget, project.co2_reduction, project.social_impact, project.duration_months, budget_per_month, co2_per_dollar, efficiency_score]], columns=["budget","co2_reduction","social_impact","duration_months","budget_per_month","co2_per_dollar","efficiency_score"])
    features_scaled=scaler.transform(features)
    with torch.no_grad():
        nn_prob=round(float(nn_model(torch.FloatTensor(features_scaled))[0][0])*100,2)
    rf_prob=round(float(rf_model.predict_proba(features_scaled)[0][1])*100,2)
    xgb_prob=round(float(xgb_model.predict_proba(features_scaled)[0][1])*100,2)
    ens_prob=round(float(ensemble_model.predict_proba(features_scaled)[0][1])*100,2) if ensemble_model else 0
    return {"RandomForest":rf_prob,"XGBoost":xgb_prob,"Ensemble":ens_prob,"PyTorch_MLP":nn_prob,
        "agreement":max(rf_prob,xgb_prob,nn_prob)-min(rf_prob,xgb_prob,nn_prob)<20}


@app.post("/predict/stacking")
def predict_stacking(project: Project):
    budget_per_month = project.budget / max(project.duration_months, 1)
    co2_per_dollar = project.co2_reduction / max(project.budget, 1) * 1000
    efficiency_score = (project.co2_reduction * project.social_impact) / max(project.duration_months, 1)
    features = pd.DataFrame([[project.budget, project.co2_reduction, project.social_impact, project.duration_months, budget_per_month, co2_per_dollar, efficiency_score]],
        columns=["budget","co2_reduction","social_impact","duration_months","budget_per_month","co2_per_dollar","efficiency_score"])
    features_scaled = scaler.transform(features)
    
    rf_prob = rf_model.predict_proba(features_scaled)[:,1]
    gb_prob = xgb_model.predict_proba(features_scaled)[:,1]
    
    import torch
    nn_model.eval()
    with torch.no_grad():
        nn_prob = nn_model(torch.FloatTensor(features_scaled.values if hasattr(features_scaled, 'values') else features_scaled)).squeeze().numpy()
    
    meta_features = np.column_stack([rf_prob, gb_prob, [float(nn_prob)]])
    prob = stacking_meta.predict_proba(meta_features)[:,1][0]
    prediction = int(prob >= best_threshold)
    
    result = {
        "model": "Stacking (RF+XGB+NN)",
        "prediction": prediction,
        "probability": round(float(prob), 4),
        "threshold": round(float(best_threshold), 3),
        "individual_probs": {
            "random_forest": round(float(rf_prob[0]), 4),
            "xgboost": round(float(gb_prob[0]), 4),
            "neural_network": round(float(nn_prob), 4)
        }
    }
    log_prediction("stacking", project, result)
    return result


@app.post("/predict/stacking")
def predict_stacking(project: Project):
    budget_per_month = project.budget / max(project.duration_months, 1)
    co2_per_dollar = project.co2_reduction / max(project.budget, 1) * 1000
    efficiency_score = (project.co2_reduction * project.social_impact) / max(project.duration_months, 1)
    features = pd.DataFrame([[project.budget, project.co2_reduction, project.social_impact, project.duration_months, budget_per_month, co2_per_dollar, efficiency_score]],
        columns=["budget","co2_reduction","social_impact","duration_months","budget_per_month","co2_per_dollar","efficiency_score"])
    features_scaled = scaler.transform(features)
    
    rf_prob = rf_model.predict_proba(features_scaled)[:,1]
    gb_prob = xgb_model.predict_proba(features_scaled)[:,1]
    
    import torch
    nn_model.eval()
    with torch.no_grad():
        nn_prob = nn_model(torch.FloatTensor(features_scaled.values if hasattr(features_scaled, 'values') else features_scaled)).squeeze().numpy()
    
    meta_features = np.column_stack([rf_prob, gb_prob, [float(nn_prob)]])
    prob = stacking_meta.predict_proba(meta_features)[:,1][0]
    prediction = int(prob >= best_threshold)
    
    return {
        "model": "Stacking (RF+XGB+NN)",
        "prediction": prediction,
        "probability": round(float(prob), 4),
        "threshold": round(float(best_threshold), 3),
        "individual_probs": {
            "random_forest": round(float(rf_prob[0]), 4),
            "xgboost": round(float(gb_prob[0]), 4),
            "neural_network": round(float(nn_prob), 4)
        }
    }


@app.post("/predict/batch")
def predict_batch(projects: List[dict]):
    from fastapi import HTTPException
    results = []
    for p in projects:
        try:
            proj = Project(**p)
            budget_per_month = proj.budget / max(proj.duration_months, 1)
            co2_per_dollar = proj.co2_reduction / max(proj.budget, 1) * 1000
            efficiency_score = (proj.co2_reduction * proj.social_impact) / max(proj.duration_months, 1)
            features = pd.DataFrame([[proj.budget, proj.co2_reduction, proj.social_impact, proj.duration_months, budget_per_month, co2_per_dollar, efficiency_score]],
                columns=["budget","co2_reduction","social_impact","duration_months","budget_per_month","co2_per_dollar","efficiency_score"])
            features_scaled = scaler.transform(features)

            rf_prob = rf_model.predict_proba(features_scaled)[:,1]
            xgb_prob = xgb_model.predict_proba(features_scaled)[:,1]
            import torch as _torch
            nn_model.eval()
            with _torch.no_grad():
                nn_prob_val = nn_model(_torch.FloatTensor(features_scaled.values if hasattr(features_scaled, "values") else features_scaled)).squeeze().item()
            meta_features = np.column_stack([rf_prob, xgb_prob, [[nn_prob_val]]])
            prob = stacking_meta.predict_proba(meta_features)[:,1][0]
            prediction = int(prob >= best_threshold)
            results.append({"input": p, "prediction": prediction, "probability": round(float(prob), 4), "status": "ok"})
        except Exception as e:
            results.append({"input": p, "error": str(e), "status": "error"})
    return {"total": len(results), "success": sum(1 for r in results if r["status"]=="ok"), "results": results}


@app.get("/predictions/history")
def predictions_history(limit: int = 50):
    if not os.path.exists(PRED_LOG):
        return {"predictions": [], "total": 0}
    df = pd.read_csv(PRED_LOG)
    return {"predictions": df.tail(limit).to_dict(orient="records"), "total": len(df)}


@app.get("/predictions/history")
def predictions_history(limit: int = 50):
    if not os.path.exists(PRED_LOG):
        return {"predictions": [], "total": 0}
    df = pd.read_csv(PRED_LOG)
    return {"predictions": df.tail(limit).to_dict(orient="records"), "total": len(df)}


@app.get("/metrics")
async def metrics():
    return METRICS

@app.get("/metrics/prometheus")
async def prometheus_metrics():
    m = METRICS
    lines = [
        f'sora_requests_total {m["requests_total"]}',
        f'sora_predictions_total {m["predictions_total"]}',
        f'sora_errors_total {m["errors_total"]}',
        f'sora_avg_response_time_ms {m["avg_response_time_ms"]}',
    ]
    for ep, count in m["requests_by_endpoint"].items():
        lines.append(f'sora_requests_by_endpoint{{path="{ep}"}} {count}')
    for st, count in m["requests_by_status"].items():
        lines.append(f'sora_requests_by_status{{status="{st}"}} {count}')
    from starlette.responses import PlainTextResponse
    return PlainTextResponse("\n".join(lines))
