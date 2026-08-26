"""seed race categories

Revision ID: 56c1c0a2d424
Revises: 2209863e2015
Create Date: 2026-08-24 13:56:56.964858

"""
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.models.base  # custom column types (GUID, JSONBType)
from app.services.categories import CATEGORY_SEEDS

# revision identifiers, used by Alembic.
revision: str = '56c1c0a2d424'
down_revision: str | Sequence[str] | None = '2209863e2015'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Seed the DTU distance categories.

    Data, not code: a boundary can be corrected with an UPDATE rather than a deploy, and
    races hold a foreign key to the row they matched.
    """
    categories = sa.table(
        "race_category",
        sa.column("id", app.models.base.GUID()),
        sa.column("key", sa.String),
        sa.column("label", sa.String),
        sa.column("kind", sa.String),
        sa.column("sort_order", sa.Integer),
        sa.column("swim_min_m", sa.Float),
        sa.column("swim_max_m", sa.Float),
        sa.column("bike_min_m", sa.Float),
        sa.column("bike_max_m", sa.Float),
        sa.column("run_min_m", sa.Float),
        sa.column("run_max_m", sa.Float),
        sa.column("tolerance_pct", sa.Float),
        sa.column("is_active", sa.Boolean),
    )
    op.bulk_insert(
        categories,
        [
            {
                "id": uuid.uuid4(),
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


def downgrade() -> None:
    keys = tuple(seed.key for seed in CATEGORY_SEEDS)
    op.execute(
        sa.text("DELETE FROM race_category WHERE key IN :keys").bindparams(
            sa.bindparam("keys", value=keys, expanding=True)
        )
    )
