"""问答链路：检索 -> 拼 Prompt -> LLM 流式 -> 保存消息与来源。

chat_events 是一个异步生成器，产出 SSE 事件字典：
  {"type": "chunk", "content": "..."}        生成增量
  {"type": "sources", "sources": [...]}      回答完成后的引用来源
  {"type": "no_evidence", "message": "..."}  检索不到足够依据
  {"type": "error", "message": "..."}        LLM 调用失败
  {"type": "done", "conversation_id": "..."} 结束
"""
import asyncio
import logging
from collections.abc import AsyncIterator

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Conversation, Message
from app.providers import get_embedder, get_llm, get_reranker
from app.providers.embeddings import Embedder
from app.providers.llm import LLM
from app.providers.rerank import Reranker
from app.services.retrieval import RetrievalHit, search_chunks

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "你是一个严谨的企业知识库问答助手。请仅依据给定的参考资料回答用户问题，"
    "如果资料不足以回答，请明确说明不知道，不要编造内容。"
    "回答尽量简洁，并在引用资料处标注 [1]、[2] 等编号。"
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


def build_messages(question: str, hits: list[RetrievalHit]) -> list[dict[str, str]]:
    context = "\n\n".join(f"[{i + 1}] {h.chunk.content}" for i, h in enumerate(hits))
    user = (
        f"参考资料：\n{context}\n\n用户问题：{question}\n\n"
        "请仅基于上述参考资料回答，引用时用 [1]、[2] 标注对应资料。"
    )
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


async def chat_events(
    db: Session,
    question: str,
    conversation_id,
    embedder: Embedder | None = None,
    llm: LLM | None = None,
    reranker: Reranker | None = None,
) -> AsyncIterator[dict]:
    embedder = embedder or get_embedder()
    llm = llm or get_llm()
    reranker = reranker or get_reranker()

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

    # ---- 2. 混合检索（关键词 BM25 + 语义向量，RRF 融合）----
    try:
        query_vec = await asyncio.to_thread(embedder.embed_query, question)
    except Exception as exc:  # noqa: BLE001
        logger.exception("embed query failed")
        yield {"type": "error", "message": f"问题向量化失败: {exc}"}
        return
    # 开启重排时先召回更宽的候选池（rerank_candidates），重排后再收窄到 top_k
    search_k = settings.rerank_candidates if settings.rerank_enabled else settings.top_k
    try:
        hits = search_chunks(db, query_vec, question, top_k=search_k)
    except ValueError as exc:
        yield {"type": "error", "message": f"检索参数错误: {exc}"}
        return
    logger.info("retrieval hits=%d", len(hits))

    # ---- 2.5 在线重排（非致命：失败回退原召回顺序）----
    if settings.rerank_enabled and settings.rerank_api_key and len(hits) > 1:
        try:
            hits = await asyncio.to_thread(_rerank_hits, reranker, question, hits, settings.top_k)
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

    # ---- 4. 流式生成 ----
    sources = build_sources(hits)
    messages = build_messages(question, hits)
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

    if error_msg:
        yield {"type": "error", "message": error_msg}
    else:
        yield {"type": "sources", "sources": sources}
    yield {"type": "done", "conversation_id": str(conversation.id)}
