"""copy requests and items

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-27 17:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0010'
down_revision: str | None = '0009'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'copy_requests',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('requester_user_id', sa.Integer(), nullable=False),
        sa.Column('target_client_id', sa.Integer(), nullable=False),
        sa.Column('target_disk_id', sa.Integer(), nullable=False),
        sa.Column('target_volume_id', sa.Integer(), nullable=True),
        sa.Column('target_path', sa.Text(), server_default='', nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('cancelled_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['requester_user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['target_client_id'], ['clients.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['target_disk_id'], ['disks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['target_volume_id'], ['volumes.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_copy_requests_requester_user_id', 'copy_requests', ['requester_user_id'])
    op.create_table(
        'copy_items',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('request_id', sa.String(length=32), nullable=False),
        sa.Column('source_disk_id', sa.Integer(), nullable=False),
        sa.Column('source_volume_id', sa.Integer(), nullable=False),
        sa.Column('source_path', sa.Text(), nullable=False),
        sa.Column('name', sa.String(length=1024), nullable=False),
        sa.Column('size', sa.BigInteger(), nullable=False),
        sa.Column('mtime', sa.DateTime(), nullable=True),
        sa.Column('owner_user_id', sa.Integer(), nullable=True),
        sa.Column('state', sa.String(length=20), nullable=False),
        sa.Column('phase', sa.String(length=10), server_default='local', nullable=False),
        sa.Column('wait_reason', sa.String(length=200), nullable=True),
        sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
        sa.Column('lease_until', sa.DateTime(), nullable=True),
        sa.Column('bytes_done', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=True),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('result_name', sa.String(length=1024), nullable=True),
        sa.Column('approved_by_user_id', sa.Integer(), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['request_id'], ['copy_requests.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['source_disk_id'], ['disks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['source_volume_id'], ['volumes.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['approved_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_copy_items_request_id', 'copy_items', ['request_id'])
    op.create_index('ix_copy_items_owner_user_id', 'copy_items', ['owner_user_id'])
    op.create_index('ix_copy_items_state', 'copy_items', ['state'])


def downgrade() -> None:
    op.drop_table('copy_items')
    op.drop_table('copy_requests')
