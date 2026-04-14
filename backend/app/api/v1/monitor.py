import json
import asyncio
import logging
import re
from typing import Any, Optional, cast
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from datetime import datetime

from app.api.deps import get_db
from app.core.config import settings
from app.models.models import Device, ModelNode
from app.schemas.schemas import ModelRegisterRequest, ModelUnregisterRequest
from app.db.database import SessionLocal

logger = logging.getLogger("MonitorRouter")
router = APIRouter()

LOCAL_RUNTIME_FALLBACKS = {
    ("127.0.0.1", 7001): ("edge_A", "edge"),
    ("127.0.0.1", 7002): ("cloud", "cloud"),
}


def serialize_heartbeat(last_heartbeat: Optional[datetime]) -> Optional[str]:
    return last_heartbeat.isoformat() if last_heartbeat else None


def serialize_model_node(node: Any) -> dict:
    supported_models = None
    if node.supported_models:
        try:
            supported_models = json.loads(node.supported_models)
        except json.JSONDecodeError:
            supported_models = None

    return {
        "id": node.id,
        "model_key": node.model_key,
        "model_name": node.model_name,
        "device_id": node.device_id,
        "node_role": node.node_role,
        "service_type": node.service_type,
        "ip_address": node.ip_address,
        "port": node.port,
        "control_path": node.control_path,
        "supported_models": supported_models,
        "status": node.status,
        "last_heartbeat": serialize_heartbeat(node.last_heartbeat),
    }


def extract_ips(device_value: Optional[str]) -> list[str]:
    if not device_value:
        return []
    return re.findall(r"(?:\d{1,3}\.){3}\d{1,3}", device_value)


def infer_runtime_context(db: Session, ip_address: str, port: int) -> tuple[str, str]:
    devices = db.query(Device).all()
    for device in devices:
        if ip_address in extract_ips(device.value):
            node_role = "cloud" if (device.device_type or "").lower() == "cloud" else "edge"
            return device.id, node_role

    if settings.LOCAL_RUNTIME_FALLBACK_ENABLED:
        fallback = LOCAL_RUNTIME_FALLBACKS.get((ip_address, port))
        if fallback:
            logger.warning(
                "⚠️ 当前启用了本地 mock runtime 兜底映射: %s:%s -> device_id=%s, node_role=%s",
                ip_address,
                port,
                fallback[0],
                fallback[1],
            )
            return fallback

    raise ValueError(f"未找到 IP {ip_address} 对应的设备资产，请先在设备管理中录入该设备")

@router.post("/models/register", summary="切分服务上线注册")
async def register_model_state(request: ModelRegisterRequest, db: Session = Depends(get_db)):
    """接收边/云节点的上线报备"""
    try:
        device_id, node_role = infer_runtime_context(db, request.ip_address, request.port)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    model_key = request.model_key.strip().lower()
    model_name = request.model_key.strip()
    service_type = "runtime"
    control_path = "/load_strategy"

    node = db.query(ModelNode).filter(
        ModelNode.ip_address == request.ip_address,
        ModelNode.port == request.port,
        ModelNode.service_type == service_type
    ).first()

    if node:
        node_obj = cast(Any, node)
        node_obj.model_key = model_key
        node_obj.model_name = model_name
        node_obj.device_id = device_id
        node_obj.node_role = node_role
        node_obj.service_type = service_type
        node_obj.control_path = control_path
        node_obj.supported_models = None
        node_obj.status = "online"
        node_obj.last_heartbeat = datetime.utcnow()
    else:
        node = ModelNode(
            model_key=model_key,
            model_name=model_name,
            device_id=device_id,
            node_role=node_role,
            service_type=service_type,
            ip_address=request.ip_address,
            port=request.port,
            control_path=control_path,
            supported_models=None,
            status="online",
            last_heartbeat=datetime.utcnow()
        )
        db.add(node)

    db.commit()
    return {"status": "success", "message": f"节点 {request.ip_address}:{request.port} 注册成功"}


@router.post("/models/unregister", summary="切分服务正常下线")
async def unregister_model_state(request: ModelUnregisterRequest, db: Session = Depends(get_db)):
    """接收边/云节点的主动下线通知"""
    node = db.query(ModelNode).filter(
        ModelNode.ip_address == request.ip_address,
        ModelNode.port == request.port
    ).first()

    if node:
        node_obj = cast(Any, node)
        node_obj.status = "offline"
        db.commit()
        return {"status": "success", "message": f"节点 {request.ip_address}:{request.port} 已标记为离线"}
    return {"status": "error", "message": "未找到指定节点"}


@router.get("/models/status", summary="获取全局流水线状态(供大屏使用)")
async def get_all_models_status(db: Session = Depends(get_db)):
    """拉取全网所有切分节点的状态给前端展示"""
    nodes = db.query(ModelNode).all()

    result = []
    for n in nodes:
        result.append(serialize_model_node(cast(Any, n)))

    return {"status": "success", "nodes": result}


@router.get("/models/stream", summary="SSE 实时状态推送流")
async def stream_models_status():
    """
    这是一个 SSE (Server-Sent Events) 接口。
    它会保持连接不断开，并每隔 2 秒主动向前端推送一次最新的数据库状态。
    """

    async def event_generator():
        while True:
            db = SessionLocal()
            try:
                nodes = db.query(ModelNode).all()
                result = []
                for n in nodes:
                    result.append(serialize_model_node(cast(Any, n)))

                payload = json.dumps({"status": "success", "nodes": result})
                yield f"data: {payload}\n\n"

            except Exception as e:
                logging.error(f"SSE 推送异常: {e}")
            finally:
                db.close()

            await asyncio.sleep(2)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
