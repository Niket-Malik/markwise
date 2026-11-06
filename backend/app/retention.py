"""Scheduled upload-retention job. Run with `python -m app.retention` from backend."""

from datetime import datetime, timedelta, timezone
import logging

from sqlalchemy import or_, select

from .main import Organization, Review, SessionLocal, StoredAsset, delete_stored_asset, remove_review_records

logger = logging.getLogger(__name__)


def purge_expired_uploads(now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    deleted = 0
    with SessionLocal() as db:
        organizations = db.scalars(select(Organization).where(or_(Organization.upload_retention_days > 0, Organization.review_retention_days > 0))).all()
        for organization in organizations:
            if organization.upload_retention_days > 0:
                cutoff = now - timedelta(days=organization.upload_retention_days)
                assets = db.scalars(select(StoredAsset).where(
                    StoredAsset.organization_id == organization.id,
                    StoredAsset.created_at < cutoff,
                )).all()
                for asset in assets:
                    try:
                        delete_stored_asset(db, asset)
                    except Exception as exc:
                        logger.error("Retention could not delete asset %s (%s)", asset.id, type(exc).__name__)
                        continue
                    deleted += 1
            if organization.review_retention_days > 0:
                review_cutoff = now - timedelta(days=organization.review_retention_days)
                reviews = db.scalars(select(Review).where(
                    Review.organization_id == organization.id,
                    Review.created_at < review_cutoff,
                )).all()
                for review in reviews:
                    remove_review_records(db, review)
                    deleted += 1
        db.commit()
    return deleted


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info("Deleted %d expired uploads", purge_expired_uploads())
