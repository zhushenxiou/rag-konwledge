"""对话记忆：上下文窗口管理的核心实现。

三个能力（全部复用 LLM.stream_chat 收集文本，**非致命**——失败静默回退）：
- estimate_tokens：CJK 感知的 token 估算（仓库无 tiktoken，纯启发式，仅用于压缩触发判断）。
- maintain_memory：滚动摘要 + 关键事实抽取（一次 LLM 调用，输出 JSON）。
- rewrite_question：多轮追问改写成自包含问题（供检索用，指代消解）。
"""
import json
import logging
import re

from app.providers.llm import LLM

logger = logging.getLogger(__name__)

# CJK 感知 token 估算：CJK 字符 / 全角标点各算 1 token，其余按 4 字符 1 token
_CJK_RANGES = (
    (0x4E00, 0x9FFF),  # CJK 统一表意文字
    (0x3000, 0x303F),  # CJK 标点
    (0xFF00, 0xFFEF),  # 全角形式
)

SYSTEM_MAINTAIN = (
    "你是对话记忆维护器。根据现有摘要、现有关键事实和新增的对话内容更新记忆，"
    '严格返回 JSON：{{"summary": "...", "facts": ["...", "..."]}}。'
    "要求：summary 是整段对话的要点摘要，不超过 300 字；"
    "facts 是长期有效、对后续回答有用的事实（用户偏好、已确认的参数/结论、指代对象等），"
    "每条不超过 30 字，总数不超过 {max_facts} 条；合并重复、删除已过时条目。"
    "只输出 JSON，不要任何解释。"
)

SYSTEM_REWRITE = (
    "你是查询改写器。根据最近对话，把用户当前问题改写成一个脱离上下文也能独立检索的"
    "自包含问句。如果问题本身已经自包含或与最近对话无关，原样输出。"
    "只输出改写后的问句，不要解释、不要加引号。"
)


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数（CJK 每字 1 token，其余 4 字符 1 token）。

    仓库没有 tiktoken，这是可控的启发式；仅用于触发滚动压缩的预算判断。
    """
    cjk = sum(1 for ch in text if any(lo <= ord(ch) <= hi for lo, hi in _CJK_RANGES))
    return cjk + (len(text) - cjk) // 4


def render_turns(turns: list[tuple[str, str]]) -> str:
    """把 [(role, content)] 渲染成对话文本，供记忆维护 / 改写 / Prompt 使用。"""
    return "\n".join(f"{role}: {content}" for role, content in turns)


async def _chat_text(llm: LLM, messages: list[dict[str, str]], temperature: float) -> str:
    """收集一次 stream_chat 的完整文本。"""
    parts = []
    async for token in llm.stream_chat(messages, temperature=temperature):
        parts.append(token)
    return "".join(parts)


def _extract_json(text: str) -> dict | None:
    """宽容解析 LLM 输出的 JSON：先整体解析，失败则截取首个 {...} 块再试。"""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


async def maintain_memory(
    llm: LLM,
    summary: str,
    facts: list[str],
    turns_text: str,
    max_facts: int,
) -> tuple[bool, str, list[str]]:
    """滚动摘要 + 关键事实抽取（一次调用）。

    返回 (ok, summary, facts)：ok=False 表示 LLM 调用失败或返回不可解析的 JSON，
    此时 summary/facts 为原值（调用方据此决定是否折叠——绝不能把消息折叠进
    空摘要，否则丢历史）。
    """
    user_parts: list[str] = []
    if summary:
        user_parts.append(f"现有摘要：\n{summary}")
    if facts:
        user_parts.append("现有关键事实：\n" + "\n".join(f"- {f}" for f in facts))
    user_parts.append("新增对话内容：\n" + turns_text)

    messages = [
        {"role": "system", "content": SYSTEM_MAINTAIN.format(max_facts=max_facts)},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]
    try:
        raw = await _chat_text(llm, messages, temperature=0.1)
        data = _extract_json(raw)
        if data is None:
            logger.warning("memory maintain: LLM 返回不可解析内容，保持原记忆")
            return False, summary, facts
        new_summary = str(data.get("summary") or "").strip()
        new_facts = [
            str(f).strip() for f in (data.get("facts") or []) if str(f).strip()
        ]
        return True, (new_summary or summary), (new_facts[:max_facts] or facts)
    except Exception as exc:  # noqa: BLE001  非致命
        logger.warning("memory maintain failed, keep old memory: %s", exc)
        return False, summary, facts


async def rewrite_question(llm: LLM, question: str, recent_turns_text: str) -> str:
    """把追问改写为自包含问题（仅用于检索）。异常 → 原样返回问题。"""
    messages = [
        {"role": "system", "content": SYSTEM_REWRITE},
        {"role": "user", "content": f"最近对话：\n{recent_turns_text}\n\n用户当前问题：{question}"},
    ]
    try:
        rewritten = (await _chat_text(llm, messages, temperature=0.1)).strip().strip("“”\"'")
        return rewritten or question
    except Exception as exc:  # noqa: BLE001  非致命，回退原问题
        logger.warning("question rewrite failed, use original: %s", exc)
        return question
