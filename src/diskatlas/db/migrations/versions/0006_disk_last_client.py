"""disk last client

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-27 09:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAMING = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def upgrade() -> None:
    with op.batch_alter_table('disks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('last_client_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_disks_last_client_id', 'clients', ['last_client_id'], ['id'], ondelete='SET NULL')
        batch_op.create_index(batch_op.f('ix_disks_last_client_id'), ['last_client_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('disks', schema=None, naming_convention=NAMING) as batch_op:
        batch_op.drop_index(batch_op.f('ix_disks_last_client_id'))
        batch_op.drop_constraint('fk_disks_last_client_id', type_='foreignkey')
        batch_op.drop_column('last_client_id')
