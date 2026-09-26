"""directory index for the file browser

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-27 15:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0008'
down_revision: str | None = '0007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('volumes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('dirs_scan_id', sa.String(length=36), nullable=True))
    with op.batch_alter_table('files', schema=None) as batch_op:
        batch_op.add_column(sa.Column('parent', sa.Text(), server_default='', nullable=False))
    # Ordner = Pfad ohne den letzten Teil (Name). Pfade ohne "/" liegen in der Wurzel ('').
    op.execute(
        "UPDATE files SET parent = substr(path, 1, length(path) - length(name) - 1) "
        "WHERE instr(path, '/') > 0 AND length(path) > length(name) "
        "AND substr(path, length(path) - length(name) + 1) = name"
    )
    op.create_index('ix_files_parent', 'files', ['volume_id', 'scan_id', 'parent', 'name'])
    op.create_table(
        'directories',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'),
                  autoincrement=True, nullable=False),
        sa.Column('volume_id', sa.Integer(), nullable=False),
        sa.Column('scan_id', sa.String(length=36), nullable=False),
        sa.Column('path', sa.Text(), nullable=False),
        sa.Column('parent', sa.Text(), nullable=False),
        sa.Column('name', sa.String(length=1024), nullable=False),
        sa.Column('file_count', sa.Integer(), nullable=False),
        sa.Column('total_size', sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(['volume_id'], ['volumes.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('volume_id', 'scan_id', 'path', name='uq_directory_path'),
    )
    op.create_index('ix_directories_parent', 'directories',
                    ['volume_id', 'scan_id', 'parent', 'name'])


def downgrade() -> None:
    op.drop_index('ix_directories_parent', table_name='directories')
    op.drop_table('directories')
    op.drop_index('ix_files_parent', table_name='files')
    with op.batch_alter_table('files', schema=None) as batch_op:
        batch_op.drop_column('parent')
    with op.batch_alter_table('volumes', schema=None) as batch_op:
        batch_op.drop_column('dirs_scan_id')
