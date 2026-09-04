"""scripts/check_duration_success_leakage.py: the duration/success leakage
measurement, against tiny crafted fixtures with a known correct answer.

The audit finding this guards: `duration_months` and `success` are both
derived from the same underlying fact (whether a project has a usable
closing date), so a naive rule using only `budget` and `duration_months` can
predict `success` at 85-89% on real data without touching genuine ESG
signal. These tests do not re-run that measurement on real data (that's
scripts/check_duration_success_leakage.py itself, exercised manually against
data/projects.csv and a live fetch -- see docs/DATASET_AUDIT_2026-09-03.md).
They pin the *arithmetic* of the naive-rule accuracy computation against
fixtures small enough to hand-verify, so a future edit to the classification
or accuracy logic that silently breaks it is caught here rather than only
by re-eyeballing a percentage against a docs page.
"""
import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import check_duration_success_leakage as leak  # noqa: E402
from fetch_wb_projects import synth_duration  # noqa: E402


class TestClassifyExact:
    def test_matches_the_real_synth_duration_output(self):
        budget, pid = 100_000, "P1"
        d = synth_duration(budget, pid)
        assert leak.classify_exact(budget, d, pid) is True

    def test_rejects_a_duration_the_formula_did_not_produce(self):
        budget, pid = 100_000, "P1"
        real_synth = synth_duration(budget, pid)
        off_by_one = real_synth + 1
        assert leak.classify_exact(budget, off_by_one, pid) is False

    def test_a_different_project_id_gives_a_different_synthetic_value(self):
        # The formula jitters by an md5 hash of the id -- this is what makes
        # "recompute and compare" meaningful rather than a budget-only check.
        budget = 100_000
        assert synth_duration(budget, "P1") != synth_duration(budget, "P2")


class TestClassifyApproximate:
    def test_a_duration_matching_the_formula_shape_is_synthetic_looking(self):
        # 6*log10(100000) == 30 exactly
        assert leak.classify_approximate(100_000, 30) is True

    def test_a_duration_far_from_the_formula_shape_is_not(self):
        assert leak.classify_approximate(100_000, 60) is False

    def test_a_duration_outside_the_declared_6_to_72_range_is_never_synthetic(self):
        # Even if it would otherwise be "close" to 6*log10(budget), the real
        # synth_duration() formula clamps to [6, 72] -- a value outside that
        # could not have come from it.
        assert leak.classify_approximate(100_000, 100) is False

    def test_boundary_is_inclusive_at_plus_6_5(self):
        # 6*log10(100000) == 30; jitter is declared as +-6 in the docstring
        # but classify_approximate uses +-6.5 deliberately, to not exclude a
        # genuine synth_duration() output through float/round edge effects.
        assert leak.classify_approximate(100_000, 36.5) is True
        assert leak.classify_approximate(100_000, 36.51) is False


class TestEvaluateArithmetic:
    """Hand-computed fixture: 7 rows, 5 the naive rule gets right, 2 wrong."""

    def _rows(self):
        # budget, pid, duration_months, success
        # synth_duration(100000, "P1") == 31; synth_duration(100000, "P2") == 25
        # synth_duration(5000000, "P3") == 41; synth_duration(5000000, "P4") == 43
        raw = [
            (100_000, "P1", 31, "0"),   # synthetic, success=0 -> correct
            (100_000, "P2", 25, "0"),   # synthetic, success=0 -> correct
            (5_000_000, "P3", 41, "1"),  # synthetic, success=1 -> WRONG
            (5_000_000, "P4", 43, "0"),  # synthetic, success=0 -> correct
            (100_000, "P1", 99, "1"),   # real,      success=1 -> correct
            (100_000, "P2", 60, "1"),   # real,      success=1 -> correct
            (5_000_000, "P3", 12, "0"),  # real,      success=0 -> WRONG
        ]
        return [
            {"budget": str(b), "source_project_id": p,
             "duration_months": str(d), "success": s, "source": "worldbank"}
            for b, p, d, s in raw
        ]

    def test_exact_mode_accuracy_matches_hand_computed_value(self):
        result = leak.evaluate(self._rows(), has_ids=True)
        assert result["rows_evaluated"] == 7
        assert result["mode"] == "exact"
        assert result["success0_synthetic_duration"] == 3
        assert result["success0_real_duration"] == 1
        assert result["success1_synthetic_duration"] == 1
        assert result["success1_real_duration"] == 2
        assert result["naive_rule_correct"] == 5
        assert result["naive_rule_accuracy"] == round(5 / 7, 4)

    def test_a_wrong_synth_duration_formula_would_change_the_count(self):
        # Mutation check: if classify_exact used the wrong formula (e.g. no
        # jitter), row 3 (P3, duration=41) might stop matching and the
        # synthetic/real split -- and therefore the accuracy -- would move.
        # Confirms the test fixture actually depends on the real formula
        # rather than passing regardless of what classify_exact does.
        rows = self._rows()
        mutated = dict(rows[0])
        mutated["duration_months"] = "999"  # no longer matches synth_duration(100000, "P1")
        rows = [mutated] + rows[1:]
        result = leak.evaluate(rows, has_ids=True)
        assert result["success0_synthetic_duration"] == 2  # was 3
        assert result["success0_real_duration"] == 2        # was 1
        assert result["naive_rule_accuracy"] != round(5 / 7, 4)

    def test_approximate_mode_accuracy_matches_hand_computed_value(self):
        raw = [
            (100_000, 30, "0"),     # synthetic-shaped, success=0 -> correct
            (100_000, 25, "1"),     # synthetic-shaped, success=1 -> WRONG
            (100_000, 40, "1"),     # real-shaped,      success=1 -> correct
            (5_000_000, 41, "0"),   # synthetic-shaped, success=0 -> correct
            (5_000_000, 100, "0"),  # real-shaped (out of range), success=0 -> WRONG
            (5_000_000, 15, "1"),   # real-shaped, success=1 -> correct
        ]
        rows = [
            {"budget": str(b), "duration_months": str(d), "success": s,
             "source": "worldbank"}
            for b, d, s in raw
        ]
        result = leak.evaluate(rows, has_ids=False)
        assert result["rows_evaluated"] == 6
        assert result["mode"] == "approximate"
        assert result["naive_rule_correct"] == 4
        assert result["naive_rule_accuracy"] == round(4 / 6, 4)

    def test_rows_with_an_unusable_success_value_are_excluded_not_miscounted(self):
        rows = self._rows()
        rows.append({"budget": "100000", "source_project_id": "P1",
                      "duration_months": "31", "success": "", "source": "worldbank"})
        result = leak.evaluate(rows, has_ids=True)
        assert result["rows_evaluated"] == 7  # the 8th row (blank success) excluded

    def test_no_rows_reports_zero_rather_than_dividing_by_zero(self):
        result = leak.evaluate([], has_ids=True)
        assert result == {"rows_evaluated": 0}


class TestLoadRows:
    def _write_csv(self, tmp_path, rows, fieldnames):
        path = tmp_path / "projects.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        return str(path)

    def test_non_worldbank_rows_are_excluded(self, tmp_path):
        rows = [
            {"budget": "1", "duration_months": "1", "success": "0", "source": "worldbank"},
            {"budget": "1", "duration_months": "1", "success": "0", "source": "synthetic"},
        ]
        path = self._write_csv(tmp_path, rows, ["budget", "duration_months", "success", "source"])
        loaded, _has_ids = leak.load_rows(path)
        assert len(loaded) == 1
        assert loaded[0]["source"] == "worldbank"

    def test_has_ids_false_when_column_present_but_entirely_blank(self, tmp_path):
        # A column existing is not the same as it carrying data -- data/
        # projects.csv predates source_project_id and would never have this
        # column, but a future partially-migrated file could have the column
        # with blank values, and that must not be read as "exact mode".
        rows = [{"budget": "1", "duration_months": "1", "success": "0",
                  "source": "worldbank", "source_project_id": ""}]
        path = self._write_csv(
            tmp_path, rows, ["budget", "duration_months", "success", "source", "source_project_id"]
        )
        _loaded, has_ids = leak.load_rows(path)
        assert has_ids is False

    def test_has_ids_true_when_any_row_carries_one(self, tmp_path):
        rows = [{"budget": "1", "duration_months": "1", "success": "0",
                  "source": "worldbank", "source_project_id": "P1"}]
        path = self._write_csv(
            tmp_path, rows, ["budget", "duration_months", "success", "source", "source_project_id"]
        )
        _loaded, has_ids = leak.load_rows(path)
        assert has_ids is True
