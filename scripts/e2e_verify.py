"""端到端验收脚本：对照 README「Demo 验收清单」（去前端项）逐项验证。

用 urllib 直连本地 API（本机 httpx->localhost 有 502 怪癖）。
用法: conda run -n langchain python e2e_verify.py
"""
import json
import time
import urllib.request
import uuid
from pathlib import Path

BASE = "http://localhost:8000"
ROOT = Path(__file__).resolve().parent.parent


def _boundary():
    return uuid.uuid4().hex


def _req(method, path, body=None, headers=None, raw=None, timeout=120):
    h = {"Content-Type": "application/json"} if body is not None else {}
    if headers:
        h.update(headers)
    data = body if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    if raw is not None:
        data = raw
    r = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return resp.status, resp.headers, resp.read()


def _upload(path: Path):
    ext = path.suffix.lstrip(".")
    boundary = _boundary()
    header = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8")
    body = header + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode("utf-8")
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    return _req("POST", "/api/documents/upload", raw=body, headers=headers)


def main():
    results = []

    def check(name, ok, detail=""):
        results.append((name, ok, detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -> {detail}" if detail else ""))

    # ---- 0. 健康检查 ----
    st, _, body = _req("GET", "/api/health")
    health = json.loads(body)
    check("0. /api/health ok", st == 200 and health["status"] == "ok", f"status={st} db={health['database']}")

    # ---- 1. 上传 md + pdf（异步入库）----
    md, pdf = ROOT / "demo" / "sample.md", ROOT / "demo" / "sample.pdf"
    doc_ids = []
    for f in (md, pdf):
        st, _, body = _upload(f)
        doc = json.loads(body)
        check(f"1. 上传 {f.name}", st == 202 and doc["status"] == "pending", f"id={doc['id']}")
        doc_ids.append(doc["id"])

    # ---- 2. 轮询到 ready ----
    for i, did in enumerate(doc_ids):
        st, _, body = _req("GET", f"/api/documents/{did}")
        doc = json.loads(body)
        deadline = time.time() + 60
        while doc["status"] == "pending" or doc["status"] == "processing":
            if time.time() > deadline:
                break
            time.sleep(1)
            st, _, body = _req("GET", f"/api/documents/{did}")
            doc = json.loads(body)
        ok = doc["status"] == "ready" and doc["chunk_count"] > 0
        check(f"2. 文档就绪 {doc['filename']}", ok, f"status={doc['status']} chunks={doc['chunk_count']} err={doc['error_message']}")

    # ---- 3. 列表 status=ready ----
    st, _, body = _req("GET", "/api/documents?status=ready")
    lst = json.loads(body)
    check("3. 文档列表 ready", st == 200 and len(lst) >= 2, f"count={len(lst)}")

    # ---- 4. 问答：命中（SSE 流式 + 引用来源）----
    st, headers, body = _req("POST", "/api/chat", {"question": "分块策略的默认参数是多少？"})
    events = [json.loads(ev) for ev in body.decode("utf-8").split("data: ")[1:] if ev.strip()]
    types = [e["type"] for e in events]
    answer = "".join(e.get("content", "") for e in events if e["type"] == "chunk")
    sources = next((e.get("sources", []) for e in events if e["type"] == "sources"), [])
    done = next((e for e in events if e["type"] == "done"), None)
    check("4. 问答命中（SSE）", st == 200 and {"chunk", "sources", "done"} <= set(types),
          f"media={headers.get('Content-Type')} answer_len={len(answer)}")
    check("4.1 引用来源", bool(sources),
          f"filename={sources[0]['filename'] if sources else None} sim={sources[0]['similarity'] if sources else None}")
    conv_id = done["conversation_id"] if done else None
    check("4.2 自动建会话", bool(conv_id), f"conversation_id={conv_id}")

    # ---- 5. 无关问题 -> no_evidence ----
    st, _, body = _req("POST", "/api/chat", {"question": "今天上海的天气怎么样？"})
    events = [json.loads(ev) for ev in body.decode("utf-8").split("data: ")[1:] if ev.strip()]
    types = [e["type"] for e in events]
    check("5. 无关问题 no_evidence", types == ["no_evidence", "done"],
          f"types={types} msg={next((e['message'] for e in events if e['type']=='no_evidence'), '')}")

    # ---- 6. 会话管理 ----
    st, _, body = _req("GET", "/api/conversations")
    convs = json.loads(body)
    check("6.1 会话列表", st == 200 and len(convs) >= 1, f"count={len(convs)}")
    st, _, body = _req("GET", f"/api/conversations/{conv_id}/messages")
    msgs = json.loads(body)
    roles = [m["role"] for m in msgs]
    check("6.2 会话消息", st == 200 and roles[:2] == ["user", "assistant"],
          f"roles={roles} sources_in_msg={bool(msgs[-1].get('sources'))}")
    st, _, body = _req("POST", "/api/conversations", {"title": "验收-新建会话"})
    new_conv = json.loads(body)
    check("6.3 新建会话", st == 201 and new_conv["title"] == "验收-新建会话")
    st, _, _ = _req("DELETE", f"/api/conversations/{new_conv['id']}")
    check("6.4 删除会话", st == 204)

    # ---- 7. 删除文档后检索不再命中（清空全部文档，保证无残留）----
    st, _, body = _req("GET", "/api/documents")
    all_docs = json.loads(body)
    for d in all_docs:
        st, _, _ = _req("DELETE", f"/api/documents/{d['id']}")
        assert st == 204, f"delete {d['id']} failed: {st}"
    check("7.1 删除全部文档", True, f"deleted={len(all_docs)}")
    time.sleep(1)
    st, _, body = _req("POST", "/api/chat", {"question": "分块策略的默认参数是多少？"})
    events = [json.loads(ev) for ev in body.decode("utf-8").split("data: ")[1:] if ev.strip()]
    types = [e["type"] for e in events]
    check("7.2 删除后检索不再命中", "no_evidence" in types, f"types={types}")

    # ---- 8. 接口文档 ----
    req = urllib.request.Request(BASE + "/docs", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            check("8. /docs 可用", resp.status == 200, f"status={resp.status}")
    except Exception as exc:  # noqa: BLE001
        check("8. /docs 可用", False, str(exc))

    # ---- 汇总 ----
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n===== 验收结果: {passed}/{len(results)} 通过 =====")
    if passed < len(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
