"""共享依赖：登录校验。"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.services import auth as auth_service

# 用 HTTPBearer 而非手撸 Header：/docs 会自动出现 Authorize 按钮，便于手工调试。
# auto_error=False → 缺 Authorization 头时返回 None 而不是直接抛 403，
# 由 require_auth 统一给出带 WWW-Authenticate 的 401。
_bearer = HTTPBearer(auto_error=False)


def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    """校验 Bearer token，返回**原始 token**（登出接口需要用它来撤销）。"""
    if credentials is None or not auth_service.verify_token(credentials.credentials):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录或登录已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials
