"""Whether a program exists in the registry, and which organisation operates it.

`registerEvidence` reverts with UnknownProgram unless the program entity is on chain, so
until this column a program row said nothing about whether evidence could be filed against
it. Existing rows are backfilled to CONFIRMED: the only programs that exist were created by
`bootstrap-chain`, which does not return until the receipt is mined.

`operator_org_ref` gains an index and a non-empty default because ownership is now resolved
against it on every creation, not only on mutation of rows the seed created.
"""
import sqlalchemy as sa

from alembic import op

revision = "0008_program_chain_entity"
down_revision = "0007_outcome_method"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "programs",
        sa.Column("chain_status", sa.String(40), nullable=False, server_default="NOT_STARTED"),
    )
    op.execute("UPDATE programs SET chain_status = 'CONFIRMED'")
    op.create_index("ix_programs_operator_org_ref", "programs", ["operator_org_ref"])


def downgrade() -> None:
    op.drop_index("ix_programs_operator_org_ref", table_name="programs")
    op.drop_column("programs", "chain_status")
