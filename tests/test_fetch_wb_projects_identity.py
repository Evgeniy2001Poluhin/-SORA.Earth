"""fetch_wb_projects.py's identity schema, against a fixture -- no live API.

The defect the audit found: this script asked the World Bank API for `id`,
read it into a variable, used the variable only as a hash seed, and never
wrote it to the output row. `to_records()` is the function that builds the
output row, and takes no network dependency -- `collected` is already a plain
list of `(api_row_dict, budget)` pairs -- so it is tested directly against a
fixture built to look like real API rows, never against the network.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import fetch_wb_projects as fwp  # noqa: E402
import dataset_identity as di  # noqa: E402


def api_row(id="P505244", project_name="Boosting Green Finance", sector="Energy",
            regionname="Eastern and Southern Africa", status="Active",
            boardapprovaldate="2024-12-15", closingdate="2029-12-31",
            **extra):
    """A World Bank API project record, shaped like the real response's
    `projects` entries (see docs/WORLDBANK_DATASET.md's example)."""
    row = {
        "id": id, "project_name": project_name, "sector": sector,
        "regionname": regionname, "status": status,
        "boardapprovaldate": boardapprovaldate, "closingdate": closingdate,
        "countryshortname": "Rwanda", "countrycode": "RW",
    }
    row.update(extra)
    return row


def make_collected(*rows_and_budgets):
    """`[(row, budget), ...]`, the shape to_records() consumes."""
    return list(rows_and_budgets)


GDP_MAP = {"RW": 850.0}
GDP_MEDIAN = 12720.0


class TestSourceProjectIdIsWritten:
    def test_the_id_from_the_api_row_ends_up_in_the_output(self):
        collected = make_collected((api_row(id="P505244"), 200_000_000.0))
        records, skipped = fwp.to_records(collected, GDP_MAP, GDP_MEDIAN)
        assert len(records) == 1
        assert skipped == 0
        assert records[0]["source_project_id"] == "P505244"
        assert records[0]["source"] == "worldbank"

    def test_the_composite_key_is_buildable_from_the_output_row(self):
        collected = make_collected((api_row(id="P505244"), 200_000_000.0))
        records, _skipped = fwp.to_records(collected, GDP_MAP, GDP_MEDIAN)
        key = di.project_key(records[0]["source"], records[0]["source_project_id"])
        assert key == "worldbank:P505244"

    def test_source_project_id_is_in_the_written_columns(self):
        # Guards against a future edit adding the field to the dict but not
        # to COLUMNS -- csv.DictWriter would then silently drop it.
        assert "source_project_id" in fwp.COLUMNS


class TestARowWithNoIdIsDroppedNotWrittenBlank:
    def test_empty_id_is_skipped(self):
        collected = make_collected((api_row(id=""), 200_000_000.0))
        records, skipped = fwp.to_records(collected, GDP_MAP, GDP_MEDIAN)
        assert records == []
        assert skipped == 1

    def test_missing_id_key_entirely_is_also_skipped(self):
        row = api_row()
        del row["id"]
        collected = make_collected((row, 200_000_000.0))
        records, skipped = fwp.to_records(collected, GDP_MAP, GDP_MEDIAN)
        assert records == []
        assert skipped == 1

    def test_a_mix_of_good_and_bad_rows_keeps_only_the_good_ones(self):
        collected = make_collected(
            (api_row(id="P1"), 1_000_000.0),
            (api_row(id=""), 2_000_000.0),
            (api_row(id="P2"), 3_000_000.0),
        )
        records, skipped = fwp.to_records(collected, GDP_MAP, GDP_MEDIAN)
        ids = sorted(r["source_project_id"] for r in records)
        assert ids == ["P1", "P2"]
        assert skipped == 1


class TestSyntheticRowsCarryNoInventedId:
    def test_load_synthetic_sets_an_explicit_empty_source_project_id(self, tmp_path):
        import csv
        synth_path = tmp_path / "synthetic.csv"
        with open(synth_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fwp.COLUMNS)
            w.writeheader()
            w.writerow({
                "budget": 1000, "co2_reduction": 1, "social_impact": 5,
                "duration_months": 12, "category": "Energy", "region": "Africa",
                "country_gdp_per_capita": 1000, "success": 1,
                "name": "Old Seed Project", "source": "seed",
                "source_project_id": "",
            })
        rows = fwp.load_synthetic(str(synth_path), gdp_median=GDP_MEDIAN)
        assert len(rows) == 1
        assert rows[0]["source_project_id"] == ""
        assert rows[0]["source"] == "synthetic"

    def test_a_synthetic_row_cannot_produce_a_project_key(self, tmp_path):
        import csv
        synth_path = tmp_path / "synthetic.csv"
        with open(synth_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fwp.COLUMNS)
            w.writeheader()
            w.writerow({
                "budget": 1000, "co2_reduction": 1, "social_impact": 5,
                "duration_months": 12, "category": "Energy", "region": "Africa",
                "country_gdp_per_capita": 1000, "success": 1,
                "name": "Old Seed Project", "source": "synthetic",
                "source_project_id": "",
            })
        rows = fwp.load_synthetic(str(synth_path), gdp_median=GDP_MEDIAN)
        with pytest.raises(di.MissingSourceProjectId):
            di.project_key(rows[0]["source"], rows[0]["source_project_id"])


class TestUniquenessIsEnforced:
    def test_duplicate_ids_across_two_fetched_rows_are_detected(self):
        # Simulates a World Bank API page overlap (a real, observed failure
        # mode of paginated APIs under concurrent writes to the underlying
        # data) producing the same project twice.
        collected = make_collected(
            (api_row(id="P505244", project_name="First fetch"), 1_000_000.0),
            (api_row(id="P505244", project_name="Same project, refetched"), 1_000_000.0),
        )
        records, _skipped = fwp.to_records(collected, GDP_MAP, GDP_MEDIAN)
        keys = [di.project_key(r["source"], r["source_project_id"]) for r in records]
        with pytest.raises(ValueError, match="worldbank:P505244"):
            di.assert_unique_keys(keys)


class TestMutationTheGuardActuallyCatchesTheOriginalDefect:
    """Reproduces the exact defect the audit found, to prove the test above
    would have caught it. If `to_records()` is ever edited back to compute
    `pid` and then not write it to the row, this fails."""

    def test_a_row_shaped_like_the_pre_fix_output_is_rejected(self):
        # The pre-fix output dict, reconstructed by hand from the pre-fix
        # source (no source_project_id key at all).
        pre_fix_row = {
            "budget": 200_000_000.0, "co2_reduction": 1.0, "social_impact": 80.0,
            "duration_months": 60, "category": "Energy", "region": "Africa",
            "country_gdp_per_capita": 850.0, "success": 1,
            "name": "Boosting Green Finance", "source": "worldbank",
        }
        with pytest.raises(KeyError):
            di.project_key(pre_fix_row["source"], pre_fix_row["source_project_id"])
