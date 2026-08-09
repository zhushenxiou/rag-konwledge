"""多路检索：混合召回（关键词 BM25 + 语义向量，RRF 融合）。

固定 hybrid：两路各召回 bm25_recall_k 个候选，并集后按 RRF 公式
    score = Σ 1/(k + rank)
融合排序，再取 top_k。既保留关键词精确命中，又补上语义近似，弥补单路召回盲区。
不再提供 keyword / semantic 单选模式——所有问答统一走混合召回 + RRF 融合。

similarity 字段的约定（引用展示用）：语义命中的分块保留余弦相似度；
仅关键词命中的分块用归一化 BM25 分数（0~1）近似，保证 sources 始终可读。
"""
import logging
from dataclasses import dataclass

import jieba
from rank_bm25 import BM25Okapi
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Chunk, Document

logger = logging.getLogger(__name__)

# 预加载词典，避免把首次分词的几百毫秒压在请求路径上
jieba.initialize()


@dataclass
class RetrievalHit:
    chunk: Chunk
    document: Document
    similarity: float


def _tokenize(text: str) -> list[str]:
    """去空白后分词。HMM 模式让未登录词（如产品名）也能被切出。"""
    return [w for w in jieba.cut(text) if w.strip()]


def _semantic_search(
    db: Session, query_vector: list[float], top_k: int, threshold: float
) -> list[RetrievalHit]:
    """pgvector 余弦距离检索（原 search_chunks 逻辑）。"""
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


def _keyword_search(db: Session, query_text: str, top_k: int) -> list[RetrievalHit]:
    """结巴分词 + BM25 关键词检索。

    demo 规模直接全量载入分块文本建语料；量级上来后可换 DB 侧
    全文检索（to_tsvector / pg_trgm）或外部 Elasticsearch。

    注意：BM25 分数可正可负（词出现在所有文档时 idf<0），"0 分"反而是
    没有命中任何查询词的文档。因此先按"是否命中查询词"过滤，再按分数排序，
    不能直接用 `s > 0` 当命中判据。
    """
    rows = db.execute(
        select(Chunk, Document)
        .join(Document, Document.id == Chunk.document_id)
        .order_by(Chunk.id)
    ).all()
    if not rows:
        return []

    query_tokens = set(_tokenize(query_text))
    if not query_tokens:
        return []

    corpus = [_tokenize(r[0].content) for r in rows]
    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(query_tokens)
    matched = [
        (score, idx)
        for idx, score in enumerate(scores)
        if query_tokens & set(corpus[idx])
    ]
    if not matched:
        return []

    matched.sort(key=lambda x: x[0], reverse=True)
    top = matched[:top_k]
    lo, hi = top[-1][0], top[0][0]
    span = hi - lo
    # 分数归一化到 0~1（min-max，在命中集合内），与余弦相似度同量纲展示
    return [
        RetrievalHit(
            chunk=rows[idx][0],
            document=rows[idx][1],
            similarity=round(1.0 if span == 0 else (score - lo) / span, 4),
        )
        for score, idx in top
    ]


def _rrf_fuse(*ranked_lists: list[RetrievalHit], k: int = 60) -> dict[int, float]:
    """Reciprocal Rank Fusion：对同一分块在每路中的排名取 1/(k + rank) 求和。"""
    fused: dict[int, float] = {}
    for hits in ranked_lists:
        for rank, hit in enumerate(hits):
            fused[hit.chunk.id] = fused.get(hit.chunk.id, 0.0) + 1.0 / (k + rank + 1)
    return fused


def search_chunks(
    db: Session,
    query_vector: list[float] | None = None,
    query_text: str | None = None,
    top_k: int | None = None,
    threshold: float | None = None,
) -> list[RetrievalHit]:
    """混合检索统一入口：语义 + 关键词两路召回，RRF 融合排序。

    参数：
      query_vector  问题向量（语义通道）
      query_text    原始查询文本（关键词通道）
      top_k / threshold  默认取 settings.top_k / settings.similarity_threshold。

    两路各召回 max(top_k, bm25_recall_k) 个候选，RRF 融合后取 top_k。
    """
    top_k = top_k or settings.top_k
    threshold = threshold if threshold is not None else settings.similarity_threshold
    recall_k = max(top_k, settings.bm25_recall_k)

    if query_vector is None or not query_text:
        raise ValueError("混合检索需要 query_text 与 query_vector")
    semantic_hits = _semantic_search(db, query_vector, recall_k, threshold)
    keyword_hits = _keyword_search(db, query_text, recall_k)

    fused = _rrf_fuse(semantic_hits, keyword_hits, k=settings.rrf_k)
    if not fused:
        return []

    # 按 RRF 分数降序取 top_k；相似度优先保留语义余弦（两路都命中时）
    ordered = sorted(fused, key=fused.get, reverse=True)[:top_k]
    by_id: dict = {h.chunk.id: h for h in semantic_hits}
    for h in keyword_hits:
        by_id.setdefault(h.chunk.id, h)
    hits = [by_id[cid] for cid in ordered]
    logger.info(
        "hybrid recall: semantic=%d keyword=%d fused=%d -> %d",
        len(semantic_hits),
        len(keyword_hits),
        len(fused),
        len(hits),
    )
    return hits
