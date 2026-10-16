import re
from pathlib import Path

from app.ingesters.source_register import SOURCE_REGISTER


SPEC = Path(__file__).resolve().parents[1] / "docs" / "DATA_TRUST_IMPLEMENTATION_SPEC.md"


def _baseline_sources(text: str) -> set[str]:
    return set(re.findall(r"^\| `([^`]+)` \|", text, re.MULTILINE))


def test_the_data_trust_baseline_covers_the_real_source_register_exactly():
    text = SPEC.read_text(encoding="utf-8")

    assert _baseline_sources(text) == set(SOURCE_REGISTER)


def test_the_spec_refuses_to_invent_historical_or_legal_provenance():
    text = SPEC.read_text(encoding="utf-8")

    assert "No snapshot id is invented retroactively" in text
    assert "explicit `unverified` rights state" in text
    assert "do not fabricate snapshot ids" in text


def test_the_spec_requires_contract_snapshot_run_and_user_evidence_layers():
    text = SPEC.read_text(encoding="utf-8")

    for heading in (
        "### PR 1 — source contracts",
        "### PR 2 — snapshot store",
        "### PR 3 — run linkage",
        "### PR 4 — evidence API and UI",
    ):
        assert heading in text

    assert "Source contract set equals `SOURCE_REGISTER` exactly" in text
    assert "Every new training/evaluation run stores at least one verified snapshot id" in text
    assert "result → run → snapshot → source contract" in text
