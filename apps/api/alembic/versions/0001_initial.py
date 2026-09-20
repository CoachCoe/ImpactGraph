"""Initial ImpactGraph schema.

Explicit DDL rather than Base.metadata.create_all: a create_all baseline cannot be
diffed, cannot express a later change, and silently drifts from an existing database.
"""
import sqlalchemy as sa

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('attestations',
    sa.Column('external_id', sa.String(length=160), nullable=False),
    sa.Column('attestation_type', sa.String(length=48), nullable=False),
    sa.Column('subject_type', sa.String(length=48), nullable=False),
    sa.Column('subject_id', sa.String(length=160), nullable=False),
    sa.Column('issuer_id', sa.String(length=160), nullable=False),
    sa.Column('issuer_wallet', sa.String(length=42), nullable=False),
    sa.Column('statement_hash', sa.String(length=71), nullable=False),
    sa.Column('verification_bundle_hash', sa.String(length=71), nullable=False),
    sa.Column('transaction_hash', sa.String(length=66), nullable=True),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('revoked_by_attestation_id', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['revoked_by_attestation_id'], ['attestations.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('external_id')
    )
    op.create_table('audit_log',
    sa.Column('actor_id', sa.String(length=160), nullable=False),
    sa.Column('action', sa.String(length=100), nullable=False),
    sa.Column('entity_type', sa.String(length=48), nullable=False),
    sa.Column('entity_id', sa.String(length=160), nullable=False),
    sa.Column('metadata_json', sa.JSON(), nullable=False),
    sa.Column('correlation_id', sa.String(length=80), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_log_action'), 'audit_log', ['action'], unique=False)
    op.create_index(op.f('ix_audit_log_correlation_id'), 'audit_log', ['correlation_id'], unique=False)
    op.create_index(op.f('ix_audit_log_entity_id'), 'audit_log', ['entity_id'], unique=False)
    op.create_table('blockchain_operations',
    sa.Column('entity_id', sa.String(length=160), nullable=False),
    sa.Column('operation_type', sa.String(length=80), nullable=False),
    sa.Column('status', sa.String(length=40), nullable=False),
    sa.Column('expected_event', sa.String(length=80), nullable=False),
    sa.Column('transaction_hash', sa.String(length=66), nullable=True),
    sa.Column('chain_id', sa.BigInteger(), nullable=False),
    sa.Column('confirmations', sa.Integer(), nullable=False),
    sa.Column('correlation_id', sa.String(length=80), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_blockchain_operations_correlation_id'), 'blockchain_operations', ['correlation_id'], unique=False)
    op.create_index(op.f('ix_blockchain_operations_entity_id'), 'blockchain_operations', ['entity_id'], unique=False)
    op.create_index(op.f('ix_blockchain_operations_transaction_hash'), 'blockchain_operations', ['transaction_hash'], unique=False)
    op.create_table('claims',
    sa.Column('external_id', sa.String(length=160), nullable=False),
    sa.Column('program_ref', sa.String(length=160), nullable=False),
    sa.Column('project_ref', sa.String(length=160), nullable=False),
    sa.Column('statement', sa.Text(), nullable=False),
    sa.Column('payload_hash', sa.String(length=71), nullable=False),
    sa.Column('status', sa.String(length=48), nullable=False),
    sa.Column('verification_policy_version', sa.String(length=20), nullable=False),
    sa.Column('verification_bundle_hash', sa.String(length=71), nullable=True),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('external_id')
    )
    op.create_table('evidence',
    sa.Column('external_id', sa.String(length=160), nullable=False),
    sa.Column('project_ref', sa.String(length=160), nullable=False),
    sa.Column('evidence_type', sa.String(length=40), nullable=False),
    sa.Column('storage_uri', sa.Text(), nullable=False),
    sa.Column('content_hash', sa.String(length=71), nullable=False),
    sa.Column('mime_type', sa.String(length=120), nullable=False),
    sa.Column('visibility', sa.String(length=24), nullable=False),
    sa.Column('workflow_status', sa.String(length=48), nullable=False),
    sa.Column('analysis_status', sa.String(length=40), nullable=False),
    sa.Column('integrity_status', sa.String(length=40), nullable=False),
    sa.Column('blockchain_status', sa.String(length=40), nullable=False),
    sa.Column('metadata_json', sa.JSON(), nullable=False),
    sa.Column('extraction', sa.JSON(), nullable=True),
    sa.Column('reconciliation', sa.JSON(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('content_hash'),
    sa.UniqueConstraint('external_id')
    )
    op.create_table('idempotency_records',
    sa.Column('key', sa.String(length=160), nullable=False),
    sa.Column('operation', sa.String(length=80), nullable=False),
    sa.Column('request_hash', sa.String(length=71), nullable=False),
    sa.Column('response_status', sa.Integer(), nullable=False),
    sa.Column('response_body', sa.JSON(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key')
    )
    op.create_table('organizations',
    sa.Column('external_id', sa.String(length=160), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_organizations_external_id'), 'organizations', ['external_id'], unique=True)
    op.create_table('outbox',
    sa.Column('topic', sa.String(length=80), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('correlation_id', sa.String(length=80), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_outbox_correlation_id'), 'outbox', ['correlation_id'], unique=False)
    op.create_index(op.f('ix_outbox_topic'), 'outbox', ['topic'], unique=False)
    op.create_table('processed_chain_events',
    sa.Column('chain_id', sa.BigInteger(), nullable=False),
    sa.Column('transaction_hash', sa.String(length=66), nullable=False),
    sa.Column('log_index', sa.Integer(), nullable=False),
    sa.Column('event_name', sa.String(length=80), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('chain_id', 'transaction_hash', 'log_index', name='uq_chain_event')
    )
    op.create_table('programs',
    sa.Column('slug', sa.String(length=120), nullable=False),
    sa.Column('name', sa.String(length=240), nullable=False),
    sa.Column('operator_name', sa.String(length=240), nullable=False),
    sa.Column('operator_org_ref', sa.String(length=160), nullable=False),
    sa.Column('region', sa.String(length=240), nullable=False),
    sa.Column('status', sa.String(length=40), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('slug')
    )
    op.create_table('provenance_edges',
    sa.Column('source_type', sa.String(length=48), nullable=False),
    sa.Column('source_id', sa.String(length=160), nullable=False),
    sa.Column('relationship', sa.String(length=40), nullable=False),
    sa.Column('target_type', sa.String(length=48), nullable=False),
    sa.Column('target_id', sa.String(length=160), nullable=False),
    sa.Column('confirmed_onchain', sa.Boolean(), nullable=False),
    sa.Column('superseded_by', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['superseded_by'], ['provenance_edges.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_id', 'relationship', 'target_id', name='uq_provenance_assertion')
    )
    op.create_table('domain_entities',
    sa.Column('external_id', sa.String(length=160), nullable=False),
    sa.Column('entity_type', sa.String(length=48), nullable=False),
    sa.Column('program_id', sa.UUID(), nullable=True),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.Column('blockchain_status', sa.String(length=40), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['program_id'], ['programs.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('external_id')
    )
    op.create_index(op.f('ix_domain_entities_entity_type'), 'domain_entities', ['entity_type'], unique=False)
    op.create_table('users',
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('display_name', sa.String(length=200), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('role', sa.String(length=40), nullable=False),
    sa.Column('wallet_address', sa.String(length=42), nullable=True),
    sa.Column('disabled', sa.Boolean(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    op.create_index(op.f('ix_users_organization_id'), 'users', ['organization_id'], unique=False)
    op.create_table('sessions',
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_sessions_expires_at'), 'sessions', ['expires_at'], unique=False)
    op.create_index(op.f('ix_sessions_token_hash'), 'sessions', ['token_hash'], unique=True)
    op.create_index(op.f('ix_sessions_user_id'), 'sessions', ['user_id'], unique=False)
    op.create_table('wallet_challenges',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('nonce', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_wallet_challenges_nonce'), 'wallet_challenges', ['nonce'], unique=True)
    op.create_index(op.f('ix_wallet_challenges_user_id'), 'wallet_challenges', ['user_id'], unique=False)
def downgrade() -> None:
    op.drop_index(op.f('ix_wallet_challenges_user_id'), table_name='wallet_challenges')
    op.drop_index(op.f('ix_wallet_challenges_nonce'), table_name='wallet_challenges')
    op.drop_table('wallet_challenges')
    op.drop_index(op.f('ix_sessions_user_id'), table_name='sessions')
    op.drop_index(op.f('ix_sessions_token_hash'), table_name='sessions')
    op.drop_index(op.f('ix_sessions_expires_at'), table_name='sessions')
    op.drop_table('sessions')
    op.drop_index(op.f('ix_users_organization_id'), table_name='users')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')
    op.drop_index(op.f('ix_domain_entities_entity_type'), table_name='domain_entities')
    op.drop_table('domain_entities')
    op.drop_table('provenance_edges')
    op.drop_table('programs')
    op.drop_table('processed_chain_events')
    op.drop_index(op.f('ix_outbox_topic'), table_name='outbox')
    op.drop_index(op.f('ix_outbox_correlation_id'), table_name='outbox')
    op.drop_table('outbox')
    op.drop_index(op.f('ix_organizations_external_id'), table_name='organizations')
    op.drop_table('organizations')
    op.drop_table('idempotency_records')
    op.drop_table('evidence')
    op.drop_table('claims')
    op.drop_index(op.f('ix_blockchain_operations_transaction_hash'), table_name='blockchain_operations')
    op.drop_index(op.f('ix_blockchain_operations_entity_id'), table_name='blockchain_operations')
    op.drop_index(op.f('ix_blockchain_operations_correlation_id'), table_name='blockchain_operations')
    op.drop_table('blockchain_operations')
    op.drop_index(op.f('ix_audit_log_entity_id'), table_name='audit_log')
    op.drop_index(op.f('ix_audit_log_correlation_id'), table_name='audit_log')
    op.drop_index(op.f('ix_audit_log_action'), table_name='audit_log')
    op.drop_table('audit_log')
    op.drop_table('attestations')

