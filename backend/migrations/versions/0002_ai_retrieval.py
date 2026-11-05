"""Add workspace AI consent, semantic vectors, and approved-source chunks."""

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision = "0002_ai_retrieval"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column("organizations", sa.Column("external_ai_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("reviews", sa.Column("ai_status", sa.String(length=32), nullable=False, server_default="disabled"))
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("knowledge_id", sa.Integer(), sa.ForeignKey("knowledge.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.UniqueConstraint("knowledge_id", "chunk_index", name="uq_knowledge_chunks_source_index"),
    )
    op.create_index("ix_knowledge_chunks_organization_id", "knowledge_chunks", ["organization_id"])
    op.create_index("ix_knowledge_chunks_knowledge_id", "knowledge_chunks", ["knowledge_id"])
    if connection.dialect.name == "postgresql":
        op.create_index(
            "ix_knowledge_chunks_embedding_hnsw",
            "knowledge_chunks",
            ["embedding"],
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        )


def downgrade():
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        op.drop_index("ix_knowledge_chunks_embedding_hnsw", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_knowledge_id", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_organization_id", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
    op.drop_column("reviews", "ai_status")
    op.drop_column("organizations", "external_ai_enabled")
