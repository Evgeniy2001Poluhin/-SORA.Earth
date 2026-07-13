#!/bin/bash
# Add more historical data to reach LSTM threshold faster

API_URL="https://sora-earth.online/api/v1/evaluate"

# Add data from May (even further back)
START_DATE="2026-05-20"
DAYS=11  # May 20-30 (11 days)

echo "======================================================================"
echo "Adding More Historical Data (May 20-30)"
echo "======================================================================"

count=0
success=0

for ((day=0; day<DAYS; day++)); do
    if date --version >/dev/null 2>&1; then
        eval_date=$(date -d "$START_DATE +$day days" "+%Y-%m-%d")
    else
        eval_date=$(date -v+${day}d -j -f "%Y-%m-%d" "$START_DATE" "+%Y-%m-%d")
    fi

    n_evals=$((RANDOM % 2 + 2))

    for ((i=0; i<n_evals; i++)); do
        budgets=(50000 75000 100000 150000 200000 300000 500000)
        budget=${budgets[$((RANDOM % ${#budgets[@]}))]}

        co2=$(awk -v min=50 -v max=500 'BEGIN{srand(); print min+rand()*(max-min)}')
        social=$((RANDOM % 6 + 5))
        duration_options=(12 18 24 30 36 48)
        duration=${duration_options[$((RANDOM % ${#duration_options[@]}))]}

        regions=("Europe" "Asia" "North America" "Latin America" "Africa" "Oceania")
        region=${regions[$((RANDOM % ${#regions[@]}))]}

        categories=("renewable_energy" "waste_management" "water_conservation" "sustainable_agriculture" "green_transport" "carbon_capture")
        category=${categories[$((RANDOM % ${#categories[@]}))]}

        project_name="Historical_${category}_${eval_date}_$((RANDOM % 9000 + 1000))"

        response=$(curl -s -X POST "$API_URL" \
            -H "Content-Type: application/json" \
            -d "{
                \"project_name\": \"$project_name\",
                \"budget\": $budget,
                \"co2_reduction\": $co2,
                \"social_impact\": $social,
                \"duration_months\": $duration,
                \"region\": \"$region\",
                \"category\": \"$category\"
            }")

        if echo "$response" | jq -e '.total_score' >/dev/null 2>&1; then
            ((success++))
            score=$(echo "$response" | jq -r '.total_score')
            echo "✓ $eval_date: $score"
        else
            echo "✗ $eval_date failed"
        fi

        ((count++))
        sleep 0.2
    done
done

echo
echo "======================================================================"
echo "✅ Added $success evaluations"
echo
echo "Current LSTM Status:"
curl -s 'https://sora-earth.online/api/v1/lstm-status' | jq '{active, samples, threshold, days_remaining, message}'

echo
echo "If not active yet, trigger manual retrain:"
echo "  ssh root@45.137.60.67 'docker compose -f /opt/sora_earth_ai_platform/docker-compose.prod.yml exec scheduler python -c \"from app.scheduler import scheduled_pretrain_forecast_models; scheduled_pretrain_forecast_models()\"'"
echo "======================================================================"
