"""Add explicit free-provider consent and embedding-space tracking."""

from alembic import op
import sqlalchemy as sa


revision = "0007_openrouter_embedding_space"
down_revision = "0006_review_visual_assets"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "organizations",
        sa.Column("openrouter_free_ai_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("knowledge_chunks", sa.Column("embedding_space", sa.String(length=200), nullable=True))


def downgrade():
    # Older application versions do not filter vectors by embedding_space and
    # would otherwise compare Nemotron vectors against OpenAI query vectors.
    # Only rebuildable vectors are cleared; approved source text/chunks remain.
    op.execute("UPDATE knowledge_chunks SET embedding = NULL")
    op.drop_column("knowledge_chunks", "embedding_space")
    op.drop_column("organizations", "openrouter_free_ai_enabled")
