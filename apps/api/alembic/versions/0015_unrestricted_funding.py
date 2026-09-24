"""Money an organisation holds before it has decided what it funds.

Receipt and assignment become separate events. A contribution arrives against the
organisation; it is assigned to a programme later, or not yet. "Held" is the money with
no programme, which is a state the model can express rather than a null a reader has to
remember to handle.

`contributor_count` lets one row stand for many small contributions, so a daily total
does not need a row per payment. See ADR-018.

Existing rows are backfilled from the programme they already name, so every figure that
was true before this migration is true after it.
"""
import sqlalchemy as sa

from alembic import op

revision = "0015_unrestricted_funding"
down_revision = "0014_risk_findings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "funding",
        sa.Column("organization_ref", sa.String(160), nullable=False, server_default=""),
    )
    op.add_column("funding", sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "funding",
        sa.Column("contributor_count", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_funding_organization_ref", "funding", ["organization_ref"])

    # Every existing row names a programme, and that programme names its operator. Doing
    # this before the column is relaxed means there is no window in which a row is
    # unassigned by accident rather than by decision.
    op.execute(
        """
        UPDATE funding
           SET organization_ref = programs.operator_org_ref,
               assigned_at = funding.created_at
          FROM programs
         WHERE programs.slug = funding.program_ref
        """
    )
    op.alter_column("funding", "program_ref", existing_type=sa.String(160), nullable=True)


def downgrade() -> None:
    # A row with no programme cannot be represented by the old schema. Refusing is the
    # honest answer: silently deleting held money, or inventing a programme for it, would
    # both be worse than stopping.
    held = op.get_bind().execute(
        sa.text("SELECT count(*) FROM funding WHERE program_ref IS NULL")
    ).scalar()
    if held:
        raise RuntimeError(
            f"{held} funding rows are held against an organisation and name no programme. "
            "The previous schema cannot hold them; assign or remove them before downgrading."
        )
    op.alter_column("funding", "program_ref", existing_type=sa.String(160), nullable=False)
    op.drop_index("ix_funding_organization_ref", table_name="funding")
    op.drop_column("funding", "contributor_count")
    op.drop_column("funding", "assigned_at")
    op.drop_column("funding", "organization_ref")
