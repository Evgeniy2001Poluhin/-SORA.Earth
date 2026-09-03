"""What the fingerprint normaliser equates, and what it must still separate.

`scripts/pg_fingerprint_normalise.py` exists because `pg_get_constraintdef`
re-renders a CHECK expression after a dump/restore: the array cast moves from
the constructor to each element, the meaning is unchanged, and a strict
comparison therefore fails on every correct restore.

Loosening a comparison is how a comparison stops comparing, so the point of this
file is the second half: everything the drill is actually guarding against must
still come out different.
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "pg_fingerprint_normalise.py"

# The two renderings measured on a real drill, PostgreSQL 16, 2026-09-03.
BEFORE = (
    "constraint|environmental_observations ck_temporal_kind_known "
    "CHECK (((temporal_kind)::text = ANY ((ARRAY['observed'::character varying, "
    "'period'::character varying, 'not_applicable'::character varying])::text[])))"
)
AFTER = (
    "constraint|environmental_observations ck_temporal_kind_known "
    "CHECK (((temporal_kind)::text = ANY (ARRAY[('observed'::character varying)::text, "
    "('period'::character varying)::text, ('not_applicable'::character varying)::text])))"
)


def run(text):
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], input=text, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_the_two_renderings_of_one_constraint_agree():
    """The whole reason this exists."""
    assert run(BEFORE) == run(AFTER)


def test_a_changed_value_list_still_differs():
    shorter = BEFORE.replace(", 'not_applicable'::character varying", "")
    assert run(BEFORE) != run(shorter)


def test_a_changed_column_still_differs():
    other = BEFORE.replace("temporal_kind", "source")
    assert run(BEFORE) != run(other)


def test_a_changed_constraint_name_still_differs():
    renamed = BEFORE.replace("ck_temporal_kind_known", "ck_temporal_kind_other")
    assert run(BEFORE) != run(renamed)


def test_a_changed_table_still_differs():
    moved = BEFORE.replace("environmental_observations", "evaluations")
    assert run(BEFORE) != run(moved)


def test_lines_that_are_not_check_constraints_pass_through_untouched():
    """Columns, indexes, row counts and the data hash stay exact. Normalising
    them would hide a width change, a dropped index or a lost row."""
    others = "\n".join([
        "alembic|d2a7f4b81c65",
        "column|region_esg_scores.region_code character varying(64) null=NO default=-",
        "index|ix_region_esg_scores_id CREATE UNIQUE INDEX ix_region_esg_scores_id ON public.region_esg_scores USING btree (id)",
        "rows|region_esg_scores=85",
        "data:region_esg_scores|9f86d081884c7d659a2feaa0c55ad015",
        "constraint|region_esg_scores region_esg_scores_pkey PRIMARY KEY (id)",
    ])
    assert run(others) == others + "\n"


def test_a_narrowed_column_still_differs():
    """The convergence migration changes region_code's width; a fingerprint
    that could not see that would miss the thing it was built for."""
    wide = "column|region_esg_scores.region_code character varying(64) null=NO default=-"
    narrow = wide.replace("(64)", "(10)")
    assert run(wide) != run(narrow)


def test_the_precedence_blindness_is_real_and_recorded():
    """Not an accident, and not to be discovered during an incident.

    Casts and parentheses are removed, so within a CHECK expression the
    grouping of AND and OR is invisible. A dump/restore cannot regroup an
    expression — PostgreSQL re-renders the tree it parsed — so this is not a
    case the drill guards against. It is asserted here so that the limit is
    behaviour on record rather than a surprise.
    """
    one = "constraint|t c CHECK (((a = 1) AND (b = 2)) OR (c = 3))"
    two = "constraint|t c CHECK ((a = 1) AND ((b = 2) OR (c = 3)))"
    assert run(one) == run(two), (
        "the normaliser has become precedence-aware; that is an improvement, "
        "and this test and the docstring in the script should now say so"
    )
