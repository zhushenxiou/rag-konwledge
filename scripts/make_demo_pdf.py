"""生成 demo/sample.pdf —— 演示用中文 PDF，与 demo/sample.md 同主题。

依赖 reportlab（仅生成演示文件需要，非运行时依赖）:
    conda run -n langchain pip install reportlab
    conda run -n langchain python scripts/make_demo_pdf.py
"""
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "demo" / "sample.pdf"

pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))

TEXT = [
    ("企业知识库问答系统介绍", True),
    ("企业知识库问答系统是一套基于 RAG（检索增强生成）技术的内部知识问答平台。"
     "它允许员工上传各类文档，并通过自然语言提问，快速获取带出处引用的答案。", False),
    ("系统架构", True),
    ("系统采用 PostgreSQL 存储业务数据与会话数据，并使用 pgvector 扩展在同一个数据库内存储向量数据。"
     "后端使用 FastAPI 提供 REST API，并通过 SSE 协议流式返回大模型生成的内容。"
     "文档解析支持 txt、markdown、pdf 和 docx 四种格式。", False),
    ("文档入库流程", True),
    ("用户上传文档后，系统按文件类型解析出纯文本，然后按 chunk_size 和 overlap 参数进行分块。"
     "每个分块调用 Embedding 模型生成向量，写入 chunks 表的 embedding 列，并更新文档状态为 ready。"
     "如果处理失败，文档状态会变为 failed，用户可以手动触发重试。", False),
    ("问答流程", True),
    ("用户提问时，系统先将问题向量化，在知识库中做余弦相似度检索，取 Top-K 个最相关的分块。"
     "系统会把检索到的分块作为上下文拼装 Prompt，调用大模型生成回答，并通过 SSE 流式返回。"
     "回答下方会展示引用来源，包括文档名和具体片段，保证答案可追溯。", False),
    ("分块策略", True),
    ("默认 chunk_size 为 500 字符，overlap 为 100 字符。"
     "分块时优先在句子边界或段落边界处切分，避免把一句话从中间硬切成两段，从而保留语义完整性。", False),
]

title_style = ParagraphStyle(
    "Title", fontName="STSong-Light", fontSize=18, leading=24, spaceAfter=16, alignment=1
)
heading_style = ParagraphStyle(
    "Heading", fontName="STSong-Light", fontSize=14, leading=20, spaceBefore=10, spaceAfter=6
)
body_style = ParagraphStyle(
    "Body", fontName="STSong-Light", fontSize=11, leading=18, firstLineIndent=2 * 11
)


def main() -> None:
    doc = SimpleDocTemplate(str(OUT), pagesize=A4, topMargin=2 * cm, bottomMargin=2 * cm)
    story: list = []
    for i, (text, is_heading) in enumerate(TEXT):
        story.append(
            Paragraph(text, title_style if i == 0 else (heading_style if is_heading else body_style))
        )
        if not is_heading:
            story.append(Spacer(1, 6))
    doc.build(story)
    print(f"[make_demo_pdf] written: {OUT}")


if __name__ == "__main__":
    main()
