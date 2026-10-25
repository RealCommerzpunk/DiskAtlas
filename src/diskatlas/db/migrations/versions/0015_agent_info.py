"""agent version and platform per client

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-25 20:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0015'
down_revision: str | None = '0014'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('clients', schema=None) as batch_op:
        batch_op.add_column(sa.Column('agent_version', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('agent_platform', sa.String(length=20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('clients', schema=None) as batch_op:
        batch_op.drop_column('agent_platform')
        batch_op.drop_column('agent_version')
