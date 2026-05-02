"""Phase 5: add unique constraint on signal_score_snapshot.signal_id

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-08

signal_score_snapshot.signal_id needs a UNIQUE constraint so that
upsert_signal_snapshots() can use ON CONFLICT (signal_id) DO UPDATE.
Previously only a regular index existed.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_snapshot_signal_id",
        "signal_score_snapshot",
        ["signal_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_snapshot_signal_id",
        "signal_score_snapshot",
        type_="unique",
    )
