import pytest

class TestRoot:
    def test_root(self, client):
        r = client.get("/")
        assert r.status_code == 200

class TestEvaluate:
    def test_valid(self, client, sample_project):
        r = client.post("/evaluate", json=sample_project)
        assert r.status_code == 200
        j = r.json()
        assert "total_score" in j
        assert 0 <= j["total_score"] <= 100
        assert "risk_level" in j
        assert "recommendations" in j
        assert isinstance(j["recommendations"], list)

    def test_high_score(self, client, high_project):
        j = client.post("/evaluate", json=high_project).json()
        assert j["total_score"] >= 90.0

    def test_low_score(self, client, low_project):
        j = client.post("/evaluate", json=low_project).json()
        assert j["total_score"] < 50

    def test_zero_budget(self, client):
        data = {"name": "Zero", "budget": 0, "co2_reduction": 50, "social_impact": 5, "duration_months": 12}
        j = client.post("/evaluate", json=data).json()
        assert j["economic_score"] < 20.0

    def test_defaults(self, client):
        r = client.post("/evaluate", json={"name": "Default"})
        assert r.status_code == 200
        assert "total_score" in r.json()

    def test_response_has_coordinates(self, client, sample_project):
        j = client.post("/evaluate", json=sample_project).json()
        assert "lat" in j and "lon" in j
        assert "region" in j

    def test_esg_weights_present(self, client, sample_project):
        j = client.post("/evaluate", json=sample_project).json()
        w = j["esg_weights"]
        assert w["environment"] == 0.4
        assert w["social"] == 0.3
        assert w["economic"] == 0.3

    def test_long_duration_penalty(self, client):
        base = {"name": "D", "budget": 50000, "co2_reduction": 70, "social_impact": 7}
        short = client.post("/evaluate", json={**base, "duration_months": 12}).json()
        long = client.post("/evaluate", json={**base, "duration_months": 50}).json()
        assert short["total_score"] > long["total_score"]

class TestRiskLevels:
    def test_low_risk(self, client, high_project):
        j = client.post("/evaluate", json=high_project).json()
        assert j["risk_level"] == "Low"

    def test_high_risk(self, client, low_project):
        j = client.post("/evaluate", json=low_project).json()
        assert j["risk_level"] == "High"

class TestRegional:
    def test_regional_difference(self, client, sample_project):
        r1 = client.post("/evaluate", json={**sample_project, "region": "Germany"}).json()
        r2 = client.post("/evaluate", json={**sample_project, "region": "Nigeria"}).json()
        assert r1["total_score"] != r2["total_score"]

    def test_countries_endpoint(self, client):
        r = client.get("/countries")
        assert r.status_code == 200
        assert "Germany" in r.json()
        assert len(r.json()) >= 100

    def test_regions_endpoint(self, client):
        r = client.get("/regions")
        assert r.status_code == 200
        regions = r.json()
        assert "Europe" in regions
        assert "Asia" in regions
        assert "Africa" in regions

class TestML:
    def test_evaluate_compare(self, client, sample_project):
        j = client.post("/evaluate-compare", json=sample_project).json()
        assert "RandomForest" in j and "XGBoost" in j
        assert "agreement" in j
        assert 0 <= j["RandomForest"]["probability"] <= 100
        assert 0 <= j["XGBoost"]["probability"] <= 100

    def test_shap(self, client, sample_project):
        j = client.post("/shap", json=sample_project).json()
        assert len(j["shap_values"]) >= 4
        assert len(j["features"]) >= 4
        assert "base_value" in j

    def test_what_if(self, client, sample_project):
        j = client.post("/what-if", json=sample_project).json()
        assert "base" in j and "variations" in j
        for key in ["budget", "co2_reduction", "social_impact", "duration_months"]:
            assert key in j["variations"]
            assert "score_change" in j["variations"][key]

    def test_model_info(self, client):
        j = client.get("/model-info").json()
        assert "best_model" in j
        assert "feature_importance" in j
        assert "dataset_size" in j

    def test_model_metrics(self, client):
        j = client.get("/model-metrics").json()
        assert "RF" in j and "XGB" in j
        for model in ["RF", "XGB"]:
            assert "accuracy" in j[model]
            assert "f1" in j[model]

class TestGHG:
    def test_calculate(self, client, ghg_data):
        j = client.post("/ghg-calculate", json=ghg_data).json()
        assert j["total_tons_co2"] > 0
        assert j["scope1"] > 0 and j["scope2"] > 0 and j["scope3"] > 0
        assert "breakdown" in j
        assert "rating" in j

    def test_zero_emissions(self, client):
        data = {"electricity_kwh": 0, "natural_gas_m3": 0, "diesel_liters": 0,
                "petrol_liters": 0, "flights_km": 0, "waste_kg": 0}
        j = client.post("/ghg-calculate", json=data).json()
        assert j["total_tons_co2"] == 0
        assert j["rating"] == "Excellent"

class TestHistory:
    def test_history_list(self, client):
        assert client.get("/history").status_code == 200

    def test_export_csv(self, client):
        r = client.get("/export/csv")
        assert r.status_code == 200
        assert "text/csv" in r.headers.get("content-type", "")

    def test_trends(self, client):
        assert client.get("/trends").status_code == 200

    def test_delete_nonexistent(self, client):
        assert client.delete("/history/99999").status_code == 200

    def test_clear_history(self, client):
        r = client.delete("/history")
        assert r.status_code == 200
        assert r.json()["status"] == "cleared"


class TestNeuralNetwork:
    def test_predict_nn(self, client, sample_project):
        r = client.post("/predict-nn", json=sample_project)
        assert r.status_code == 200
        j = r.json()
        assert "PyTorch_MLP" in j
        assert "Ensemble" in j
        assert 0 <= j["PyTorch_MLP"] <= 100

    def test_nn_different_inputs(self, client, high_project, low_project):
        h = client.post("/predict-nn", json=high_project).json()
        l = client.post("/predict-nn", json=low_project).json()
        assert h["PyTorch_MLP"] != l["PyTorch_MLP"]

class TestEdgeCases:
    def test_max_values(self, client):
        data = {"name":"Max","budget":500000,"co2_reduction":200,"social_impact":10,"duration_months":120}
        assert client.post("/evaluate", json=data).status_code == 200

    def test_min_values(self, client):
        data = {"name":"Min","budget":0,"co2_reduction":0,"social_impact":1,"duration_months":1}
        assert client.post("/evaluate", json=data).status_code == 200

    def test_all_countries(self, client):
        countries = client.get("/countries").json()
        for c in ["Russia","China","Brazil","India","Germany"]:
            assert c in countries

    def test_ghg_high_emissions(self, client):
        data = {"electricity_kwh":100000,"natural_gas_m3":5000,"diesel_liters":2000,
                "petrol_liters":3000,"flights_km":50000,"waste_kg":10000}
        j = client.post("/ghg-calculate", json=data).json()
        assert j["rating"] == "High"
        assert j["total_tons_co2"] > 30

    def test_evaluate_saves_history(self, client):
        client.delete("/history")
        client.post("/evaluate", json={"name":"HTest","budget":50000,"co2_reduction":50,"social_impact":5,"duration_months":12})
        h = client.get("/history").json()
        assert len(h) >= 1

    def test_multiple_evals(self, client):
        client.delete("/history")
        for i in range(5):
            client.post("/evaluate", json={"name":f"T{i}","budget":10000*(i+1),"co2_reduction":20+i*10,"social_impact":3+i,"duration_months":12})
        assert len(client.get("/history").json()) == 5

    def test_trends_data(self, client):
        t = client.get("/trends").json()
        assert len(t) >= 5

class TestConsistency:
    def test_same_input_same_output(self, client, sample_project):
        r1 = client.post("/evaluate", json=sample_project).json()
        r2 = client.post("/evaluate", json=sample_project).json()
        assert r1["total_score"] == r2["total_score"]

    def test_shap_four_features(self, client, sample_project):
        j = client.post("/shap", json=sample_project).json()
        assert len(j["shap_values"]) >= 4

    def test_whatif_budget(self, client, sample_project):
        j = client.post("/what-if", json=sample_project).json()
        assert j["variations"]["budget"]["new_value"] > sample_project["budget"]


class TestStacking:
    def test_predict_stacking(self, client):
        r = client.post("/predict/stacking", json={"budget": 500000, "co2_reduction": 1200, "social_impact": 8, "duration_months": 18})
        assert r.status_code == 200
        j = r.json()
        assert j["model"] == "Stacking (RF+XGB+NN)"
        assert "prediction" in j
        assert "probability" in j
        assert "threshold" in j
        assert "individual_probs" in j
        assert all(k in j["individual_probs"] for k in ["random_forest", "xgboost", "neural_network"])

    def test_stacking_probability_range(self, client):
        r = client.post("/predict/stacking", json={"budget": 100000, "co2_reduction": 300, "social_impact": 3, "duration_months": 6})
        j = r.json()
        assert 0 <= j["probability"] <= 1
        assert j["prediction"] in [0, 1]
        for v in j["individual_probs"].values():
            assert 0 <= v <= 1

    def test_stacking_threshold_applied(self, client):
        r = client.post("/predict/stacking", json={"budget": 200000, "co2_reduction": 500, "social_impact": 5, "duration_months": 12})
        j = r.json()
        expected = 1 if j["probability"] >= j["threshold"] else 0
        assert j["prediction"] == expected


class TestBatch:
    def test_batch_predict(self, client):
        r = client.post("/predict/batch", json=[
            {"budget": 500000, "co2_reduction": 1200, "social_impact": 8, "duration_months": 18},
            {"budget": 100000, "co2_reduction": 300, "social_impact": 3, "duration_months": 6}
        ])
        assert r.status_code == 200
        j = r.json()
        assert j["total"] == 2
        assert j["success"] == 2
        assert len(j["results"]) == 2

    def test_batch_with_invalid(self, client):
        r = client.post("/predict/batch", json=[
            {"budget": 500000, "co2_reduction": 1200, "social_impact": 8, "duration_months": 18},
            {"budget": -100, "co2_reduction": 300, "social_impact": 3, "duration_months": 6}
        ])
        j = r.json()
        assert j["total"] == 2
        assert any(r["status"] == "error" for r in j["results"])


class TestPredictionHistory:
    def test_history_endpoint(self, client):
        r = client.get("/predictions/history")
        assert r.status_code == 200
        assert "predictions" in r.json()
        assert "total" in r.json()
