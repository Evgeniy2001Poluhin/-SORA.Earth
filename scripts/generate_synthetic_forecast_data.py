#!/usr/bin/env python3
"""Generate synthetic evaluation data for forecast model training.

Creates realistic time series data with:
- Trends (upward/downward)
- Seasonality (weekly patterns)
- Noise (random variations)
"""

import sys
import os
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import SessionLocal, Evaluation

def generate_synthetic_data(days=90, points_per_day=5):
    """Generate synthetic evaluation data."""

    np.random.seed(42)

    start_date = datetime.now() - timedelta(days=days)

    data = []

    for day in range(days):
        for point in range(points_per_day):
            timestamp = start_date + timedelta(days=day, hours=np.random.randint(0, 24),
                                               minutes=np.random.randint(0, 60))

            # Generate realistic ESG scores with trends and noise
            # Baseline around 60-70 with weekly seasonality
            base_score = 65
            trend = day * 0.05  # Slight upward trend
            seasonality = 5 * np.sin(2 * np.pi * day / 7)  # Weekly pattern
            noise = np.random.normal(0, 3)

            total_score = np.clip(base_score + trend + seasonality + noise, 30, 100)

            # Related metrics
            success_prob = np.clip((total_score - 30) / 70 + np.random.normal(0, 0.1), 0, 1)
            co2_reduction = np.random.uniform(50, 500)
            social_impact = np.random.randint(5, 10)

            data.append({
                'created_at': timestamp,
                'total_score': total_score,
                'environment_score': np.clip(total_score + np.random.normal(0, 5), 0, 100),
                'social_score': np.clip(total_score + np.random.normal(0, 5), 0, 100),
                'economic_score': np.clip(total_score + np.random.normal(0, 5), 0, 100),
                'success_probability': success_prob,
                'co2_reduction': co2_reduction,
                'social_impact': social_impact,
                'budget': np.random.uniform(50000, 500000),
                'duration_months': np.random.randint(12, 48),
                'name': f'Synthetic Project {day}_{point}',
                'region': np.random.choice(['Europe', 'Asia', 'Americas']),
                'risk_level': np.random.choice(['Low', 'Medium', 'High']),
                'recommendation': 'Synthetic data for training'
            })

    return data


def insert_synthetic_data():
    """Insert synthetic data into database."""
    db = SessionLocal()

    try:
        # Clear old synthetic data
        db.query(Evaluation).filter(Evaluation.name.like('Synthetic Project%')).delete()
        db.commit()

        print("Generating synthetic data...")
        data = generate_synthetic_data(days=90, points_per_day=5)

        print(f"Inserting {len(data)} synthetic evaluations...")

        for record in data:
            eval_obj = Evaluation(**record)
            db.add(eval_obj)

        db.commit()
        print(f"✓ Inserted {len(data)} synthetic evaluations")

        # Verify
        count = db.query(Evaluation).count()
        synthetic_count = db.query(Evaluation).filter(Evaluation.name.like('Synthetic Project%')).count()

        print(f"\nDatabase statistics:")
        print(f"  Total evaluations: {count}")
        print(f"  Synthetic data: {synthetic_count}")

        # Check date range
        first = db.query(Evaluation).order_by(Evaluation.created_at.asc()).first()
        last = db.query(Evaluation).order_by(Evaluation.created_at.desc()).first()

        if first and last:
            days = (last.created_at - first.created_at).days
            print(f"  Date range: {first.created_at.date()} to {last.created_at.date()}")
            print(f"  Days of data: {days}")

    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    insert_synthetic_data()
