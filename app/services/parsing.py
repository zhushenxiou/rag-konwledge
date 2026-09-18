"""文档解析：txt / md / pdf / docx -> 文本分节列表。

每节为 {"text": str, "metadata": dict}。
- pdf : 按页分节，metadata 带 page
- md  : 按标题分节，metadata 带 heading
- txt / docx : 按段落分节
"""
import re
from pathlib import Path

from app.services.chunking import Section

# layout 模式用空格还原文位，其中绝大部分是"填到下一列"的无效留白
_WS_RUN = re.compile(r"[ \t]{3,}")

# 围栏代码块的分界行（``` 或 ~~~，3 个及以上）
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")


def parse_file(path: Path, source_type: str) -> list[Section]:
    if source_type == "txt":
        return _parse_txt(path)
    if source_type == "md":
        return _parse_md(path)
    if source_type == "pdf":
        return _parse_pdf(path)
    if source_type == "docx":
        return _parse_docx(path)
    raise ValueError(f"不支持的文件类型: {source_type}")


def _read_text(path: Path) -> str:
    """优先 utf-8，回退 gbk，兼容中文文本。"""
    for encoding in ("utf-8", "gbk", "utf-8-sig"):
        try:
            return Path(path).read_text(encoding=encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return Path(path).read_text(errors="ignore")


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in text.splitlines() if p.strip()]


def _parse_txt(path: Path) -> list[Section]:
    text = _read_text(path)
    return [Section(text=p, metadata={}) for p in _paragraphs(text)] or [
        Section(text="", metadata={})
    ]


def _parse_md(path: Path) -> list[Section]:
    """按标题分节。

    **标题行本身要同时写进正文**：原实现只把它塞进 `metadata["heading"]`，而该字段
    全仓库只有写、没有任何读（只随 chunk 落库到 `chunk_metadata` 列）。检索侧只看
    chunk 正文——向量与 BM25 都只索引 `content`——于是标题文字既进不了向量也进不了
    关键词索引。而标题恰恰是关键词最密集、最像用户提问的部分（"环境变量参考"、
    "如何配置数据库"），丢了它，问标题就永远命中不到。实测仓库自己的 README.md：
    修复前 42 个节的标题文字在 chunk 正文里出现 **0 次**（其中还混着 8 个被误判成标题的
    代码块注释），修复后 41 个真标题全部进入正文。

    代码块按**围栏**识别（``` / ~~~）：围栏内既不当标题、也不 strip。原实现逐行
    `strip()` 且完全不认围栏，于是 ① 代码块里的 `# 注释` 被当成标题（连带把
    `heading` 元数据污染成一句注释）；② `    return 1` 被压成 `return 1` ——
    缩进是代码语义的一部分，压掉就等于把代码改坏。
    """

    text = _read_text(path)
    sections: list[Section] = []
    heading = ""
    buf: list[str] = []
    fence = ""  # 非空表示正处于围栏代码块内，值为开栏标记
    for line in text.splitlines():
        m = _FENCE.match(line)
        if m:
            marker = m.group(1)
            if not fence:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = ""  # 同字符且不短于开栏标记，才算闭合
            buf.append(line.rstrip())
            continue
        if fence:
            buf.append(line.rstrip())  # 代码块内：保留缩进，空行也不丢
            continue
        if line.strip().startswith("#"):
            if buf:
                sections.append(Section(text="\n".join(buf), metadata={"heading": heading}))
                buf = []
            heading = line.strip()
            buf.append(heading)  # 标题进入正文，检索才命中得到
        elif line.strip():
            buf.append(line.strip())
    if buf:
        sections.append(Section(text="\n".join(buf), metadata={"heading": heading}))
    return sections or [Section(text="", metadata={})]


def _squeeze(text: str) -> str:
    """压缩 layout 模式的空格填充。

    保留 2 个空格当"这里是列边界"的记号，但不保留列宽 —— 下游不按列位做算术，
    列宽纯属浪费。实测一份成绩单：16808 字符压到 9321，分块数从 44 落回 25
    （与 plain 模式的 24 持平）。压成 1 个空格会让列边界与普通词间空格混同。
    """
    return "\n".join(_WS_RUN.sub("  ", line).rstrip() for line in text.splitlines())


def _parse_pdf(path: Path) -> list[Section]:
    """用 layout 模式按**文字坐标**还原版面。

    plain 模式（pypdf 默认）按文字绘制顺序输出，同一视觉行的内容会被拆成独立行，
    甚至一个词、一个字符一行 —— 实测一份成绩单 PDF 出来 **385 行碎片**：

        院 / ( / 系 / )/ / 部：计算机工程学院 / 20 / 计算机科学与技术 / 1

    `院(系)/部：` 这种标签被拆成 5 行，行列语义彻底消失。同一份文件换 layout
    模式只有 **37 行**，列对齐、行成组。散文 PDF 下两种模式输出一致（实测逐字节
    相同），故统一用 layout 不必分情况。

    `extraction_mode` 需要 pypdf>=5（requirements.txt 已锁定），故不再兼容更老版本。
    """
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    sections: list[Section] = []
    for i, page in enumerate(reader.pages, start=1):
        text = _squeeze(page.extract_text(extraction_mode="layout") or "").strip()
        if text:
            sections.append(Section(text=text, metadata={"page": i}))
    return sections


def _cell_text(cell) -> str:
    """单元格内容压成一行（Markdown 表格单元格不能含换行）。

    用 `iter_inner_content` 而非 `cell.paragraphs`，否则**嵌套表格**会被丢掉
    —— 和当初 `doc.paragraphs` 丢掉表格是同一个错误。
    """
    from docx.table import Table

    parts: list[str] = []
    for block in cell.iter_inner_content():
        if isinstance(block, Table):
            # 嵌套表格拍平：外层单元格里塞不下换行
            parts.extend(" ".join(r) for r in _table_rows(block))
        else:
            text = block.text.strip()
            if text:
                parts.append(text)
    return " ".join(parts)


def _table_rows(table) -> list[list[str]]:
    """表格 -> 二维文本。合并单元格（gridSpan/vMerge）由 python-docx 重复返回同一
    单元格，文本因此重复但**列仍然对齐**，这正是我们想要的。"""
    rows: list[list[str]] = []
    for row in table.rows:
        cells = [_cell_text(c) for c in row.cells]
        if any(cells):  # 丢掉整行皆空的行
            rows.append(cells)
    return rows


def _table_to_text(table) -> str:
    """表格 -> Markdown 文本。

    为什么不直接拼成空格分隔的一行：表格是**二维**的，拍平成一维会永久丢失
    行列对应关系，LLM 再也无法回答"温度那一行的单位是什么"。Markdown 是纯文本
    里唯一能同时表达二维结构、又不需要额外字段的方案。
    """
    rows = _table_rows(table)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]  # 行列不齐时补齐
    # 单元格内的 | 必须转义，否则会把表格切断
    lines = ["| " + " | ".join(c.replace("|", "\\|") for c in r) + " |" for r in rows]
    if len(lines) > 1:
        # 首行之后插入分隔行，明确"这是表头"；单行表格不插（否则只剩一个假表头）
        lines.insert(1, "| " + " | ".join(["---"] * width) + " |")
    return "\n".join(lines)


def _parse_docx(path: Path) -> list[Section]:
    """按**文档顺序**遍历段落与表格。

    不能用 `doc.paragraphs`：它只产出正文直属的 `<w:p>`，而表格内容是
    `<w:tbl> → <w:tr> → <w:tc> → <w:p>` 的嵌套结构，一整块静默消失——
    文档状态仍是 ready、chunk 数也 > 0，只是检索永远查不到表格内容。
    这是最坏的失败形态：不报错、看不出、查不到。
    """
    from docx import Document as DocxDocument
    from docx.table import Table

    doc = DocxDocument(str(path))
    sections: list[Section] = []
    for block in doc.iter_inner_content():  # 按 XML 顺序产出 Paragraph / Table
        if isinstance(block, Table):
            text = _table_to_text(block)
            if text:
                # has_table 语义是"**含有**表格"：_merge_meta 是累加的，
                # 表格与段落合并进同一 chunk 时它会保留下来，不会被清掉。
                sections.append(Section(text=text, metadata={"has_table": True}))
        else:
            text = block.text.strip()
            if text:
                sections.append(Section(text=text, metadata={}))
    return sections or [Section(text="", metadata={})]
