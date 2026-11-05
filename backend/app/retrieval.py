"""Workspace-scoped hybrid retrieval over approved knowledge only."""

from __future__ import annotations

import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .main import Knowledge, KnowledgeChunk

logger = logging.getLogger(__name__)
STOP_WORDS = {"about", "after", "also", "been", "before", "being", "from", "have", "into", "more", "only", "should", "that", "their", "them", "then", "there", "these", "this", "those", "with", "would", "your"}


def _tokens(value: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]{2,}", value.casefold()) if word not in STOP_WORDS}


def lexical_search(content: str, sources: list[Knowledge], limit: int = 8) -> list[Knowledge]:
    draft_terms = _tokens(content)
    if not draft_terms:
        return []
    scored = []
    for item in sources:
        searchable = " ".join((item.title, item.body, item.category, item.passage))
        terms = _tokens(searchable)
        overlap = len(draft_terms & terms)
        if overlap:
            score = overlap / max(1, len(draft_terms))
            scored.append((score, item.id, item))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [item for _, _, item in scored[:limit]]


def retrieve_approved(
    db: Session,
    organization_id: int,
    content: str,
    query_embedding: list[float] | None = None,
    limit: int = 8,
) -> list[Knowledge]:
    active = db.scalars(
        select(Knowledge).where(Knowledge.organization_id == organization_id, Knowledge.status == "active").limit(1000)
    ).all()
    if query_embedding and db.bind is not None and db.bind.dialect.name == "postgresql":
        try:
            rows = db.execute(
                select(KnowledgeChunk, Knowledge)
                .join(Knowledge, Knowledge.id == KnowledgeChunk.knowledge_id)
                .where(
                    KnowledgeChunk.organization_id == organization_id,
                    Knowledge.organization_id == organization_id,
                    Knowledge.status == "active",
                    KnowledgeChunk.embedding.is_not(None),
                )
                .order_by(KnowledgeChunk.embedding.cosine_distance(query_embedding))
                .limit(limit * 2)
            ).all()
            if rows:
                # Add lexical matches to improve recall for exact names, numeric facts, and terminology.
                merged = {}
                for _chunk, source in rows:
                    merged.setdefault(source.id, source)
                merged.update({row.id: row for row in lexical_search(content, active, limit)})
                return list(merged.values())[:limit]
        except Exception as exc:
            logger.warning("Vector retrieval failed; using workspace lexical retrieval (%s)", type(exc).__name__)
    return lexical_search(content, active, limit)
