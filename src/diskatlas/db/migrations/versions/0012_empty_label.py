"""automatic label for empty disks

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-25 10:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0012'
down_revision: str | None = '0011'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('empty_label', sa.String(length=100), server_default='leer', nullable=False)
        )
    with op.batch_alter_table('disks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('empty_label_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_disks_empty_label_id', 'labels', ['empty_label_id'], ['id'], ondelete='SET NULL'
        )


def downgrade() -> None:
    with op.batch_alter_table('disks', schema=None) as batch_op:
        batch_op.drop_constraint('fk_disks_empty_label_id', type_='foreignkey')
        batch_op.drop_column('empty_label_id')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('empty_label')
