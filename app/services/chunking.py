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

# Markdown 表格行：以 | 开头、以 | 结尾。docx 表格由 parsing 转成这种形式，
# md 里手写的表格天然就是这种形式，故两者共用同一条识别路径。
_TABLE_ROW = re.compile(r"^\|.*\|$")
# 表头下的分隔行，如 `| --- | :---: |`
_TABLE_SEP = re.compile(r"^\|(?:\s*:?-+:?\s*\|)+$")

# 围栏代码块的分界行（``` 或 ~~~，3 个及以上）
_FENCE_LINE = re.compile(r"^\s*(?:`{3,}|~{3,})")

# 节与节之间插入的分隔。原先这里是 `buffer += text` 直接拼接，节边界被整个抹掉：
# txt 每个非空行是一节，实测三段并成 `第一段内容。第二段内容。第三段内容。`；
# docx 两段并成 `第一段第二段`；md 的代码围栏会粘到上一段句尾
# （`…→ 测试。```powershell`）而不再位于行首、围栏因此失效。
# 实测仓库自己的 README.md：41 个节间边界有 38 个是这种无分隔粘连。
_SECTION_SEP = "\n\n"


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


def _split_table_rows(block: list[str], chunk_size: int) -> list[str]:
    """超长表格按**行**切，每块重复表头。

    为什么必须重复表头：列名是表格语义的全部。原实现把整段表格当普通文本走
    `_split_at_sentence`，而表格行里没有句子结束符 → 走硬切分支 → 切点落在
    **一行中间**，切出 `| XG-1013 | 195W | 3.9` 这种残片；更要命的是第二块起
    完全没有表头，只剩一串无列名的值，"XG-1015 的额定功率是多少"就只能靠猜列序。

    块间**刻意不重叠**：表格行是自包含的，重叠只会让同一行在多个 chunk 里重复
    召回，并不带来上下文收益（普通文本靠 overlap 保上下文连续性）。

    单行本身就超 chunk_size 时无法再切，该块会超限 —— 这是刻意的终止条件，
    否则这里会死循环。
    """
    has_sep = len(block) > 1 and _TABLE_SEP.match(block[1])
    header = block[:2] if has_sep else block[:1]
    body = block[len(header) :]
    head_text = "\n".join(header)
    if not body:
        return [head_text]

    groups: list[list[str]] = []
    current: list[str] = []
    size = len(head_text)
    for line in body:
        if current and size + 1 + len(line) > chunk_size:
            groups.append(current)
            current, size = [], len(head_text)
        current.append(line)
        size += 1 + len(line)
    if current:
        groups.append(current)
    return ["\n".join(header + g) for g in groups]


def _table_parts(text: str, chunk_size: int) -> list[tuple[bool, str]] | None:
    """文本里含**超限**表格时，切成 (是否表格, 文本) 序列；否则返回 None。

    返回 None 表示"走原路径" —— 绝大多数节（纯段落、装得下的小表格）都走这条，
    行为与加表格处理之前逐字节一致。只有真的出现装不下的表格，才切到新路径。

    围栏代码块内的 `|…|` 行**不算表格**：靠行结构识别分不出"真的表格"和"代码里
    演示的表格"，一份讲 Markdown 语法的文档会被切成十几块、每块顶着 ` ``` ` 当表头。
    """
    lines = text.splitlines()
    parts: list[tuple[bool, str]] = []
    prose: list[str] = []
    found_oversized = False
    in_fence = False
    i = 0
    while i < len(lines):
        if _FENCE_LINE.match(lines[i]):
            in_fence = not in_fence
            prose.append(lines[i])
            i += 1
            continue
        if in_fence or not _TABLE_ROW.match(lines[i]):
            prose.append(lines[i])
            i += 1
            continue
        j = i
        while j < len(lines) and _TABLE_ROW.match(lines[j]):
            j += 1
        block = lines[i:j]
        block_text = "\n".join(block)
        if len(block_text) > chunk_size:
            found_oversized = True
            if prose:
                parts.append((False, "\n".join(prose)))
                prose = []
            parts.append((True, block_text))
        else:
            prose.extend(block)  # 小表格仍与相邻文字一起走原路径（合并进同一 chunk）
        i = j
    if prose:
        parts.append((False, "\n".join(prose)))
    return parts if found_oversized else None


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

    def flush(with_overlap: bool) -> None:
        """把 buffer 落成一个 chunk。

        with_overlap=False 用于「超限表格之前」：表格要独立成块，不能把上一段
        文字的重叠尾巴带进表格 chunk，否则表格块里混进散文、列结构不再干净。
        """
        nonlocal buffer, buffer_meta
        if not buffer.strip():
            buffer, buffer_meta = "", {}
            return
        pieces.append(ChunkPiece(content=buffer, metadata=buffer_meta))
        buffer = buffer[-overlap:] if (with_overlap and overlap) else ""
        buffer_meta = dict(buffer_meta) if with_overlap else {}

    def feed(text: str, meta: dict) -> None:
        nonlocal buffer, buffer_meta
        sep = _SECTION_SEP if buffer else ""
        # 当前 buffer 加上该节会超长：先把 buffer 切走（分隔符也计入，否则会超限）
        if buffer and len(buffer) + len(sep) + len(text) > chunk_size:
            flush(with_overlap=True)
            sep = _SECTION_SEP if buffer else ""
        buffer += sep + text
        buffer_meta = _merge_meta(buffer_meta, meta) if buffer_meta else dict(meta)
        # 单个大节超过 chunk_size：反复在句子边界切
        while len(buffer) > chunk_size:
            head, buffer = _split_at_sentence(buffer, chunk_size, overlap)
            pieces.append(ChunkPiece(content=head, metadata=dict(buffer_meta)))

    for sec in sections:
        text = sec.text.strip()
        if not text:
            continue
        parts = _table_parts(text, chunk_size)
        if parts is None:
            feed(text, sec.metadata)
            continue
        for is_table, part in parts:
            if is_table:
                flush(with_overlap=False)  # 超限表格独立成块
                for piece in _split_table_rows(part.splitlines(), chunk_size):
                    pieces.append(ChunkPiece(content=piece, metadata=dict(sec.metadata)))
            else:
                feed(part, sec.metadata)

    flush(with_overlap=False)
    return pieces
