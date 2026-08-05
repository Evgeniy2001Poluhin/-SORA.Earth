"""relabel indicators the source never published

Issue #97. `EN.ATM.CO2E.PC` and `GE.EST` do not exist at the World Bank:

    GET /v2/country/{iso}/indicator/EN.ATM.CO2E.PC
    200 OK
    [{"message": [{"id": "175", "key": "Invalid format",
                   "value": "The indicator was not found. It may have been
                             deleted or archived."}]}]

`GE.EST` is archived under source 57 (WDI Database Archives), along with every
sibling -- GE.PER.RNK, CC.EST, RL.EST, RQ.EST refuse identically.

So nothing was ever fetched for either. Every value stored under those codes
came from the static benchmark fallback in app/country_benchmarks.py: 15,316
rows each, none of them dated, wearing a World Bank indicator code.

The `source` column recorded the truth all along -- `benchmark`, not
`world_bank`. The label did not. A row saying `indicator_code =
'EN.ATM.CO2E.PC'` reads as a World Bank CO2 observation to anyone querying by
code, which is exactly what the forecasting feature builder does.

This renames the code on rows that did not come from the World Bank, and
leaves any row that did. The rename is deliberately keyed on `source` rather
than on the code alone: if a genuine world_bank row for either code ever
existed, it stays as it is and the count below will show it.

Values, periods and fetch times are untouched -- the trigger from #90 forbids
changing `value` or `fetched_at`, and this does not attempt to. Only the label
changes, from a claim that was never true to one that is.

Revision ID: c58e21a9f7d4
Revises: a91d7c4e28b6
Create Date: 2026-08-06

"""
import sqlalchemy as sa
from alembic import op

revision = "c58e21a9f7d4"
down_revision = "a91d7c4e28b6"
branch_labels = None
depends_on = None

RENAMES = {
    "EN.ATM.CO2E.PC": "benchmark:co2_per_capita",
    "GE.EST": "benchmark:gov_effectiveness",
}


def upgrade() -> None:
    conn = op.get_bind()
    for old, new in RENAMES.items():
        result = conn.execute(
            sa.text(
                "UPDATE country_indicator_history "
                "   SET indicator_code = :new "
                " WHERE indicator_code = :old "
                "   AND source <> 'world_bank'"
            ),
            {"old": old, "new": new},
        )
        print(f"  {old} -> {new}: {result.rowcount} row(s) relabelled")

        remaining = conn.execute(
            sa.text(
                "SELECT count(*) FROM country_indicator_history "
                " WHERE indicator_code = :old"
            ),
            {"old": old},
        ).scalar()
        # Not an error: a world_bank row for a code the World Bank refuses
        # would be worth knowing about, and silently renaming it would destroy
        # the evidence.
        if remaining:
            print(f"  {remaining} row(s) still carry {old} with "
                  f"source='world_bank' -- left as they are")


def downgrade() -> None:
    conn = op.get_bind()
    for old, new in RENAMES.items():
        conn.execute(
            sa.text(
                "UPDATE country_indicator_history "
                "   SET indicator_code = :old "
                " WHERE indicator_code = :new"
            ),
            {"old": old, "new": new},
        )
