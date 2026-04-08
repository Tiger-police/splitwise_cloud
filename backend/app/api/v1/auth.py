from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.models import User
from app.schemas.schemas import AuthTokenResponse, LoginRequest, TokenExchangeRequest
from app.core.security import (
    create_access_token,
    decode_openwebui_access_token,
    verify_password,
)
from app.core.config import settings
from jwt import PyJWTError
import logging

logger = logging.getLogger("AuthRouter")
router = APIRouter()


def build_auth_token_response(user: User) -> dict:
    access_token = create_access_token(data={"sub": user.username, "role": user.role})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "username": user.username,
        "role": user.role,
    }


@router.post("/login", response_model=AuthTokenResponse, summary="用户登录并获取 JWT 令牌")
async def login(request: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == request.username).first()

    if not user or not verify_password(request.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="账号或密码错误！")

    logger.info(f"🔑 用户 [{user.username}] 登录成功")
    return build_auth_token_response(user)


@router.post("/auth/exchange", response_model=AuthTokenResponse, summary="使用 OpenWebUI token 换取云端调度业务 token")
async def exchange_openwebui_token(request: TokenExchangeRequest, db: Session = Depends(get_db)):
    try:
        external_payload = decode_openwebui_access_token(request.openwebui_token)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PyJWTError as exc:
        raise HTTPException(status_code=401, detail="OpenWebUI token 无效或已过期") from exc

    external_user_id = external_payload.get(settings.OPENWEBUI_USER_ID_CLAIM)
    if not isinstance(external_user_id, str) or not external_user_id.strip():
        raise HTTPException(status_code=401, detail="OpenWebUI token 缺少可识别的用户唯一 ID")

    user = db.query(User).filter(User.openwebui_user_id == external_user_id.strip()).first()
    if not user:
        raise HTTPException(status_code=403, detail="当前 OpenWebUI 用户尚未绑定云端调度系统账号")
    if not user.allowed_devices:
        raise HTTPException(status_code=403, detail="当前用户未分配可用设备")

    if settings.OPENWEBUI_SKIP_SIGNATURE_VERIFY:
        logger.warning("⚠️ 当前处于 OpenWebUI 跳过验签模式，仅可用于开发联调")

    logger.info("🔁 OpenWebUI 用户 ID [%s] 已映射到本地用户 [%s]", external_user_id, user.username)
    return build_auth_token_response(user)
