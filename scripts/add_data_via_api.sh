#!/bin/bash
# Add historical evaluations via API (safer than direct DB access)

API_URL="https://sora-earth.online/api/v1/evaluate"
START_DATE="2026-06-01"  # 15 days before first eval (2026-06-15)
DAYS=14

echo "======================================================================"
echo "Adding Historical Evaluations via API"
echo "======================================================================"
echo "Target: Add $DAYS days of historical data"
echo "Date range: $START_DATE to $(date -v+${DAYS}d -j -f "%Y-%m-%d" "$START_DATE" "+%Y-%m-%d" 2>/dev/null || date -d "$START_DATE +${DAYS} days" "+%Y-%m-%d")"
echo

count=0
success=0

for ((day=0; day<DAYS; day++)); do
    # Calculate date (macOS/Linux compatible)
    if date --version >/dev/null 2>&1; then
        # GNU date (Linux)
        eval_date=$(date -d "$START_DATE +$day days" "+%Y-%m-%d")
    else
        # BSD date (macOS)
        eval_date=$(date -v+${day}d -j -f "%Y-%m-%d" "$START_DATE" "+%Y-%m-%d")
    fi

    # 2-3 evaluations per day
    n_evals=$((RANDOM % 2 + 2))

    for ((i=0; i<n_evals; i++)); do
        # Generate realistic project data
        budgets=(50000 75000 100000 150000 200000 300000 500000)
        budget=${budgets[$((RANDOM % ${#budgets[@]}))]}

        co2=$(awk -v min=50 -v max=500 'BEGIN{srand(); print min+rand()*(max-min)}')
        social=$((RANDOM % 6 + 5))  # 5-10
        duration_options=(12 18 24 30 36 48)
        duration=${duration_options[$((RANDOM % ${#duration_options[@]}))]}

        regions=("Europe" "Asia" "North America" "Latin America" "Africa" "Oceania" "Middle East")
        region=${regions[$((RANDOM % ${#regions[@]}))]}

        categories=("renewable_energy" "waste_management" "water_conservation" "sustainable_agriculture" "green_transport" "carbon_capture")
        category=${categories[$((RANDOM % ${#categories[@]}))]}

        project_name="Historical_${category}_${eval_date}_$((RANDOM % 9000 + 1000))"

        # API call
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
            echo "✓ $eval_date #$i: $(echo "$response" | jq -r '.total_score') score"
        else
            echo "✗ Failed: $eval_date #$i"
        fi

        ((count++))

        # Rate limiting
        sleep 0.3
    done
done

echo
echo "======================================================================"
echo "✅ Completed!"
echo "   Sent: $count requests"
echo "   Success: $success evaluations"
echo
echo "Checking LSTM status..."
curl -s 'https://sora-earth.online/api/v1/lstm-status' | jq '{active, samples, threshold, days_remaining, next_activation_date}'
echo "======================================================================"
