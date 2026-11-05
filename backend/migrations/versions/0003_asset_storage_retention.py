"""Track uploaded assets and workspace upload retention policy."""

from alembic import op
import sqlalchemy as sa

revision = "0003_asset_lifecycle"
down_revision = "0002_ai_retrieval"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("organizations", sa.Column("upload_retention_days", sa.Integer(), nullable=False, server_default="0"))
    op.create_table(
        "stored_assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(160), nullable=False, server_default="application/octet-stream"),
        sa.Column("storage_key", sa.String(1024), nullable=False, unique=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_stored_assets_organization_id", "stored_assets", ["organization_id"])


def downgrade():
    op.drop_index("ix_stored_assets_organization_id", table_name="stored_assets")
    op.drop_table("stored_assets")
    op.drop_column("organizations", "upload_retention_days")
