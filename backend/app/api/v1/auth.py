from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.models import User
from app.schemas.schemas import AuthTokenResponse, LoginRequest
from app.core.security import create_access_token, verify_password
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
