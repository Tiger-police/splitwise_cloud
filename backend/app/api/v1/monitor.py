import json
import asyncio
import logging
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from datetime import datetime

from app.api.deps import get_db
from app.models.models import ModelNode
from app.schemas.schemas import ModelRegisterRequest, ModelUnregisterRequest
from app.db.database import SessionLocal

logger = logging.getLogger("MonitorRouter")
router = APIRouter()

@router.post("/models/register", summary="切分服务上线注册")
async def register_model_state(request: ModelRegisterRequest, db: Session = Depends(get_db)):
    """接收边/云节点的上线报备"""
    node = db.query(ModelNode).filter(
        ModelNode.ip_address == request.ip_address,
        ModelNode.port == request.port
    ).first()

    if node:
        node.model_name = request.model_name
        node.status = "online"
        node.last_heartbeat = datetime.utcnow()
    else:
        node = ModelNode(
            model_name=request.model_name,
            ip_address=request.ip_address,
            port=request.port,
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
        node.status = "offline"
        db.commit()
        return {"status": "success", "message": f"节点 {request.ip_address}:{request.port} 已标记为离线"}
    return {"status": "error", "message": "未找到指定节点"}


@router.get("/models/status", summary="获取全局流水线状态(供大屏使用)")
async def get_all_models_status(db: Session = Depends(get_db)):
    """拉取全网所有切分节点的状态给前端展示"""
    nodes = db.query(ModelNode).all()

    result = []
    for n in nodes:
        result.append({
            "id": n.id,
            "model_name": n.model_name,
            "ip_address": n.ip_address,
            "port": n.port,
            "status": n.status,
            "last_heartbeat": n.last_heartbeat.isoformat() if n.last_heartbeat else None
        })

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
                    result.append({
                        "id": n.id,
                        "model_name": n.model_name,
                        "ip_address": n.ip_address,
                        "port": n.port,
                        "status": n.status,
                        "last_heartbeat": n.last_heartbeat.isoformat() if n.last_heartbeat else None
                    })

                payload = json.dumps({"status": "success", "nodes": result})
                yield f"data: {payload}\n\n"

            except Exception as e:
                logging.error(f"SSE 推送异常: {e}")
            finally:
                db.close()

            await asyncio.sleep(2)

    return StreamingResponse(event_generator(), media_type="text/event-stream")