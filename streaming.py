from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from app.auth import get_current_user
import json, asyncio, pickle, numpy as np, os

router = APIRouter(prefix="/predict", tags=["streaming"])

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_model():
    with open(os.path.join(ROOT, "models", "ensemble_model_v2.pkl"), "rb") as f:
        return pickle.load(f)


def _load_scaler():
    with open(os.path.join(ROOT, "models", "scaler.pkl"), "rb") as f:
        return pickle.load(f)


async def _stream_predictions(projects: list, model, scaler):
    start_msg = json.dumps({"event": "start", "total": len(projects)})
    yield "data: " + start_msg + "\n\n"

    for i, p in enumerate(projects):
        await asyncio.sleep(0.01)
        try:
            bpm = p["budget"] / max(p.get("duration_months", 1), 1)
            cpd = p["co2_reduction"] / max(p["budget"], 1) * 1000
            eff = p["co2_reduction"] * p.get("social_impact", 5) / max(p.get("duration_months", 1), 1)

            features = [p["budget"], p["co2_reduction"], p.get("social_impact", 5), p.get("duration_months", 12), bpm, cpd, eff]
            n_feat = scaler.n_features_in_
            X = np.array(features[:n_feat]).reshape(1, -1)
            X_scaled = scaler.transform(X)

            try:
                prob = float(model.predict_proba(X_scaled)[0][1])
                prediction = int(prob >= 0.45)
            except Exception:
                prob = 0.5
                prediction = 0

            if prob >= 0.7:
                risk = "Low"
            elif prob < 0.4:
                risk = "High"
            else:
                risk = "Medium"

            result = {
                "event": "prediction",
                "index": i,
                "project_name": p.get("project_name", "Project " + str(i + 1)),
                "prediction": prediction,
                "probability": round(prob, 4),
                "risk_level": risk,
            }
        except Exception as e:
            result = {"event": "error", "index": i, "error": str(e)}

        yield "data: " + json.dumps(result) + "\n\n"

    summary = json.dumps({"event": "complete", "total": len(projects), "processed": len(projects)})
    yield "data: " + summary + "\n\n"


@router.post("/stream", summary="Stream batch predictions via SSE")
async def predict_stream(projects: list[dict], user=Depends(get_current_user)):
    """Stream predictions for multiple projects using Server-Sent Events."""
    model = _load_model()
    scaler = _load_scaler()
    return StreamingResponse(
        _stream_predictions(projects, model, scaler),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
