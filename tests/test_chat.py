"""问答链路测试：SSE 事件、消息持久化、引用来源、无依据处理、重排。"""
from sqlalchemy import select

from app.models import Chunk, Conversation, Document, Message
from app.services.chat_service import chat_events


async def _collect(gen):
    return [e async for e in gen]


def _seed_matching_chunk(db, embedder, content: str = "chunk_size 默认是多少字符 overlap 默认 100") -> Document:
    doc = Document(filename="rag.md", file_path="x", file_size=1, source_type="md")
    db.add(doc)
    db.flush()
    db.add(
        Chunk(
            document_id=doc.id,
            content=content,
            chunk_index=0,
            embedding=embedder.embed_query("chunk_size 默认是多少"),
        )
    )
    db.commit()
    return doc


async def test_chat_happy_path_streams_answer_and_sources(db, fake_embedder, fake_llm, fake_reranker):
    _seed_matching_chunk(db, fake_embedder)

    events = await _collect(
        chat_events(
            db, "chunk_size 默认是多少", None,
            embedder=fake_embedder, llm=fake_llm, reranker=fake_reranker,
        )
    )
    types = [e["type"] for e in events]
    assert {"chunk", "sources", "done"} <= set(types)

    answer = "".join(e["content"] for e in events if e["type"] == "chunk")
    assert answer == "mock-answer-part2"

    sources = next(e for e in events if e["type"] == "sources")["sources"]
    assert sources and sources[0]["filename"] == "rag.md"
    assert sources[0]["similarity"] > 0.5

    done = next(e for e in events if e["type"] == "done")
    assert done["conversation_id"]

    msgs = db.execute(select(Message).order_by(Message.created_at)).scalars().all()
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[1].sources and msgs[1].sources[0]["filename"] == "rag.md"


async def test_chat_no_evidence_when_nothing_retrieved(db, fake_embedder, fake_llm, fake_reranker):
    # 知识库为空，且问题与任何内容都不相关
    events = await _collect(
        chat_events(
            db, "某个完全无关的问题", None,
            embedder=fake_embedder, llm=fake_llm, reranker=fake_reranker,
        )
    )
    types = [e["type"] for e in events]
    assert types == ["no_evidence", "done"]

    msg = db.execute(
        select(Message).where(Message.role == "assistant")
    ).scalars().first()
    assert msg and "没有找到足够依据" in msg.content


async def test_chat_reuses_existing_conversation(db, fake_embedder, fake_llm, fake_reranker):
    conv = Conversation(title="旧会话")
    db.add(conv)
    db.commit()
    _seed_matching_chunk(db, fake_embedder)

    events = await _collect(
        chat_events(
            db, "chunk_size 默认是多少", conv.id,
            embedder=fake_embedder, llm=fake_llm, reranker=fake_reranker,
        )
    )
    done = next(e for e in events if e["type"] == "done")
    assert done["conversation_id"] == str(conv.id)

    msgs = db.execute(
        select(Message).where(Message.conversation_id == conv.id)
    ).scalars().all()
    assert len(msgs) == 2


async def test_chat_unknown_conversation_returns_error(db, fake_embedder, fake_llm, fake_reranker):
    import uuid

    events = await _collect(
        chat_events(
            db, "问题", uuid.uuid4(),
            embedder=fake_embedder, llm=fake_llm, reranker=fake_reranker,
        )
    )
    assert events[0]["type"] == "error"


async def test_chat_hybrid_keyword_channel_rescues(db, fake_embedder, fake_llm, fake_reranker):
    # 混合检索：语义通道相似度为 0（向量正交），但 BM25 通道能按关键词命中
    doc = Document(filename="rag.md", file_path="x", file_size=1, source_type="md")
    db.add(doc)
    db.flush()
    db.add(
        Chunk(
            document_id=doc.id,
            content="数据库 向量 检索 语义",
            chunk_index=0,
            embedding=[0.0, 1, 0, 0, 0, 0, 0, 0],  # 与问题向量正交，语义通道被阈值滤掉
        )
    )
    db.commit()

    events = await _collect(
        chat_events(
            db, "检索 功能", None,
            embedder=fake_embedder, llm=fake_llm, reranker=fake_reranker,
        )
    )
    types = [e["type"] for e in events]
    assert {"chunk", "sources", "done"} <= set(types)
    sources = next(e for e in events if e["type"] == "sources")["sources"]
    assert sources and sources[0]["snippet"].startswith("数据库 向量")


def _seed_three_chunks(db) -> None:
    """三个分块，混合检索两路下都会被召回（召回顺序与 BM25/Jieba 分词相关，不确定）。"""
    doc = Document(filename="rag.md", file_path="x", file_size=1, source_type="md")
    db.add(doc)
    db.flush()
    db.add_all(
        [
            Chunk(document_id=doc.id, content="alpha 语义 匹配", chunk_index=0, embedding=[1.0, 0, 0, 0, 0, 0, 0, 0]),
            Chunk(document_id=doc.id, content="bravo 关键词 命中", chunk_index=1, embedding=[0.0, 1, 0, 0, 0, 0, 0, 0]),
            Chunk(document_id=doc.id, content="charlie 仅语义 命中", chunk_index=2, embedding=[1.0, 0, 0, 0, 0, 0, 0, 0]),
        ]
    )
    db.commit()


class ContentScoreReranker:
    """按内容前缀打分（bravo > alpha > charlie），与输入顺序无关，保证断言确定。"""

    _SCORE = {"bravo": 0.9, "alpha": 0.6, "charlie": 0.3}

    def rerank(self, query: str, texts: list[str], top_n: int) -> list[float]:
        return [self._SCORE.get(t.split()[0], 0.0) for t in texts]


async def test_chat_rerank_reorders_hits(db, fake_embedder, fake_llm):
    # 重排器按内容给分 → 无论召回原顺序如何，输出都应为 [bravo, alpha, charlie]
    _seed_three_chunks(db)

    events = await _collect(
        chat_events(
            db, "语义 关键词 命中", None,
            embedder=fake_embedder, llm=fake_llm, reranker=ContentScoreReranker(),
        )
    )
    assert {"chunk", "sources", "done"} <= {e["type"] for e in events}
    sources = next(e for e in events if e["type"] == "sources")["sources"]
    snippets = [s["snippet"] for s in sources]
    assert snippets == ["bravo 关键词 命中", "alpha 语义 匹配", "charlie 仅语义 命中"]
    # 重排分数写入 similarity，展示口径与最终排序一致
    assert sources[0]["similarity"] == 0.9


async def test_chat_rerank_failure_falls_back(db, fake_embedder, fake_llm):
    # 重排调用失败不致命：回退到原召回顺序，问答照常，不产生 error 事件
    class BoomReranker:
        def __init__(self) -> None:
            self.calls = 0

        def rerank(self, query, texts, top_n):
            self.calls += 1
            raise RuntimeError("network down")

    boom = BoomReranker()
    _seed_three_chunks(db)

    events = await _collect(
        chat_events(
            db, "语义 关键词 命中", None,
            embedder=fake_embedder, llm=fake_llm, reranker=boom,
        )
    )
    types = [e["type"] for e in events]
    assert {"chunk", "sources", "done"} <= set(types)
    assert "error" not in types
    assert boom.calls == 1  # 重排确实被调用过，只是失败了
    sources = next(e for e in events if e["type"] == "sources")["sources"]
    # 回退：原召回的全部候选都在，顺序未丢失（不依赖 BM25 具体顺序）
    assert {s["snippet"] for s in sources} == {"alpha 语义 匹配", "bravo 关键词 命中", "charlie 仅语义 命中"}
