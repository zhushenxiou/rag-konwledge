"""问答链路测试：SSE 事件、消息持久化、引用来源、无依据处理。"""
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


async def test_chat_happy_path_streams_answer_and_sources(db, fake_embedder, fake_llm):
    _seed_matching_chunk(db, fake_embedder)

    events = await _collect(
        chat_events(db, "chunk_size 默认是多少", None, embedder=fake_embedder, llm=fake_llm)
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


async def test_chat_no_evidence_when_nothing_retrieved(db, fake_embedder, fake_llm):
    # 知识库为空，且问题与任何内容都不相关
    events = await _collect(
        chat_events(db, "某个完全无关的问题", None, embedder=fake_embedder, llm=fake_llm)
    )
    types = [e["type"] for e in events]
    assert types == ["no_evidence", "done"]

    msg = db.execute(
        select(Message).where(Message.role == "assistant")
    ).scalars().first()
    assert msg and "没有找到足够依据" in msg.content


async def test_chat_reuses_existing_conversation(db, fake_embedder, fake_llm):
    conv = Conversation(title="旧会话")
    db.add(conv)
    db.commit()
    _seed_matching_chunk(db, fake_embedder)

    events = await _collect(
        chat_events(db, "chunk_size 默认是多少", conv.id, embedder=fake_embedder, llm=fake_llm)
    )
    done = next(e for e in events if e["type"] == "done")
    assert done["conversation_id"] == str(conv.id)

    msgs = db.execute(
        select(Message).where(Message.conversation_id == conv.id)
    ).scalars().all()
    assert len(msgs) == 2


async def test_chat_unknown_conversation_returns_error(db, fake_embedder, fake_llm):
    import uuid

    events = await _collect(
        chat_events(db, "问题", uuid.uuid4(), embedder=fake_embedder, llm=fake_llm)
    )
    assert events[0]["type"] == "error"
