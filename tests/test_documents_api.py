"""文档 API 层测试：重命名（PATCH /api/documents/{id}）。"""
from fastapi.testclient import TestClient

from app.main import app
from app.models import Document

client = TestClient(app)


def test_rename_document(db):
    doc = Document(filename="a.md", file_path="x", file_size=1, source_type="md")
    db.add(doc)
    db.commit()
    db.refresh(doc)

    resp = client.patch(f"/api/documents/{doc.id}", json={"filename": "新名字.md"})
    assert resp.status_code == 200
    assert resp.json()["filename"] == "新名字.md"

    db.refresh(doc)
    assert doc.filename == "新名字.md"


def test_rename_blank_rejected(db):
    doc = Document(filename="a.md", file_path="x", file_size=1, source_type="md")
    db.add(doc)
    db.commit()
    db.refresh(doc)

    resp = client.patch(f"/api/documents/{doc.id}", json={"filename": "   "})
    assert resp.status_code == 400


def test_rename_missing_returns_404(db):
    import uuid

    resp = client.patch(f"/api/documents/{uuid.uuid4()}", json={"filename": "x.md"})
    assert resp.status_code == 404
