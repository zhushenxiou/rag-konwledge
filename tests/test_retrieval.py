"""检索逻辑测试：Top-K 排序、相似度、阈值过滤。"""
import pytest

from app.models import Chunk, Document
from app.services.retrieval import search_chunks


def _seed(db) -> Document:
    doc = Document(filename="t.md", file_path="x", file_size=1, source_type="md")
    db.add(doc)
    db.flush()
    db.add_all(
        [
            Chunk(document_id=doc.id, content="alpha", chunk_index=0, embedding=[1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
            Chunk(document_id=doc.id, content="bravo", chunk_index=1, embedding=[0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
            Chunk(document_id=doc.id, content="charlie", chunk_index=2, embedding=[0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        ]
    )
    db.commit()
    return doc


def test_topk_sorted_by_similarity(db):
    _seed(db)
    hits = search_chunks(db, [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], top_k=3, threshold=0.0)
    assert len(hits) == 3
    assert hits[0].chunk.content == "alpha"
    assert hits[0].similarity == pytest.approx(1.0, abs=1e-4)
    # 相似度降序
    sims = [h.similarity for h in hits]
    assert sims == sorted(sims, reverse=True)
    assert hits[0].document.filename == "t.md"


def test_topk_limit(db):
    _seed(db)
    hits = search_chunks(db, [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], top_k=2, threshold=0.0)
    assert len(hits) == 2


def test_threshold_filters_low_similarity(db):
    _seed(db)
    hits = search_chunks(db, [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], top_k=4, threshold=0.99)
    assert len(hits) == 1
    assert hits[0].chunk.content == "alpha"


def test_no_hit_when_below_threshold(db):
    _seed(db)
    hits = search_chunks(db, [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0], top_k=4, threshold=0.5)
    assert hits == []
