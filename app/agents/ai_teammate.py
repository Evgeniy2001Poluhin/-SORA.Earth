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
        """Drift check via DriftDetector.check_drift() (replaces legacy .detect())."""
        try:
            from app.drift_detection import drift_detector
            result = drift_detector.check_drift()
        except Exception as e:
            self.observations.append(Observation(
                category="drift", severity="info",
                message=f"Drift check unavailable: {type(e).__name__}: {str(e)[:100]}"
            ))
            return

        if not isinstance(result, dict):
            self.observations.append(Observation(
                category="drift", severity="info",
                message=f"Drift check returned non-dict result: {type(result).__name__}"
            ))
            return

        status = result.get("status", "ok")
        if status == "insufficient_data":
            self.observations.append(Observation(
                category="drift", severity="info",
                message=f"Drift check skipped: insufficient data "
                        f"(ref={result.get('reference_samples', 0)}, "
                        f"cur={result.get('current_samples', 0)})"
            ))
            return

        drift_flag = bool(result.get("drift_detected") or result.get("is_drift"))
        drift_score = float(result.get("drift_score") or 0.0)

        if drift_flag:
            drifted = result.get("drifted_features") or result.get("features") or []
            self.observations.append(Observation(
                category="drift", severity="warning",
                message=f"Drift detected on features: {drifted} (score={drift_score:.4f})",
                metric_name="drifted_features_count",
                metric_value=len(drifted),
            ))
        else:
            self.observations.append(Observation(
                category="drift", severity="info",
                message=f"No drift detected (score={drift_score:.4f})",
            ))

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
