"""对话记忆（上下文窗口管理）测试：滚动压缩 / 关键事实抽取 / 查询改写。

- 全部用例显式传 memory_enabled=True 开启记忆链路（conftest 默认关闭，既有用例不动）。
- 使用 _ScriptedLLM：按调用序号返回预设 token 列表，可指定某次调用抛异常，并记录
  每次 messages 与 temperature，便于断言改写/折叠/抽取的输入。
- 改写判别组由实测选定（pick_embed_pair.py）：raw "那它呢？" 语义余弦 0.33 < 0.5 且
  与分块无关键词重叠（必 miss）；改写 "overlap 默认参数是多少" 与分块向量相同（必 hit）。
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import Chunk, Conversation, Document, Message
from app.services.chat_service import chat_events
from app.services.memory import estimate_tokens


async def _collect(gen):
    return [e async for e in gen]


def _seed_chunk(db, embedder, content: str, query: str) -> None:
    """seed 一个 embedding=embed(query) 的分块（embed 决定检索命中）。"""
    doc = Document(filename="rag.md", file_path="x", file_size=1, source_type="md")
    db.add(doc)
    db.flush()
    db.add(
        Chunk(
            document_id=doc.id,
            content=content,
            chunk_index=0,
            embedding=embedder.embed_query(query),
        )
    )
    db.commit()


class _ScriptedLLM:
    """按调用序号返回预设 token 列表；raise_at 中的序号调用抛异常；记录 messages/temperature。"""

    def __init__(self, scripts: list[list[str]], raise_at: set[int] | None = None):
        self.scripts = scripts
        self.raise_at = raise_at or set()
        self.calls: list[list[dict[str, str]]] = []
        self.temperatures: list[float | None] = []

    async def stream_chat(self, messages, temperature=None):
        idx = len(self.calls)
        self.calls.append(messages)
        self.temperatures.append(temperature)
        if idx in self.raise_at:
            raise RuntimeError(f"scripted failure at call {idx}")
        if idx >= len(self.scripts):
            return
        for token in self.scripts[idx]:
            yield token


def test_estimate_tokens():
    # CJK 每字 1 token，其余按 4 字符 1 token（启发式）
    assert estimate_tokens("") == 0
    assert estimate_tokens("中文") == 2
    assert estimate_tokens("hello") == 1  # 5 // 4
    assert estimate_tokens("你好hello") == 3  # 2 CJK + 5 // 4


async def test_memory_extracts_facts_every_turn(db, fake_embedder, fake_reranker):
    _seed_chunk(db, fake_embedder, content="chunk_size 默认 500 字符，overlap 默认 100。", query="chunk_size 默认是多少")
    llm = _ScriptedLLM([
        ["mock-", "answer"],                                  # call0: 主回答
        ['{"summary": "", "facts": ["用户关注分块参数"]}'],    # call1: 抽取
    ])

    events = await _collect(chat_events(
        db, "chunk_size 默认是多少", None,
        embedder=fake_embedder, llm=llm, reranker=fake_reranker,
        memory_enabled=True, rewrite_enabled=False,
    ))
    assert {"chunk", "sources", "done"} <= {e["type"] for e in events}

    conv = db.execute(select(Conversation)).scalars().one()
    assert conv.key_facts == ["用户关注分块参数"]
    # 抽取输入包含最新一轮 user + assistant 原文
    extract_msg = llm.calls[1][-1]["content"]
    assert "user: chunk_size 默认是多少" in extract_msg
    assert "assistant: mock-answer" in extract_msg
    assert llm.temperatures[1] == 0.1  # 记忆调用走低温


async def test_memory_folds_old_turns_when_over_budget(db, fake_embedder, fake_reranker):
    _seed_chunk(db, fake_embedder, content="chunk_size 默认 500 字符，overlap 默认 100。", query="chunk_size 默认是多少")
    conv = Conversation(title="长对话")
    db.add(conv)
    db.commit()
    block = ("这是一段历史对话消息的内容填充文本，用于把未折叠窗口撑到超过预算，"
             "从而在下一轮触发滚动压缩把旧消息折叠进摘要。") * 4
    first_user = "最早第一条标记：" + block
    # 显式递增 created_at：同一 commit 内 func.now() 时间戳相同，次级排序键是随机
    # UUID 会导致顺序不定；用真实时间差保证 _load_unfolded 的 (created_at, id) 排序确定
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(4):
        db.add(Message(conversation_id=conv.id, role="user", content=first_user if i == 0 else block,
                       created_at=base + timedelta(seconds=i * 2)))
        db.add(Message(conversation_id=conv.id, role="assistant", content=block,
                       created_at=base + timedelta(seconds=i * 2 + 1)))
    db.commit()

    llm = _ScriptedLLM([
        ['{"summary": "旧对话摘要", "facts": ["用户问过参数"]}'],  # call0: 折叠
        ["回答", "内容"],                                          # call1: 主回答
        ['{"summary": "旧对话摘要", "facts": ["用户问过参数"]}'],  # call2: 抽取
    ])
    events = await _collect(chat_events(
        db, "chunk_size 默认是多少", conv.id,
        embedder=fake_embedder, llm=llm, reranker=fake_reranker,
        memory_enabled=True, rewrite_enabled=False,
    ))
    assert {"chunk", "done"} <= {e["type"] for e in events}

    msgs = db.execute(select(Message).where(Message.conversation_id == conv.id)).scalars().all()
    assert len(msgs) == 10  # 8 历史 + 当前问题 + 助手回答
    folded = [m for m in msgs if m.is_folded]
    assert len(folded) == 5  # 折叠最早 5 条，保留最近 4 条原文（2 轮）
    assert db.get(Conversation, conv.id).summary == "旧对话摘要"

    main_msg = llm.calls[1][-1]["content"]
    assert "旧对话摘要" in main_msg
    assert "最早第一条标记" not in main_msg  # 最早原文已折叠，不进 Prompt
    assert "chunk_size 默认是多少" in main_msg


async def test_memory_rewrite_uses_rewritten_query(db, fake_embedder, fake_reranker):
    # 分块只匹配改写后问题：raw "那它呢？" 语义余弦 0.33 < 0.5 且无关键词重叠 → 必 miss
    _seed_chunk(db, fake_embedder, content="overlap 默认参数是 100，chunk_size 默认 500。", query="overlap 默认参数是多少")

    def _seed_history(conv):
        db.add(Message(conversation_id=conv.id, role="user", content="overlap 的默认值是多少？"))
        db.add(Message(conversation_id=conv.id, role="assistant", content="overlap 默认 100 字符。"))
        db.commit()

    # 对照：不改写 → 原问题检索无命中 → no_evidence（证明改写是命中的关键）
    conv_raw = Conversation(title="对照")
    db.add(conv_raw)
    db.commit()
    _seed_history(conv_raw)
    events_raw = await _collect(chat_events(
        db, "那它呢？", conv_raw.id,
        embedder=fake_embedder, llm=_ScriptedLLM([]), reranker=fake_reranker,
        memory_enabled=True, rewrite_enabled=False,
    ))
    assert "no_evidence" in [e["type"] for e in events_raw]

    # 主场景：改写生效 → 命中并流式回答
    llm = _ScriptedLLM([
        ["overlap 默认参数是多少"],                            # call0: 改写
        ["mock-", "answer"],                                    # call1: 主回答
        ['{"summary": "", "facts": ["用户追问 overlap"]}'],     # call2: 抽取
    ])
    conv = Conversation(title="主场景")
    db.add(conv)
    db.commit()
    _seed_history(conv)
    events = await _collect(chat_events(
        db, "那它呢？", conv.id,
        embedder=fake_embedder, llm=llm, reranker=fake_reranker,
        memory_enabled=True, rewrite_enabled=True,
    ))
    types = [e["type"] for e in events]
    assert "no_evidence" not in types
    assert {"chunk", "sources", "done"} <= set(types)

    # 改写调用：system 是改写器，输入含历史与当前问题
    assert llm.calls[0][0]["content"].startswith("你是查询改写器")
    rewrite_msg = llm.calls[0][-1]["content"]
    assert "overlap 的默认值是多少" in rewrite_msg
    assert "那它呢" in rewrite_msg
    # 生成 Prompt：用原问题 + 最近对话 + 检索片段（改写结果只驱动检索，不改生成）
    main_msg = llm.calls[1][-1]["content"]
    assert "那它呢" in main_msg
    assert "overlap 的默认值是多少" in main_msg
    assert "overlap 默认参数是 100" in main_msg


async def test_memory_failure_falls_back(db, fake_embedder, fake_reranker):
    # 折叠/抽取调用失败 → 主回答照常、无 error、无折叠、不写记忆
    _seed_chunk(db, fake_embedder, content="chunk_size 默认 500 字符，overlap 默认 100。", query="chunk_size 默认是多少")
    conv = Conversation(title="长对话")
    db.add(conv)
    db.commit()
    block = ("这是一段历史对话消息的内容填充文本，用于把未折叠窗口撑到超过预算，"
             "从而在下一轮触发滚动压缩把旧消息折叠进摘要。") * 4
    for _ in range(4):
        db.add(Message(conversation_id=conv.id, role="user", content=block))
        db.add(Message(conversation_id=conv.id, role="assistant", content=block))
    db.commit()

    # call0 折叠与 call2 抽取都在抛异常前不产出，脚本内容用不上；主回答在 index 1
    llm = _ScriptedLLM([[], ["回答"]], raise_at={0, 2})
    events = await _collect(chat_events(
        db, "chunk_size 默认是多少", conv.id,
        embedder=fake_embedder, llm=llm, reranker=fake_reranker,
        memory_enabled=True, rewrite_enabled=False,
    ))
    types = [e["type"] for e in events]
    assert {"chunk", "done"} <= set(types)
    assert "error" not in types
    # 折叠失败 → 不折叠、不写摘要（绝不把历史折叠进空摘要）
    msgs = db.execute(select(Message).where(Message.conversation_id == conv.id)).scalars().all()
    assert all(not m.is_folded for m in msgs)
    assert db.get(Conversation, conv.id).summary is None


async def test_memory_disabled_no_extra_calls(db, fake_embedder, fake_reranker):
    _seed_chunk(db, fake_embedder, content="chunk_size 默认 500 字符，overlap 默认 100。", query="chunk_size 默认是多少")
    llm = _ScriptedLLM([["mock-", "answer"]])

    events = await _collect(chat_events(
        db, "chunk_size 默认是多少", None,
        embedder=fake_embedder, llm=llm, reranker=fake_reranker,
        memory_enabled=False,
    ))
    assert {"chunk", "done"} <= {e["type"] for e in events}
    assert len(llm.calls) == 1  # 仅主回答，无改写/折叠/抽取
    conv = db.execute(select(Conversation)).scalars().one()
    assert conv.summary is None
    assert conv.key_facts == []
