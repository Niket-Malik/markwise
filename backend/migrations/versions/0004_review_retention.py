"""Add workspace review retention controls."""

from alembic import op
import sqlalchemy as sa

revision = "0004_review_retention"
down_revision = "0003_asset_lifecycle"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("organizations", sa.Column("review_retention_days", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    op.drop_column("organizations", "review_retention_days")
