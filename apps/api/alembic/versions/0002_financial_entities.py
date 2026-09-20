"""Typed financial and delivery entities.

Funding, Allocation, FinancialTransaction, Delivery and Outcome previously existed only
as JSON blobs in a generic domain_entities table, so nothing could create or mutate one
and money had no type. They are now real records with integer minor units and an ISO
currency.
"""
import sqlalchemy as sa

from alembic import op

revision = "0002_financial_entities"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('allocations',
    sa.Column('external_id', sa.String(length=160), nullable=False),
    sa.Column('program_ref', sa.String(length=160), nullable=False),
    sa.Column('project_ref', sa.String(length=160), nullable=False),
    sa.Column('funding_ref', sa.String(length=160), nullable=False),
    sa.Column('purpose', sa.String(length=240), nullable=False),
    sa.Column('amount_minor', sa.BigInteger(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_allocations_external_id'), 'allocations', ['external_id'], unique=True)
    op.create_index(op.f('ix_allocations_funding_ref'), 'allocations', ['funding_ref'], unique=False)
    op.create_index(op.f('ix_allocations_program_ref'), 'allocations', ['program_ref'], unique=False)
    op.create_index(op.f('ix_allocations_project_ref'), 'allocations', ['project_ref'], unique=False)
    op.create_table('deliveries',
    sa.Column('external_id', sa.String(length=160), nullable=False),
    sa.Column('program_ref', sa.String(length=160), nullable=False),
    sa.Column('project_ref', sa.String(length=160), nullable=False),
    sa.Column('financial_transaction_ref', sa.String(length=160), nullable=True),
    sa.Column('item', sa.String(length=240), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('delivered_on', sa.String(length=10), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_deliveries_external_id'), 'deliveries', ['external_id'], unique=True)
    op.create_index(op.f('ix_deliveries_financial_transaction_ref'), 'deliveries', ['financial_transaction_ref'], unique=False)
    op.create_index(op.f('ix_deliveries_program_ref'), 'deliveries', ['program_ref'], unique=False)
    op.create_index(op.f('ix_deliveries_project_ref'), 'deliveries', ['project_ref'], unique=False)
    op.create_table('financial_transactions',
    sa.Column('external_id', sa.String(length=160), nullable=False),
    sa.Column('program_ref', sa.String(length=160), nullable=False),
    sa.Column('allocation_ref', sa.String(length=160), nullable=True),
    sa.Column('payer_ref', sa.String(length=160), nullable=False),
    sa.Column('payee_ref', sa.String(length=160), nullable=False),
    sa.Column('payee_name', sa.String(length=240), nullable=False),
    sa.Column('occurred_on', sa.String(length=10), nullable=False),
    sa.Column('memo', sa.String(length=400), nullable=False),
    sa.Column('provider', sa.String(length=80), nullable=False),
    sa.Column('source_ref', sa.String(length=160), nullable=False),
    sa.Column('match_status', sa.String(length=24), nullable=False),
    sa.Column('amount_minor', sa.BigInteger(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'source_ref', name='uq_financial_transaction_source')
    )
    op.create_index(op.f('ix_financial_transactions_allocation_ref'), 'financial_transactions', ['allocation_ref'], unique=False)
    op.create_index(op.f('ix_financial_transactions_external_id'), 'financial_transactions', ['external_id'], unique=True)
    op.create_index(op.f('ix_financial_transactions_match_status'), 'financial_transactions', ['match_status'], unique=False)
    op.create_index(op.f('ix_financial_transactions_occurred_on'), 'financial_transactions', ['occurred_on'], unique=False)
    op.create_index(op.f('ix_financial_transactions_program_ref'), 'financial_transactions', ['program_ref'], unique=False)
    op.create_index(op.f('ix_financial_transactions_provider'), 'financial_transactions', ['provider'], unique=False)
    op.create_table('funding',
    sa.Column('external_id', sa.String(length=160), nullable=False),
    sa.Column('program_ref', sa.String(length=160), nullable=False),
    sa.Column('funder_name', sa.String(length=240), nullable=False),
    sa.Column('received_on', sa.String(length=10), nullable=False),
    sa.Column('source_ref', sa.String(length=160), nullable=False),
    sa.Column('amount_minor', sa.BigInteger(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_funding_external_id'), 'funding', ['external_id'], unique=True)
    op.create_index(op.f('ix_funding_program_ref'), 'funding', ['program_ref'], unique=False)
    op.create_table('outcomes',
    sa.Column('external_id', sa.String(length=160), nullable=False),
    sa.Column('program_ref', sa.String(length=160), nullable=False),
    sa.Column('delivery_ref', sa.String(length=160), nullable=True),
    sa.Column('metric', sa.String(length=160), nullable=False),
    sa.Column('value', sa.Integer(), nullable=False),
    sa.Column('unit', sa.String(length=80), nullable=False),
    sa.Column('region', sa.String(length=240), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_outcomes_delivery_ref'), 'outcomes', ['delivery_ref'], unique=False)
    op.create_index(op.f('ix_outcomes_external_id'), 'outcomes', ['external_id'], unique=True)
    op.create_index(op.f('ix_outcomes_program_ref'), 'outcomes', ['program_ref'], unique=False)
def downgrade() -> None:
    op.drop_index(op.f('ix_outcomes_program_ref'), table_name='outcomes')
    op.drop_index(op.f('ix_outcomes_external_id'), table_name='outcomes')
    op.drop_index(op.f('ix_outcomes_delivery_ref'), table_name='outcomes')
    op.drop_table('outcomes')
    op.drop_index(op.f('ix_funding_program_ref'), table_name='funding')
    op.drop_index(op.f('ix_funding_external_id'), table_name='funding')
    op.drop_table('funding')
    op.drop_index(op.f('ix_financial_transactions_provider'), table_name='financial_transactions')
    op.drop_index(op.f('ix_financial_transactions_program_ref'), table_name='financial_transactions')
    op.drop_index(op.f('ix_financial_transactions_occurred_on'), table_name='financial_transactions')
    op.drop_index(op.f('ix_financial_transactions_match_status'), table_name='financial_transactions')
    op.drop_index(op.f('ix_financial_transactions_external_id'), table_name='financial_transactions')
    op.drop_index(op.f('ix_financial_transactions_allocation_ref'), table_name='financial_transactions')
    op.drop_table('financial_transactions')
    op.drop_index(op.f('ix_deliveries_project_ref'), table_name='deliveries')
    op.drop_index(op.f('ix_deliveries_program_ref'), table_name='deliveries')
    op.drop_index(op.f('ix_deliveries_financial_transaction_ref'), table_name='deliveries')
    op.drop_index(op.f('ix_deliveries_external_id'), table_name='deliveries')
    op.drop_table('deliveries')
    op.drop_index(op.f('ix_allocations_project_ref'), table_name='allocations')
    op.drop_index(op.f('ix_allocations_program_ref'), table_name='allocations')
    op.drop_index(op.f('ix_allocations_funding_ref'), table_name='allocations')
    op.drop_index(op.f('ix_allocations_external_id'), table_name='allocations')
    op.drop_table('allocations')

