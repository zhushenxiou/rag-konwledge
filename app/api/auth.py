"""登录 / 登出 / 验证码 / 当前用户。

本路由整体**不鉴权**（`app/main.py` 未挂 `require_auth`），否则无法登录；
其中 logout / me 通过函数级 `Depends(require_auth)` 单独保护。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import require_auth
from app.config import settings
from app.schemas import CaptchaOut, LoginRequest, LoginResponse, MeOut
from app.services import auth as auth_service

logger = logging.getLogger(__name__)

router = APIRouter()

if settings.auth_captcha_bypass:
    # 醒目告警：此模式下验证码形同虚设
    logger.warning(
        "AUTH_CAPTCHA_BYPASS=true：/api/auth/captcha 会额外返回明文 code，"
        "仅供本地自动化验收，切勿在对外环境开启"
    )


@router.get("/captcha", response_model=CaptchaOut, response_model_exclude_none=True)
def get_captcha():
    """取一张新验证码。明文只留在服务端，下发给前端的是图片 data URI。"""
    captcha_id, code, image = auth_service.new_captcha()
    payload: dict[str, str] = {"captcha_id": captcha_id, "image": image}
    if settings.auth_captcha_bypass:
        # 黑盒脚本读不出图片，只能把明文一并给出（见模块顶部 warning）
        payload["code"] = code
    return payload


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest):
    # 先校验验证码：不通过就不必再比对口令，也少给爆破者一点口令正确性的信号
    if not auth_service.verify_captcha(payload.captcha_id, payload.captcha_code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "验证码错误或已过期")
    if not auth_service.authenticate(payload.username, payload.password):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "账号或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    ttl_seconds = settings.auth_token_ttl_minutes * 60
    return {
        "token": auth_service.issue_token(ttl_seconds),
        "token_type": "bearer",
        "expires_in": ttl_seconds,
    }


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(token: str = Depends(require_auth)) -> None:
    auth_service.revoke_token(token)


@router.get("/me", response_model=MeOut)
def me(token: str = Depends(require_auth)):
    """供前端启动时校验本地存量 token 是否仍有效，并拿到展示用用户名。"""
    return {"username": settings.auth_username}
