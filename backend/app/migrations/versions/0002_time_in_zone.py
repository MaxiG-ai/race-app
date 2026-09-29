"""time in HR and power zones per recorded activity

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-24 09:00:00

Existing rows stay null: the zones were never stored, so `cli reimport` fills them in
from the kept originals.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "recorded_activity",
        sa.Column("time_in_zone", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("recorded_activity", "time_in_zone")
