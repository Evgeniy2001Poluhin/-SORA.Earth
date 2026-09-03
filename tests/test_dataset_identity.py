"""scripts/dataset_identity.py: the composite key both World Bank pipelines share.

`data/projects.csv` -- what production trains on today -- has no project
identifier. `fetch_wb_projects.py` requests one from the API and throws it
away before writing the row. This module is the fix, and these are the tests
that would have caught either defect: an id silently dropped, or two
pipelines minting incompatible keys for the same source.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import dataset_identity as di  # noqa: E402


class TestProjectKey:
    def test_builds_the_composite_key(self):
        assert di.project_key("worldbank", "P505244") == "worldbank:P505244"

    def test_strips_surrounding_whitespace_from_the_id(self):
        # World Bank's own API has been observed to pad fields; a key that
        # differs only by whitespace is a duplicate a naive comparison misses.
        assert di.project_key("worldbank", "  P505244  ") == "worldbank:P505244"

    @pytest.mark.parametrize("bad_id", [None, "", "   "])
    def test_refuses_a_missing_id_rather_than_building_a_hollow_key(self, bad_id):
        # "worldbank:" is a valid-looking string. It must never be produced --
        # every row with no id would silently collide on it.
        with pytest.raises(di.MissingSourceProjectId):
            di.project_key("worldbank", bad_id)

    def test_requires_a_source(self):
        with pytest.raises(ValueError):
            di.project_key("", "P505244")

    def test_different_sources_cannot_collide_on_the_same_raw_id(self):
        # The whole reason the key is composite rather than bare id.
        a = di.project_key("worldbank", "1001")
        b = di.project_key("some_future_source", "1001")
        assert a != b


class TestSyntheticKey:
    def test_always_returns_none(self):
        # There is no generator-issued id recorded anywhere for the existing
        # synthetic rows in data/projects.csv. Returning anything else here
        # would be inventing a stable identity that was never actually stable.
        assert di.synthetic_key() is None
        assert di.synthetic_key("Solar Farm", source="synthetic") is None

    def test_is_not_confused_with_project_key(self):
        # A caller must never be able to get a truthy key for a synthetic row
        # by calling the wrong function and not noticing.
        assert di.synthetic_key() != di.project_key("worldbank", "1")


class TestNameMatchHeuristic:
    def test_same_normalised_name_gives_the_same_heuristic(self):
        a = di.name_match_heuristic("Boosting Green Finance")
        b = di.name_match_heuristic("  boosting   green finance  ")
        assert a == b

    def test_different_names_give_different_heuristics(self):
        a = di.name_match_heuristic("Boosting Green Finance")
        b = di.name_match_heuristic("Rural Water Access Program")
        assert a != b

    def test_the_heuristic_is_not_a_project_key(self):
        # It must not be assignable to source_project_id or compared with
        # project_key with ==; it exists for probabilistic overlap estimation
        # only, per the audit document's explicit warning against treating
        # name-matching as identity.
        heuristic = di.name_match_heuristic("Boosting Green Finance")
        real_key = di.project_key("worldbank", heuristic)
        assert real_key != heuristic  # the composite prefix always differs
        assert ":" not in heuristic  # and the heuristic itself is not key-shaped


class TestAssertUniqueKeys:
    def test_passes_on_all_unique_keys(self):
        di.assert_unique_keys(["worldbank:1", "worldbank:2", "worldbank:3"])

    def test_raises_naming_the_first_duplicate(self):
        with pytest.raises(ValueError, match="worldbank:2"):
            di.assert_unique_keys(["worldbank:1", "worldbank:2", "worldbank:2"])

    def test_empty_input_passes_vacuously(self):
        di.assert_unique_keys([])


class TestManifestWriting:
    def test_writes_valid_json_with_the_expected_fields(self, tmp_path):
        manifest_path = str(tmp_path / "out.csv.manifest.json")
        di.write_manifest(
            manifest_path,
            source_url="https://example.invalid/api",
            query_params={"max_projects": 100},
            fetch_timestamp="2026-09-03T00:00:00Z",
            commit_sha="abc123",
            stage_counts=[
                di.StageCounts("fetched", 100),
                di.StageCounts("dropped_no_id", 3, reason="empty id"),
            ],
            unique_ids=97,
            duplicate_ids=0,
            columns=["a", "b", "c"],
        )
        import json
        with open(manifest_path) as f:
            manifest = json.load(f)
        assert manifest["source_url"] == "https://example.invalid/api"
        assert manifest["unique_ids"] == 97
        assert manifest["duplicate_ids"] == 0
        assert manifest["stage_counts"] == [
            {"stage": "fetched", "count": 100},
            {"stage": "dropped_no_id", "count": 3, "reason": "empty id"},
        ]
        assert "schema_hash" in manifest
        assert manifest["columns"] == ["a", "b", "c"]

    def test_write_is_atomic_no_tmp_file_left_behind(self, tmp_path):
        manifest_path = str(tmp_path / "out.manifest.json")
        di.write_manifest(
            manifest_path, source_url="x", query_params={},
            fetch_timestamp="t", commit_sha="c",
            stage_counts=[di.StageCounts("s", 1)],
            unique_ids=1, duplicate_ids=0, columns=["a"],
        )
        assert os.path.exists(manifest_path)
        assert not os.path.exists(manifest_path + ".tmp")

    def test_includes_content_sha256_when_output_path_given(self, tmp_path):
        output_path = tmp_path / "data.csv"
        output_path.write_text("budget,name\n100,x\n")
        manifest_path = str(tmp_path / "data.csv.manifest.json")
        di.write_manifest(
            manifest_path, source_url="x", query_params={},
            fetch_timestamp="t", commit_sha="c",
            stage_counts=[di.StageCounts("s", 1)],
            unique_ids=1, duplicate_ids=0, columns=["budget", "name"],
            output_path=str(output_path),
        )
        import json, hashlib
        with open(manifest_path) as f:
            manifest = json.load(f)
        expected = hashlib.sha256(output_path.read_bytes()).hexdigest()
        assert manifest["content_sha256"] == expected


class TestSchemaHashDetectsDrift:
    def test_same_columns_same_hash(self):
        assert di.schema_hash(["a", "b"]) == di.schema_hash(["a", "b"])

    def test_added_column_changes_the_hash(self):
        assert di.schema_hash(["a", "b"]) != di.schema_hash(["a", "b", "c"])

    def test_reordered_columns_change_the_hash(self):
        # Column order is part of the CSV's contract for any positional
        # reader; a silent reorder should be visible in the manifest.
        assert di.schema_hash(["a", "b"]) != di.schema_hash(["b", "a"])
