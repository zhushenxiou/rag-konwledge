"""登录鉴权：验证码、登录 / 登出、受保护路由覆盖。

测试**不去 OCR 图片验证码**：直接调 app.services.auth.new_captcha() 拿明文，再走真实
的 /api/auth/login 接口。验证码的签发 / 一次性 / 过期链路仍然是真实代码在跑，只是省掉了
"看图"这一步。
"""
import base64
import io
import re

import pytest
from PIL import Image

from app.config import settings
from app.main import app
from app.services import auth as auth_service

# 允许免登录访问的 /api 端点。新增开放接口必须同步这里，
# 否则 test_only_whitelisted_endpoints_are_reachable_without_login 会红。
PUBLIC_ENDPOINTS = {
    ("GET", "/api/health"),
    ("GET", "/api/auth/captcha"),
    ("POST", "/api/auth/login"),
}


def _all_api_endpoints() -> list[tuple[str, str]]:
    """全部 /api 端点（method, path），取自 OpenAPI。

    不遍历 app.routes：FastAPI 0.141 起 include_router 的结果被包成内部的
    _IncludedRouter，子路由不再摊平进 app.routes，遍历路由树会依赖框架内部结构。
    """
    endpoints: list[tuple[str, str]] = []
    for path, operations in app.openapi()["paths"].items():
        if path.startswith("/api"):
            endpoints.extend((method.upper(), path) for method in operations)
    return sorted(endpoints)


def _concrete(path: str) -> str:
    """把 {param} 换成占位 UUID 让路由能匹配（鉴权在参数校验之前，值本身不重要）。"""
    return re.sub(r"\{[^}]+\}", "00000000-0000-0000-0000-000000000000", path)


def _login(client, captcha_id: str, code: str, username=None, password=None):
    return client.post(
        "/api/auth/login",
        json={
            "username": settings.auth_username if username is None else username,
            "password": settings.auth_password if password is None else password,
            "captcha_id": captcha_id,
            "captcha_code": code,
        },
    )


# ---- 验证码 ----
def test_captcha_returns_png_data_uri(anon_client):
    resp = anon_client.get("/api/auth/captcha")
    assert resp.status_code == 200
    image = resp.json()["image"]
    assert image.startswith("data:image/png;base64,")
    raw = base64.b64decode(image.split(",", 1)[1])
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic
    assert Image.open(io.BytesIO(raw)).size == (120, 44)


def test_captcha_response_omits_plaintext_code(anon_client):
    """明文只存服务端：未开 AUTH_CAPTCHA_BYPASS 时响应体里不能有 code 字段。"""
    assert settings.auth_captcha_bypass is False
    body = anon_client.get("/api/auth/captcha").json()
    assert set(body) == {"captcha_id", "image"}


def test_captcha_codes_are_random_four_digits():
    codes, ids = [], set()
    for _ in range(20):
        captcha_id, code, image = auth_service.new_captcha()
        ids.add(captcha_id)
        codes.append(code)
        assert re.fullmatch(r"\d{4}", code)
        assert image.startswith("data:image/png;base64,")
    assert len(ids) == 20
    # 不要求 20 个码值全不同（4 位数会有生日碰撞），但绝不该退化成同一个值
    assert len(set(codes)) >= 15


# ---- 登录 ----
def test_login_success_returns_bearer_token(anon_client):
    captcha_id, code, _ = auth_service.new_captcha()
    resp = _login(anon_client, captcha_id, code)
    assert resp.status_code == 200
    body = resp.json()
    assert body["token"]
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == settings.auth_token_ttl_minutes * 60


def test_login_wrong_password_returns_401(anon_client):
    captcha_id, code, _ = auth_service.new_captcha()
    assert _login(anon_client, captcha_id, code, password="wrong").status_code == 401


def test_login_unknown_username_returns_401(anon_client):
    captcha_id, code, _ = auth_service.new_captcha()
    assert _login(anon_client, captcha_id, code, username="nobody").status_code == 401


def test_login_non_ascii_credentials_return_401_not_500(anon_client):
    """回归：hmac.compare_digest 对含非 ASCII 的 str 会抛 TypeError，
    所以口令比较前必须 encode 成 bytes —— 否则输入中文账号直接 500。"""
    captcha_id, code, _ = auth_service.new_captcha()
    resp = _login(anon_client, captcha_id, code, username="张三", password="口令")
    assert resp.status_code == 401


def test_login_wrong_captcha_returns_400(anon_client):
    captcha_id, code, _ = auth_service.new_captcha()
    wrong = "0000" if code != "0000" else "1111"  # 别撞上真码造成偶发失败
    assert _login(anon_client, captcha_id, wrong).status_code == 400


def test_unknown_captcha_id_returns_400(anon_client):
    assert _login(anon_client, "no-such-captcha-id", "1234").status_code == 400


def test_captcha_is_single_use(anon_client):
    """一次性：同一 captcha_id 第二次校验必然失败，防重放。"""
    captcha_id, code, _ = auth_service.new_captcha()
    assert _login(anon_client, captcha_id, code).status_code == 200
    assert _login(anon_client, captcha_id, code).status_code == 400


def test_captcha_consumed_even_when_password_wrong(anon_client):
    """验证码在比对口令之前就被消费：口令错了，这张验证码也一并作废。"""
    captcha_id, code, _ = auth_service.new_captcha()
    assert _login(anon_client, captcha_id, code, password="wrong").status_code == 401
    assert _login(anon_client, captcha_id, code).status_code == 400


def test_expired_captcha_returns_400(anon_client, monkeypatch):
    """把 TTL 改成负数造出「已过期」，不 sleep 120 秒。"""
    monkeypatch.setattr(settings, "captcha_ttl_seconds", -1)
    captcha_id, code, _ = auth_service.new_captcha()
    assert _login(anon_client, captcha_id, code).status_code == 400


# ---- token 与会话 ----
def test_token_lifecycle_at_service_level():
    token = auth_service.issue_token(60)
    assert auth_service.verify_token(token) is True
    assert auth_service.verify_token(auth_service.issue_token(0)) is False  # 立刻过期
    auth_service.revoke_token(token)
    assert auth_service.verify_token(token) is False
    assert auth_service.verify_token("forged-token") is False


def test_me_returns_username_when_logged_in(auth_client):
    resp = auth_client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json() == {"username": settings.auth_username}


def test_forged_token_rejected(anon_client):
    resp = anon_client.get("/api/auth/me", headers={"Authorization": "Bearer forged-token"})
    assert resp.status_code == 401


def test_missing_or_malformed_auth_header_returns_401(anon_client):
    """HTTPBearer(auto_error=False) 的意义：缺头 / 格式错都走统一的 401，
    而不是默认的 403 —— 前端只认 401 来跳登录页。"""
    assert anon_client.get("/api/auth/me").status_code == 401
    assert anon_client.get("/api/auth/me", headers={"Authorization": "token 123"}).status_code == 401


def test_logout_revokes_token(auth_client):
    header = auth_client.headers["Authorization"]
    assert auth_client.post("/api/auth/logout").status_code == 204
    # 撤销后仍用这个 token 请求必须被拒
    assert auth_client.get("/api/auth/me", headers={"Authorization": header}).status_code == 401


# ---- 路由覆盖（回归网）----
@pytest.mark.parametrize(
    "method,path", [e for e in _all_api_endpoints() if e not in PUBLIC_ENDPOINTS]
)
def test_protected_endpoint_rejects_anonymous(anon_client, method, path):
    """未登录访问受保护接口一律 401。

    端点清单取自 OpenAPI，所以**任何新增接口都会自动纳入**：只要它忘了挂 require_auth，
    这里就会红。这是"以后新增路由不会漏"的真正保障，而不是靠人记得。
    """
    resp = anon_client.request(method, _concrete(path))
    assert resp.status_code == 401, f"{method} {path} 未登录访问返回了 {resp.status_code}"


def test_only_whitelisted_endpoints_are_reachable_without_login(anon_client):
    """反向校验：未登录时**只有**白名单里的端点不是 401。

    把新接口顺手加进 PUBLIC_ENDPOINTS 蒙混过关时，这条例行会把"到底开放了什么"摆到台面上。
    """
    reachable = {
        (method, path)
        for method, path in _all_api_endpoints()
        if anon_client.request(method, _concrete(path)).status_code != 401
    }
    assert reachable == PUBLIC_ENDPOINTS


@pytest.mark.parametrize("path", ["/docs", "/openapi.json", "/api/health"])
def test_docs_and_health_stay_open(anon_client, path):
    """文档与探活保持开放（手工联调、e2e 脚本的活命线）。"""
    assert anon_client.get(path).status_code == 200
