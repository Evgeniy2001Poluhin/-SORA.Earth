"""enrich_worldbank_dataset.py's identity schema, against a fixture -- no live API.

`map_wb_project_to_schema()` is the row-building function; it makes no
network call as long as `gdp_cache` already has an entry for the row's
country -- pre-populated below for exactly that reason, so these tests never
reach `fetch_gdp_per_capita()`'s live request.
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import enrich_worldbank_dataset as ewd  # noqa: E402
import dataset_identity as di  # noqa: E402


def api_project(id="P505244", project_name="Boosting Green Finance",
                 countryname="Republic of Rwanda", countrycode="RW",
                 totalamt="200,000,000", status="Active", **extra):
    project = {
        "id": id, "project_name": project_name,
        "countryname": countryname, "countrycode": countrycode,
        "countryshortname": countryname, "totalamt": totalamt,
        "status": status, "boardapprovaldate": "2024-12-15",
        "closingdate": "2029-12-31",
        "sector1": {"Name": "Energy", "Percent": 100},
        "theme1": "Social Protection",
    }
    project.update(extra)
    return project


# Pre-populated so map_wb_project_to_schema never calls fetch_gdp_per_capita.
GDP_CACHE = {"RW": 850.0}


class TestSourceProjectIdIsWritten:
    def test_the_id_ends_up_as_source_project_id(self):
        mapped = ewd.map_wb_project_to_schema(api_project(id="P505244"), GDP_CACHE)
        assert mapped is not None
        assert mapped["source_project_id"] == "P505244"

    def test_source_is_lowercase_worldbank_matching_the_other_pipeline(self):
        # docs/DATASET_AUDIT_2026-09-03.md found these two pipelines writing
        # different schemas for the same source ("WorldBank" here,
        # "worldbank" in fetch_wb_projects.py) -- a caller joining rows from
        # both had to normalise case first just to find a match.
        mapped = ewd.map_wb_project_to_schema(api_project(), GDP_CACHE)
        assert mapped["source"] == "worldbank"

    def test_the_composite_key_matches_the_one_fetch_wb_projects_would_build(self):
        mapped = ewd.map_wb_project_to_schema(api_project(id="P505244"), GDP_CACHE)
        key = di.project_key(mapped["source"], mapped["source_project_id"])
        assert key == "worldbank:P505244"


class TestNoBudgetIsStillRejected:
    """Unrelated to identity, but a row rejected here must not reach the
    identity check at all -- confirms the two concerns stay independent."""

    def test_zero_budget_returns_none(self):
        mapped = ewd.map_wb_project_to_schema(api_project(totalamt="0"), GDP_CACHE)
        assert mapped is None


class TestDeduplicationByCompositeKey:
    """The main() dedup block reimplemented here at the DataFrame level, so it
    can be tested without a live fetch. Mirrors the logic in main() exactly:
    a difference here is a regression in main() to fix."""

    def _dedup(self, df):
        no_id_mask = df["source_project_id"].isna() | (df["source_project_id"] == "")
        no_id_count = int(no_id_mask.sum())
        with_id_df = df[~no_id_mask].copy()
        with_id_df["_project_key"] = with_id_df.apply(
            lambda r: di.project_key(r["source"], r["source_project_id"]), axis=1)
        duplicate_count = int(with_id_df["_project_key"].duplicated(keep="first").sum())
        with_id_df = with_id_df.drop_duplicates(subset=["_project_key"], keep="first")
        unique_ids = with_id_df["_project_key"].nunique()
        with_id_df = with_id_df.drop(columns=["_project_key"])
        result = pd.concat([df[no_id_mask], with_id_df], ignore_index=True)
        return result, no_id_count, duplicate_count, unique_ids

    def test_a_duplicate_source_project_id_is_removed_keeping_the_first(self):
        df = pd.DataFrame([
            {"source": "worldbank", "source_project_id": "P1", "name": "First seen"},
            {"source": "worldbank", "source_project_id": "P1", "name": "Refetched"},
            {"source": "worldbank", "source_project_id": "P2", "name": "Different project"},
        ])
        result, no_id, dup, unique = self._dedup(df)
        assert len(result) == 2
        assert dup == 1
        assert unique == 2
        assert sorted(result["name"]) == ["Different project", "First seen"]

    def test_rows_with_no_id_are_kept_not_dropped(self):
        # A row with no source_project_id (a pre-existing synthetic row) must
        # survive dedup -- it cannot be deduplicated, but it is not a
        # duplicate either, and dropping it would delete legitimate data.
        df = pd.DataFrame([
            {"source": "synthetic", "source_project_id": "", "name": "Old seed row"},
            {"source": "worldbank", "source_project_id": "P1", "name": "WB row"},
        ])
        result, no_id, dup, unique = self._dedup(df)
        assert len(result) == 2
        assert no_id == 1
        assert dup == 0

    def test_different_sources_with_the_same_raw_id_do_not_collide(self):
        df = pd.DataFrame([
            {"source": "worldbank", "source_project_id": "1", "name": "WB one"},
            {"source": "future_source", "source_project_id": "1", "name": "Different source"},
        ])
        result, no_id, dup, unique = self._dedup(df)
        assert len(result) == 2
        assert dup == 0
        assert unique == 2


class TestMutationTheDedupWouldMissAUnqualifiedCollision:
    """Proves the composite key matters: a bare-id dedup (the pre-fix logic)
    would have merged these two distinct rows into one."""

    def test_bare_id_dedup_would_have_wrongly_merged_rows_from_two_sources(self):
        df = pd.DataFrame([
            {"project_id": "1", "source": "worldbank", "name": "WB project"},
            {"project_id": "1", "source": "future_source", "name": "Unrelated project"},
        ])
        # The old logic: drop_duplicates(subset=["project_id"]) alone.
        bare_id_result = df.drop_duplicates(subset=["project_id"], keep="first")
        assert len(bare_id_result) == 1, (
            "if this assertion ever fails, pandas' behaviour changed and the "
            "premise of this test needs re-checking, not just deleting it"
        )
        # The fix: composite key keeps both, because they are genuinely
        # different projects that happen to share a raw numeric id.
        df["source_project_id"] = df["project_id"]
        result, _no_id, dup, _unique = TestDeduplicationByCompositeKey()._dedup(df)
        assert len(result) == 2
        assert dup == 0
