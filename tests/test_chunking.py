"""分块逻辑测试：chunk_size / overlap / 句子边界 / metadata / 表格按行切。"""
import re

from app.services.chunking import Section, chunk_sections

_ROW = re.compile(r"^\|.*\|$")
_HEADER = "| 型号 | 额定功率 | 重量 | 适用场景 |"


def _table_text(n_rows: int) -> str:
    rows = [
        f"| XG-{1000 + i} | {i * 15}W | {i * 0.3:.1f}kg | 工业现场连续作业 |"
        for i in range(1, n_rows + 1)
    ]
    return "\n".join([_HEADER, "| --- | --- | --- | --- |"] + rows)


def _table_section(n_rows: int, metadata=None) -> Section:
    return Section(text=_table_text(n_rows), metadata=metadata or {"has_table": True})


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


# ---------------------------------------------------------------- 节间分隔
# 回归背景：`feed` 原先是 `buffer += text`，两个 section 之间不插任何东西，
# 节边界被整个抹掉 —— txt 每个非空行是一节，实测三段并成
# `第一段内容。第二段内容。第三段内容。`；docx 两段并成 `第一段第二段`；
# md 的代码围栏粘到上一段句尾（`…→ 测试。```powershell`）而不再位于行首。
# 现有用例一条都没覆盖到，所以这个缺陷长期全绿。


def test_sections_are_joined_with_a_blank_line():
    sections = [
        Section(text="第一段内容。", metadata={}),
        Section(text="第二段内容。", metadata={}),
    ]

    pieces = chunk_sections(sections, chunk_size=500, overlap=50)

    assert len(pieces) == 1
    assert pieces[0].content == "第一段内容。\n\n第二段内容。"


def test_section_separator_keeps_code_fence_at_line_start():
    """围栏必须留在行首 —— 粘到上一段句尾后它只是个普通反引号串，不再成围栏。"""
    sections = [
        Section(text="运行下面的命令。", metadata={}),
        Section(text="```powershell\npip install -r requirements.txt\n```", metadata={}),
    ]

    content = chunk_sections(sections, chunk_size=500, overlap=50)[0].content

    assert "。```" not in content
    assert "\n```powershell" in content


def test_separator_survives_between_multiline_sections():
    sections = [
        Section(text="第一段第一行\n第一段第二行", metadata={}),
        Section(text="第二段", metadata={}),
    ]

    content = chunk_sections(sections, chunk_size=500, overlap=50)[0].content

    assert content == "第一段第一行\n第一段第二行\n\n第二段"


def test_separator_counts_against_the_budget():
    """分隔符要计入预算：125 + 2 + 125 已超 250，必须切开。

    不计分隔符的话这里正好 250、不会被切，于是每次合并都悄悄多出 2 个字符。
    """
    body = "甲" * 125
    sections = [Section(text=body, metadata={}), Section(text=body, metadata={})]

    pieces = chunk_sections(sections, chunk_size=250, overlap=20)

    assert len(pieces) == 2, "分隔符未计入预算，两节被并成超限的一块"
    assert all(len(p.content) <= 250 for p in pieces)


# ---------------------------------------------------------------- 表格按行切
# 回归背景：超长表格原先当普通文本走 `_split_at_sentence`，而表格行里没有句子
# 结束符 → 走硬切分支 → 切点落在一行中间，切出 `| XG-1013 | 195W | 3.9` 这种
# 残片；且第二块起完全没有表头，只剩一串无列名的值。现在按行切并重复表头。


def test_oversized_table_splits_at_row_boundaries():
    pieces = chunk_sections([_table_section(30)], chunk_size=500, overlap=100)

    assert len(pieces) >= 2, "30 行表格应被切开"
    for p in pieces:
        for line in p.content.splitlines():
            assert _ROW.match(line), f"表格被切在行中间，产生残片: {line!r}"


def test_every_table_chunk_repeats_the_header():
    """列名是表格语义的全部 —— 丢一次，后面所有块就只剩无列名的值。"""
    pieces = chunk_sections([_table_section(30)], chunk_size=500, overlap=100)

    assert len(pieces) >= 2
    for p in pieces:
        assert p.content.startswith(_HEADER), f"该块缺表头: {p.content[:60]!r}"


def test_table_chunks_share_no_rows():
    """表格块间**刻意不重叠**：行是自包含的，重叠只会让同一行重复召回。"""
    pieces = chunk_sections([_table_section(30)], chunk_size=500, overlap=100)

    rows = [l for p in pieces for l in p.content.splitlines() if l.startswith("| XG-")]
    assert len(rows) == 30, f"数据行应恰好出现 30 次，实际 {len(rows)}"
    assert len(rows) == len(set(rows)), "出现了重复的数据行"


def test_no_data_row_is_lost():
    pieces = chunk_sections([_table_section(30)], chunk_size=500, overlap=100)

    seen = {l for p in pieces for l in p.content.splitlines() if l.startswith("| XG-")}
    assert len(seen) == 30


def test_oversized_table_does_not_merge_with_surrounding_text():
    """超限表格独立成块：散文与表格混在一个 chunk 里会互相稀释向量语义。"""
    sections = [
        Section(text="前文", metadata={}),
        _table_section(30),
        Section(text="后文", metadata={}),
    ]
    pieces = chunk_sections(sections, chunk_size=500, overlap=100)

    assert pieces[0].content == "前文"
    assert pieces[-1].content == "后文"
    for p in pieces[1:-1]:
        assert "前文" not in p.content and "后文" not in p.content


def test_small_table_still_merges_with_surrounding_text():
    """装得下的表格保持原行为（与相邻文字合并），改动不能波及这条路径。"""
    sections = [
        Section(text="前文", metadata={}),
        _table_section(2),
        Section(text="后文", metadata={}),
    ]
    pieces = chunk_sections(sections, chunk_size=500, overlap=100)

    assert len(pieces) == 1
    assert pieces[0].content.startswith("前文")
    assert "后文" in pieces[0].content


def test_markdown_table_is_split_without_has_table_flag():
    """md 里手写的表格走同一条识别路径 —— 靠行结构，不靠 has_table 标记。"""
    sections = [_table_section(30, metadata={"heading": "# 规格"})]

    pieces = chunk_sections(sections, chunk_size=500, overlap=100)

    assert len(pieces) >= 2
    assert all(p.content.startswith(_HEADER) for p in pieces)
    assert all(p.metadata.get("heading") == "# 规格" for p in pieces)


def test_table_without_separator_repeats_first_row():
    """没有 `| --- |` 分隔行的表格块，首行即表头。"""
    rows = [f"| r{i} | {'v' * 40} |" for i in range(20)]
    pieces = chunk_sections([Section(text="\n".join(rows), metadata={})],
                            chunk_size=200, overlap=50)

    assert len(pieces) >= 2
    assert all(p.content.startswith("| r0 |") for p in pieces)


def test_single_row_longer_than_budget_terminates():
    """一行本身就超预算时无法再切：允许该块超限，但必须终止且不丢内容。"""
    long_row = "| " + "X" * 600 + " | 1 |"
    section = Section(text="\n".join(["| A | B |", "| --- | --- |", long_row]), metadata={})

    pieces = chunk_sections([section], chunk_size=500, overlap=100)

    assert len(pieces) == 1
    assert long_row in pieces[0].content


def test_table_metadata_survives_splitting():
    pieces = chunk_sections([_table_section(30)], chunk_size=500, overlap=100)

    assert all(p.metadata.get("has_table") for p in pieces)


# ---------------------------------------------------------------- 围栏内的表格
# 回归背景：表格靠**行结构**识别，分不出"真的表格"和"代码块里演示的表格"。
# 一份讲 Markdown 语法的文档（代码块里正是表格示例）会被按行切成十几块，
# 每块顶着 ` ``` ` 当"表头"，chunk 数凭空膨胀、内容也被切碎。


def test_pipe_lines_inside_a_fence_are_not_a_table():
    from app.services.chunking import _table_parts

    body = "\n".join(f"| r{i} | {'v' * 40} |" for i in range(20))

    # 先证明这段内容本身确实会被当成表格（不是因为"没超限"才返回 None）
    assert _table_parts(body, chunk_size=200) is not None
    assert _table_parts(f"```\n{body}\n```", chunk_size=200) is None


def test_tilde_fenced_block_is_not_a_table():
    from app.services.chunking import _table_parts

    body = "\n".join(f"| r{i} | {'v' * 40} |" for i in range(20))

    assert _table_parts(f"~~~\n{body}\n~~~", chunk_size=200) is None


def test_fenced_block_does_not_get_a_fake_table_header():
    """端到端：围栏块不能被按行切 —— 那样每块都会拿开栏行当"表头"。

    判据用"以开栏行开头的块数"而不是"是否以开栏行开头"：正常硬切的第一块本来
    就从开栏行起头，只有**按行切**才会让每一块都以它开头。
    """
    body = "\n".join(f"| r{i} | {'v' * 40} |" for i in range(20))
    sections = [Section(text=f"```markdown\n{body}\n```", metadata={})]

    pieces = chunk_sections(sections, chunk_size=200, overlap=50)

    headed = [p for p in pieces if p.content.startswith("```markdown")]
    assert len(headed) == 1, f"{len(headed)} 块以开栏行开头，说明被当成表格按行切了"
    joined = "".join(p.content for p in pieces)
    for i in range(20):
        assert f"| r{i} |" in joined, f"第 {i} 行丢失"
