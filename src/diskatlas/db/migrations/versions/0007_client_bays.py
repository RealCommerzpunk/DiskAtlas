"""client bays

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-27 10:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0007'
down_revision: str | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('clients', schema=None) as batch_op:
        batch_op.add_column(sa.Column('has_bays', sa.Boolean(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('bay_count', sa.Integer(), server_default='4', nullable=False))
        batch_op.add_column(sa.Column('bay_ports', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('bay_reverse', sa.Boolean(), server_default='0', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('clients', schema=None) as batch_op:
        batch_op.drop_column('bay_reverse')
        batch_op.drop_column('bay_ports')
        batch_op.drop_column('bay_count')
        batch_op.drop_column('has_bays')
