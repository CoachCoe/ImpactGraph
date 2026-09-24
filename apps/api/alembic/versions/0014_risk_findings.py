"""Things that looked wrong, and what a person decided about each one.

The perceptual hash sits beside the content hash rather than replacing it. The content
hash proves a file's bytes are unchanged; the perceptual hash survives a re-encode or a
crop and so can find the same photograph filed twice under two identities. They answer
different questions.
"""
import sqlalchemy as sa

from alembic import op

revision = "0014_risk_findings"
down_revision = "0013_beneficiary_confirmation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("evidence", sa.Column("perceptual_hash", sa.String(32), nullable=True))
    op.create_index("ix_evidence_perceptual_hash", "evidence", ["perceptual_hash"])
    op.create_table(
        "risk_findings",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("external_id", sa.String(160), nullable=False),
        sa.Column("organization_ref", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("subjects", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="OPEN"),
        sa.Column("assigned_to", sa.String(160), nullable=True),
        sa.Column("disposition_note", sa.Text(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_unique_constraint("uq_risk_finding_external_id", "risk_findings", ["external_id"])
    op.create_index("ix_risk_findings_organization", "risk_findings", ["organization_ref"])
    op.create_index("ix_risk_findings_kind", "risk_findings", ["kind"])
    op.create_index("ix_risk_findings_state", "risk_findings", ["state"])


def downgrade() -> None:
    op.drop_index("ix_risk_findings_state", table_name="risk_findings")
    op.drop_index("ix_risk_findings_kind", table_name="risk_findings")
    op.drop_index("ix_risk_findings_organization", table_name="risk_findings")
    op.drop_constraint("uq_risk_finding_external_id", "risk_findings")
    op.drop_table("risk_findings")
    op.drop_index("ix_evidence_perceptual_hash", table_name="evidence")
    op.drop_column("evidence", "perceptual_hash")
