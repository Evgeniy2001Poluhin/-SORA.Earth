"""
Tests for CSRD compliance category handling.

Covers НАХОДКА-2: substring matching defects where "refor" in category
matches both "reforestation" (correct) and "reform of procurement" (false positive),
and "no reforestation is planned" (negation, also false positive).
"""
import pytest
from app.services.compliance_engine import assess_csrd
from app.schemas import ProjectInput


# Base project inputs for consistency
BASE_INPUTS = {
    "name": "Test Project",
    "budget": 500000,
    "co2_reduction": 500,
    "social_impact": 10,
    "duration_months": 36,
}


class TestE4BiodiversityCategories:
    """Test E4_Biodiversity scoring with explicit categories."""

    def test_reforestation_category_scores_high(self):
        """CONTROL: Valid reforestation project gets high E4 score."""
        proj = ProjectInput(**BASE_INPUTS, category="reforestation")
        result = assess_csrd(proj)
        e4 = result["categories"]["E4_Biodiversity"]
        assert e4["score"] == 85.0, "reforestation should get biodiversity score"
        assert result["audit_ready"] is True

    def test_biodiversity_category_scores_high(self):
        """CONTROL: Valid biodiversity project gets high E4 score."""
        proj = ProjectInput(**BASE_INPUTS, category="biodiversity")
        result = assess_csrd(proj)
        e4 = result["categories"]["E4_Biodiversity"]
        assert e4["score"] == 85.0, "biodiversity should get biodiversity score"
        assert result["audit_ready"] is True

    def test_agro_category_scores_high(self):
        """Training data 'agro' category should count as biodiversity."""
        proj = ProjectInput(**BASE_INPUTS, category="agro")
        result = assess_csrd(proj)
        e4 = result["categories"]["E4_Biodiversity"]
        assert e4["score"] == 85.0, "agro should count as biodiversity"

    def test_no_reforestation_planned_scores_low(self):
        """RED-FIRST: Text negating reforestation should NOT get biodiversity score.

        Defect: "refor" substring matches "no reforestation is planned",
        giving false E4=90 and audit_ready=true.
        Fixed: Explicit category list rejects arbitrary text.
        """
        proj = ProjectInput(**BASE_INPUTS, category="no reforestation is planned")
        result = assess_csrd(proj)
        e4 = result["categories"]["E4_Biodiversity"]
        assert e4["score"] == 50.0, "negation of reforestation should NOT count"
        assert result["audit_ready"] is False, "should not be audit ready with low E4"

    def test_reform_of_procurement_scores_low(self):
        """RED-FIRST: 'reform' is not 'reforestation' despite containing 'refor'.

        Defect: "refor" substring matches "reform of procurement",
        giving false biodiversity credit.
        Fixed: Explicit category list rejects unrelated text.
        """
        proj = ProjectInput(**BASE_INPUTS, category="reform of procurement")
        result = assess_csrd(proj)
        e4 = result["categories"]["E4_Biodiversity"]
        assert e4["score"] == 50.0, "reform is not reforestation"
        assert result["audit_ready"] is False

    def test_water_only_category_scores_low(self):
        """CONTROL: Water project without biodiversity gets low E4 score."""
        proj = ProjectInput(**BASE_INPUTS, category="water")
        result = assess_csrd(proj)
        e4 = result["categories"]["E4_Biodiversity"]
        assert e4["score"] == 50.0, "water alone should not count as biodiversity"

    def test_deforestation_risk_high_scores_low(self):
        """RED-FIRST: Stating deforestation risk should NOT count as biodiversity action.

        The finding: 'water, deforestation risk high' gets E4=55 (no match).
        But any substring with 'refor' would match. This verifies the explicit
        list rejects risk statements.
        """
        proj = ProjectInput(**BASE_INPUTS, category="water, deforestation risk high")
        result = assess_csrd(proj)
        e4 = result["categories"]["E4_Biodiversity"]
        assert e4["score"] == 50.0, "risk mention is not action"

    def test_unknown_category_scores_low(self):
        """Training data 'Unknown' category should get base score."""
        proj = ProjectInput(**BASE_INPUTS, category="Unknown")
        result = assess_csrd(proj)
        e4 = result["categories"]["E4_Biodiversity"]
        assert e4["score"] == 50.0

    def test_unmapped_category_scores_low(self):
        """Arbitrary unmapped category should get base score."""
        proj = ProjectInput(**BASE_INPUTS, category="quantum computing")
        result = assess_csrd(proj)
        e4 = result["categories"]["E4_Biodiversity"]
        assert e4["score"] == 50.0, "unmapped category gets neutral score"

    def test_none_category_scores_low(self):
        """None category should get base score."""
        proj = ProjectInput(**BASE_INPUTS, category=None)
        result = assess_csrd(proj)
        e4 = result["categories"]["E4_Biodiversity"]
        assert e4["score"] == 50.0


class TestE3WaterCategories:
    """Test E3_Water scoring with explicit categories."""

    def test_water_category_scores_high(self):
        """CONTROL: Water project gets high E3 score."""
        inputs = {**BASE_INPUTS, "category": "water", "social_impact": 5}
        proj = ProjectInput(**inputs)
        result = assess_csrd(proj)
        e3 = result["categories"]["E3_Water"]
        # base 80 + 5*1.5 = 87.5
        assert e3["score"] == 87.5, "water category should score high on E3"

    def test_solar_energy_scores_medium(self):
        """CONTROL: Solar energy gets medium E3 score (renewable but not water)."""
        inputs = {**BASE_INPUTS, "category": "Solar Energy", "social_impact": 5}
        proj = ProjectInput(**inputs)
        result = assess_csrd(proj)
        e3 = result["categories"]["E3_Water"]
        # base 55 + 5*1.5 = 62.5
        assert e3["score"] == 62.5, "solar should get renewable score"

    def test_energy_category_scores_medium(self):
        """Training 'energy' category should get renewable score."""
        inputs = {**BASE_INPUTS, "category": "energy", "social_impact": 5}
        proj = ProjectInput(**inputs)
        result = assess_csrd(proj)
        e3 = result["categories"]["E3_Water"]
        assert e3["score"] == 62.5

    def test_wind_energy_scores_medium(self):
        """Wind energy gets medium E3 score."""
        inputs = {**BASE_INPUTS, "category": "Wind Energy", "social_impact": 5}
        proj = ProjectInput(**inputs)
        result = assess_csrd(proj)
        e3 = result["categories"]["E3_Water"]
        assert e3["score"] == 62.5

    def test_wastewater_does_not_match_water(self):
        """RED-FIRST: 'wastewater' contains 'water' but may not be a water project.

        With explicit categories, unmapped text gets the base score.
        """
        inputs = {**BASE_INPUTS, "category": "wastewater treatment", "social_impact": 5}
        proj = ProjectInput(**inputs)
        result = assess_csrd(proj)
        e3 = result["categories"]["E3_Water"]
        # Unmapped: base 45 + 5*1.5 = 52.5
        assert e3["score"] == 52.5, "unmapped substring should not match"

    def test_waste_category_scores_low(self):
        """Training 'waste' category (not water) gets base score."""
        inputs = {**BASE_INPUTS, "category": "waste", "social_impact": 5}
        proj = ProjectInput(**inputs)
        result = assess_csrd(proj)
        e3 = result["categories"]["E3_Water"]
        assert e3["score"] == 52.5


class TestLatLonDeadConditionRemoved:
    """Verify that the dead lat/lon condition is removed.

    Finding: ProjectInput defaults lat=50.0, lon=10.0, so the condition
    `if p.lat is not None and p.lon is not None: base += 5.0` was always true,
    adding 5 points to everyone.

    Fix: Removed the condition. E4 base scores are 85.0 or 50.0, never 90.0 or 55.0.
    """

    def test_e4_score_without_lat_lon_bonus(self):
        """E4 scores should be 85 or 50, never 90 or 55 (old lat/lon bonus)."""
        # Biodiversity category
        proj_bio = ProjectInput(**BASE_INPUTS, category="reforestation")
        result_bio = assess_csrd(proj_bio)
        assert result_bio["categories"]["E4_Biodiversity"]["score"] == 85.0, \
            "biodiversity score should be 85, not 90 (no lat/lon bonus)"

        # Non-biodiversity
        proj_other = ProjectInput(**BASE_INPUTS, category="waste")
        result_other = assess_csrd(proj_other)
        assert result_other["categories"]["E4_Biodiversity"]["score"] == 50.0, \
            "base score should be 50, not 55 (no lat/lon bonus)"

    def test_none_lat_lon_same_as_provided(self):
        """With lat/lon removed, None vs provided should make no difference."""
        proj_none = ProjectInput(**BASE_INPUTS, category="agro", lat=None, lon=None)
        proj_set = ProjectInput(**BASE_INPUTS, category="agro", lat=50.0, lon=10.0)

        result_none = assess_csrd(proj_none)
        result_set = assess_csrd(proj_set)

        assert result_none["categories"]["E4_Biodiversity"]["score"] == \
               result_set["categories"]["E4_Biodiversity"]["score"], \
               "lat/lon should not affect E4 score"


class TestCaseInsensitivity:
    """Verify category matching is case-insensitive."""

    @pytest.mark.parametrize("category", [
        "Reforestation",
        "REFORESTATION",
        "rEfOrEsTaTiOn",
        "Water",
        "WATER",
        "Solar Energy",
        "SOLAR ENERGY",
    ])
    def test_case_insensitive_matching(self, category):
        """Categories should match regardless of case."""
        proj = ProjectInput(**BASE_INPUTS, category=category)
        result = assess_csrd(proj)

        cat_lower = category.lower()
        if cat_lower in ("reforestation", "agro", "biodiversity"):
            assert result["categories"]["E4_Biodiversity"]["score"] == 85.0
        elif cat_lower == "water":
            assert result["categories"]["E3_Water"]["score"] >= 80.0
        elif cat_lower in ("solar energy", "wind energy", "energy"):
            assert result["categories"]["E3_Water"]["score"] >= 55.0


class TestGapMessages:
    """Verify gap messages match the new logic."""

    def test_biodiversity_gap_message(self):
        """Non-biodiversity project should get biodiversity gap message."""
        proj = ProjectInput(**BASE_INPUTS, category="waste")
        result = assess_csrd(proj)
        gaps = result["categories"]["E4_Biodiversity"]["gaps"]
        assert "No biodiversity impact assessment" in gaps

    def test_water_gap_message(self):
        """Non-water project should get water gap message."""
        proj = ProjectInput(**BASE_INPUTS, category="waste")
        result = assess_csrd(proj)
        gaps = result["categories"]["E3_Water"]["gaps"]
        assert "Missing water risk assessment" in gaps
