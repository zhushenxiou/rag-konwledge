"""登录鉴权：内存态 token 会话 + 图形验证码。

Demo 定位（单账号、单进程），刻意不引入 JWT / Redis / 密码哈希库：

- **token 与验证码明文只存进程内存**。后果有二，都是刻意的取舍：
  1) 后端重启 → 全体登出（前端拿到 401 会干净地跳回登录页）；
  2) uvicorn 多 worker 之间不共享（本服务按单 worker 运行）。生产环境应换成
     Redis 或 DB 表。
- 口令来自 `settings.auth_username` / `auth_password`（默认 zhuliang/zhuliang），
  以**明文**比对。Demo 可接受，真实场景必须存哈希（bcrypt/argon2）并走 HTTPS。

线程安全：接口层全是同步函数、中间没有 `await` 让出点，单进程 asyncio 下
dict 的读改写不会被打断，故不额外加锁。
"""
import hmac
import secrets
import time

from app.config import settings
from app.services import captcha as captcha_service

_token_store: dict[str, float] = {}                # token -> 过期时间戳
_captcha_store: dict[str, tuple[str, float]] = {}  # captcha_id -> (明文 code, 过期时间戳)


def _purge_expired(now: float) -> None:
    """惰性清理过期项：只在生成新条目时顺带扫一遍，避免长期运行下无界增长。"""
    for token, expires_at in list(_token_store.items()):
        if expires_at <= now:
            _token_store.pop(token, None)
    for captcha_id, (_, expires_at) in list(_captcha_store.items()):
        if expires_at <= now:
            _captcha_store.pop(captcha_id, None)


# ---- 会话 token ----
def issue_token(ttl_seconds: int) -> str:
    now = time.time()
    _purge_expired(now)
    token = secrets.token_urlsafe(32)  # 不透明随机串，服务端查表校验；无签名故无法伪造
    _token_store[token] = now + ttl_seconds
    return token


def verify_token(token: str) -> bool:
    expires_at = _token_store.get(token)
    if expires_at is None:
        return False
    if expires_at <= time.time():
        _token_store.pop(token, None)
        return False
    return True


def revoke_token(token: str) -> None:
    _token_store.pop(token, None)


# ---- 账号口令 ----
def authenticate(username: str, password: str) -> bool:
    """常量时间比较，避免通过响应耗时逐字节猜口令。

    注意 `hmac.compare_digest` 不接受含非 ASCII 的 str（会抛 TypeError），
    而用户名/口令是用户输入、可能是中文，故一律编码成 bytes 再比。
    """
    user_ok = hmac.compare_digest(
        username.encode("utf-8"), settings.auth_username.encode("utf-8")
    )
    password_ok = hmac.compare_digest(
        password.encode("utf-8"), settings.auth_password.encode("utf-8")
    )
    return user_ok and password_ok  # 两个比较都执行完再取与，不短路


# ---- 验证码 ----
def new_captcha() -> tuple[str, str, str]:
    """生成验证码，返回 `(captcha_id, 明文 code, 图片 data URI)`。

    明文只留在服务端；下发给前端的是图片。`code` 仅供测试 / e2e 脚本使用，
    API 层在非 bypass 模式下不得把它写进响应体。
    """
    now = time.time()
    _purge_expired(now)
    captcha_id = secrets.token_urlsafe(16)
    code = captcha_service.generate_code()
    _captcha_store[captcha_id] = (code, now + settings.captcha_ttl_seconds)
    return captcha_id, code, captcha_service.render(code)


def verify_captcha(captcha_id: str, code: str) -> bool:
    """校验验证码。**取出即删**：同一 captcha_id 只有第一次校验有效，防重放。"""
    item = _captcha_store.pop(captcha_id, None)
    if item is None:
        return False
    expected, expires_at = item
    if expires_at <= time.time():
        return False
    return hmac.compare_digest(code.strip().encode("utf-8"), expected.encode("utf-8"))
