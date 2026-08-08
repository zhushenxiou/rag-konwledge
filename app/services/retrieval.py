"""向量检索：pgvector 余弦距离，阈值过滤 + Top-K。

similarity = 1 - cosine_distance，默认 threshold=0.5，即 distance <= 0.5。
"""
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Chunk, Document


@dataclass
class RetrievalHit:
    chunk: Chunk
    document: Document
    similarity: float


def search_chunks(
    db: Session,
    query_vector: list[float],
    top_k: int | None = None,
    threshold: float | None = None,
) -> list[RetrievalHit]:
    top_k = top_k or settings.top_k
    threshold = threshold if threshold is not None else settings.similarity_threshold

    distance = Chunk.embedding.cosine_distance(query_vector)
    stmt = (
        select(Chunk, Document, distance.label("distance"))
        .join(Document, Document.id == Chunk.document_id)
        .where(distance <= 1 - threshold)
        .order_by(distance.asc())
        .limit(top_k)
    )
    rows = db.execute(stmt).all()
    return [
        RetrievalHit(chunk=chunk, document=doc, similarity=round(1 - dist, 4))
        for chunk, doc, dist in rows
    ]
