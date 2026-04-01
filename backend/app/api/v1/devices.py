from fastapi import APIRouter, HTTPException, Depends
from app.models.models import User, Device
from app.schemas.schemas import DeviceCreate
from app.api.deps import get_current_admin, get_db
from sqlalchemy.orm import Session

router = APIRouter()


@router.get("", summary="【Admin】获取全量设备列表")
async def list_devices(admin_user: User = Depends(get_current_admin), db=Depends(get_db)):
    devices = db.query(Device).all()
    return [{"id": d.id, "name": d.name, "value": d.value, "type": d.device_type} for d in devices]


@router.post("", summary="【Admin】录入新设备")
async def create_device(dev_in: DeviceCreate, admin_user: User = Depends(get_current_admin), db=Depends(get_db)):
    if db.query(Device).filter(Device.id == dev_in.id).first():
        raise HTTPException(status_code=400, detail="设备编号已存在")

    db.add(Device(id=dev_in.id, name=dev_in.name, value=dev_in.value, device_type=dev_in.device_type))

    # 自动授权给所有 admin
    admins = db.query(User).filter(User.role == "admin").all()
    for admin in admins:
        if admin.allowed_devices:
            if dev_in.id not in admin.allowed_devices.split(","):
                admin.allowed_devices += f",{dev_in.id}"
        else:
            admin.allowed_devices = dev_in.id

    db.commit()
    return {"status": "success", "message": "设备录入成功"}


@router.delete("/{device_id}", summary="【Admin】删除设备")
async def delete_device(device_id: str, admin_user: User = Depends(get_current_admin), db=Depends(get_db)):
    if device_id == "cloud":
        raise HTTPException(status_code=400, detail="主节点不可删")
    dev = db.query(Device).filter(Device.id == device_id).first()
    if not dev:
        raise HTTPException(status_code=404, detail="未找到设备")

    db.delete(dev)

    # 级联清理普通用户的残留权限
    for u in db.query(User).all():
        if u.allowed_devices:
            keys = u.allowed_devices.split(",")
            if device_id in keys:
                keys.remove(device_id)
                u.allowed_devices = ",".join(keys)

    db.commit()
    return {"status": "success"}


@router.get("/prometheus/targets/{job_type}", summary="提供给 Prometheus 的动态服务发现接口")
async def get_prometheus_targets(job_type: str, db: Session = Depends(get_db)):
    """
    Prometheus 会定时拉取这个接口。
    job_type 可以是 'node' (查 9100) 或 'gpu' (查 9400)
    """
    devices = db.query(Device).all()
    targets_list = []

    for dev in devices:
        # 解析数据库中 10.x.x.x:9100|10.x.x.x:9400 的格式
        endpoints = dev.value.split('|')
        target_ip_port = None

        for ep in endpoints:
            if job_type == "node" and ":9100" in ep:
                target_ip_port = ep
            elif job_type == "gpu" and ":9400" in ep:
                target_ip_port = ep

        if target_ip_port:
            # 组装成 Prometheus 要求的 HTTP SD JSON 格式
            targets_list.append({
                "targets": [target_ip_port],
                "labels": {
                    "device_id": dev.id,  # 比如 edge_A
                    "device_name": dev.name  # 比如 📱 边缘节点 A
                }
            })

    return targets_list