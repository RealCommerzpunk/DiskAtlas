"""agent transfer: client capability/pubkey, item claim and relay state

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-27 19:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0011'
down_revision: str | None = '0010'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('clients', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('transfer_enabled', sa.Boolean(), server_default='0', nullable=False)
        )
        batch_op.add_column(sa.Column('pubkey', sa.String(length=64), nullable=True))
    with op.batch_alter_table('copy_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('claimed_by_client_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('relay_epk', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('relay_next', sa.Integer(), server_default='0', nullable=False))
        batch_op.add_column(
            sa.Column('relay_pending', sa.Boolean(), server_default='0', nullable=False)
        )
        batch_op.add_column(sa.Column('relay_blob', sa.String(length=32), nullable=True))
        batch_op.add_column(
            sa.Column('relay_bytes', sa.BigInteger(), server_default='0', nullable=False)
        )
        batch_op.add_column(sa.Column('relay_final', sa.Boolean(), server_default='0', nullable=False))
        batch_op.create_foreign_key(
            'fk_copy_items_claimed_by_client', 'clients', ['claimed_by_client_id'], ['id'],
            ondelete='SET NULL',
        )


def downgrade() -> None:
    with op.batch_alter_table('copy_items', schema=None) as batch_op:
        batch_op.drop_constraint('fk_copy_items_claimed_by_client', type_='foreignkey')
        batch_op.drop_column('relay_final')
        batch_op.drop_column('relay_bytes')
        batch_op.drop_column('relay_blob')
        batch_op.drop_column('relay_pending')
        batch_op.drop_column('relay_next')
        batch_op.drop_column('relay_epk')
        batch_op.drop_column('claimed_by_client_id')
    with op.batch_alter_table('clients', schema=None) as batch_op:
        batch_op.drop_column('pubkey')
        batch_op.drop_column('transfer_enabled')
