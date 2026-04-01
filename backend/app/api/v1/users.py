from fastapi import APIRouter, HTTPException, Depends
from app.db.database import SessionLocal
from app.models.models import User, Device
from app.schemas.schemas import UserCreate
from app.api.deps import get_current_user, get_current_admin, get_db
from app.core.security import get_password_hash

router = APIRouter()

@router.get("/my_devices", summary="获取当前用户有权查看的设备列表")
async def get_my_devices(current_username: str = Depends(get_current_user), db = Depends(get_db)):
    user = db.query(User).filter(User.username == current_username).first()
    if not user or not user.allowed_devices:
        raise HTTPException(status_code=403, detail="您没有分配任何设备权限")

    allowed_keys = user.allowed_devices.split(",")
    devices = db.query(Device).filter(Device.id.in_(allowed_keys)).all()
    return {"user": current_username, "devices": [{"name": d.name, "value": d.value} for d in devices]}

@router.get("", summary="【Admin】获取所有账号列表")
async def list_users(admin_user: User = Depends(get_current_admin), db = Depends(get_db)):
    users = db.query(User).all()
    return [{"username": u.username, "role": u.role, "devices": u.allowed_devices} for u in users]


@router.post("", summary="【Admin】创建新账号")
async def create_user(user_in: UserCreate, admin_user: User = Depends(get_current_admin), db=Depends(get_db)):
    if db.query(User).filter(User.username == user_in.username).first():
        raise HTTPException(status_code=400, detail="账号名已存在")

    if user_in.role == "user":
        device_ids = [d_id for d_id in user_in.allowed_devices.split(",") if d_id]
        selected_devices = db.query(Device).filter(Device.id.in_(device_ids)).all()

        cloud_count = sum(1 for d in selected_devices if d.device_type == "cloud")
        edge_count = sum(1 for d in selected_devices if d.device_type == "edge")

        if cloud_count != 1 or edge_count != 1:
            raise HTTPException(status_code=400, detail="普通用户必须且只能分配 1个云端设备 和 1个边端设备！")

        final_devices = user_in.allowed_devices
    else:
        all_devices = db.query(Device).all()
        final_devices = ",".join([d.id for d in all_devices])

    new_user = User(
        username=user_in.username,
        hashed_password=get_password_hash(user_in.password),
        role=user_in.role,
        allowed_devices=final_devices
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