"""race categories replace race_type

Introduces the `race_categories` table (§9), seeds it from the published DTU distance
list, moves every race onto it, and drops the old free-choice `race_type` column.

Ordering matters: the table has to exist and be seeded before races can point at it, and
`race_type` can only be dropped once its values have been carried across.

Revision ID: e4c73cebf4d3
Revises: 0a1226f04a0c
Create Date: 2026-08-26 14:33:19.522374

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.services.categories import CATEGORY_SEEDS

revision: str = "e4c73cebf4d3"
down_revision: str | Sequence[str] | None = "0a1226f04a0c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: Old enum value → new category key. Every mapping is a real correspondence rather than
#: a guess: an Olympic-distance race *is* a Kurzdistanz (1.5 / 40 / 10), a half iron is a
#: Mitteldistanz (1.9 / 90 / 21), a full iron is a Langdistanz (3.8 / 180 / 42.2).
#: `custom` had no distance meaning at all, so it maps to nothing and those races are
#: left for the classifier to judge from their measurements.
_RACE_TYPE_TO_CATEGORY = {
    "SPRINT": "sprint",
    "OLYMPIC": "kurz",
    "HALF_IRON": "mittel",
    "FULL_IRON": "lang",
}


def upgrade() -> None:
    bind = op.get_bind()
    # `create_table` creates an enum type implicitly; `add_column` does not. Both are
    # created up front so the two paths behave the same.
    category_kind = postgresql.ENUM(
        "DISTANCE", "AGE_CLASS", "RELAY", name="category_kind", create_type=False
    )
    classification_source = postgresql.ENUM(
        "AUTO", "MANUAL", "UNCLASSIFIED", name="classification_source", create_type=False
    )
    category_kind.create(bind, checkfirst=True)
    classification_source.create(bind, checkfirst=True)

    categories = op.create_table(
        "race_categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("kind", category_kind, nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("swim_min_m", sa.Float(), nullable=True),
        sa.Column("swim_max_m", sa.Float(), nullable=True),
        sa.Column("bike_min_m", sa.Float(), nullable=True),
        sa.Column("bike_max_m", sa.Float(), nullable=True),
        sa.Column("run_min_m", sa.Float(), nullable=True),
        sa.Column("run_max_m", sa.Float(), nullable=True),
        sa.Column("tolerance_pct", sa.Float(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )

    op.bulk_insert(
        categories,
        [
            {
                "key": seed.key,
                "label": seed.label,
                "kind": seed.kind.name,
                "sort_order": seed.sort_order,
                "swim_min_m": seed.swim[0],
                "swim_max_m": seed.swim[1],
                "bike_min_m": seed.bike[0],
                "bike_max_m": seed.bike[1],
                "run_min_m": seed.run[0],
                "run_max_m": seed.run[1],
                "tolerance_pct": 10.0,
                "is_active": True,
            }
            for seed in CATEGORY_SEEDS
        ],
    )

    op.add_column("races", sa.Column("race_category_id", sa.Integer(), nullable=True))
    op.add_column("races", sa.Column("auto_category_id", sa.Integer(), nullable=True))
    op.add_column(
        "races",
        sa.Column(
            "classification_source",
            classification_source,
            server_default="UNCLASSIFIED",
            nullable=False,
        ),
    )
    op.add_column("races", sa.Column("classified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "races",
        sa.Column("classification_detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_foreign_key(
        "fk_races_race_category_id",
        "races",
        "race_categories",
        ["race_category_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_races_auto_category_id",
        "races",
        "race_categories",
        ["auto_category_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Carry the old choices across as MANUAL: the user picked those values explicitly, so
    # the classifier must not quietly overwrite them. Races that were `custom` — which is
    # what every imported race got — stay UNCLASSIFIED so the classifier can judge them
    # from their measured distances (`python -m app.cli reclassify`).
    for old_value, key in _RACE_TYPE_TO_CATEGORY.items():
        op.execute(
            sa.text(
                """
                UPDATE races
                   SET race_category_id = (SELECT id FROM race_categories WHERE key = :key),
                       classification_source = 'MANUAL'
                 WHERE race_type::text = :old_value
                """
            ).bindparams(key=key, old_value=old_value)
        )

    op.drop_column("races", "race_type")
    op.execute("DROP TYPE IF EXISTS race_type")


def downgrade() -> None:
    race_type = postgresql.ENUM(
        "SPRINT", "OLYMPIC", "HALF_IRON", "FULL_IRON", "CUSTOM", name="race_type"
    )
    race_type.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "races",
        sa.Column("race_type", race_type, autoincrement=False, nullable=True),
    )

    reverse = {key: old for old, key in _RACE_TYPE_TO_CATEGORY.items()}
    for key, old_value in reverse.items():
        op.execute(
            sa.text(
                """
                UPDATE races
                   SET race_type = CAST(:old_value AS race_type)
                 WHERE race_category_id = (SELECT id FROM race_categories WHERE key = :key)
                """
            ).bindparams(key=key, old_value=old_value)
        )
    # Anything the old enum could not express becomes `custom`, which is what it meant.
    op.execute("UPDATE races SET race_type = 'CUSTOM'::race_type WHERE race_type IS NULL")
    op.alter_column("races", "race_type", nullable=False)

    op.drop_constraint("fk_races_auto_category_id", "races", type_="foreignkey")
    op.drop_constraint("fk_races_race_category_id", "races", type_="foreignkey")
    op.drop_column("races", "classification_detail")
    op.drop_column("races", "classified_at")
    op.drop_column("races", "classification_source")
    op.drop_column("races", "auto_category_id")
    op.drop_column("races", "race_category_id")
    op.drop_table("race_categories")
    op.execute("DROP TYPE IF EXISTS category_kind")
    op.execute("DROP TYPE IF EXISTS classification_source")
