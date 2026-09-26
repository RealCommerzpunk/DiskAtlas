"""ownership shares transfers

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-26 20:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# SQLite kennt bei der ursprünglichen, unbenannten UNIQUE-Bedingung keinen Namen: Die
# Namenskonvention gibt ihr beim Neuaufbau der Tabelle einen, damit sie entfernt werden kann.
NAMING = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def _label_name_unique(bind) -> str:
    """Name der UNIQUE-Bedingung auf labels.name (PostgreSQL vergibt automatisch einen)."""
    for constraint in sa.inspect(bind).get_unique_constraints("labels"):
        if constraint["column_names"] == ["name"] and constraint.get("name"):
            return constraint["name"]
    return "uq_labels_name"


def upgrade() -> None:
    with op.batch_alter_table('disks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('owner_user_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_disks_owner_user_id', 'users', ['owner_user_id'], ['id'], ondelete='SET NULL')
        batch_op.create_index(batch_op.f('ix_disks_owner_user_id'), ['owner_user_id'], unique=False)

    old_unique = _label_name_unique(op.get_bind())
    with op.batch_alter_table('labels', schema=None, naming_convention=NAMING) as batch_op:
        batch_op.add_column(sa.Column('owner_user_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_labels_owner_user_id', 'users', ['owner_user_id'], ['id'], ondelete='CASCADE')
        batch_op.drop_constraint(old_unique, type_='unique')
        batch_op.create_unique_constraint('uq_labels_owner_name', ['owner_user_id', 'name'])
        batch_op.create_index(batch_op.f('ix_labels_owner_user_id'), ['owner_user_id'], unique=False)

    with op.batch_alter_table('host_states', schema=None) as batch_op:
        batch_op.add_column(sa.Column('user_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_host_states_user_id', 'users', ['user_id'], ['id'], ondelete='SET NULL')

    with op.batch_alter_table('commands', schema=None) as batch_op:
        batch_op.add_column(sa.Column('user_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_commands_user_id', 'users', ['user_id'], ['id'], ondelete='CASCADE')
        batch_op.create_index(batch_op.f('ix_commands_user_id'), ['user_id'], unique=False)

    op.create_table('disk_shares',
    sa.Column('disk_id', sa.Integer(), nullable=False),
    sa.Column('viewer_user_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['disk_id'], ['disks.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['viewer_user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('disk_id', 'viewer_user_id')
    )
    op.create_table('disk_transfer_requests',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('disk_id', sa.Integer(), nullable=False),
    sa.Column('from_user_id', sa.Integer(), nullable=True),
    sa.Column('to_user_id', sa.Integer(), nullable=False),
    sa.Column('requested_by_client_id', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('resolved_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['disk_id'], ['disks.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['from_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['requested_by_client_id'], ['clients.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['to_user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('disk_transfer_requests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_disk_transfer_requests_disk_id'), ['disk_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('disk_transfer_requests', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_disk_transfer_requests_disk_id'))
    op.drop_table('disk_transfer_requests')
    op.drop_table('disk_shares')

    with op.batch_alter_table('commands', schema=None, naming_convention=NAMING) as batch_op:
        batch_op.drop_index(batch_op.f('ix_commands_user_id'))
        batch_op.drop_constraint('fk_commands_user_id', type_='foreignkey')
        batch_op.drop_column('user_id')

    with op.batch_alter_table('host_states', schema=None, naming_convention=NAMING) as batch_op:
        batch_op.drop_constraint('fk_host_states_user_id', type_='foreignkey')
        batch_op.drop_column('user_id')

    with op.batch_alter_table('labels', schema=None, naming_convention=NAMING) as batch_op:
        batch_op.drop_index(batch_op.f('ix_labels_owner_user_id'))
        batch_op.drop_constraint('uq_labels_owner_name', type_='unique')
        batch_op.create_unique_constraint('uq_labels_name', ['name'])
        batch_op.drop_constraint('fk_labels_owner_user_id', type_='foreignkey')
        batch_op.drop_column('owner_user_id')

    with op.batch_alter_table('disks', schema=None, naming_convention=NAMING) as batch_op:
        batch_op.drop_index(batch_op.f('ix_disks_owner_user_id'))
        batch_op.drop_constraint('fk_disks_owner_user_id', type_='foreignkey')
        batch_op.drop_column('owner_user_id')
