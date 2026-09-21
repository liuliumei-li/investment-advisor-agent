"""chat_sessions / chat_messages / advices / data_citations / agent_runs / compliance_audit_logs(US-06)

Revision ID: 6a6e62716301
Revises: 462d3a7e1d90
Create Date: 2026-09-21 14:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '6a6e62716301'
down_revision: Union[str, None] = '462d3a7e1d90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _bigint() -> sa.BigInteger:
    return sa.BigInteger().with_variant(sa.Integer(), 'sqlite')


def upgrade() -> None:
    op.create_table('chat_sessions',
        sa.Column('id', _bigint(), autoincrement=True, nullable=False),
        sa.Column('user_id', _bigint(), nullable=False),
        sa.Column('scenario', sa.Enum('market', 'industry', 'stock', 'etf', 'cb', 'portfolio', 'general', name='scenario'), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('last_active_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_chat_sessions_user_id'), 'chat_sessions', ['user_id'], unique=False)
    op.create_table('chat_messages',
        sa.Column('id', _bigint(), autoincrement=True, nullable=False),
        sa.Column('session_id', _bigint(), nullable=False),
        sa.Column('user_id', _bigint(), nullable=False),
        sa.Column('role', sa.Enum('user', 'assistant', name='messagerole'), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('advice_id', _bigint(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['advice_id'], ['advices.id'], ),
        sa.ForeignKeyConstraint(['session_id'], ['chat_sessions.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_chat_messages_session_id'), 'chat_messages', ['session_id'], unique=False)
    op.create_table('advices',
        sa.Column('id', _bigint(), autoincrement=True, nullable=False),
        sa.Column('session_id', _bigint(), nullable=False),
        sa.Column('user_id', _bigint(), nullable=False),
        sa.Column('scenario', sa.Enum('market', 'industry', 'stock', 'etf', 'cb', 'portfolio', 'general', name='scenario'), nullable=False),
        sa.Column('conclusion', sa.Text(), nullable=False),
        sa.Column('logic_chain', sa.JSON(), nullable=True),
        sa.Column('risk_tips', sa.Text(), nullable=False),
        sa.Column('position_suggestion', sa.JSON(), nullable=True),
        sa.Column('return_expectation', sa.JSON(), nullable=True),
        sa.Column('divergence_summary', sa.JSON(), nullable=True),
        sa.Column('compliance_status', sa.Enum('pending', 'passed', 'rejected', name='compliancestatus'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['chat_sessions.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_advices_session_id'), 'advices', ['session_id'], unique=False)
    op.create_index(op.f('ix_advices_user_id'), 'advices', ['user_id'], unique=False)
    op.create_table('data_citations',
        sa.Column('id', _bigint(), autoincrement=True, nullable=False),
        sa.Column('advice_id', _bigint(), nullable=False),
        sa.Column('source_name', sa.String(length=100), nullable=False),
        sa.Column('source_type', sa.Enum('quote', 'news', 'research', 'profile', name='sourcetype'), nullable=False),
        sa.Column('data_point', sa.Text(), nullable=False),
        sa.Column('source_url', sa.String(length=500), nullable=False),
        sa.Column('data_timestamp', sa.String(length=40), nullable=False),
        sa.Column('verified', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['advice_id'], ['advices.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_data_citations_advice_id'), 'data_citations', ['advice_id'], unique=False)
    op.create_table('agent_runs',
        sa.Column('id', _bigint(), autoincrement=True, nullable=False),
        sa.Column('advice_id', _bigint(), nullable=False),
        sa.Column('coordinator_trace', sa.JSON(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['advice_id'], ['advices.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('advice_id')
    )
    op.create_table('compliance_audit_logs',
        sa.Column('id', _bigint(), autoincrement=True, nullable=False),
        sa.Column('advice_id', _bigint(), nullable=False),
        sa.Column('passed', sa.Boolean(), nullable=False),
        sa.Column('matched_rules', sa.JSON(), nullable=True),
        sa.Column('action', sa.Enum('pass', 'rewrite', 'reject', name='complianceaction'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['advice_id'], ['advices.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_compliance_audit_logs_advice_id'), 'compliance_audit_logs', ['advice_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_compliance_audit_logs_advice_id'), table_name='compliance_audit_logs')
    op.drop_table('compliance_audit_logs')
    op.drop_table('agent_runs')
    op.drop_index(op.f('ix_data_citations_advice_id'), table_name='data_citations')
    op.drop_table('data_citations')
    op.drop_index(op.f('ix_advices_user_id'), table_name='advices')
    op.drop_index(op.f('ix_advices_session_id'), table_name='advices')
    op.drop_table('advices')
    op.drop_index(op.f('ix_chat_messages_session_id'), table_name='chat_messages')
    op.drop_table('chat_messages')
    op.drop_index(op.f('ix_chat_sessions_user_id'), table_name='chat_sessions')
    op.drop_table('chat_sessions')
