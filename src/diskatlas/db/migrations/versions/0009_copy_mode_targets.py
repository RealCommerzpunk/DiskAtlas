"""copy permission per share and default copy target per client

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-27 16:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0009'
down_revision: str | None = '0008'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('disk_shares', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('copy_mode', sa.String(length=10), server_default='never', nullable=False)
        )
        batch_op.create_check_constraint(
            'ck_share_copy_mode', "copy_mode IN ('never', 'ask', 'always')"
        )
    op.create_table(
        'client_copy_targets',
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('disk_id', sa.Integer(), nullable=True),
        sa.Column('volume_id', sa.Integer(), nullable=True),
        sa.Column('path', sa.Text(), server_default='', nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['disk_id'], ['disks.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['volume_id'], ['volumes.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('client_id'),
    )


def downgrade() -> None:
    op.drop_table('client_copy_targets')
    with op.batch_alter_table('disk_shares', schema=None) as batch_op:
        batch_op.drop_constraint('ck_share_copy_mode', type_='check')
        batch_op.drop_column('copy_mode')
