"""holdings / holding_snapshots

Revision ID: 7e2c04d91a8f
Revises: b3305938d78b
Create Date: 2026-09-20 20:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '7e2c04d91a8f'
down_revision: Union[str, None] = 'b3305938d78b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('holding_snapshots',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_holding_snapshots_user_id'), 'holding_snapshots', ['user_id'], unique=False)
    op.create_table('holdings',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
        sa.Column('snapshot_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
        sa.Column('user_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
        sa.Column('asset_type', sa.Enum('stock', 'etf', 'cb', 'fund', name='assettype'), nullable=False),
        sa.Column('code', sa.String(length=20), nullable=True),
        sa.Column('name', sa.String(length=50), nullable=True),
        sa.Column('quantity', sa.Numeric(precision=16, scale=4), nullable=True),
        sa.Column('cost_price', sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['snapshot_id'], ['holding_snapshots.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_holdings_snapshot_id'), 'holdings', ['snapshot_id'], unique=False)
    op.create_index(op.f('ix_holdings_user_id'), 'holdings', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_holdings_user_id'), table_name='holdings')
    op.drop_index(op.f('ix_holdings_snapshot_id'), table_name='holdings')
    op.drop_table('holdings')
    op.drop_index(op.f('ix_holding_snapshots_user_id'), table_name='holding_snapshots')
    op.drop_table('holding_snapshots')
