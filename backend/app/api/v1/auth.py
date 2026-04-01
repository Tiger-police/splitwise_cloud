from fastapi import APIRouter, HTTPException
from app.db.database import SessionLocal
from app.models.models import User
from app.schemas.schemas import LoginRequest
from app.core.security import verify_password, create_access_token
import logging

logger = logging.getLogger("AuthRouter")
router = APIRouter()


@router.post("/login", summary="用户登录并获取 JWT 令牌")
async def login(request: LoginRequest):
    db = SessionLocal()
    user = db.query(User).filter(User.username == request.username).first()
    db.close()

    if not user or not verify_password(request.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="账号或密码错误！")

    access_token = create_access_token(data={"sub": user.username, "role": user.role})
    logger.info(f"🔑 用户 [{user.username}] 登录成功")

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "username": user.username,
        "role": user.role
    }