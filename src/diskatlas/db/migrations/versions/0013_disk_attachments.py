"""disk attachments (wipe reports)

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-25 12:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0013'
down_revision: str | None = '0012'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'disk_attachments',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('disk_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('size', sa.BigInteger(), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('uploaded_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['disk_id'], ['disks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['uploaded_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_disk_attachments_disk_id', 'disk_attachments', ['disk_id'])


def downgrade() -> None:
    op.drop_index('ix_disk_attachments_disk_id', table_name='disk_attachments')
    op.drop_table('disk_attachments')
