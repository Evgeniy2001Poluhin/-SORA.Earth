"""scripts/ablation_harness.py -- the instrument, checked before it is trusted.

#232 says the reported AUC 0.905 cannot serve as evidence until the leakage
through `duration_months` is removed or quantified. Quantifying it needs a
harness, and a harness that is wrong produces a number with the same shape
as the right answer. So these tests check the instrument on fixtures small
enough to verify by hand, and none of them fits anything expensive: the
estimator is a 10-tree forest over ~200 synthetic rows, so the whole file
runs in about a second in CI.

The four mutations the reviewer asked for are at the bottom, each performed
against the real module and then reverted, so what is asserted is that the
guard *fires*, not that a copy of it would.
"""
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import ablation_harness as ah  # noqa: E402


def tiny_forest():
    from sklearn.ensemble import RandomForestClassifier
    return RandomForestClassifier(n_estimators=10, random_state=0)


def synthetic(n=240, *, leak=True, seed=0):
    """A dataset with one deliberately leaking column.

    `duration_months` is built from the label, so any model given it should
    be near-perfect and any model denied it should be near-chance. That
    contrast is the thing the real harness is meant to measure, so it is
    what the fixture makes checkable.
    """
    rng = np.random.default_rng(seed)
    y = rng.integers(0, 2, n)
    return pd.DataFrame({
        "source_project_id": [f"P{i:05d}" for i in range(n)],
        "name": [f"Project {i}" for i in range(n)],
        "success": y,
        # The leak: separated classes, a little noise so it is not literally
        # a copy of the target (which a different guard refuses outright).
        "duration_months": (y * 40 + rng.normal(0, 1.5, n) + 6) if leak
                            else rng.normal(30, 5, n),
        "budget": rng.lognormal(12, 1.0, n),          # unrelated to y
        "co2_reduction": np.zeros(n),                  # constant, as in production
        "social_impact": rng.uniform(40, 95, n),
        "country_gdp_per_capita": rng.uniform(500, 60000, n),
        "category": rng.choice(["energy", "water", "agro"], n),
        "region": rng.choice(["EU", "AFR", "APAC"], n),
    })


def split_of(df, seed=0):
    return ah.grouped_split(df, seed=seed)


# --- the contrast the harness exists to measure ---------------------------

class TestLeakageIsVisibleAndRemovable:
    def test_a_leaking_feature_scores_near_perfect(self):
        splits = split_of(synthetic())
        result = ah.run_variant(ah.VARIANTS_BY_NAME["leakage_only"], splits,
                                threshold=0.5, n_boot=20, seed=0,
                                estimator_factory=tiny_forest)
        assert result["metrics"]["roc_auc"] > 0.95, result["metrics"]

    def test_removing_it_drops_the_score_to_near_chance(self):
        splits = split_of(synthetic())
        result = ah.run_variant(ah.VARIANTS_BY_NAME["minimal_baseline"], splits,
                                threshold=0.5, n_boot=20, seed=0,
                                estimator_factory=tiny_forest)
        assert result["metrics"]["roc_auc"] < 0.70, result["metrics"]

    def test_the_gap_between_them_is_what_gets_reported(self):
        """Both variants must be measurable the same way for the difference
        to mean anything -- same split, same estimator, same threshold."""
        splits = split_of(synthetic())
        with_leak = ah.run_variant(ah.VARIANTS_BY_NAME["leakage_only"], splits,
                                    threshold=0.5, n_boot=20, seed=0,
                                    estimator_factory=tiny_forest)
        without = ah.run_variant(ah.VARIANTS_BY_NAME["minimal_baseline"], splits,
                                  threshold=0.5, n_boot=20, seed=0,
                                  estimator_factory=tiny_forest)
        assert with_leak["metrics"]["roc_auc"] - without["metrics"]["roc_auc"] > 0.25
        for row in (with_leak, without):
            assert row["metrics"]["threshold"] == 0.5
            assert "roc_auc" in row["confidence_intervals_95"]


# --- splitting ------------------------------------------------------------

class TestGroupedSplit:
    def test_every_row_of_one_id_lands_in_one_part(self):
        """The property a random row split does not have."""
        df = synthetic(n=120)
        df = pd.concat([df, df.assign(name=df["name"] + " (restated)")],
                       ignore_index=True)   # each id now appears twice
        splits = split_of(df)
        home = {}
        for part, frame in splits.items():
            for pid in frame["source_project_id"]:
                assert home.setdefault(pid, part) == part

    def test_no_id_appears_in_two_parts(self):
        splits = split_of(synthetic())
        ah.assert_no_id_overlap(splits)          # must not raise

    def test_an_overlapping_split_is_refused(self):
        df = synthetic(n=60)
        bad = {"train": df.iloc[:40].copy(), "test": df.iloc[30:].copy()}
        with pytest.raises(ah.SplitOverlap):
            ah.assert_no_id_overlap(bad)

    def test_a_missing_id_column_refuses_rather_than_falling_back(self):
        df = synthetic().drop(columns=["source_project_id"])
        with pytest.raises(ah.MissingProjectId):
            ah.grouped_split(df)

    def test_a_blank_id_refuses(self):
        df = synthetic(n=30)
        df.loc[0, "source_project_id"] = "  "
        with pytest.raises(ah.MissingProjectId):
            ah.grouped_split(df)

    def test_the_same_seed_gives_the_same_split(self):
        df = synthetic()
        a, b = split_of(df, seed=7), split_of(df, seed=7)
        for part in a:
            assert list(a[part]["source_project_id"]) == list(b[part]["source_project_id"])


class TestLegacyApproximateIsLabelledNotDisguised:
    def test_it_groups_by_name_when_there_is_no_id(self):
        df = synthetic().drop(columns=["source_project_id"])
        splits = ah.legacy_approximate_split(df)
        assert sum(len(f) for f in splits.values()) == len(df)
        ah.assert_no_id_overlap(splits, "_approx_group_key")

    def test_rows_sharing_a_name_stay_together(self):
        df = synthetic(n=60).drop(columns=["source_project_id"])
        df = pd.concat([df, df], ignore_index=True)   # each name twice
        splits = ah.legacy_approximate_split(df)
        home = {}
        for part, frame in splits.items():
            for key in frame["_approx_group_key"]:
                assert home.setdefault(key, part) == part


class TestTemporalSplitRefusesOnThisSchema:
    def test_no_date_column_is_a_refusal_not_a_row_order_fallback(self):
        with pytest.raises(ah.NoTemporalColumn):
            ah.temporal_split(synthetic())

    def test_given_a_real_date_column_it_preserves_ordering(self):
        """The behaviour the refusal is protecting: when an ordering exists,
        train must come before test in it."""
        df = synthetic(n=90)
        df["observed_at"] = pd.date_range("2026-01-01", periods=len(df), freq="D")
        df = df.sample(frac=1.0, random_state=1)      # shuffled input
        splits = ah.temporal_split(df, "observed_at")
        assert splits["train"]["observed_at"].max() <= splits["validation"]["observed_at"].min()
        assert splits["validation"]["observed_at"].max() <= splits["test"]["observed_at"].min()


# --- guards ---------------------------------------------------------------

class TestFeatureGuards:
    def test_the_target_as_a_feature_is_refused(self):
        df = synthetic()
        with pytest.raises(ah.TargetInFeatures):
            ah.assert_features_are_usable(df, ["budget", "success"])

    def test_an_exact_copy_of_the_target_is_refused(self):
        df = synthetic()
        df["sneaky"] = df["success"]
        with pytest.raises(ah.FeatureIsTargetCopy):
            ah.assert_features_are_usable(df, ["sneaky"])

    def test_a_copy_of_the_target_as_strings_is_still_refused(self):
        df = synthetic()
        df["sneaky"] = df["success"].astype(str)
        with pytest.raises(ah.FeatureIsTargetCopy):
            ah.assert_features_are_usable(df, ["sneaky"])

    def test_an_absent_feature_is_refused(self):
        with pytest.raises(ah.MissingFeature):
            ah.assert_features_are_usable(synthetic(), ["not_a_column"])

    def test_one_class_is_refused(self):
        df = synthetic()
        df["success"] = 1
        with pytest.raises(ah.SingleClass):
            ah.assert_both_classes(df["success"], "train")

    def test_a_split_too_small_for_an_interval_is_refused(self):
        splits = split_of(synthetic(n=30))
        with pytest.raises(ah.SplitTooSmall):
            ah.assert_split_big_enough(splits, min_rows=200)

    def test_esg_only_may_not_contain_a_target_derived_feature(self):
        """The declaration is enforced, not decorative."""
        ah.assert_group_excludes_target_derived(ah.VARIANTS_BY_NAME["esg_only"])
        polluted = ah.Variant("esg_only_polluted",
                              ("social_impact", "duration_months"), True, "test")
        with pytest.raises(ah.TargetDerivedFeatureInCleanGroup):
            ah.assert_group_excludes_target_derived(polluted)

    def test_duration_months_is_the_feature_marked_target_derived(self):
        assert ah.FEATURES["duration_months"].target_derived is True
        assert ah.FEATURES["budget"].target_derived is False


class TestDatasetAndOutputGuards:
    def test_a_manifest_that_disagrees_is_refused(self, tmp_path):
        data = tmp_path / "d.csv"
        data.write_text("a,b\n1,2\n")
        manifest = tmp_path / "m.json"
        manifest.write_text(json.dumps({"transformed_sha256": "0" * 64}))
        with pytest.raises(ah.DatasetHashMismatch):
            ah.assert_dataset_matches_manifest(str(data), str(manifest))

    def test_a_matching_manifest_passes_and_returns_the_hash(self, tmp_path):
        data = tmp_path / "d.csv"
        data.write_text("a,b\n1,2\n")
        real = ah.content_sha256(str(data))
        manifest = tmp_path / "m.json"
        manifest.write_text(json.dumps({"transformed_sha256": real}))
        assert ah.assert_dataset_matches_manifest(str(data), str(manifest)) == real

    def test_writing_over_an_existing_result_needs_force(self, tmp_path):
        out = tmp_path / "r.json"
        ah.write_result(str(out), {"a": 1})
        with pytest.raises(ah.ResultExists):
            ah.write_result(str(out), {"a": 2})
        ah.write_result(str(out), {"a": 2}, force=True)
        assert json.loads(out.read_text())["a"] == 2

    def test_it_refuses_to_write_into_models_or_data(self):
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        for protected in ("models", "data"):
            with pytest.raises(ah.ProtectedOutputPath):
                ah.assert_output_is_not_protected(
                    os.path.join(root, protected, "ablation.json"))

    def test_an_ordinary_output_path_is_allowed(self, tmp_path):
        ah.assert_output_is_not_protected(str(tmp_path / "ablation.json"))


# --- description ----------------------------------------------------------

class TestDescribeColumns:
    def test_a_zero_is_a_value_not_a_missing_one(self):
        """The mistake the audit itself made first: `is_missing()` counted
        "0" as absent and reported `success` 30.34% missing when it was 0%."""
        described = ah.describe_columns(synthetic(), ["co2_reduction"])
        assert described["co2_reduction"]["n_missing"] == 0
        assert described["co2_reduction"]["n_zero"] > 0

    def test_a_constant_column_is_flagged(self):
        described = ah.describe_columns(synthetic(), ["co2_reduction", "budget"])
        assert described["co2_reduction"]["is_constant"] is True
        assert described["budget"]["is_constant"] is False

    def test_lineage_and_target_derivation_are_carried_into_the_report(self):
        described = ah.describe_columns(synthetic(), ["duration_months"])
        assert described["duration_months"]["target_derived"] is True
        assert described["duration_months"]["lineage"] == "mixed"


# --- interval arithmetic --------------------------------------------------

class TestBootstrapInterval:
    def test_the_percentiles_are_the_ones_claimed(self):
        """0..100 makes the answer checkable without running anything: the
        2.5th and 97.5th percentiles are 2.5 and 97.5."""
        assert ah.bootstrap_ci(list(range(101))) == (2.5, 97.5)

    def test_a_different_alpha_moves_them_predictably(self):
        assert ah.bootstrap_ci(list(range(101)), alpha=0.10) == (5.0, 95.0)

    def test_identical_resamples_give_a_zero_width_interval(self):
        assert ah.bootstrap_ci([0.8] * 50) == (0.8, 0.8)

    def test_no_resamples_is_not_a_zero_interval(self):
        assert ah.bootstrap_ci([]) == (None, None)


class TestClusterBootstrap:
    """Rows inside one project are not independent draws.

    A row bootstrap over a clustered test set counts one project's agreement
    with itself as evidence and reports an interval narrower than the data
    supports. Under `legacy-approximate` the clusters are whole name-groups,
    which makes this the difference between an interval and a decoration.
    """

    @staticmethod
    def clustered(n_clusters=40, per_cluster=8):
        """Identical rows within a cluster, and clusters that differ from
        each other -- so cluster-level variance is the only variance there
        is, and the two bootstraps must visibly disagree.

        The first attempt at this fixture used p=0.9/y=1 and p=0.1/y=0,
        which gives every row the same squared error (0.01): a mean with no
        variance, identical under either resampling, and a test that could
        not distinguish the two methods it existed to compare. The clusters
        have to differ in how *wrong* they are, not only in their label.
        """
        y, p, g = [], [], []
        for c in range(n_clusters):
            well_calibrated = c % 2 == 0
            for _ in range(per_cluster):
                y.append(1 if well_calibrated else 0)
                # squared error 0.0025 vs 0.2025 -- constant inside a
                # cluster, far apart between them
                p.append(0.95 if well_calibrated else 0.45)
                g.append(f"C{c}")
        return np.array(y), np.array(p, dtype=float), np.array(g)

    def test_a_cluster_resample_never_splits_a_cluster(self):
        rng = np.random.default_rng(0)
        _y, _p, g = self.clustered(n_clusters=6, per_cluster=5)
        idx = ah.resample_indices(rng, len(g), g)
        counts = {}
        for name in g[idx]:
            counts[name] = counts.get(name, 0) + 1
        # every present cluster appears as a whole multiple of its size
        assert counts and all(v % 5 == 0 for v in counts.values()), counts

    def test_without_groups_it_is_an_ordinary_row_bootstrap(self):
        rng = np.random.default_rng(0)
        idx = ah.resample_indices(rng, 100, None)
        assert len(idx) == 100
        assert idx.min() >= 0 and idx.max() < 100

    def test_the_cluster_interval_is_wider_than_the_row_interval(self):
        """The substantive property. If this stops holding, the harness has
        gone back to treating correlated rows as independent."""
        y, p, g = self.clustered()
        row = ah.bootstrap_metrics(y, p, threshold=0.5, n_boot=200, seed=0)
        cluster = ah.bootstrap_metrics(y, p, threshold=0.5, n_boot=200, seed=0,
                                        groups=g)
        row_width = row["brier"]["ci_hi"] - row["brier"]["ci_lo"]
        cluster_width = cluster["brier"]["ci_hi"] - cluster["brier"]["ci_lo"]
        assert cluster_width > row_width * 1.5, (row_width, cluster_width)

    def test_the_report_records_which_bootstrap_was_used(self):
        splits = split_of(synthetic())
        result = ah.run_variant(ah.VARIANTS_BY_NAME["minimal_baseline"], splits,
                                threshold=0.5, n_boot=10, seed=0,
                                estimator_factory=tiny_forest,
                                group_col=ah.ID_COLUMN)
        assert result["bootstrap"]["mode"] == "cluster"
        assert result["bootstrap"]["unit"] == ah.ID_COLUMN
        assert result["bootstrap"]["n_clusters"] == len(splits["test"])

    def test_without_a_group_column_it_says_row_rather_than_pretending(self):
        splits = split_of(synthetic())
        result = ah.run_variant(ah.VARIANTS_BY_NAME["minimal_baseline"], splits,
                                threshold=0.5, n_boot=10, seed=0,
                                estimator_factory=tiny_forest, group_col=None)
        assert result["bootstrap"]["mode"] == "row"
        assert result["bootstrap"]["n_clusters"] is None


# --- the four mutations the review asked for ------------------------------

class TestMutations:
    """Each disables one guard in the real module, asserts a test above now
    fails, and restores it. Written as explicit save/restore rather than
    monkeypatch so a failure mid-test cannot leave the module altered for
    the rest of the session."""

    def test_removing_the_id_overlap_check_is_caught(self):
        original = ah.assert_no_id_overlap
        try:
            ah.assert_no_id_overlap = lambda *a, **k: None      # mutation
            df = synthetic(n=60)
            bad = {"train": df.iloc[:40].copy(), "test": df.iloc[30:].copy()}
            ah.assert_no_id_overlap(bad)        # the mutant accepts overlap
            with pytest.raises(ah.SplitOverlap):
                original(bad)                   # the real one does not
        finally:
            ah.assert_no_id_overlap = original

    def test_a_random_row_split_is_caught(self):
        """The mutation: grouping replaced by an independent per-row draw."""
        rng = np.random.default_rng(0)
        df = synthetic(n=120)
        df = pd.concat([df, df], ignore_index=True)     # each id twice
        random_row_split = {}
        draw = rng.integers(0, 2, len(df))
        random_row_split["train"] = df[draw == 0].copy()
        random_row_split["test"] = df[draw == 1].copy()
        # The property the real splitter has and this one does not:
        with pytest.raises(ah.SplitOverlap):
            ah.assert_no_id_overlap(random_row_split)
        ah.assert_no_id_overlap(split_of(df))          # real one is clean

    def test_letting_a_target_derived_feature_into_esg_only_is_caught(self):
        original = ah.VARIANTS_BY_NAME["esg_only"]
        try:
            polluted = ah.Variant(
                original.name, original.features + ("duration_months",),
                original.must_exclude_target_derived, original.why)
            ah.VARIANTS_BY_NAME["esg_only"] = polluted       # mutation
            with pytest.raises(ah.TargetDerivedFeatureInCleanGroup):
                ah.run_variant(ah.VARIANTS_BY_NAME["esg_only"],
                               split_of(synthetic()), threshold=0.5,
                               n_boot=5, seed=0, estimator_factory=tiny_forest)
        finally:
            ah.VARIANTS_BY_NAME["esg_only"] = original

    def test_wrong_interval_arithmetic_is_caught(self):
        """The mutation: 5th/95th percentiles reported as a 95% interval."""
        def wrong_ci(values, alpha=0.05):
            arr = np.asarray(values, dtype=float)
            return (float(np.percentile(arr, 5)), float(np.percentile(arr, 95)))

        assert wrong_ci(list(range(101))) != (2.5, 97.5)
        assert ah.bootstrap_ci(list(range(101))) == (2.5, 97.5)


# --- end to end, on fixtures only ----------------------------------------

class TestEndToEnd:
    def test_main_writes_a_report_that_matches_the_documented_schema(self, tmp_path):
        data = tmp_path / "fixture.csv"
        synthetic(n=900).to_csv(data, index=False)
        out = tmp_path / "ablation.json"
        rc = ah.main(["--data", str(data), "--out", str(out),
                      "--bootstrap", "5", "--min-rows-per-split", "50",
                      "--variants", "leakage_only,minimal_baseline"])
        assert rc == 0
        report = json.loads(out.read_text())
        assert report["schema_version"] == ah.SCHEMA_VERSION
        assert report["split"]["mode"] == "grouped"
        assert report["split"]["id_overlap"] == {"checked": True, "overlapping_ids": 0}
        assert {v["variant"] for v in report["variants"]} == {
            "leakage_only", "minimal_baseline"}
        for variant in report["variants"]:
            assert set(variant["metrics"]) >= {
                "roc_auc", "pr_auc", "log_loss", "brier", "calibration_error",
                "threshold", "accuracy_at_threshold", "f1_at_threshold"}
        for part in ("train", "validation", "test"):
            summary = report["split"]["summary"][part]
            assert summary["rows"] > 0
            assert summary["unique_project_ids"] == summary["rows"]

    def test_the_report_says_what_it_is_not(self, tmp_path):
        data = tmp_path / "fixture.csv"
        synthetic(n=900).to_csv(data, index=False)
        out = tmp_path / "ablation.json"
        ah.main(["--data", str(data), "--out", str(out), "--bootstrap", "5",
                 "--min-rows-per-split", "50", "--variants", "minimal_baseline"])
        report = json.loads(out.read_text())
        joined = " ".join(report["not_claimed"]).lower()
        assert "not a retrain" in joined
        assert "models/" in joined
