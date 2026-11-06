"""Add a monotonic generation for invalidating in-flight AI operations."""

from alembic import op
import sqlalchemy as sa


revision = "0008_workspace_consent_generation"
down_revision = "0007_openrouter_embedding_space"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "organizations",
        sa.Column("external_ai_consent_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "organizations",
        sa.Column("openrouter_consent_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_column("organizations", "openrouter_consent_version")
    op.drop_column("organizations", "external_ai_consent_version")
