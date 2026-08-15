"""问答链路：检索 -> 拼 Prompt -> LLM 流式 -> 保存消息与来源。

chat_events 是一个异步生成器，产出 SSE 事件字典：
  {"type": "chunk", "content": "..."}        生成增量
  {"type": "sources", "sources": [...]}      回答完成后的引用来源
  {"type": "no_evidence", "message": "..."}  检索不到足够依据
  {"type": "error", "message": "..."}        LLM 调用失败
  {"type": "done", "conversation_id": "..."} 结束

上下文窗口管理（实现见 app/services/memory.py，全部**非致命**，失败静默回退）：
- 滚动压缩：未折叠原文窗口超预算时，把旧轮折叠进对话摘要（conversations.summary）。
- 查询改写：多轮时把追问改写成自包含问题，仅用于检索（指代消解）。
- 关键事实抽取：每轮回答落库后抽取长期事实，持久化到 conversations.key_facts。
"""
import asyncio
import logging
from collections.abc import AsyncIterator

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Conversation, Message
from app.providers import get_embedder, get_llm, get_reranker
from app.providers.embeddings import Embedder
from app.providers.llm import LLM
from app.providers.rerank import Reranker
from app.services.memory import estimate_tokens, maintain_memory, render_turns, rewrite_question
from app.services.retrieval import RetrievalHit, search_chunks

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "你是一个严谨的企业知识库问答助手。请仅依据给定的参考资料回答用户问题，"
    "如果资料不足以回答，请明确说明不知道，不要编造内容。"
    "回答尽量简洁，并在引用资料处标注 [1]、[2] 等编号。"
    "可参考对话记忆中的摘要、关键事实与最近对话来理解上下文，但企业信息一律以参考资料为准。"
)
NO_EVIDENCE_MSG = "当前知识库中没有找到足够依据来回答该问题。请尝试换一种问法，或先上传相关资料。"


def build_sources(hits: list[RetrievalHit]) -> list[dict]:
    """把检索命中的分块整理成可追溯的引用列表。"""
    return [
        {
            "document_id": str(h.document.id),
            "filename": h.document.filename,
            "chunk_id": str(h.chunk.id),
            "chunk_index": h.chunk.chunk_index,
            "similarity": h.similarity,
            "snippet": h.chunk.content[:200],
        }
        for h in hits
    ]


def build_messages(
    question: str,
    hits: list[RetrievalHit],
    summary: str = "",
    facts: list[str] | None = None,
    recent: list[tuple[str, str]] | None = None,
) -> list[dict[str, str]]:
    """组装生成 Prompt：system + 记忆块（摘要/事实/最近对话）+ 检索片段 + 问题。

    summary / facts / recent 均可选——不传则与纯单轮完全一致（兼容旧调用与测试）。
    """
    context = "\n\n".join(f"[{i + 1}] {h.chunk.content}" for i, h in enumerate(hits))
    parts: list[str] = []
    if summary:
        parts.append(f"对话记忆摘要：\n{summary}")
    if facts:
        parts.append("长期关键信息：\n" + "\n".join(f"- {f}" for f in facts))
    if recent:
        parts.append("最近对话：\n" + render_turns(recent))
    parts.append(f"参考资料：\n{context}")
    parts.append(f"用户问题：{question}")
    user = "\n\n".join(parts) + "\n\n请仅基于上述参考资料回答，引用时用 [1]、[2] 标注对应资料。"
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _rerank_hits(
    reranker: Reranker, question: str, hits: list[RetrievalHit], top_k: int
) -> list[RetrievalHit]:
    """在线重排：调 rerank 模型按 (query, chunk) 相关性打分，取 top_k。

    同步函数，由调用方放到 asyncio.to_thread 里跑。相似度字段改写为
    rerank 模型的相关性分数（0~1，请求内相对值），让 sources 展示的
    similarity 与最终排序口径一致。
    """
    scores = reranker.rerank(question, [h.chunk.content for h in hits], top_k)
    ranked = sorted(zip(hits, scores), key=lambda pair: pair[1], reverse=True)
    out: list[RetrievalHit] = []
    for hit, score in ranked[:top_k]:
        hit.similarity = round(float(score), 4)
        out.append(hit)
    return out


def _save_message(
    db: Session,
    conversation_id,
    role: str,
    content: str,
    sources: list[dict],
) -> Message:
    msg = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        sources=sources or [],
    )
    db.add(msg)
    return msg


def _load_unfolded(db: Session, conversation_id) -> list[Message]:
    """按时间序取未折叠消息（order by created_at, id，避免同秒并列顺序不定）。"""
    stmt = (
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.is_folded.is_(False),
        )
        .order_by(Message.created_at.asc(), Message.id.asc())
    )
    return list(db.execute(stmt).scalars())


async def chat_events(
    db: Session,
    question: str,
    conversation_id,
    embedder: Embedder | None = None,
    llm: LLM | None = None,
    reranker: Reranker | None = None,
    memory_enabled: bool | None = None,
    rewrite_enabled: bool | None = None,
    memory_extract: bool | None = None,
) -> AsyncIterator[dict]:
    embedder = embedder or get_embedder()
    llm = llm or get_llm()
    reranker = reranker or get_reranker()
    mem_on = settings.memory_enabled if memory_enabled is None else memory_enabled
    rw_on = settings.memory_rewrite_enabled if rewrite_enabled is None else rewrite_enabled
    extract_on = settings.memory_extract_every_turn if memory_extract is None else memory_extract

    # ---- 1. 会话：创建或复用，保存用户问题 ----
    if conversation_id is not None:
        conversation = db.get(Conversation, conversation_id)
        if conversation is None:
            yield {"type": "error", "message": "会话不存在，请重新发起问答"}
            return
        conversation.updated_at = func.now()
    else:
        conversation = Conversation(title=(question[:30] or "新对话"))
        db.add(conversation)
        db.flush()
    _save_message(db, conversation.id, "user", question, [])
    db.commit()

    summary = conversation.summary or ""
    facts = list(conversation.key_facts or [])
    prior_msgs: list[Message] = []

    # ---- 1.5 对话记忆维护（非致命）：滚动压缩 + 查询改写 ----
    if mem_on:
        recent_msgs = _load_unfolded(db, conversation.id)
        # 滚动压缩：未折叠原文窗口超预算 → 折叠旧轮进摘要，保留最近 N 轮原文
        recent_text = render_turns([(m.role, m.content) for m in recent_msgs])
        if estimate_tokens(recent_text) > settings.memory_recent_tokens:
            keep_n = settings.memory_recent_rounds * 2  # N 轮 = 2N 条消息
            fold_msgs = recent_msgs[: max(0, len(recent_msgs) - keep_n)]
            if fold_msgs:
                fold_text = render_turns([(m.role, m.content) for m in fold_msgs])
                ok, summary, facts = await maintain_memory(
                    llm, summary, facts, fold_text, settings.memory_max_facts
                )
                # 只有记忆更新成功才折叠：折叠进空摘要 = 丢历史，绝不发生
                if ok and summary:
                    for m in fold_msgs:
                        m.is_folded = True
                    conversation.summary = summary
                    conversation.key_facts = facts
                    db.commit()
                    logger.info("memory fold: folded %d messages", len(fold_msgs))
                    recent_msgs = recent_msgs[len(fold_msgs):]  # 保留窗口 = 最近 keep_n 条
                else:
                    logger.warning("memory fold skipped: 记忆更新未成功，保留原文窗口")
        prior_msgs = recent_msgs[:-1]  # 去掉刚保存的当前问题，得到历史原文窗口
        # 查询改写（仅检索用）：有历史才做，失败用原问题
        search_question = question
        if rw_on and prior_msgs:
            recent_turns = render_turns([(m.role, m.content) for m in prior_msgs[-4:]])
            search_question = await rewrite_question(llm, question, recent_turns)
            logger.info("rewrite: %r -> %r", question, search_question)
    else:
        search_question = question

    # ---- 2. 混合检索（关键词 BM25 + 语义向量，RRF 融合）----
    try:
        query_vec = await asyncio.to_thread(embedder.embed_query, search_question)
    except Exception as exc:  # noqa: BLE001
        logger.exception("embed query failed")
        yield {"type": "error", "message": f"问题向量化失败: {exc}"}
        return
    # 开启重排时先召回更宽的候选池（rerank_candidates），重排后再收窄到 top_k
    search_k = settings.rerank_candidates if settings.rerank_enabled else settings.top_k
    try:
        hits = search_chunks(db, query_vec, search_question, top_k=search_k)
    except ValueError as exc:
        yield {"type": "error", "message": f"检索参数错误: {exc}"}
        return
    logger.info("retrieval hits=%d", len(hits))

    # ---- 2.5 在线重排（非致命：失败回退原召回顺序）----
    if settings.rerank_enabled and settings.rerank_api_key and len(hits) > 1:
        try:
            hits = await asyncio.to_thread(_rerank_hits, reranker, search_question, hits, settings.top_k)
            logger.info("rerank applied: candidates=%d -> top_k=%d", search_k, len(hits))
        except Exception as exc:  # noqa: BLE001
            logger.warning("rerank failed, fallback to original order: %s", exc)
            hits = hits[: settings.top_k]
    else:
        hits = hits[: settings.top_k]

    # ---- 3. 无依据处理 ----
    if not hits:
        _save_message(db, conversation.id, "assistant", NO_EVIDENCE_MSG, [])
        db.commit()
        yield {"type": "no_evidence", "message": NO_EVIDENCE_MSG}
        yield {"type": "done", "conversation_id": str(conversation.id)}
        return

    # ---- 4. 流式生成（Prompt = 记忆块 + 检索片段 + 问题）----
    sources = build_sources(hits)
    recent_for_prompt = (
        [(m.role, m.content) for m in prior_msgs] if mem_on else None
    )
    messages = build_messages(
        question, hits, summary=summary, facts=facts, recent=recent_for_prompt
    )
    collected: list[str] = []
    error_msg: str | None = None
    try:
        async for token in llm.stream_chat(messages):
            collected.append(token)
            yield {"type": "chunk", "content": token}
    except Exception as exc:  # noqa: BLE001
        logger.exception("LLM stream failed")
        error_msg = f"模型调用失败: {exc}"

    answer = "".join(collected)
    _save_message(db, conversation.id, "assistant", answer or "", sources)
    db.commit()

    # ---- 5. 每轮抽取关键事实（非致命）----
    if mem_on and extract_on and answer:
        latest_turn = render_turns([("user", question), ("assistant", answer)])
        ok, summary, facts = await maintain_memory(
            llm, summary, facts, latest_turn, settings.memory_max_facts
        )
        if ok:
            conversation.summary = summary
            conversation.key_facts = facts
            db.commit()
        else:
            logger.warning("memory extract skipped: 抽取未成功，保持原记忆")

    if error_msg:
        yield {"type": "error", "message": error_msg}
    else:
        yield {"type": "sources", "sources": sources}
    yield {"type": "done", "conversation_id": str(conversation.id)}
