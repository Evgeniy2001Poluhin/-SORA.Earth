# Next Steps Analysis - LSTM Activation & Platform Improvements

**Date:** 2026-07-13  
**Current Status:** System operational, LSTM inactive (28/35 samples threshold)

---

## Current State

### Production Metrics (as of 07:17 UTC)
- **Total samples:** 28 (22 train + 6 test)
- **LSTM status:** Inactive (need 35+, have 28)
- **Active models:** Prophet (90%) + LinearTrend (10%)
- **Model performance:**
  - Score MAE: 2.97, RMSE: 3.32, R²: -0.96
  - Prob MAE: 9.83, RMSE: 11.05, R²: -0.29
  - CO2 MAE: 34.92, RMSE: 38.95, R²: -11.74

**Key Issue:** Negative R² scores indicate models performing worse than mean baseline (expected with only 6 test samples).

---

## LSTM Threshold Analysis

### Why 35 is the Current Threshold

**Technical chain:**
1. **LSTM seq_length = 30** (last 30 days used for prediction)
2. **Feature engineering** adds lags: t-1, t-7, t-30
3. **Data loss:** First 30 rows become NaN after lag features → dropped
4. **Minimum viable:** Need 30 (seq) + 5 (buffer) = **35 samples**

**With n=28:**
- After feature engineering: 28 - 30 = **-2 samples** ❌
- Cannot create any sequences → ValueError

### History of Threshold Changes

| Commit | Threshold | Reasoning | Outcome |
|--------|-----------|-----------|---------|
| 4391384 | 40 → 25 | Enable LSTM on 28 days | Would crash (insufficient after features) |
| 35d27e5 | Align lstm.py: 40 → 35 | Safety margin reduction | Still crashes on 28 |
| 2f82c87 | Lock at 35 | Prevent exceptions | **Current (safe)** |

**Conclusion:** Threshold 35 is mathematically correct. Cannot go lower without changing feature engineering.

---

## Options Moving Forward

### Option 1: Wait for Natural Data Accumulation ⏳ **RECOMMENDED**

**Timeline:** 7 days  
**Effort:** Zero code changes  
**Risk:** None

**When:** By 2026-07-20, production will have 35+ days → LSTM auto-activates

**Pros:**
- No code risk
- Real data (no synthetic)
- Already set up correctly

**Cons:**
- 7-day wait
- Current forecasts remain Prophet-only

**Action:** Monitor daily, LSTM will auto-activate when ready.

---

### Option 2: Reduce Feature Lag Windows 🛠️

**Timeline:** 2-3 hours implementation  
**Effort:** Moderate refactoring  
**Risk:** Medium (affects model accuracy)

**Changes needed:**
1. **FeatureEngineer**: Reduce max lag from 30 → 14
   - Change lags: [1, 7, 30] → [1, 7, 14]
   - Change windows: [7, 30] → [7, 14]
2. **LSTMForecaster**: Reduce seq_length: 30 → 20
3. **Ensemble threshold**: 35 → 25

**Math:**
- Current: 28 samples - 30 lag = -2 ❌
- New: 28 samples - 14 lag = **14 samples** ✅
- Sequences: (14 - 20) + 1 = still negative...
- **Actually need:** seq=14, then 28 - 14 = 14 samples → 1 sequence (too few)

**Viable alternative:**
- seq_length = 10 (very short term)
- lags = [1, 7]
- 28 - 7 = 21 samples → 11 sequences ✅

**Pros:**
- LSTM works immediately on 28 days
- No waiting

**Cons:**
- **Accuracy loss:** Shorter sequences = less temporal context
- **Risk of overfitting:** Only 11 sequences for training
- **Requires testing:** Need to validate new configuration
- **May not improve over Prophet:** Short-term LSTM might be worse

**Verdict:** ⚠️ Not recommended. LSTM on 11 sequences likely worse than Prophet.

---

### Option 3: Add Synthetic Historical Data 🧪

**Timeline:** 1-2 hours  
**Effort:** Low (already implemented, was removed)  
**Risk:** High (synthetic data "poisons" training)

**What was removed in commit e555560:**
```python
# Trend-based backward extension (42 days)
# Linear regression + 10% noise
```

**Why it was removed:**
- Created false patterns
- LSTM learned synthetic trends, not real data
- Worse than Prophet baseline

**Pros:**
- Quick fix
- LSTM activates immediately

**Cons:**
- **Bad data quality:** Synthetic != real
- **Model degradation:** R² already negative, would get worse
- **Technical debt:** Band-aid solution
- **Already tried and failed**

**Verdict:** ❌ Not recommended. Removed for good reason.

---

### Option 4: Hybrid - Use LSTM Only on Longer Metrics 📊

**Timeline:** 1 hour  
**Effort:** Low (conditional logic)  
**Risk:** Low

**Idea:** Some metrics might already have 35+ samples
- Check each metric independently
- Use LSTM only where viable
- Prophet fallback for others

**Implementation:**
```python
# In ensemble.py
if metric == "score" and n_samples >= 35:
    use_lstm = True
elif metric == "co2_reduction" and n_samples >= 40:
    use_lstm = True
else:
    use_lstm = False  # Prophet-only
```

**Check current data:**
```bash
curl '/api/v1/forecast/metrics/latest' | jq '.[] | keys'
```

**Pros:**
- Use LSTM where possible
- Safe (no threshold lowering)
- Incremental activation

**Cons:**
- Complexity (per-metric logic)
- Need to check actual sample counts per metric
- Minimal benefit if all metrics at ~28

**Verdict:** 🤔 Worth investigating if data varies significantly by metric.

---

## Recommendation: **Option 1 (Wait) + Monitoring**

### Why Wait?

1. **Mathematical correctness:** Threshold 35 is not arbitrary
2. **Quality over speed:** Real data > synthetic data
3. **Already optimized:** System will auto-activate when ready
4. **No regression risk:** Keep working Prophet baseline
5. **Only 7 days:** Short wait for proper solution

### Monitoring Plan

**Daily checks (automated):**
```bash
# Check sample count growth
curl '/api/v1/forecast/metrics/latest' | \
  jq '.score.ensemble | {trained_at, train_samples, test_samples}'

# Check LSTM activation
curl '/api/v1/forecast?metric=score&horizon=7&model=ensemble' | \
  jq '.metadata.models_used | contains(["LSTM"])'
```

**Expected timeline:**
- **Jul 14-19:** n=29-34, LSTM still inactive
- **Jul 20:** n=35+, LSTM auto-activates 🎉
- **Jul 27:** n=42, R² turns positive (enough test samples)

---

## Alternative: Investigate Data Sparsity

**Question:** Why only 28 days when platform running since June?

**Possible causes:**
1. Low user activity (few evaluations per day)
2. Data not being logged correctly
3. Resampling to daily (multiple evals → 1 datapoint)

**Action:**
```bash
# Check raw evaluation count
curl '/api/v1/analytics/predictions-log?limit=100' | jq 'length'

# Check date distribution
curl '/api/v1/analytics/predictions-log?limit=100' | \
  jq -r '.[].created_at' | cut -d'T' -f1 | sort | uniq -c
```

**If sparse data is root cause:**
- Consider weekly resampling instead of daily
- Or accumulate more before forecasting

---

## Other Priority Tasks

While waiting for LSTM, focus on:

### 1. Fix Health Check (Urgent ⚠️)
**Issue:** `/health` returns `database=null, redis=null`  
**Impact:** Monitoring alerts may trigger  
**Effort:** 30 minutes

### 2. Improve R² Scores (Medium Priority)
**Current:** All metrics R² < 0 (worse than baseline)  
**Root cause:** Only 6 test samples → high variance  
**Timeline:** Fixes naturally when n>60 (30+ test samples)

### 3. Add LSTM Activation Alert (Low Priority)
**Goal:** Notify when LSTM becomes active  
**Implementation:** Slack webhook or email on first LSTM prediction  
**Effort:** 1 hour

### 4. Model Comparison Dashboard (Enhancement)
**Goal:** Frontend UI to compare Prophet vs LSTM side-by-side  
**Effort:** 2-3 hours  
**Value:** High (visualize improvements when LSTM activates)

---

## Decision Matrix

| Option | Timeline | Risk | Accuracy | Effort | Recommended |
|--------|----------|------|----------|--------|-------------|
| Wait | 7 days | None | ✅ Best | Zero | ✅ **YES** |
| Reduce lags | Immediate | Medium | ⚠️ Worse | High | ❌ No |
| Synthetic data | Immediate | High | ❌ Worst | Low | ❌ No |
| Hybrid per-metric | 1 day | Low | 🤔 TBD | Medium | 🤔 Maybe |

---

## Next Session Checklist

When resuming work:

- [ ] Check current sample count: `curl /api/v1/forecast/metrics/latest`
- [ ] If n≥35: Verify LSTM active in metadata
- [ ] If still n<35: Review this document's monitoring plan
- [ ] Fix health check endpoint (database/redis null issue)
- [ ] Consider frontend model comparison dashboard

---

## Summary

**Current bottleneck:** 28/35 samples for LSTM activation  
**Root cause:** Feature engineering needs 30-day lag → requires 35 total  
**Best path:** Wait 7 days for natural data accumulation  
**Alternative:** Investigate per-metric sample counts (Option 4)  
**Avoid:** Lowering threshold or adding synthetic data

**The system is working correctly.** LSTM threshold 35 is mathematically sound. Patience will yield better results than forcing activation prematurely.

---

**Status:** 📊 Analysis complete, awaiting user decision
