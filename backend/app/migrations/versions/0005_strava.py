"""strava connection and pulled activities

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-21 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("owner", sa.Column("strava_athlete_id", sa.BigInteger(), nullable=True))
    op.create_unique_constraint("uq_owner_strava_athlete_id", "owner", ["strava_athlete_id"])
    op.add_column(
        "site_settings",
        sa.Column("strava_tagline", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "site_settings",
        sa.Column("strava_public_consent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "strava_account",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("athlete_id", sa.BigInteger(), nullable=False),
        sa.Column("athlete_name", sa.String(), nullable=True),
        sa.Column("access_token", sa.String(), nullable=False),
        sa.Column("refresh_token", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_pull_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("needs_reconnect", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("athlete_id"),
    )
    op.create_table(
        "strava_activity",
        sa.Column("activity_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("sport_type", sa.String(), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("race_id", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("message", sa.String(), nullable=True),
        sa.Column("tagline", sa.String(), nullable=True),
        sa.Column("tagline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["race_id"], ["race.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("activity_id"),
    )
    op.create_index("ix_strava_activity_race_id", "strava_activity", ["race_id"])


def downgrade() -> None:
    op.drop_index("ix_strava_activity_race_id", table_name="strava_activity")
    op.drop_table("strava_activity")
    op.drop_table("strava_account")
    op.drop_column("site_settings", "strava_public_consent_at")
    op.drop_column("site_settings", "strava_tagline")
    op.drop_constraint("uq_owner_strava_athlete_id", "owner", type_="unique")
    op.drop_column("owner", "strava_athlete_id")
