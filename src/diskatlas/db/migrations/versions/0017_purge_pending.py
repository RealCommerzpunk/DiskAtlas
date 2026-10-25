"""background purge of stale index rows

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-25 22:30:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0017'
down_revision: str | None = '0016'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('volumes', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('purge_pending', sa.Boolean(), server_default='0', nullable=False)
        )
    # Bestand: Reste früherer Läufe einmal im Hintergrund prüfen lassen
    op.execute("UPDATE volumes SET purge_pending = 1")


def downgrade() -> None:
    with op.batch_alter_table('volumes', schema=None) as batch_op:
        batch_op.drop_column('purge_pending')
