"""文档解析：txt / md / pdf / docx -> 文本分节列表。

每节为 {"text": str, "metadata": dict}。
- pdf : 按页分节，metadata 带 page
- md  : 按标题分节，metadata 带 heading
- txt / docx : 按段落分节
"""
from pathlib import Path

from app.services.chunking import Section


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
    text = _read_text(path)
    sections: list[Section] = []
    heading = ""
    buf: list[str] = []
    for line in text.splitlines():
        if line.strip().startswith("#"):
            if buf:
                sections.append(Section(text="\n".join(buf), metadata={"heading": heading}))
                buf = []
            heading = line.strip()
        else:
            if line.strip():
                buf.append(line.strip())
    if buf:
        sections.append(Section(text="\n".join(buf), metadata={"heading": heading}))
    return sections or [Section(text="", metadata={})]


def _parse_pdf(path: Path) -> list[Section]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    sections: list[Section] = []
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            sections.append(Section(text=text, metadata={"page": i}))
    return sections


def _parse_docx(path: Path) -> list[Section]:
    from docx import Document as DocxDocument

    doc = DocxDocument(str(path))
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    return [Section(text=p, metadata={}) for p in paragraphs] or [
        Section(text="", metadata={})
    ]
