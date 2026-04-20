from fastapi import APIRouter, HTTPException, Depends
from app.models.models import User
from app.schemas.schemas import UserCreate
from app.api.deps import get_current_admin, get_db
from app.core.security import get_password_hash

router = APIRouter()


@router.get("/my_devices", include_in_schema=False)
async def reserved_my_devices_path():
    # 保留 404 占位，避免被 /{username} 的动态路由误命中后返回 405。
    raise HTTPException(status_code=404, detail="Not Found")

@router.get("", summary="【Admin】获取所有账号列表")
async def list_users(admin_user: User = Depends(get_current_admin), db = Depends(get_db)):
    users = db.query(User).filter(User.role == "admin").all()
    return [
        {
            "username": u.username,
            "role": u.role,
            "device_scope": "all",
        }
        for u in users
    ]


@router.post("", summary="【Admin】创建新账号")
async def create_user(user_in: UserCreate, admin_user: User = Depends(get_current_admin), db=Depends(get_db)):
    if db.query(User).filter(User.username == user_in.username).first():
        raise HTTPException(status_code=400, detail="账号名已存在")

    new_user = User(
        username=user_in.username,
        hashed_password=get_password_hash(user_in.password),
        role="admin",
    )
    db.add(new_user)
    db.commit()
    return {"status": "success", "message": "创建成功"}

@router.delete("/{username}", summary="【Admin】删除账号")
async def delete_user(username: str, admin_user: User = Depends(get_current_admin), db = Depends(get_db)):
    if username == "admin":
        raise HTTPException(status_code=400, detail="超级管理员账号不可删除")
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="未找到该账号")
    db.delete(user)
    db.commit()
    return {"status": "success"}
