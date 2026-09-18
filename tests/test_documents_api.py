"""文档 API 层测试：重命名（PATCH /api/documents/{id}）。

用 auth_client（已登录）而非裸 TestClient —— 文档路由现在要求 Bearer token，
不带就是 401。鉴权本身的用例见 tests/test_auth.py。
"""
from app.models import Document


def test_rename_document(auth_client, db):
    doc = Document(filename="a.md", file_path="x", file_size=1, source_type="md")
    db.add(doc)
    db.commit()
    db.refresh(doc)

    resp = auth_client.patch(f"/api/documents/{doc.id}", json={"filename": "新名字.md"})
    assert resp.status_code == 200
    assert resp.json()["filename"] == "新名字.md"

    db.refresh(doc)
    assert doc.filename == "新名字.md"


def test_rename_blank_rejected(auth_client, db):
    doc = Document(filename="a.md", file_path="x", file_size=1, source_type="md")
    db.add(doc)
    db.commit()
    db.refresh(doc)

    resp = auth_client.patch(f"/api/documents/{doc.id}", json={"filename": "   "})
    assert resp.status_code == 400


def test_rename_missing_returns_404(auth_client, db):
    import uuid

    resp = auth_client.patch(f"/api/documents/{uuid.uuid4()}", json={"filename": "x.md"})
    assert resp.status_code == 404
