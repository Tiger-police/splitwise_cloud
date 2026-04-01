from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from jwt import PyJWTError

from app.core.config import settings
from app.db.database import SessionLocal
from app.models.models import User

security = HTTPBearer()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """基础门禁：校验 Token"""
    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="Token 数据异常")
        return username
    except PyJWTError:
        raise HTTPException(status_code=401, detail="Token 已过期或被篡改，请重新登录")

async def get_current_admin(
    username: str = Depends(get_current_user),
    db = Depends(get_db)  # 直接使用上面的 get_db 依赖
):
    """高级门禁：必须是 admin"""
    user = db.query(User).filter(User.username == username).first()
    if not user or user.role != "admin":
        raise HTTPException(status_code=403, detail="权限不足！只有管理员可执行此操作")
    return user