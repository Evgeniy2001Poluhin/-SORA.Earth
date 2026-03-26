<div align="center">

# 🌍 SORA.Earth AI Platform

**AI-Powered ESG Assessment & Environmental Project Evaluation**

[![Python](https://img.shields.io/badge/Python-3.9+-blue?logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green?logo=fastapi)](https://fastapi.tiangolo.com)
[![XGBoost](https://img.shields.io/badge/XGBoost-ML-orange?logo=xgboost)](https://xgboost.ai)
[![Tests](https://img.shields.io/badge/Tests-26%20passed-brightgreen?logo=pytest)](tests/)
[![Docker](https://img.shields.io/badge/Docker-Ready-blue?logo=docker)](Dockerfile)

</div>

---

## Overview

SORA.Earth is a full-stack AI platform for evaluating environmental sustainability projects using Machine Learning and ESG scoring. The system predicts project success probability, calculates ESG scores with regional adjustments, and provides SHAP-based model interpretability.

### Key Features

- ML Prediction: XGBoost/RandomForest ensemble (86% CV accuracy, 96% AUC)
-SG Scoring: Multi-dimensional assessment with regional factors for 110+ countries
- Interactive Map: Leaflet.js with project geolocation
- SHAP Explainability: Feature importance, waterfall plots, dependence analysis
- GHG Calculator: Scope 1/2/3 emissions by GRI Standards
- What-If Analysis: Sensitivity testing for key parameters
- Compare: Side-by-side evaluation of up to 3 projects
- Trends: Time-series tracking of ESG scores
- REST API: Full API with Swagger documentation at /docs
- Docker: One-command deployment

---

## Architecture

    sora_earth_ai_platform/
    +-- app/
    |   +-- main.py              # FastAPI backend (18 endpoints)
    |   +-- schemas.py           # Pydantic models
    |   +-- static/index.html    # SPA frontend (6 pages)
    +-- data/
    |   +-- projects.csv         # Dataset (100 projects, 8 features)
    |   +-- history.db           # SQLite evaluation history
    +-- models/
    |   +-- model.pkl            # RandomForest
    |   +-- xgb_model.pkl        # XGBoost (best)
    |   +-- scaler.pkl           # StandardScaler
    |   +-- meta.json            # Training metadata
    |   +-- metrics.json         # Model metrics
    +-- notebooks/
    |   +-- eda_executed.ipynb    # Exploratory Data Analysis
    |   +-- model_executed.ipynb  # Model Training & Evaluation
    |   +-- shap_executed.ipynb   # SHAP Interpretability
    +-- plots/                    # 23 visualization files
    +-- tests/
    |   +-- conftest.py           # Fixtures
    |   +-- test_api.py           # 26 API tests
    +-- train_model.py            # Training pipeline
    +-- Dockerfile
    +-- docker-compose.yml
    +-- requirements.txt
    +-- README.md

---

## Quick Start

### Local

    git clone https://github.com/username/sora_earth_ai_platform.git
    cd sora_earth_ai_platform
    python3 -m venv venv && source venv/bin/activate
    pip install -r requirements.txt
    python train_model.py
    uvicorn app.main:app --host 0.0.0.0 --port 8000

Open http://localhost:8000

### Docker

    docker-compose up --build

---

## Model Performance

| Model | CV Accuracy | Test AUC | Test F1 |
|-------|-----------|----------|---------|
| **XGBoost** | **86.0%** | **96.0%** | **93.3%** |
| GradientBoosting | 83.0% | - | - |
| RandomForest | 79.0% | 90.7% | 93.8% |
| LogisticRegression | 78.0% | - | - |

### Feature Importance (SHAP)

| Feature | Importance |
|---------|-----------|
| CO2 Reduction | 41.0% |
| Duration | 25.3% |
| Social Impact | 21.0% |
| Budget | 12.8% |

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | / | Web interface |
| POST | /evaluate | ESG evaluation |
| POST | /evaluate-compare | RF vs XGBoost |
| POST | /shap | SHAP explanation |
| POST | /what-if | Sensitivity analysis |
| POST | /ghg-calculate | GHG emissions |
| GET | /history | Evaluation history |
| GET | /export/csv | Export CSV report |
| GET | /model-info | Model metadata |
| GET | /model-metrics | Performance metrics |
| GET | /trends | Score trends |
| GET | /countries | Country list (110+) |
| GET | /docs | Swagger UI |

---

## Testing

    python -m pytest -v
    # 26 passed in 0.20s

Test coverage: Root, Evaluate, Risk, Regional, ML (SHAP, What-If, Compare), GHG, History, Export.

---

## Tech Stack

- **Backend**: FastAPI, Uvicorn, SQLite
- **ML**: scikit-learn, XGBoost, SHAP
- **Frontend**: Vanilla JS, Chart.js, Leaflet.js
- **Data**: Pandas, NumPy, Seaborn, Matplotlib
- **Testing**: Pytest
- **Deploy**: Docker, GitHub Actions

---

## Author

**Polukhin Evgeniy** — NRNU MEPhI, 09.04.04

---

## License

MIT License (c) 2026 SORA.Earth
