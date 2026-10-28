#!/usr/bin/env python3
"""Clean up synthetic forecast training data from database.

This script removes the 450 synthetic evaluations that were added
for model training purposes.
"""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import SessionLocal, Evaluation


def cleanup_synthetic_data():
    """Remove synthetic data from database."""
    db = SessionLocal()

    try:
        # Public rows only, in every count and in the DELETE. A matching name
        # does not make a saved pilot evaluation synthetic: somebody may have
        # called their own project "Synthetic Project ...", and it is theirs.
        public = Evaluation.data_scope == "public"
        total_before = db.query(Evaluation).filter(public).count()
        synthetic_count = db.query(Evaluation).filter(
            public, Evaluation.name.like('Synthetic Project%')
        ).count()

        print(f"Database statistics BEFORE cleanup:")
        print(f"  Total evaluations: {total_before}")
        print(f"  Synthetic data: {synthetic_count}")

        if synthetic_count == 0:
            print("\n✓ No synthetic data found. Database is clean.")
            return

        # Confirm deletion
        response = input(f"\nDelete {synthetic_count} synthetic records? (yes/no): ")

        if response.lower() != 'yes':
            print("Cancelled. No changes made.")
            return

        # Delete synthetic data
        deleted = db.query(Evaluation).filter(
            public, Evaluation.name.like('Synthetic Project%')
        ).delete(synchronize_session=False)

        db.commit()

        # Verify
        total_after = db.query(Evaluation).filter(public).count()

        print(f"\n✓ Deleted {deleted} synthetic records")
        print(f"\nDatabase statistics AFTER cleanup:")
        print(f"  Total evaluations: {total_after}")
        print(f"  Real data only: {total_after}")

    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    cleanup_synthetic_data()
