"""分块：段落感知，chunk_size / overlap 可配置，避免在句子中间硬切。

输入为 Section 列表（每个 section 自带 metadata，如页码/标题）。
算法：尽量把相邻段落合并进一个 chunk，超过 chunk_size 时在句子边界处切开，
切分时保留 overlap 长度的尾部文本以保证上下文连续。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.config import settings

# 中英文句子结束符
_SENT_END = re.compile(r"(?<=[。！？!?；;])")


@dataclass
class Section:
    """文档解析结果中的一个节：文本 + 元信息（页码、标题等）。"""

    text: str
    metadata: dict = field(default_factory=dict)


@dataclass
class ChunkPiece:
    content: str
    metadata: dict = field(default_factory=dict)


def _merge_meta(a: dict, b: dict) -> dict:
    """合并两个 section 的 metadata：记录页码集合与最新标题。"""
    out = dict(a)
    pages: set = set()
    for m in (a, b):
        if m.get("page"):
            pages.add(int(m["page"]))
    if pages:
        out["pages"] = sorted(pages)
    if b.get("heading"):
        out["heading"] = b["heading"]
    return out


def _split_at_sentence(text: str, chunk_size: int, overlap: int) -> tuple[str, str]:
    """在尽量靠近 chunk_size 的句子边界处切开，返回 (head, tail)。

    tail 从 cut - overlap 开始，保证相邻分块上下文连续。
    """
    if len(text) <= chunk_size:
        return text, ""
    cut = None
    for m in _SENT_END.finditer(text):
        if m.end() <= chunk_size:
            cut = m.end()
        else:
            break
    # 找不到合适的边界（或边界太靠前）则硬切
    if cut is None or cut < chunk_size * 0.6:
        cut = chunk_size
    head = text[:cut]
    tail = text[max(0, cut - overlap) :]
    return head, tail


def chunk_sections(
    sections: list[Section],
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[ChunkPiece]:
    chunk_size = chunk_size or settings.chunk_size
    overlap = min(overlap if overlap is not None else settings.overlap, chunk_size - 1)

    pieces: list[ChunkPiece] = []
    buffer = ""
    buffer_meta: dict = {}

    for sec in sections:
        text = sec.text.strip()
        if not text:
            continue
        # 当前 buffer 加上该节会超长：先把 buffer 切走
        if buffer and len(buffer) + len(text) > chunk_size:
            pieces.append(ChunkPiece(content=buffer, metadata=buffer_meta))
            buffer = buffer[-overlap:] if overlap else ""
            buffer_meta = dict(buffer_meta)

        buffer += text
        buffer_meta = _merge_meta(buffer_meta, sec.metadata) if buffer_meta else dict(sec.metadata)

        # 单个大节超过 chunk_size：反复在句子边界切
        while len(buffer) > chunk_size:
            head, buffer = _split_at_sentence(buffer, chunk_size, overlap)
            pieces.append(ChunkPiece(content=head, metadata=dict(buffer_meta)))

    if buffer.strip():
        pieces.append(ChunkPiece(content=buffer, metadata=buffer_meta))

    return pieces
