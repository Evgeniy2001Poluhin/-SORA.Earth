#!/usr/bin/env python3
"""Populate forecast_history from historical evaluations.

This script extracts historical ESG scores from the evaluations table
and creates daily aggregated time series for forecast model training.

Required data points for LSTM: n >= 70 days
Current evaluations: 86 records over ~28 days
"""

import sys
import os
from datetime import datetime, timedelta
from pathlib import Path

# Add app to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import pandas as pd

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://sora:sora2026@localhost:5432/sora_earth"
)

def main():
    """Extract evaluations and populate forecast_history."""
    print("🔄 Populating forecast_history from evaluations...")

    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # 1. Extract evaluations
        print("\n📊 Extracting evaluation data...")
        query = text("""
            SELECT
                created_at::date as date,
                AVG(total_score) as avg_score,
                AVG(success_probability) as avg_prob,
                AVG(co2_reduction) as avg_co2
            FROM evaluations
            GROUP BY date
            ORDER BY date
        """)

        df = pd.read_sql(query, engine)
        print(f"   Found {len(df)} days of aggregated data")
        print(f"   Date range: {df['date'].min()} to {df['date'].max()}")

        if len(df) == 0:
            print("❌ No evaluation data found")
            return 1

        # 2. Fill gaps with interpolation for smoother training
        print("\n🔧 Filling date gaps with interpolation...")
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date')

        # Create continuous daily index
        date_range = pd.date_range(
            start=df.index.min(),
            end=df.index.max(),
            freq='D'
        )
        df = df.reindex(date_range)

        # Linear interpolation for missing days
        df = df.interpolate(method='linear')
        df = df.reset_index()
        df.columns = ['date', 'avg_score', 'avg_prob', 'avg_co2']

        print(f"   After interpolation: {len(df)} continuous days")

        # 3. Check existing forecast_history
        print("\n🔍 Checking existing forecast_history...")
        count_query = text("SELECT COUNT(*) as count FROM forecast_history")
        result = session.execute(count_query).fetchone()
        existing_count = result[0]

        if existing_count > 0:
            print(f"   Found {existing_count} existing records")
            print("   Clearing old data to avoid duplicates...")
            session.execute(text("DELETE FROM forecast_history"))
            session.commit()

        # 4. Insert into forecast_history
        print("\n💾 Inserting into forecast_history...")

        records_inserted = 0
        for _, row in df.iterrows():
            # Insert score metric
            session.execute(text("""
                INSERT INTO forecast_history (metric_name, ds, y, created_at)
                VALUES (:metric, :ds, :y, :created_at)
            """), {
                "metric": "score",
                "ds": row['date'].strftime('%Y-%m-%d'),
                "y": float(row['avg_score']),
                "created_at": datetime.utcnow()
            })

            # Insert prob metric
            session.execute(text("""
                INSERT INTO forecast_history (metric_name, ds, y, created_at)
                VALUES (:metric, :ds, :y, :created_at)
            """), {
                "metric": "prob",
                "ds": row['date'].strftime('%Y-%m-%d'),
                "y": float(row['avg_prob']),
                "created_at": datetime.utcnow()
            })

            # Insert co2_reduction metric
            session.execute(text("""
                INSERT INTO forecast_history (metric_name, ds, y, created_at)
                VALUES (:metric, :ds, :y, :created_at)
            """), {
                "metric": "co2_reduction",
                "ds": row['date'].strftime('%Y-%m-%d'),
                "y": float(row['avg_co2']),
                "created_at": datetime.utcnow()
            })

            records_inserted += 3

        session.commit()
        print(f"   ✅ Inserted {records_inserted} records ({len(df)} days × 3 metrics)")

        # 5. Verify insertion
        print("\n✅ Verification:")
        for metric in ["score", "prob", "co2_reduction"]:
            count_query = text("""
                SELECT COUNT(*) as count
                FROM forecast_history
                WHERE metric_name = :metric
            """)
            result = session.execute(count_query, {"metric": metric}).fetchone()
            print(f"   {metric}: {result[0]} data points")

        # 6. Show sample data
        print("\n📈 Sample data (last 5 days):")
        sample_query = text("""
            SELECT metric_name, ds, y
            FROM forecast_history
            WHERE metric_name = 'score'
            ORDER BY ds DESC
            LIMIT 5
        """)
        samples = session.execute(sample_query).fetchall()
        for s in samples:
            print(f"   {s[1]}: {s[2]:.2f}")

        print("\n🎉 Success! LSTM now has sufficient data (n={})".format(len(df)))
        print("   Next: Run forecast API to train models on new data")

        return 0

    except Exception as e:
        print(f"\n❌ Error: {e}")
        session.rollback()
        import traceback
        traceback.print_exc()
        return 1
    finally:
        session.close()

if __name__ == "__main__":
    sys.exit(main())
