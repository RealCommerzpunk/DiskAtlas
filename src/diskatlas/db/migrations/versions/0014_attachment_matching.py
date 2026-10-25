"""attachments: unassigned uploads, detected serial and document date

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-25 18:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0014'
down_revision: str | None = '0013'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('disk_attachments', schema=None) as batch_op:
        batch_op.alter_column('disk_id', existing_type=sa.Integer(), nullable=True)
        batch_op.add_column(sa.Column('detected_serial', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('document_date', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.execute("DELETE FROM disk_attachments WHERE disk_id IS NULL")
    with op.batch_alter_table('disk_attachments', schema=None) as batch_op:
        batch_op.drop_column('document_date')
        batch_op.drop_column('detected_serial')
        batch_op.alter_column('disk_id', existing_type=sa.Integer(), nullable=False)
