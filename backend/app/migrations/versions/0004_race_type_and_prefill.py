"""race type, the incomplete override, and prefilled / confirmed official results

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-17 12:00:00

Backfill:
- A race whose recordings are all one sport leg (the old way of making a run race: grouping
  a single upload) becomes that single-sport race, unless its typed result has splits for
  other legs (then it is a triathlon with lost files). Everything else is a triathlon. A
  single-sport race's one split is its total, so a typed result gets that split set.
- Results already in the database were typed in by hand from the official results, so they
  count as confirmed.
- Races without a result get one prefilled from their primary recordings, exactly as an
  import would make it now (services/results.py `prefill`).
"""

from collections import defaultdict
from collections.abc import Sequence
from datetime import timedelta

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEGMENTS = ("swim", "t1", "bike", "t2", "run")


def upgrade() -> None:
    op.add_column(
        "race",
        sa.Column(
            "race_type",
            sa.Enum(
                "triathlon", "swim", "bike", "run", name="racetype", native_enum=False, length=20
            ),
            nullable=False,
            server_default="triathlon",
        ),
    )
    op.add_column(
        "race",
        sa.Column("marked_complete", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "official_result",
        sa.Column("is_prefilled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "official_result",
        sa.Column("is_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE race SET race_type = legs.segment
            FROM (
                SELECT race_id, min(segment) AS segment FROM recorded_activity
                GROUP BY race_id
                HAVING count(DISTINCT segment) = 1 AND min(segment) IN ('swim', 'bike', 'run')
            ) AS legs
            WHERE race.id = legs.race_id
              AND NOT EXISTS (
                SELECT 1 FROM official_result o WHERE o.race_id = race.id AND (
                    (legs.segment <> 'swim' AND o.swim_time IS NOT NULL)
                    OR o.t1_time IS NOT NULL OR o.t2_time IS NOT NULL
                    OR (legs.segment <> 'bike' AND o.bike_time IS NOT NULL)
                    OR (legs.segment <> 'run' AND o.run_time IS NOT NULL)))
            """
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE official_result o SET
                swim_time = CASE WHEN r.race_type = 'swim' THEN o.total_time END,
                t1_time = NULL, t2_time = NULL,
                bike_time = CASE WHEN r.race_type = 'bike' THEN o.total_time END,
                run_time = CASE WHEN r.race_type = 'run' THEN o.total_time END,
                total_is_derived = false
            FROM race r WHERE r.id = o.race_id AND r.race_type <> 'triathlon'
            """
        )
    )
    conn.execute(sa.text("UPDATE official_result SET is_confirmed = true"))
    _prefill_missing_results(conn)

    for table, column in (
        ("race", "race_type"),
        ("race", "marked_complete"),
        ("official_result", "is_prefilled"),
        ("official_result", "is_confirmed"),
    ):
        op.alter_column(table, column, server_default=None)


def _prefill_missing_results(conn) -> None:
    rows = conn.execute(
        sa.text(
            """
            SELECT a.race_id, r.race_type, a.segment, a.start_time, a.elapsed_time
            FROM recorded_activity a JOIN race r ON r.id = a.race_id
            WHERE a.is_primary
              AND NOT EXISTS (SELECT 1 FROM official_result o WHERE o.race_id = a.race_id)
            """
        )
    ).all()
    by_race: dict[int, dict] = defaultdict(lambda: {"type": None, "legs": {}})
    for race_id, race_type, segment, start, elapsed in rows:
        by_race[race_id]["type"] = race_type
        by_race[race_id]["legs"][segment] = (start, elapsed)
    for race_id, info in by_race.items():
        expected = SEGMENTS if info["type"] == "triathlon" else (info["type"],)
        legs = {s: v for s, v in info["legs"].items() if s in expected}
        if not legs:
            continue
        complete = len(legs) == len(expected)
        if complete:
            total = sum(elapsed for _, elapsed in legs.values())
        else:
            first = min(start for start, _ in legs.values())
            last = max(start + timedelta(seconds=elapsed) for start, elapsed in legs.values())
            total = round((last - first).total_seconds())
        values = {f"{s}_time": legs[s][1] if s in legs else None for s in SEGMENTS}
        conn.execute(
            sa.text(
                """
                INSERT INTO official_result (race_id, total_time, total_is_derived,
                    swim_time, t1_time, bike_time, t2_time, run_time,
                    is_prefilled, is_confirmed, entered_at)
                VALUES (:race_id, :total, :derived,
                    :swim_time, :t1_time, :bike_time, :t2_time, :run_time,
                    true, false, now())
                """
            ),
            {
                "race_id": race_id,
                "total": total,
                "derived": complete and len(expected) > 1,
                **values,
            },
        )


def downgrade() -> None:
    op.execute("DELETE FROM official_result WHERE is_prefilled")
    op.drop_column("official_result", "is_confirmed")
    op.drop_column("official_result", "is_prefilled")
    op.drop_column("race", "marked_complete")
    op.drop_column("race", "race_type")
