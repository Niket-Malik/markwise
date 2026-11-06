"""Link visual reviews to their private source assets for explicit AI retries."""

from alembic import op
import sqlalchemy as sa


revision = "0006_review_visual_assets"
down_revision = "0005_finding_decisions"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("reviews") as batch_op:
        batch_op.add_column(sa.Column("source_asset_id", sa.Integer(), sa.ForeignKey("stored_assets.id", name="fk_reviews_source_asset_id_stored_assets", ondelete="SET NULL"), nullable=True))
        batch_op.add_column(sa.Column("reference_asset_id", sa.Integer(), sa.ForeignKey("stored_assets.id", name="fk_reviews_reference_asset_id_stored_assets", ondelete="SET NULL"), nullable=True))
        batch_op.create_index("ix_reviews_source_asset_id", ["source_asset_id"])
        batch_op.create_index("ix_reviews_reference_asset_id", ["reference_asset_id"])


def downgrade():
    with op.batch_alter_table("reviews") as batch_op:
        batch_op.drop_index("ix_reviews_reference_asset_id")
        batch_op.drop_index("ix_reviews_source_asset_id")
        batch_op.drop_column("reference_asset_id")
        batch_op.drop_column("source_asset_id")
