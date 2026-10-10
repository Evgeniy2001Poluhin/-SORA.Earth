"""Test that the baseline generator reproduces the baseline it made."""
import json
from pathlib import Path
import pytest


def test_generator_countries_and_presets_match_baseline():
    """The generator's COUNTRIES x PRESETS must equal the baseline's (country, preset) pairs."""
    # Import generator constants (no side effects now)
    from tests.generate_baseline_testclient import COUNTRIES, PRESETS

    # Load baseline
    baseline_path = Path(__file__).parent / "baseline_scores.json"
    baseline = json.loads(baseline_path.read_text())

    # Extract (country, preset) pairs from baseline
    baseline_pairs = {(case["country"], case["preset"]) for case in baseline["cases"]}

    # Generate expected pairs from generator constants
    generator_pairs = {(country, preset_name) for country in COUNTRIES for preset_name in PRESETS.keys()}

    assert generator_pairs == baseline_pairs, (
        f"Generator pairs != baseline pairs.\n"
        f"Missing from generator: {baseline_pairs - generator_pairs}\n"
        f"Extra in generator: {generator_pairs - baseline_pairs}"
    )


def test_generator_payloads_match_baseline():
    """Each preset's payload in the generator must match the baseline payload."""
    from tests.generate_baseline_testclient import COUNTRIES, PRESETS

    baseline_path = Path(__file__).parent / "baseline_scores.json"
    baseline = json.loads(baseline_path.read_text())

    # Build lookup: (country, preset) -> payload
    baseline_payloads = {
        (case["country"], case["preset"]): case["payload"]
        for case in baseline["cases"]
    }

    # Check each generator payload matches baseline
    for country in COUNTRIES:
        for preset_name, preset in PRESETS.items():
            generator_payload = {
                "project_name": preset_name,
                "country": country,
                **preset
            }
            baseline_payload = baseline_payloads[(country, preset_name)]

            assert generator_payload == baseline_payload, (
                f"Payload mismatch for {country}/{preset_name}:\n"
                f"Generator: {generator_payload}\n"
                f"Baseline:  {baseline_payload}"
            )
