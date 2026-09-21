"""user_profiles 增加 source_trace 列(逐要素溯源与待确认冲突,US-04)

Revision ID: f9fc8dd8995c
Revises: 7e2c04d91a8f
Create Date: 2026-09-21 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f9fc8dd8995c'
down_revision: Union[str, None] = '7e2c04d91a8f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('user_profiles', sa.Column('source_trace', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('user_profiles', 'source_trace')
