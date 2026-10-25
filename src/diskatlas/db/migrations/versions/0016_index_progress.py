"""track running index progress (stale detection)

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-25 21:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0016'
down_revision: str | None = '0015'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('volumes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('pending_scan_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('index_touched_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('volumes', schema=None) as batch_op:
        batch_op.drop_column('index_touched_at')
        batch_op.drop_column('pending_scan_id')
