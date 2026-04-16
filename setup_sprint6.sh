#!/bin/bash
set -e

echo "=== 1. Создаём структуру Grafana provisioning ==="
mkdir -p grafana/provisioning/datasources
mkdir -p grafana/provisioning/dashboards

echo "=== 2. datasource.yml ==="
cat > grafana/provisioning/datasources/datasource.yml << 'EOF'
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    orgId: 1
    url: http://prometheus:9090
    isDefault: true
    editable: true
EOF

echo "=== 3. dashboards.yml ==="
cat > grafana/provisioning/dashboards/dashboards.yml << 'EOF'
apiVersion: 1
providers:
  - name: 'sora'
    orgId: 1
    folder: 'SORA.Earth'
    type: file
    disableDeletion: false
    allowUiUpdates: true
    options:
      path: /etc/grafana/provisioning/dashboards
EOF

echo "=== 4. Dashboard JSON ==="
cat > grafana/provisioning/dashboards/sora-mlops-overview.json << 'DASHBOARD_EOF'
{
  "annotations": {"list": []},
  "editable": true,
  "graphTooltip": 1,
  "id": null,
  "links": [],
  "panels": [
    {"type": "row", "title": "Platform Overview", "gridPos": {"h": 1, "w": 24, "x": 0, "y": 0}, "collapsed": false},
    {
      "type": "stat", "title": "App Version",
      "gridPos": {"h": 4, "w": 4, "x": 0, "y": 1},
      "targets": [{"expr": "sora_app_info", "legendFormat": "{{version}}", "refId": "A"}],
      "fieldConfig": {"defaults": {"mappings": [{"type": "value", "options": {"1": {"text": "v2.0.0", "color": "green"}}}], "thresholds": {"steps": [{"color": "green", "value": null}]}}}
    },
    {
      "type": "stat", "title": "HTTP Requests/sec",
      "gridPos": {"h": 4, "w": 5, "x": 4, "y": 1},
      "targets": [{"expr": "sum(rate(http_requests_total[5m]))", "legendFormat": "RPS", "refId": "A"}],
      "fieldConfig": {"defaults": {"unit": "reqps", "thresholds": {"steps": [{"color": "green", "value": null}, {"color": "yellow", "value": 10}, {"color": "red", "value": 50}]}}}
    },
    {
      "type": "stat", "title": "Total Predictions",
      "gridPos": {"h": 4, "w": 5, "x": 9, "y": 1},
      "targets": [{"expr": "sum(sora_predictions_total)", "legendFormat": "total", "refId": "A"}],
      "fieldConfig": {"defaults": {"thresholds": {"steps": [{"color": "blue", "value": null}]}}}
    },
    {
      "type": "gauge", "title": "Current Model AUC",
      "gridPos": {"h": 4, "w": 5, "x": 14, "y": 1},
      "targets": [{"expr": "sora_model_auc", "legendFormat": "AUC", "refId": "A"}],
      "fieldConfig": {"defaults": {"min": 0, "max": 1, "thresholds": {"steps": [{"color": "red", "value": null}, {"color": "yellow", "value": 0.7}, {"color": "green", "value": 0.85}]}}}
    },
    {
      "type": "gauge", "title": "Current Model Accuracy",
      "gridPos": {"h": 4, "w": 5, "x": 19, "y": 1},
      "targets": [{"expr": "sora_model_accuracy", "legendFormat": "Accuracy", "refId": "A"}],
      "fieldConfig": {"defaults": {"min": 0, "max": 1, "thresholds": {"steps": [{"color": "red", "value": null}, {"color": "yellow", "value": 0.7}, {"color": "green", "value": 0.85}]}}}
    },
    {"type": "row", "title": "HTTP Metrics", "gridPos": {"h": 1, "w": 24, "x": 0, "y": 5}, "collapsed": false},
    {
      "type": "timeseries", "title": "HTTP Request Rate (by status)",
      "gridPos": {"h": 8, "w": 12, "x": 0, "y": 6},
      "targets": [{"expr": "sum by (status) (rate(http_requests_total[5m]))", "legendFormat": "{{status}}", "refId": "A"}],
      "fieldConfig": {"defaults": {"unit": "reqps", "custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 15}}}
    },
    {
      "type": "timeseries", "title": "HTTP Request Duration (p50 / p95 / p99)",
      "gridPos": {"h": 8, "w": 12, "x": 12, "y": 6},
      "targets": [
        {"expr": "histogram_quantile(0.50, sum by (le) (rate(http_request_duration_seconds_bucket[5m])))", "legendFormat": "p50", "refId": "A"},
        {"expr": "histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket[5m])))", "legendFormat": "p95", "refId": "B"},
        {"expr": "histogram_quantile(0.99, sum by (le) (rate(http_request_duration_seconds_bucket[5m])))", "legendFormat": "p99", "refId": "C"}
      ],
      "fieldConfig": {"defaults": {"unit": "s", "custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 10}}}
    },
    {"type": "row", "title": "ML Predictions", "gridPos": {"h": 1, "w": 24, "x": 0, "y": 14}, "collapsed": false},
    {
      "type": "timeseries", "title": "Prediction Rate (by model)",
      "gridPos": {"h": 8, "w": 12, "x": 0, "y": 15},
      "targets": [{"expr": "sum by (model) (rate(sora_predictions_total[5m]))", "legendFormat": "{{model}}", "refId": "A"}],
      "fieldConfig": {"defaults": {"unit": "reqps", "custom": {"drawStyle": "bars", "lineWidth": 1, "fillOpacity": 50}}}
    },
    {
      "type": "timeseries", "title": "Prediction Latency (p50 / p95 / p99)",
      "gridPos": {"h": 8, "w": 12, "x": 12, "y": 15},
      "targets": [
        {"expr": "histogram_quantile(0.50, sum by (le) (rate(sora_prediction_latency_ms_bucket[5m])))", "legendFormat": "p50", "refId": "A"},
        {"expr": "histogram_quantile(0.95, sum by (le) (rate(sora_prediction_latency_ms_bucket[5m])))", "legendFormat": "p95", "refId": "B"},
        {"expr": "histogram_quantile(0.99, sum by (le) (rate(sora_prediction_latency_ms_bucket[5m])))", "legendFormat": "p99", "refId": "C"}
      ],
      "fieldConfig": {"defaults": {"unit": "ms", "custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 15}}}
    },
    {"type": "row", "title": "MLOps Lifecycle", "gridPos": {"h": 1, "w": 24, "x": 0, "y": 23}, "collapsed": false},
    {
      "type": "stat", "title": "Drift Events",
      "gridPos": {"h": 5, "w": 4, "x": 0, "y": 24},
      "targets": [{"expr": "sora_drift_detected_total", "legendFormat": "drift", "refId": "A"}],
      "fieldConfig": {"defaults": {"thresholds": {"steps": [{"color": "green", "value": null}, {"color": "orange", "value": 1}, {"color": "red", "value": 5}]}}}
    },
    {
      "type": "stat", "title": "Models Promoted",
      "gridPos": {"h": 5, "w": 5, "x": 4, "y": 24},
      "targets": [{"expr": "sora_model_promoted_total", "legendFormat": "promoted", "refId": "A"}],
      "fieldConfig": {"defaults": {"thresholds": {"steps": [{"color": "green", "value": null}]}}}
    },
    {
      "type": "stat", "title": "Models Rejected",
      "gridPos": {"h": 5, "w": 5, "x": 9, "y": 24},
      "targets": [{"expr": "sora_model_rejected_total", "legendFormat": "rejected", "refId": "A"}],
      "fieldConfig": {"defaults": {"thresholds": {"steps": [{"color": "green", "value": null}, {"color": "red", "value": 1}]}}}
    },
    {
      "type": "timeseries", "title": "Model AUC Over Time",
      "gridPos": {"h": 5, "w": 10, "x": 14, "y": 24},
      "targets": [{"expr": "sora_model_auc", "legendFormat": "AUC", "refId": "A"}],
      "fieldConfig": {"defaults": {"min": 0, "max": 1, "unit": "percentunit", "custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 20, "gradientMode": "scheme"}, "thresholds": {"mode": "absolute", "steps": [{"color": "red", "value": null}, {"color": "yellow", "value": 0.7}, {"color": "green", "value": 0.85}]}, "color": {"mode": "thresholds"}}}
    }
  ],
  "schemaVersion": 39,
  "tags": ["sora", "mlops", "fastapi"],
  "templating": {"list": []},
  "time": {"from": "now-6h", "to": "now"},
  "timepicker": {},
  "timezone": "browser",
  "title": "SORA MLOps Overview",
  "uid": "sora-mlops-overview",
  "version": 1
}
DASHBOARD_EOF

echo "=== 5. Обновляем docker-compose.yml — Grafana volumes ==="
# Проверяем есть ли уже grafana сервис с provisioning
if grep -q 'provisioning/datasources' docker-compose.yml 2>/dev/null; then
  echo "  → Grafana provisioning volumes уже есть в docker-compose.yml, пропускаем"
else
  echo "  → Нужно добавить volumes в grafana сервис docker-compose.yml"
  echo "    Добавь эти строки в секцию grafana → volumes:"
  echo "      - ./grafana/provisioning/datasources:/etc/grafana/provisioning/datasources"
  echo "      - ./grafana/provisioning/dashboards:/etc/grafana/provisioning/dashboards"
fi

echo ""
echo "=== 6. Добавляем Full Pipeline кнопку в admin-dashboard.html ==="

# Проверяем есть ли уже кнопка
if grep -q 'triggerFullPipeline' app/static/admin-dashboard.html 2>/dev/null; then
  echo "  → Full Pipeline кнопка уже есть, пропускаем"
else
  # Вставляем кнопку после Trigger Retrain
  # Сначала добавим HTML кнопку рядом с btnRetrain
  sed -i.bak '/<button.*btnRetrain\|Trigger Retrain/a\
        <button id="btnFullPipeline" onclick="triggerFullPipeline()" class="btn btn-warning" title="refresh → drift → retrain → validate → promote">⚡ Full Pipeline</button>' app/static/admin-dashboard.html 2>/dev/null || echo "  → sed не нашёл btnRetrain, добавь кнопку вручную"

  # Добавляем JS функцию перед закрывающим </script>
  # Ищем последний </script> и вставляем перед ним
  cat >> /tmp/full_pipeline_fn.js << 'JEOF'

async function triggerFullPipeline() {
  const btn = document.getElementById('btnFullPipeline');
  btn.disabled = true; btn.textContent = '⏳ Running...';
  try {
    const res = await fetch('/api/v1/mlops/full-pipeline', {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' }
    });
    const data = await res.json();
    if (res.ok) {
      showToast('Full pipeline: ' + (data.decision || data.status || 'done'), 'success');
    } else {
      showToast('Pipeline error: ' + (data.detail || res.status), 'error');
    }
  } catch (e) { showToast('Pipeline failed: ' + e.message, 'error'); }
  finally { btn.disabled = false; btn.textContent = '⚡ Full Pipeline'; loadTimeline(); loadSnapshot(); }
}
JEOF

  # Вставляем функцию перед последним </script>
  if [ -f app/static/admin-dashboard.html ]; then
    # Находим номер последней строки с </script>
    LAST_SCRIPT_LINE=$(grep -n '</script>' app/static/admin-dashboard.html | tail -1 | cut -d: -f1)
    if [ -n "$LAST_SCRIPT_LINE" ]; then
      sed -i.bak2 "$((LAST_SCRIPT_LINE-1))r /tmp/full_pipeline_fn.js" app/static/admin-dashboard.html
      echo "  → triggerFullPipeline() добавлена в admin-dashboard.html"
    else
      echo "  → Не нашёл </script>, добавь функцию вручную"
    fi
  fi
  rm -f /tmp/full_pipeline_fn.js
fi

echo ""
echo "=== 7. Обновляем docker-compose.yml — grafana volumes ==="
# Патчим grafana volumes если их нет
if [ -f docker-compose.yml ]; then
  if ! grep -q 'grafana/provisioning' docker-compose.yml; then
    # Ищем строку с grafana_data и добавляем после неё provisioning
    sed -i.bak3 '/grafana_data:\/var\/lib\/grafana/a\
      - ./grafana/provisioning/datasources:/etc/grafana/provisioning/datasources\
      - ./grafana/provisioning/dashboards:/etc/grafana/provisioning/dashboards' docker-compose.yml 2>/dev/null \
    && echo "  → Grafana provisioning volumes добавлены" \
    || echo "  → Не удалось автопатчить, добавь volumes вручную"
  else
    echo "  → Уже есть"
  fi
fi

echo ""
echo "=== 8. Cleanup backup files ==="
rm -f app/static/admin-dashboard.html.bak app/static/admin-dashboard.html.bak2 docker-compose.yml.bak3

echo ""
echo "=== 9. Пересобираем и запускаем ==="
echo "Запусти:"
echo "  docker compose up -d --build app grafana"
echo ""
echo "Затем проверь:"
echo "  # Grafana dashboard"
echo "  open http://localhost:3000  (admin / sora2026)"
echo "  # → Dashboards → SORA.Earth → SORA MLOps Overview"
echo ""
echo "  # Full Pipeline кнопка"
echo "  open http://localhost:8000/static/admin-dashboard.html"
echo ""
echo "  # Нагрузить метрики для графиков:"
echo '  for i in $(seq 1 10); do'
echo '    curl -s -X POST http://localhost:8000/api/v1/predict -H "Content-Type: application/json" -d "{\"budget\":$((RANDOM*3)),\"co2_reduction\":$((RANDOM%100)),\"social_impact\":$((RANDOM%10)),\"duration_months\":$((RANDOM%36+6))}" > /dev/null'
echo '    curl -s -X POST http://localhost:8000/api/v1/predict/neural -H "Content-Type: application/json" -d "{\"budget\":$((RANDOM*3)),\"co2_reduction\":$((RANDOM%100)),\"social_impact\":$((RANDOM%10)),\"duration_months\":$((RANDOM%36+6))}" > /dev/null'
echo '    curl -s -X POST http://localhost:8000/api/v1/predict/stacking -H "Content-Type: application/json" -d "{\"budget\":$((RANDOM*3)),\"co2_reduction\":$((RANDOM%100)),\"social_impact\":$((RANDOM%10)),\"duration_months\":$((RANDOM%36+6))}" > /dev/null'
echo '  done'
echo ""
echo "✅ Готово!"
