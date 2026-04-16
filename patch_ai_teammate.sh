#!/bin/bash
set -e

echo "=== Step 1: Create app/agents/ai_teammate.py ==="
mkdir -p app/agents
cat > app/agents/__init__.py << 'EOF'
EOF

cat > app/agents/ai_teammate.py << 'PYEOF'
"""
SORA AI Teammate — autonomous agent that monitors platform health
and takes corrective actions through the Admin AI Control Layer.

Operates in two modes:
  - observe: read-only, generates recommendations
  - auto:    read + execute (refresh, retrain, full-pipeline)
"""
import logging
import json
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import List, Optional

from app.database import SessionLocal, RetrainLog, DataRefreshLog

logger = logging.getLogger("sora.ai_teammate")


# --------------- Decision Model ---------------

@dataclass
class Observation:
    category: str          # health | drift | freshness | model_quality | pipeline
    severity: str          # info | warning | critical
    message: str
    metric_name: Optional[str] = None
    metric_value: Optional[float] = None


@dataclass
class Decision:
    action: str            # no_action | recommend_refresh | recommend_retrain | execute_refresh | execute_retrain | execute_full_pipeline
    reason: str
    observations: List[dict] = field(default_factory=list)
    executed: bool = False
    result: Optional[dict] = None


@dataclass
class TeammateReport:
    timestamp: str
    mode: str
    observations: List[dict]
    decisions: List[dict]
    summary: str


# --------------- Thresholds ---------------

THRESHOLDS = {
    "max_hours_since_refresh": 48,
    "max_hours_since_retrain": 168,     # 7 days
    "min_auc": 0.85,
    "max_consecutive_failures": 3,
}


# --------------- Core Logic ---------------

class AITeammate:
    def __init__(self, mode: str = "observe"):
        assert mode in ("observe", "auto"), f"Invalid mode: {mode}"
        self.mode = mode
        self.observations: List[Observation] = []
        self.decisions: List[Decision] = []

    # ---- Observation Phase ----

    def observe(self) -> List[Observation]:
        """Gather observations from DB and platform state."""
        self.observations = []
        db = SessionLocal()
        try:
            self._check_refresh_freshness(db)
            self._check_retrain_freshness(db)
            self._check_model_quality(db)
            self._check_recent_failures(db)
            self._check_drift(db)
        finally:
            db.close()
        return self.observations

    def _check_refresh_freshness(self, db):
        last = db.query(DataRefreshLog).filter(
            DataRefreshLog.status == "success"
        ).order_by(DataRefreshLog.timestamp.desc()).first()

        if not last or not last.timestamp:
            self.observations.append(Observation(
                category="freshness", severity="warning",
                message="No successful data refresh found"
            ))
            return

        hours_ago = (datetime.utcnow() - last.timestamp).total_seconds() / 3600
        if hours_ago > THRESHOLDS["max_hours_since_refresh"]:
            self.observations.append(Observation(
                category="freshness", severity="warning",
                message=f"Last successful refresh was {hours_ago:.0f}h ago (threshold: {THRESHOLDS['max_hours_since_refresh']}h)",
                metric_name="hours_since_refresh", metric_value=round(hours_ago, 1)
            ))
        else:
            self.observations.append(Observation(
                category="freshness", severity="info",
                message=f"Data refresh is fresh ({hours_ago:.0f}h ago)",
                metric_name="hours_since_refresh", metric_value=round(hours_ago, 1)
            ))

    def _check_retrain_freshness(self, db):
        last = db.query(RetrainLog).filter(
            RetrainLog.status == "success"
        ).order_by(RetrainLog.started_at.desc()).first()

        if not last or not last.started_at:
            self.observations.append(Observation(
                category="freshness", severity="warning",
                message="No successful retrain found"
            ))
            return

        hours_ago = (datetime.utcnow() - last.started_at).total_seconds() / 3600
        if hours_ago > THRESHOLDS["max_hours_since_retrain"]:
            self.observations.append(Observation(
                category="freshness", severity="warning",
                message=f"Last successful retrain was {hours_ago:.0f}h ago (threshold: {THRESHOLDS['max_hours_since_retrain']}h)",
                metric_name="hours_since_retrain", metric_value=round(hours_ago, 1)
            ))
        else:
            self.observations.append(Observation(
                category="freshness", severity="info",
                message=f"Model retrain is fresh ({hours_ago:.0f}h ago)",
                metric_name="hours_since_retrain", metric_value=round(hours_ago, 1)
            ))

    def _check_model_quality(self, db):
        last = db.query(RetrainLog).filter(
            RetrainLog.status == "success",
            RetrainLog.metrics_json.isnot(None)
        ).order_by(RetrainLog.started_at.desc()).first()

        if not last:
            self.observations.append(Observation(
                category="model_quality", severity="info",
                message="No retrain metrics available yet"
            ))
            return

        try:
            metrics = json.loads(last.metrics_json)
            auc = metrics.get("auc") or metrics.get("roc_auc")
            if auc is not None:
                if auc < THRESHOLDS["min_auc"]:
                    self.observations.append(Observation(
                        category="model_quality", severity="critical",
                        message=f"Model AUC {auc:.4f} is below threshold {THRESHOLDS['min_auc']}",
                        metric_name="auc", metric_value=auc
                    ))
                else:
                    self.observations.append(Observation(
                        category="model_quality", severity="info",
                        message=f"Model AUC {auc:.4f} is healthy",
                        metric_name="auc", metric_value=auc
                    ))
        except Exception:
            pass

    def _check_recent_failures(self, db):
        recent = (
            db.query(RetrainLog)
            .order_by(RetrainLog.started_at.desc())
            .limit(THRESHOLDS["max_consecutive_failures"])
            .all()
        )
        if len(recent) >= THRESHOLDS["max_consecutive_failures"]:
            if all(r.status == "failed" for r in recent):
                self.observations.append(Observation(
                    category="health", severity="critical",
                    message=f"Last {THRESHOLDS['max_consecutive_failures']} retrains all failed",
                    metric_name="consecutive_failures",
                    metric_value=THRESHOLDS["max_consecutive_failures"]
                ))

    def _check_drift(self, db):
        try:
            from app.drift_detection import drift_detector
            result = drift_detector.detect()
            if result.get("drift_detected"):
                drifted = result.get("drifted_features", [])
                self.observations.append(Observation(
                    category="drift", severity="warning",
                    message=f"Drift detected on features: {drifted}",
                    metric_name="drifted_features_count",
                    metric_value=len(drifted)
                ))
            else:
                self.observations.append(Observation(
                    category="drift", severity="info",
                    message="No drift detected"
                ))
        except Exception as e:
            self.observations.append(Observation(
                category="drift", severity="info",
                message=f"Drift check unavailable: {str(e)[:100]}"
            ))

    # ---- Decision Phase ----

    def decide(self) -> List[Decision]:
        """Based on observations, produce decisions."""
        self.decisions = []

        warnings = [o for o in self.observations if o.severity in ("warning", "critical")]
        if not warnings:
            self.decisions.append(Decision(
                action="no_action",
                reason="All observations healthy, no action needed",
                observations=[asdict(o) for o in self.observations]
            ))
            return self.decisions

        # Stale data → refresh
        stale_refresh = [o for o in warnings if o.category == "freshness" and "refresh" in o.message.lower()]
        if stale_refresh:
            action = "execute_refresh" if self.mode == "auto" else "recommend_refresh"
            self.decisions.append(Decision(
                action=action,
                reason=stale_refresh[0].message,
                observations=[asdict(o) for o in stale_refresh]
            ))

        # Drift or low AUC or stale model → retrain
        needs_retrain = [o for o in warnings if o.category in ("drift", "model_quality") or
                         (o.category == "freshness" and "retrain" in o.message.lower())]
        if needs_retrain:
            action = "execute_retrain" if self.mode == "auto" else "recommend_retrain"
            self.decisions.append(Decision(
                action=action,
                reason="; ".join(o.message for o in needs_retrain),
                observations=[asdict(o) for o in needs_retrain]
            ))

        # Consecutive failures → escalate (never auto-execute)
        failures = [o for o in warnings if o.category == "health" and "failed" in o.message.lower()]
        if failures:
            self.decisions.append(Decision(
                action="escalate",
                reason="Multiple consecutive failures detected — manual review needed",
                observations=[asdict(o) for o in failures]
            ))

        return self.decisions

    # ---- Execution Phase (auto mode only) ----

    def execute(self) -> List[Decision]:
        """Execute decisions that require action (auto mode only)."""
        for d in self.decisions:
            if d.action == "execute_refresh":
                d.result = self._do_refresh()
                d.executed = True
            elif d.action == "execute_retrain":
                d.result = self._do_retrain()
                d.executed = True
        return self.decisions

    def _do_refresh(self) -> dict:
        try:
            from app.external_data import refresh_live_data
            result = refresh_live_data(trigger_source="ai_agent")
            logger.info("AI Teammate executed data refresh: %s", result)
            return {"status": "success", "fetched": result.get("fetched", 0)}
        except Exception as e:
            logger.error("AI Teammate refresh failed: %s", e)
            return {"status": "error", "error": str(e)[:300]}

    def _do_retrain(self) -> dict:
        try:
            from app.scheduler import closed_loop_retrain
            result = closed_loop_retrain(trigger_source="ai_agent")
            logger.info("AI Teammate executed closed-loop retrain: %s", result)
            return result
        except Exception as e:
            logger.error("AI Teammate retrain failed: %s", e)
            return {"status": "error", "error": str(e)[:300]}

    # ---- Full Run ----

    def run(self) -> TeammateReport:
        """Full cycle: observe → decide → (optionally execute) → report."""
        self.observe()
        self.decide()

        if self.mode == "auto":
            self.execute()

        critical = [o for o in self.observations if o.severity == "critical"]
        warnings = [o for o in self.observations if o.severity == "warning"]
        actions = [d for d in self.decisions if d.action != "no_action"]

        if critical:
            summary = f"CRITICAL: {len(critical)} critical issue(s) found. {len(actions)} action(s) taken."
        elif warnings:
            summary = f"WARNING: {len(warnings)} warning(s) found. {len(actions)} action(s) {'executed' if self.mode == 'auto' else 'recommended'}."
        else:
            summary = "OK: All systems healthy. No action needed."

        report = TeammateReport(
            timestamp=datetime.utcnow().isoformat(),
            mode=self.mode,
            observations=[asdict(o) for o in self.observations],
            decisions=[asdict(d) for d in self.decisions],
            summary=summary,
        )

        logger.info("AI Teammate report: %s", summary)
        return report
PYEOF
echo "app/agents/ai_teammate.py created"

echo "=== Step 2: Create API route app/api/ai_teammate_routes.py ==="
cat > app/api/ai_teammate_routes.py << 'PYEOF'
"""API routes for AI Teammate."""
from fastapi import APIRouter, Depends, Query
from app.auth import require_admin
from app.agents.ai_teammate import AITeammate
from dataclasses import asdict

router = APIRouter(prefix="/admin/ai-teammate", tags=["ai-teammate"])


@router.post("/run")
def run_teammate(
    mode: str = Query("observe", regex="^(observe|auto)$"),
    _admin=Depends(require_admin),
):
    """Run AI Teammate: observe (read-only) or auto (read + execute)."""
    teammate = AITeammate(mode=mode)
    report = teammate.run()
    return asdict(report)


@router.get("/status")
def teammate_status(_admin=Depends(require_admin)):
    """Quick observe-only status check."""
    teammate = AITeammate(mode="observe")
    report = teammate.run()
    return asdict(report)
PYEOF
echo "app/api/ai_teammate_routes.py created"

echo "=== Step 3: Register route in app/main.py ==="
python3 << 'PYEOF3'
content = open('app/main.py').read()
if 'ai_teammate_routes' in content:
    print('ai_teammate_routes already registered, skipping')
else:
    # Find the last router include line and add after it
    import_line = 'from app.api import ai_teammate_routes'
    include_line = 'app.include_router(ai_teammate_routes.router, prefix="/api/v1")'

    # Add import near other api imports
    marker = 'from app.api import admin_ai_control'
    if marker in content:
        content = content.replace(marker, marker + '\n' + import_line)
    else:
        # fallback: add before first "app = FastAPI"
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if 'from app.api' in line:
                lines.insert(i + 1, import_line)
                break
        content = '\n'.join(lines)

    # Add include_router near other includes
    include_marker = 'app.include_router(admin_ai_control.router'
    if include_marker in content:
        # Find the full line
        for line in content.split('\n'):
            if include_marker in line:
                content = content.replace(line, line + '\n' + include_line)
                break
    else:
        # fallback: add after last include_router
        lines = content.split('\n')
        last_include = 0
        for i, line in enumerate(lines):
            if 'include_router' in line:
                last_include = i
        lines.insert(last_include + 1, include_line)
        content = '\n'.join(lines)

    open('app/main.py', 'w').write(content)
    print('ai_teammate_routes registered in main.py')
PYEOF3

echo "=== Step 4: Create tests/test_ai_teammate.py ==="
cat > tests/test_ai_teammate.py << 'PYEOF'
"""Tests for AI Teammate agent."""
import pytest
from app.agents.ai_teammate import AITeammate, Observation, THRESHOLDS
from dataclasses import asdict


class TestAITeammateObserve:
    def test_observe_returns_observations(self):
        t = AITeammate(mode="observe")
        obs = t.observe()
        assert isinstance(obs, list)
        assert len(obs) > 0
        for o in obs:
            assert isinstance(o, Observation)
            assert o.category in ("health", "drift", "freshness", "model_quality", "pipeline")
            assert o.severity in ("info", "warning", "critical")

    def test_decide_after_observe(self):
        t = AITeammate(mode="observe")
        t.observe()
        decisions = t.decide()
        assert isinstance(decisions, list)
        assert len(decisions) > 0
        for d in decisions:
            assert d.action in (
                "no_action", "recommend_refresh", "recommend_retrain",
                "execute_refresh", "execute_retrain", "execute_full_pipeline",
                "escalate"
            )

    def test_observe_mode_never_executes(self):
        t = AITeammate(mode="observe")
        report = t.run()
        for d in t.decisions:
            assert d.executed is False
            assert "execute_" not in d.action or d.action.startswith("recommend") or d.action in ("no_action", "escalate", "recommend_refresh", "recommend_retrain")

    def test_run_returns_report(self):
        t = AITeammate(mode="observe")
        report = t.run()
        assert report.timestamp
        assert report.mode == "observe"
        assert isinstance(report.observations, list)
        assert isinstance(report.decisions, list)
        assert isinstance(report.summary, str)


class TestAITeammateAPI:
    @pytest.fixture(autouse=True)
    def setup(self):
        from fastapi.testclient import TestClient
        from app.main import app
        self.client = TestClient(app)

    def _admin_token(self):
        r = self.client.post('/api/v1/auth/login-json',
                             json={"username": "admin", "password": "sora2026"})
        return r.json()["access_token"]

    def test_teammate_status(self):
        token = self._admin_token()
        r = self.client.get('/api/v1/admin/ai-teammate/status',
                            headers={'Authorization': f'Bearer {token}'})
        assert r.status_code == 200
        data = r.json()
        assert 'observations' in data
        assert 'decisions' in data
        assert 'summary' in data
        assert data['mode'] == 'observe'

    def test_teammate_run_observe(self):
        token = self._admin_token()
        r = self.client.post('/api/v1/admin/ai-teammate/run?mode=observe',
                             headers={'Authorization': f'Bearer {token}'})
        assert r.status_code == 200
        data = r.json()
        assert data['mode'] == 'observe'
        for d in data['decisions']:
            assert d['executed'] is False

    def test_teammate_run_auto(self):
        token = self._admin_token()
        r = self.client.post('/api/v1/admin/ai-teammate/run?mode=auto',
                             headers={'Authorization': f'Bearer {token}'})
        assert r.status_code == 200
        data = r.json()
        assert data['mode'] == 'auto'

    def test_teammate_requires_auth(self):
        r = self.client.get('/api/v1/admin/ai-teammate/status')
        assert r.status_code in (401, 403)

    def test_teammate_invalid_mode(self):
        token = self._admin_token()
        r = self.client.post('/api/v1/admin/ai-teammate/run?mode=yolo',
                             headers={'Authorization': f'Bearer {token}'})
        assert r.status_code == 422
PYEOF
echo "tests/test_ai_teammate.py created"

echo ""
echo "=== ALL DONE ==="
echo "Now run:"
echo "  pytest tests/test_ai_teammate.py -v"
echo "  pytest tests/ -x -q"
