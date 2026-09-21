"""profile_update_events 画像更新历史表(US-05)

Revision ID: 462d3a7e1d90
Revises: f9fc8dd8995c
Create Date: 2026-09-21 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '462d3a7e1d90'
down_revision: Union[str, None] = 'f9fc8dd8995c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('profile_update_events',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('trigger', sa.String(length=20), nullable=False),
        sa.Column('changes', sa.JSON(), nullable=True),
        sa.Column('conflicts', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_profile_update_events_user_id'), 'profile_update_events', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_profile_update_events_user_id'), table_name='profile_update_events')
    op.drop_table('profile_update_events')
