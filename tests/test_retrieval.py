"""检索逻辑测试：Top-K 排序、相似度、阈值过滤、混合召回（BM25 + 向量）+ RRF。"""
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
    # 关键词通道用无重叠词触发空召回，仅语义通道参与，便于断言纯语义排序
    _seed(db)
    hits = search_chunks(
        db, [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], "完全不相关的词",
        top_k=3, threshold=0.0,
    )
    assert len(hits) == 3
    assert hits[0].chunk.content == "alpha"
    assert hits[0].similarity == pytest.approx(1.0, abs=1e-4)
    # 相似度降序
    sims = [h.similarity for h in hits]
    assert sims == sorted(sims, reverse=True)
    assert hits[0].document.filename == "t.md"


def test_topk_limit(db):
    _seed(db)
    hits = search_chunks(
        db, [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], "完全不相关的词",
        top_k=2, threshold=0.0,
    )
    assert len(hits) == 2


def test_threshold_filters_low_similarity(db):
    _seed(db)
    hits = search_chunks(
        db, [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], "完全不相关的词",
        top_k=4, threshold=0.99,
    )
    assert len(hits) == 1
    assert hits[0].chunk.content == "alpha"


def test_no_hit_when_below_threshold(db):
    _seed(db)
    hits = search_chunks(
        db, [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0], "完全不相关的词",
        top_k=4, threshold=0.5,
    )
    assert hits == []


# ---- 混合检索：BM25 + 向量两路召回，RRF 融合 ----
# 三块内容：A 语义+关键词都能命中，B 仅关键词命中，C 仅语义命中


def _seed_modes(db) -> Document:
    doc = Document(filename="modes.md", file_path="x", file_size=1, source_type="md")
    db.add(doc)
    db.flush()
    db.add_all(
        [
            Chunk(
                document_id=doc.id, content="alpha 语义 匹配",
                chunk_index=0, embedding=[1.0, 0, 0, 0, 0, 0, 0, 0],
            ),
            Chunk(
                document_id=doc.id, content="bravo 关键词 命中",
                chunk_index=1, embedding=[0.0, 1, 0, 0, 0, 0, 0, 0],
            ),
            Chunk(
                document_id=doc.id, content="charlie 仅语义 命中",
                chunk_index=2, embedding=[1.0, 0, 0, 0, 0, 0, 0, 0],
            ),
        ]
    )
    db.commit()
    return doc


def test_hybrid_rrf_boosts_multi_channel_hit(db):
    _seed_modes(db)
    # 语义：A、C 命中（B 向量正交被阈值滤掉）；关键词：A、B 命中
    # RRF 后 A 两路都中，分数必为最高且唯一置顶；B、C 各单路命中，顺序不保证
    hits = search_chunks(db, [1.0, 0, 0, 0, 0, 0, 0, 0], "alpha bravo", top_k=3)
    contents = [h.chunk.content for h in hits]
    assert contents[0] == "alpha 语义 匹配"  # 两路命中 -> RRF 最高
    assert set(contents) == {"alpha 语义 匹配", "bravo 关键词 命中", "charlie 仅语义 命中"}
    # 同时被两路命中的分块保留语义余弦相似度
    assert hits[0].similarity == pytest.approx(1.0, abs=1e-4)


def test_hybrid_keyword_rescues_below_threshold(db):
    _seed_modes(db)
    # 语义通道 0.99 阈值下只留 A、C（余弦=1）；B 仅关键词命中，被 RRF 挽救
    hits = search_chunks(
        db, [1.0, 0, 0, 0, 0, 0, 0, 0], "bravo", top_k=3, threshold=0.99
    )
    assert "bravo 关键词 命中" in {h.chunk.content for h in hits}


def test_hybrid_no_hit_when_both_channels_empty(db):
    _seed_modes(db)
    # 向量正交且无关键词重叠 -> 两路都空，整体无召回
    hits = search_chunks(
        db, [0.0, 0, 0, 0, 0, 0, 0, 1], "完全不相关的词", top_k=3
    )
    assert hits == []


def test_missing_inputs_raise(db):
    _seed_modes(db)
    with pytest.raises(ValueError):
        search_chunks(db, query_vector=None, query_text="bravo")
    with pytest.raises(ValueError):
        search_chunks(db, query_vector=[1.0, 0, 0, 0, 0, 0, 0, 0], query_text=None)
