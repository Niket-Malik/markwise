"""Record reasons for human finding decisions and overrides."""

from alembic import op
import sqlalchemy as sa

revision = "0005_finding_decisions"
down_revision = "0004_review_retention"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "finding_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("finding_id", sa.Integer(), sa.ForeignKey("findings.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_finding_decisions_organization_id", "finding_decisions", ["organization_id"])
    op.create_index("ix_finding_decisions_finding_id", "finding_decisions", ["finding_id"])


def downgrade():
    op.drop_index("ix_finding_decisions_finding_id", table_name="finding_decisions")
    op.drop_index("ix_finding_decisions_organization_id", table_name="finding_decisions")
    op.drop_table("finding_decisions")
