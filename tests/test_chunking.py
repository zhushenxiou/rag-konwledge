"""分块逻辑测试：chunk_size / overlap / 句子边界 / metadata。"""
from app.services.chunking import Section, chunk_sections


def test_empty_input_returns_no_chunks():
    assert chunk_sections([]) == []


def test_chunks_respect_size():
    text = "。".join(f"这是第{i}句话的内容" for i in range(300)) + "。"
    pieces = chunk_sections([Section(text=text)], chunk_size=120, overlap=20)
    assert pieces
    for p in pieces:
        assert len(p.content) <= 120


def test_consecutive_chunks_have_overlap():
    text = "。".join(f"这是第{i}句话的内容" for i in range(400)) + "。"
    overlap = 30
    pieces = chunk_sections([Section(text=text)], chunk_size=100, overlap=overlap)
    assert len(pieces) >= 2
    for a, b in zip(pieces, pieces[1:]):
        tail = a.content[-overlap:]
        assert tail and b.content.startswith(tail), "相邻分块应保留 overlap 尾部"


def test_split_at_sentence_boundary():
    # 一句话 30 字符 + 句号，chunk_size=45 应切在句号后而不是句中
    text = "A" * 30 + "。" + "B" * 30 + "。" + "C" * 30 + "。"
    pieces = chunk_sections([Section(text=text)], chunk_size=45, overlap=5)
    assert len(pieces) >= 2
    assert pieces[0].content.endswith("。")


def test_metadata_pages_merged():
    sections = [
        Section(text="第一页的内容 " * 12, metadata={"page": 1}),
        Section(text="第二页的内容 " * 12, metadata={"page": 2}),
    ]
    pieces = chunk_sections(sections, chunk_size=80, overlap=10)
    # 跨页合并的 chunk 应记录 pages 集合
    pages = set()
    for p in pieces:
        pages.update(p.metadata.get("pages", [p.metadata.get("page")]))
    assert 1 in pages and 2 in pages


def test_heading_metadata_kept():
    sections = [
        Section(text="xxx", metadata={"heading": "# 架构"}),
        Section(text="yyy", metadata={"heading": ""}),
    ]
    pieces = chunk_sections(sections, chunk_size=500, overlap=50)
    assert pieces and pieces[0].metadata.get("heading") == "# 架构"
