"""What makes holding one evidence object lawful, and who is answerable for it.

Not a consent table. The operating organisation delivers the aid it photographs, so
consent obtained from a recipient is not freely given and the basis for ordinary
programme imagery is more likely legitimate interest with an objection route. The basis
is therefore a value rather than an assumption, and the controller is per object because
it differs between a programme run alone, one co-built with a partner, and one run by
another organisation on a platform handed to it.

Nothing is backfilled. Existing evidence predates any declaration that it holds personal
data, and inventing a lawful basis for it would be the opposite of what this records.
"""
import sqlalchemy as sa

from alembic import op

revision = "0009_data_protection"
down_revision = "0008_program_chain_entity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_protection_records",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("evidence_ref", sa.String(160), nullable=False, unique=True),
        sa.Column("lawful_basis", sa.String(40), nullable=False),
        sa.Column("special_category", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("controller_org_ref", sa.String(160), nullable=False),
        sa.Column("joint_controller_org_ref", sa.String(160), nullable=True),
        sa.Column("subject_reference", sa.String(160), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("captured_by", sa.String(160), nullable=False),
        sa.Column("retain_until", sa.String(10), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("erased_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_data_protection_records_evidence_ref", "data_protection_records", ["evidence_ref"]
    )
    op.create_index(
        "ix_data_protection_records_controller", "data_protection_records", ["controller_org_ref"]
    )
    op.create_index(
        "ix_data_protection_records_subject", "data_protection_records", ["subject_reference"]
    )
    # Evidence says whether it holds personal data. Declared by the operator uploading it,
    # because nothing else can know, and the registration gate reads it.
    op.add_column(
        "evidence",
        sa.Column("personal_data", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("evidence", "personal_data")
    op.drop_index("ix_data_protection_records_subject", table_name="data_protection_records")
    op.drop_index("ix_data_protection_records_controller", table_name="data_protection_records")
    op.drop_index("ix_data_protection_records_evidence_ref", table_name="data_protection_records")
    op.drop_table("data_protection_records")
