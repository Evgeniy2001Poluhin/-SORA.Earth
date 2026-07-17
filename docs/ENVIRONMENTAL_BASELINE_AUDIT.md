# ENVIRONMENTAL BASELINE AUDIT

**Date:** 2026-07-17  
**Platform:** SORA.Earth AI Platform  
**Scope:** Real-time environmental data ingestion, forecasting, and MLOps validation

---

## 1. What Works in Production (file:line)

### ✅ Real-Time Environmental Data Ingestion
- **OpenAQ Integration**: `app/ingesters/openaq.py:40-81`
  - Fetches air quality data (PM2.5, NO2, O3, SO2) from 23 global capitals
  - 6-hour TTL refresh cycle (line 42)
  - Stores metrics in `region_signals` table → `app/database.py:180-191`
  - **Status**: ✅ Production-ready with API key requirement

### ✅ Time Series Forecasting Infrastructure
- **Prophet Model**: `app/services/forecasting/prophet.py:1-206`
  - Seasonal decomposition (monthly/quarterly ESG reporting cycles) (lines 85-100)
  - Bayesian uncertainty intervals with 1000 MC samples (line 73)
  - 80/20 train/test split validation (line 179)
  - Walk-forward validation for ESG metrics (lines 166-205)
  - **Status**: ✅ Functional with MAE/RMSE/MAPE metrics

- **Rolling Window Features**: `app/services/forecasting/features.py:48-65`
  - Rolling mean/std for lag detection
  - `rolling(w, min_periods=1).mean()` pattern confirmed (line 64)
  - **Status**: ✅ Used in LSTM preprocessing

### ✅ MLOps Quality Gates
- **AUC Validation**: `app/scheduler.py:370-412`
  - **Hard threshold**: AUC ≥ 0.80 (line 382) — **INCREASED from 0.70**
  - Drift detection → auto-retrain → validate → promote/reject loop
  - Model rejection on AUC degradation > 2% (line 390)
  - Prometheus metrics: `sora_model_promoted_total`, `sora_model_rejected_total` (lines 386, 393)
  - **Status**: ✅ Production closed-loop active

- **Scheduled Jobs**: `app/scheduler.py:690-739`
  - Daily closed-loop at 03:00 UTC (line 691)
  - External data refresh every 6h (line 700)
  - Weekly full pipeline Sun 03:30 UTC (line 708)
  - Ingester runs every 24h (line 718)
  - Forecast model pretraining every 6h (line 726)
  - **Status**: ✅ Running in `scheduler` container

### ✅ Database Schema for Environmental Tracking
- **RegionSignal**: `app/database.py:180-191`
  - Stores real-time environmental metrics (PM2.5, CO2, etc.)
  - Indexed by `region_code`, `source`, `observed_at`
  - **Status**: ✅ Ready for time series queries

- **RegionESGScore**: `app/database.py:193-207`
  - Aggregated ESG scores per region (env/social/gov)
  - Confidence scores + sources count
  - Auto-updated `updated_at` timestamp
  - **Status**: ✅ Production table

- **ForecastHistory**: `app/database.py:134-146`
  - Stores forecast results with JSON payloads
  - Indexed by `metric`, `model`, `country`, `created_at`
  - **Status**: ✅ Active for Prophet/LSTM forecasts

- **ForecastModelMetrics**: `app/database.py:148-167`
  - Tracks MAE/RMSE/MAPE over time
  - Train/test sample counts + training duration
  - **Status**: ✅ Used for model performance monitoring

---

## 2. What Exists But Unverified

### ⚠️ MLflow LSTM Model (Stub Implementation)
- **File**: `app/services/forecasting/production_forecaster.py:102-138`
- **Issue**: MLflow inference returns dummy forecast (line 129: `[50.0] * horizon`)
- **Comment**: "MLflow model inference not fully implemented" (line 118)
- **Missing**:
  - Feature engineering pipeline for LSTM
  - Sequence preparation (last 30 days)
  - MC Dropout sampling for uncertainty
- **Impact**: Falls back to Ensemble (Prophet + Linear) instead of pre-trained LSTM
- **Risk**: Medium — fallback works but negates MLflow training investment

### ⚠️ Train/Test Split vs Walk-Forward Validation
- **Current**: `app/api/retrain.py:146`
  ```python
  train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
  ```
- **Issue**: Random split on **time series data** violates temporal order
- **Best Practice**: Prophet uses 80/20 temporal split (`prophet.py:179`) but retraining uses shuffled split
- **Impact**: Overestimated AUC during validation (data leakage from future → past)
- **Files Affected**:
  - `app/api/retrain.py:146` — RandomForest retraining
  - `app/api/ab_comparison.py:8` — A/B test model comparison
- **Risk**: High — inflates validation metrics, masks overfitting

### ⚠️ No TimeSeriesSplit Found
- **Search Result**: `grep -r "TimeSeriesSplit" app/` → **0 matches**
- **Expected**: For temporal data (ESG scores, predictions), should use `sklearn.model_selection.TimeSeriesSplit`
- **Impact**: No walk-forward validation for non-forecast models
- **Risk**: Medium — Prophet does it correctly, but RandomForest/XGBoost don't

### ⚠️ Environmental Crisis Tables Missing
- **Search**: `grep -i "environmental\|crisis" app/database.py` in table names
- **Result**: **No dedicated tables** for:
  - Climate crisis events (floods, droughts, wildfires)
  - Environmental alerts/thresholds
  - Crisis impact scores
- **Found Instead**:
  - `region_signals` — generic metric storage (could be extended)
  - `region_esg_scores` — aggregated scores (no crisis flag)
- **Impact**: Cannot track environmental emergencies vs baseline conditions
- **Risk**: Low (out of scope for current features) but **critical for roadmap items** (crisis detection, real-time alerts)

---

## 3. Critical Gaps

### 🔴 Gap 1: Time Series Data Leakage in Retraining
- **Location**: `app/api/retrain.py:146`
- **Current**: `train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)`
- **Problem**: Shuffles rows → future data leaks into training set for time-based predictions
- **Solution**: Replace with temporal split:
  ```python
  split_idx = int(len(df) * 0.8)
  X_train, y_train = X.iloc[:split_idx], y.iloc[:split_idx]
  X_test, y_test = X.iloc[split_idx:], y.iloc[split_idx:]
  ```
- **Evidence**: Prophet already does this correctly (`prophet.py:179`)
- **Impact**: **High** — Current AUC threshold (0.80) may be artificially inflated

### 🔴 Gap 2: No Walk-Forward Validation for RF/XGBoost
- **Location**: `app/api/retrain.py` (retraining logic)
- **Current**: Single 80/20 split
- **Best Practice**: Walk-forward cross-validation for time series:
  - Train on t₀...tₙ, test on tₙ₊₁...tₙ₊ₖ
  - Retrain, test on tₙ₊ₖ₊₁...tₙ₊₂ₖ
  - Average metrics across folds
- **Solution**: Use `TimeSeriesSplit(n_splits=5)` from sklearn
- **Impact**: **High** — Needed to validate AUC 0.80 threshold is realistic

### 🔴 Gap 3: MLflow LSTM Inference Not Production-Ready
- **Location**: `app/services/forecasting/production_forecaster.py:102-138`
- **Problem**: Returns stub data instead of real LSTM predictions
- **Missing Components**:
  1. Feature engineering pipeline matching training code
  2. Sequence windowing (last N days)
  3. MC Dropout for epistemic uncertainty
  4. Proper date generation for forecast horizon
- **Impact**: **Medium** — Fallback to Ensemble works but:
  - Wastes MLflow training compute
  - LSTM's superior performance unavailable in production
  - No benefit from PyTorch GPU acceleration

### 🔴 Gap 4: No Crisis Detection Logic
- **Database**: No `environmental_crisis` or `crisis_log` table
- **Schema Needed**:
  ```python
  class EnvironmentalCrisis(Base):
      __tablename__ = "environmental_crises"
      id = Column(Integer, primary_key=True)
      region_code = Column(String(10), index=True)
      crisis_type = Column(String(50))  # 'flood', 'drought', 'wildfire', 'air_quality'
      severity = Column(Float)  # 0-100 scale
      detected_at = Column(DateTime, index=True)
      resolved_at = Column(DateTime, nullable=True)
      signal_ids = Column(Text)  # JSON array of RegionSignal IDs that triggered detection
  ```
- **Ingestion Path**: `OpenAQIngester` fetches PM2.5 but **no threshold checks** happen
- **Impact**: **Critical for roadmap** — Cannot build crisis alerts without detection logic

### 🔴 Gap 5: Forecast Metrics Not Exposed in API
- **Database**: `ForecastModelMetrics` table exists (`database.py:148-167`)
- **Problem**: No API endpoint to query historical MAE/RMSE trends
- **Use Case**:
  - Monitor forecast degradation over time
  - Compare Prophet vs LSTM vs Ensemble performance
  - Alert when MAPE > threshold (e.g., 15%)
- **Impact**: **Low** (monitoring gap, not blocking features)

---

## 4. First 3 Tasks to Implement

### Task 1: Fix Time Series Data Leakage in Retraining [CRITICAL]
**Priority**: 🔴 **P0 — Blocks accurate AUC validation**

**Files to Change**:
1. `app/api/retrain.py:146` — Replace `train_test_split` with temporal split
2. `app/api/ab_comparison.py` (if uses shuffled split)

**Implementation**:
```python
# BEFORE (line 146):
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)

# AFTER:
# Temporal split (newest 20% as holdout)
split_idx = int(len(X) * 0.8)
X_train, y_train = X.iloc[:split_idx], y.iloc[:split_idx]
X_test, y_test = X.iloc[split_idx:], y.iloc[split_idx:]

# Note: Remove stratify (not valid for temporal data)
# Log warning if test set has class imbalance
```

**Validation**:
- Re-run retrain job: `curl -X POST http://localhost:8000/api/v1/model/retrain`
- Compare new AUC to current (expect **drop of 3-5%** due to no leakage)
- Update `MIN_AUC_THRESHOLD` in `scheduler.py:382` if needed

**Estimated Time**: 1 hour

---

### Task 2: Implement Walk-Forward Cross-Validation [HIGH]
**Priority**: 🟠 **P1 — Needed to validate forecasting robustness**

**Files to Change**:
1. `app/api/retrain.py` — Add `walk_forward_validate()` function
2. `app/services/forecasting/prophet.py` — Refactor `validate()` to use it

**Implementation**:
```python
from sklearn.model_selection import TimeSeriesSplit

def walk_forward_validate(model, X, y, n_splits=5):
    """
    Perform walk-forward validation on time series.
    Returns average MAE, RMSE, AUC across folds.
    """
    tscv = TimeSeriesSplit(n_splits=n_splits)
    scores = []
    
    for train_idx, test_idx in tscv.split(X):
        X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
        y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]
        
        model.fit(X_train, y_train)
        y_pred = model.predict_proba(X_test)[:, 1]
        
        auc = roc_auc_score(y_test, y_pred)
        scores.append(auc)
    
    return {
        "mean_auc": np.mean(scores),
        "std_auc": np.std(scores),
        "fold_scores": scores
    }
```

**Integration**:
- Call in `_do_retrain()` before promoting model
- Add `walk_forward_auc` to `retrain_log.metrics_json`
- Fail promotion if `std_auc > 0.05` (high variance across folds)

**Estimated Time**: 3 hours

---

### Task 3: Add Environmental Crisis Detection Logic [MEDIUM]
**Priority**: 🟡 **P2 — Foundation for roadmap alert features**

**Files to Create/Change**:
1. **New migration**: `alembic revision --autogenerate -m "add_environmental_crisis_table"`
2. **New module**: `app/services/crisis_detector.py`
3. **Integrate**: `app/scheduler.py` — Add crisis detection job (runs after ingesters)

**Database Schema** (in migration):
```python
class EnvironmentalCrisis(Base):
    __tablename__ = "environmental_crises"
    id = Column(Integer, primary_key=True)
    region_code = Column(String(10), index=True, nullable=False)
    crisis_type = Column(String(50), nullable=False)  # 'air_quality', 'co2_spike'
    severity = Column(Float, nullable=False)  # 0-100 scale
    threshold_exceeded = Column(Float)  # Actual value that triggered
    threshold_limit = Column(Float)  # Configured limit
    detected_at = Column(DateTime, default=datetime.utcnow, index=True)
    resolved_at = Column(DateTime, nullable=True)
    signal_ids = Column(Text)  # JSON array of RegionSignal IDs
```

**Crisis Detector** (`app/services/crisis_detector.py`):
```python
from app.database import SessionLocal, RegionSignal, EnvironmentalCrisis
from datetime import datetime, timedelta
import json

CRISIS_THRESHOLDS = {
    "pm25_ugm3": 75.0,  # WHO guideline: 24h mean > 75 μg/m³
    "no2_ugm3": 200.0,  # 1h mean > 200 μg/m³
    "o3_ugm3": 160.0,   # 8h mean > 160 μg/m³
}

def detect_crises():
    """Check recent signals for threshold violations."""
    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(hours=6)
        
        for metric, threshold in CRISIS_THRESHOLDS.items():
            # Find violations
            violations = db.query(RegionSignal).filter(
                RegionSignal.metric == metric,
                RegionSignal.observed_at >= cutoff,
                RegionSignal.value > threshold
            ).all()
            
            for v in violations:
                # Check if crisis already logged
                existing = db.query(EnvironmentalCrisis).filter(
                    EnvironmentalCrisis.region_code == v.region_code,
                    EnvironmentalCrisis.crisis_type == metric,
                    EnvironmentalCrisis.resolved_at.is_(None)
                ).first()
                
                if not existing:
                    # Log new crisis
                    crisis = EnvironmentalCrisis(
                        region_code=v.region_code,
                        crisis_type=metric,
                        severity=min(100, (v.value / threshold) * 100),
                        threshold_exceeded=v.value,
                        threshold_limit=threshold,
                        signal_ids=json.dumps([v.id])
                    )
                    db.add(crisis)
        
        db.commit()
    finally:
        db.close()
```

**Scheduler Integration** (`app/scheduler.py:740+`):
```python
scheduler.add_job(
    detect_crises,
    IntervalTrigger(hours=6),
    id="auto_crisis_detection",
    name="Detect environmental crises every 6h",
    replace_existing=True,
)
```

**Estimated Time**: 4 hours

---

## Summary

| Category | Count | Status |
|----------|-------|--------|
| Production-Ready | 5 | ✅ OpenAQ, Prophet, AUC gates, DB schema, scheduled jobs |
| Unverified/Partial | 4 | ⚠️ MLflow stub, random splits, missing TimeSeriesSplit, no crisis tables |
| Critical Gaps | 5 | 🔴 Data leakage, no walk-forward validation, LSTM inference, crisis logic, metrics API |

**Next Steps**:
1. Fix data leakage (Task 1) — **1 hour, blocks accurate validation**
2. Implement walk-forward CV (Task 2) — **3 hours, validates forecast robustness**
3. Add crisis detection (Task 3) — **4 hours, unlocks alert features**

**Total Estimated Time**: 8 hours

---

**Notes**:
- Prophet forecasting is **production-ready** with proper temporal validation
- RandomForest/XGBoost retraining has **time series data leakage** (high priority fix)
- OpenAQ integration works but **no crisis detection logic** hooks into it yet
- MLflow LSTM exists in training but **not inference** (medium priority)
- AUC threshold raised to 0.80 is **good** but may need adjustment after fixing leakage
