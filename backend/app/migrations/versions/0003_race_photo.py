"""race photos

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-24 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "race_photo",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("race_id", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("caption", sa.String(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("is_cover", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["race_id"], ["race.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("race_id", "content_hash"),
    )
    op.create_index("ix_race_photo_race_id", "race_photo", ["race_id"])
    op.create_index(
        "uq_race_photo_cover",
        "race_photo",
        ["race_id"],
        unique=True,
        postgresql_where=sa.text("is_cover"),
    )


def downgrade() -> None:
    op.drop_index("uq_race_photo_cover", table_name="race_photo")
    op.drop_index("ix_race_photo_race_id", table_name="race_photo")
    op.drop_table("race_photo")
