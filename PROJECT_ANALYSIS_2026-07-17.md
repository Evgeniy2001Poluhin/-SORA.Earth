# SORA.Earth AI Platform - Анализ и план развития
**Дата:** 2026-07-17
**Статус:** Production (45.137.60.67)

---

## 1. ТЕКУЩЕЕ СОСТОЯНИЕ

### ✅ Что работает хорошо

#### A. MLOps Infrastructure
- **Drift Detection**: Автоматическая проверка каждые 6 часов (KS-test)
- **Auto-Retrain**: Ретрейн при обнаружении дрифта с валидацией AUC
- **Model Validation**: Модель принимается только если не деградирует (AUC delta >= 0)
- **Закрытый цикл**: Refresh → Drift → Retrain → Validate → Promote/Reject
- **Distributed Locks**: Redis-based locks предотвращают конфликты

#### B. Мониторинг
- **Prometheus**: 10+ custom metrics (predictions, drift, retrain)
- **Grafana**: Dashboard с визуализацией MLOps метрик
- **Latency tracking**: p95 latency < 200ms target
- **Health checks**: `/health`, `/ready` endpoints

#### C. ML Pipeline
- **4 модели**: RandomForest, XGBoost, PyTorch MLP, Stacking Ensemble v2
- **SHAP explainability**: Local + global explanations
- **Calibration**: Uncertainty quantification (p5/p95 percentiles)
- **A/B Testing**: Endpoint для сравнения моделей
- **Caching**: Redis (predictions) + in-memory LRU (benchmarks)

#### D. Week 1 Improvements (коммиты f81624f, 45739fc)
- **Prophet forecasting**: Оптимизация с декомпозицией тренда
- **LSTM progress**: Виджет статуса обучения
- **Grafana auto-provisioning**: Dashboard появляется автоматически
- **Metrics persistence**: Prometheus данные сохраняются

---

## 2. ПРОБЛЕМЫ И ОГРАНИЧЕНИЯ

### 🔴 Критические

#### A. Свежесть данных
**Проблема:**
- External data refresh: **1 раз в сутки** (daily job)
- World Bank API: данные могут быть устаревшими на месяцы
- Нет real-time источников

**Влияние:**
- Модель обучена на старых данных → предсказания не учитывают текущие события
- Пример: геополитические кризисы, климатические катастрофы не отражаются

**Решение:**
1. Увеличить частоту до **каждые 6 часов**
2. Добавить real-time data sources (см. раздел 3)
3. Версионирование данных (track data freshness)

#### B. Качество модели
**Проблема:**
- Validation threshold: **AUC > 0.70** (низкий порог)
- Только non-degradation check (new_auc >= old_auc)
- Нет absolute quality gate

**Риски:**
- Модель может быть promoted с AUC 0.71 (плохое качество)
- Постепенная деградация не блокируется

**Решение:**
1. Поднять absolute threshold: **AUC >= 0.80**
2. Добавить precision/recall thresholds
3. Business metrics validation (см. раздел 3.C)

#### C. Feature Engineering
**Проблема:**
- Только **9 features** для основной модели
- Отсутствуют важные факторы:
  - Геополитические риски
  - Макроэкономика (inflation, GDP growth)
  - Climate data (extreme weather events)
  - Social stability indices

**Влияние:**
- Модель не видит полной картины
- Упущенные корреляции

### 🟡 Средние

#### D. Data Quality
**Проблема:**
- Нет валидации на outliers
- Нет проверки completeness
- Missing values обрабатываются просто (fillna)

**Решение:**
1. Data quality metrics (см. раздел 3.D)
2. Outlier detection pipeline
3. Feature importance monitoring

#### E. Model Interpretability
**Проблема:**
- SHAP работает, но нет:
  - Feature importance drift tracking
  - Explanation consistency checks
  - Counterfactual analysis

**Решение:**
1. Track SHAP values over time
2. Alert on explanation drift
3. What-if analysis improvements

#### F. Scalability
**Проблема:**
- Single backend instance
- PostgreSQL connection pool: 10 connections
- Redis: single instance (no cluster)

**Риски при росте:**
- Bottleneck на базе
- Потеря кэша при рестарте

---

## 3. ПЛАН РАЗВИТИЯ

### Phase 1: Data Freshness & Quality (2-3 недели)

#### A. Real-Time Data Sources
```python
# Новые источники (API интеграция):
1. Climate Data:
   - OpenWeatherMap API (extreme events)
   - NOAA Climate Data Online
   - Copernicus Climate Change Service

2. Economic Indicators:
   - IMF Data API (GDP, inflation real-time)
   - OECD Data API
   - Trading Economics API

3. Geopolitical Risk:
   - GDELT Project (global events)
   - Political Risk Services (PRS)
   - Fragile States Index (real-time updates)

4. ESG Data:
   - Refinitiv ESG API
   - MSCI ESG Ratings API
   - Sustainalytics API
```

**Implementation:**
- Новый сервис: `data_ingestion_service`
- Scheduler jobs для каждого источника
- Кэширование с TTL
- Fallback на старые данные при сбое API

#### B. Data Quality Pipeline
```python
class DataQualityCheck:
    def validate(self, df: pd.DataFrame) -> QualityReport:
        checks = [
            self.check_completeness(df),      # Missing values %
            self.check_outliers(df),          # IQR method
            self.check_schema(df),            # Expected dtypes
            self.check_distributions(df),     # KS-test vs baseline
            self.check_correlations(df),      # Feature correlation drift
        ]
        return QualityReport(checks=checks, passed=all(c.passed for c in checks))
```

**Метрики:**
- Data quality score (0-100)
- Alerting при score < 80
- Dashboard в Grafana

#### C. Feature Store
```python
# Централизованное хранилище фичей с версионированием
class FeatureStore:
    def get_features(self, entity_id: str, timestamp: datetime) -> dict:
        """Point-in-time correct features."""
        pass
    
    def register_feature(self, name: str, schema: Schema):
        """Register new feature with metadata."""
        pass
```

**Преимущества:**
- Версионирование фичей
- Point-in-time correctness
- Feature lineage tracking

---

### Phase 2: Model Quality & Monitoring (3-4 недели)

#### A. Advanced Model Validation
```python
class ModelValidator:
    def validate(self, model, X_test, y_test) -> ValidationReport:
        return ValidationReport(
            auc=self.check_auc(model, X_test, y_test, threshold=0.80),
            precision=self.check_precision(model, X_test, y_test, threshold=0.75),
            recall=self.check_recall(model, X_test, y_test, threshold=0.70),
            calibration=self.check_calibration(model, X_test, y_test),
            fairness=self.check_fairness(model, X_test, y_test),  # By region
            business_metrics=self.check_business_impact(model, X_test, y_test),
        )
```

**Business Metrics:**
- Expected ROI from predictions
- False positive cost (rejected good projects)
- False negative cost (approved bad projects)
- Regional bias check

#### B. Model Performance Tracking
```sql
-- Новая таблица: model_performance_log
CREATE TABLE model_performance_log (
    id SERIAL PRIMARY KEY,
    model_version VARCHAR(50),
    measured_at TIMESTAMP,
    metric_name VARCHAR(50),  -- 'auc', 'precision', 'recall', etc.
    metric_value FLOAT,
    data_slice VARCHAR(100),  -- 'global', 'region_africa', 'budget_high', etc.
    sample_size INT
);
```

**Dashboard:**
- AUC over time (rolling 7 days)
- Precision/Recall by region
- Prediction distribution drift
- SHAP values drift

#### C. Shadow Mode для новых моделей
```python
@app.post("/api/v1/predict")
async def predict(request: PredictRequest):
    # Primary prediction
    result_v1 = model_v1.predict(features)
    
    # Shadow prediction (не показывается пользователю)
    result_v2 = model_v2.predict(features)
    
    # Log both для сравнения
    log_prediction(version="v1", result=result_v1, is_shadow=False)
    log_prediction(version="v2", result=result_v2, is_shadow=True)
    
    return result_v1  # Shadow не влияет на ответ
```

**Преимущества:**
- A/B тест без риска
- Сбор метрик новой модели
- Gradual rollout после валидации

---

### Phase 3: Advanced ML & Automation (4-6 недель)

#### A. Feature Engineering Automation
```python
# AutoML для генерации фичей
class AutoFeatureEngineering:
    def generate_features(self, df: pd.DataFrame) -> pd.DataFrame:
        generators = [
            PolynomialFeaturesGenerator(degree=2),
            InteractionFeaturesGenerator(),
            TimeSeriesFeaturesGenerator(),  # lags, rolling stats
            GeographicFeaturesGenerator(),  # region clusters
        ]
        for gen in generators:
            df = gen.transform(df)
        # Feature selection (SHAP-based importance)
        df = self.select_top_k_features(df, k=50)
        return df
```

#### B. Automated Hyperparameter Tuning
```python
# Optuna integration (уже есть в requirements)
def retrain_with_tuning(X_train, y_train, X_val, y_val):
    study = optuna.create_study(direction="maximize")
    study.optimize(
        lambda trial: objective(trial, X_train, y_train, X_val, y_val),
        n_trials=100,
        timeout=3600  # 1 час
    )
    best_model = train_with_params(study.best_params)
    return best_model
```

**Запускать:**
- Weekly (не при каждом ретрейне)
- Только если текущая модель стагнирует

#### C. Multi-Objective Optimization
```python
# Оптимизация не только AUC, но и business metrics
class MultiObjectiveValidator:
    def score(self, model, X, y) -> float:
        auc = roc_auc_score(y, model.predict_proba(X)[:, 1])
        expected_roi = self.calculate_expected_roi(model, X, y)
        fairness = self.calculate_fairness_score(model, X, y)
        
        # Weighted sum
        return 0.5 * auc + 0.3 * expected_roi + 0.2 * fairness
```

#### D. Active Learning
```python
# Модель запрашивает метки для самых неопределенных примеров
class ActiveLearner:
    def get_uncertain_samples(self, X_unlabeled, n=100):
        probs = self.model.predict_proba(X_unlabeled)
        uncertainty = 1 - np.abs(probs[:, 1] - 0.5) * 2  # Entropy
        return X_unlabeled[np.argsort(-uncertainty)[:n]]
```

**UI для ручной разметки:**
- Admin dashboard с uncertain predictions
- Expert labels → retrain

---

### Phase 4: Scalability & Production Hardening (4-6 недель)

#### A. Horizontal Scaling
```yaml
# Kubernetes deployment (вместо docker-compose)
apiVersion: apps/v1
kind: Deployment
metadata:
  name: backend
spec:
  replicas: 3  # Horizontal scaling
  template:
    spec:
      containers:
      - name: backend
        image: sora-backend:latest
        resources:
          requests:
            memory: "2Gi"
            cpu: "1000m"
          limits:
            memory: "4Gi"
            cpu: "2000m"
```

#### B. Database Optimization
```python
# Read replicas для аналитических запросов
DATABASES = {
    "default": {  # Write master
        "ENGINE": "postgresql",
        "HOST": "postgres-master",
    },
    "replica": {  # Read replica
        "ENGINE": "postgresql",
        "HOST": "postgres-replica",
    }
}

# Router
class DatabaseRouter:
    def db_for_read(self, model, **hints):
        if model._meta.app_label == 'analytics':
            return 'replica'
        return 'default'
```

#### C. Redis Cluster
```yaml
# Redis Cluster для HA
redis:
  cluster:
    enabled: true
    nodes: 6  # 3 masters + 3 replicas
```

#### D. Model Serving Optimization
```python
# Batch predictions для эффективности
@app.post("/api/v1/predict/batch")
async def predict_batch(requests: List[PredictRequest]):
    # Vectorized prediction (10x faster)
    features = pd.DataFrame([r.dict() for r in requests])
    predictions = model.predict_proba(features)[:, 1]
    return [{"success_prob": float(p)} for p in predictions]
```

---

## 4. ПРИОРИТИЗАЦИЯ

### Immediate (1-2 недели)
1. ✅ **Деплой Week 1** (Prophet + LSTM + Grafana) — в процессе
2. 🔴 **Поднять AUC threshold до 0.80** — критично
3. 🔴 **Data freshness: 6h вместо 24h** — быстрый win

### Short-term (1 месяц)
4. 🟡 **Data Quality Pipeline** (Phase 1.B)
5. 🟡 **Model Performance Dashboard** (Phase 2.B)
6. 🟡 **Climate API integration** (Phase 1.A)

### Mid-term (2-3 месяца)
7. 🟢 **Feature Store** (Phase 1.C)
8. 🟢 **Shadow Mode** (Phase 2.C)
9. 🟢 **Advanced validation** (Phase 2.A)

### Long-term (3-6 месяцев)
10. 🔵 **AutoML features** (Phase 3.A)
11. 🔵 **Kubernetes migration** (Phase 4.A)
12. 🔵 **Active Learning** (Phase 3.D)

---

## 5. МЕТРИКИ УСПЕХА

### Текущие (baseline)
- Model AUC: **~0.75** (предположение, нужно померить)
- Prediction latency: **~100ms** (p95)
- Drift detection rate: **~1/week**
- Retrain success rate: **~80%**

### Target (через 3 месяца)
- Model AUC: **≥ 0.85**
- Prediction latency: **< 50ms** (p95)
- Data freshness: **< 6 hours**
- False positive rate: **< 15%**
- Model explain coverage: **100%** (все предсказания с SHAP)

### Target (через 6 месяцев)
- Model AUC: **≥ 0.90**
- Multi-modal predictions (табличные + текст + geo)
- Real-time data ingestion (< 1 min latency)
- Auto-scaling (10x traffic без деградации)

---

## 6. ОЦЕНКА СТОИМОСТИ

### Phase 1 (Data Freshness & Quality)
- **Разработка**: 80-120 часов
- **API costs**: ~$200-500/месяц (real-time data sources)
- **Infrastructure**: текущая (достаточно)

### Phase 2 (Model Quality)
- **Разработка**: 120-160 часов
- **Compute**: +20% (shadow mode predictions)
- **Storage**: +500 GB (performance logs)

### Phase 3 (Advanced ML)
- **Разработка**: 160-200 часов
- **Compute**: +50% (AutoML tuning)
- **Labeling budget**: $1000-2000 (active learning)

### Phase 4 (Scalability)
- **Разработка**: 120-160 часов
- **Infrastructure**: +300% (K8s cluster, replicas)
- **Migration effort**: 40-60 часов

---

## 7. РИСКИ

| Риск | Вероятность | Влияние | Митигация |
|------|-------------|---------|-----------|
| API rate limits | Высокая | Средняя | Fallback, кэширование |
| Data quality degradation | Средняя | Высокая | Automated monitoring + alerts |
| Model drift ускоряется | Средняя | Высокая | Чаще проверять (3h вместо 6h) |
| Scalability bottleneck | Низкая | Средняя | Load testing перед Phase 4 |
| Business metrics противоречат AUC | Низкая | Высокая | Multi-objective optimization |

---

## 8. NEXT STEPS

### Сегодня (после деплоя):
1. ✅ Проверить LSTM status
2. ✅ Проверить Prophet MAE в логах
3. ✅ Подтвердить Grafana dashboard
4. 📊 **Померить текущий AUC модели в production**

### Завтра:
1. Поднять AUC threshold: `0.70 → 0.80`
2. Настроить data refresh на 6h
3. Добавить Climate API (OpenWeatherMap)

### На неделе:
1. Имплементировать Data Quality Pipeline
2. Создать Model Performance Dashboard в Grafana
3. Написать тесты для новых фичей

---

## Выводы

**Проект работает хорошо** (MLOps инфраструктура solid), но есть **критические gaps**:

1. **Свежесть данных** — данные обновляются раз в сутки, слишком медленно
2. **Validation threshold** — AUC > 0.70 слишком низкий, нужно 0.80+
3. **Feature coverage** — только 9 фичей, упускаем важные сигналы

**Приоритет:** Сначала fix data freshness + validation, потом Advanced ML.

**ROI:** Phase 1-2 дадут самый большой impact (~20-30% улучшение AUC) за 1-2 месяца.
