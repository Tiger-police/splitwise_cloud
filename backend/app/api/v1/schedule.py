import asyncio
import json
import logging
import uuid
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import (
    get_current_edge_session,
    get_current_openwebui_user_id,
    get_db,
)
from app.core.config import settings
from app.core.security import decode_openwebui_access_token
from app.db.database import SessionLocal
from app.models.models import Device, EdgeSession, ScheduleTask
from app.schemas.schemas import (
    EdgeTriggerRequest,
    RuntimeProgressCallbackRequest,
    ScheduleTaskAcceptedResponse,
    ScheduleTaskStrategyResponse,
    ScheduleTaskStatusResponse,
    StrategyCallbackRequest,
)
from app.services.model_registry import MODEL_REGISTRY
from app.services.network_probe import get_network_metrics
from app.services.prometheus_metrics import get_prometheus_metrics
from app.services.runtime_dispatcher import (
    dispatch_strategy_to_runtime,
    extract_ip,
    find_runtime_node,
)
from app.services.schedule_presenter import (
    build_strategy_display_layer_partitions,
    build_strategy_display_summary,
    clamp_progress,
    serialize_task,
)
from app.services.schedule_queue import (
    build_logical_queue_metrics,
    count_queued_tasks_for_device_pair,
    find_active_task_for_device_pair,
    recalculate_queue_positions_for_device_pair,
)
from app.services.scheduler import encode_state
from app.services.schedule_task_service import fail_task, update_task

TASK_TERMINAL_STATUSES = {"completed", "failed"}
PENDING_STRATEGY_TASKS = {}

router = APIRouter()
logger = logging.getLogger("ScheduleRouter")


def decode_query_token_to_openwebui_user_id(token: str) -> str:
    payload = decode_openwebui_access_token(token)
    external_user_id = payload.get(settings.OPENWEBUI_USER_ID_CLAIM)
    if not isinstance(external_user_id, str) or not external_user_id.strip():
        raise HTTPException(status_code=401, detail="OpenWebUI token 缺少可识别的用户唯一 ID")
    return external_user_id.strip()


async def promote_next_queued_task_for_device_pair(
    *,
    edge_device_id: str,
    cloud_device_id: str,
) -> bool:
    db = SessionLocal()
    try:
        active_task = find_active_task_for_device_pair(
            db,
            edge_device_id=edge_device_id,
            cloud_device_id=cloud_device_id,
        )
        if active_task:
            return False

        next_task = (
            db.query(ScheduleTask)
            .filter(
                ScheduleTask.edge_device_id == edge_device_id,
                ScheduleTask.cloud_device_id == cloud_device_id,
                ScheduleTask.queue_status == "queued",
                ScheduleTask.status == "accepted",
                ScheduleTask.phase == "queued",
            )
            .order_by(ScheduleTask.created_at.asc(), ScheduleTask.task_id.asc())
            .first()
        )
        if not next_task:
            return False

        next_task.queue_status = "running"
        next_task.queue_position = 0
        next_task.phase = "strategy"
        next_task.message = "前序任务已结束，当前任务开始执行"
        next_task.updated_at = datetime.utcnow()
        db.add(next_task)
        db.commit()
        db.refresh(next_task)

        recalculate_queue_positions_for_device_pair(
            db,
            edge_device_id=edge_device_id,
            cloud_device_id=cloud_device_id,
        )

        asyncio.create_task(
            process_schedule_task(
                next_task.task_id,
                next_task.openwebui_user_id,
                next_task.edge_session_id,
                {"model_type": next_task.model_type},
            )
        )
        logger.info(
            "已自动推进排队任务: task_id=%s, edge_device_id=%s, cloud_device_id=%s",
            next_task.task_id,
            edge_device_id,
            cloud_device_id,
        )
        return True
    finally:
        db.close()


async def fail_task_and_promote(
    db: Session,
    task: ScheduleTask,
    message: str,
    error_detail: str | None = None,
) -> None:
    edge_device_id = task.edge_device_id
    cloud_device_id = task.cloud_device_id
    fail_task(db, task, message, error_detail)
    if edge_device_id and cloud_device_id:
        await promote_next_queued_task_for_device_pair(
            edge_device_id=edge_device_id,
            cloud_device_id=cloud_device_id,
        )


async def complete_task_and_promote(db: Session, task: ScheduleTask) -> None:
    edge_device_id = task.edge_device_id
    cloud_device_id = task.cloud_device_id
    update_task(
        db,
        task,
        status="completed",
        message="边云模型均已加载完成，任务结束",
        edge_status="ready",
        cloud_status="ready",
        edge_progress=100,
        cloud_progress=100,
        edge_message="边端模型已加载完成",
        cloud_message="云端模型已加载完成",
    )
    if edge_device_id and cloud_device_id:
        await promote_next_queued_task_for_device_pair(
            edge_device_id=edge_device_id,
            cloud_device_id=cloud_device_id,
        )


def derive_edge_storage_limit_gb_from_metrics(edge_metrics: dict) -> float:
    """
    当前将 storage_limit_gb 解释为“边端可用显存预算(GB)”。
    直接由已有 GPU 指标推导：
    available_vram_gb = (gpu_mem_total_mb - gpu_mem_used_mb) / 1024
    若 GPU 指标异常，则回退为 16GB。
    """
    gpu_total_mb = float(edge_metrics.get("gpu_mem_total_mb", 0.0) or 0.0)
    gpu_used_mb = float(edge_metrics.get("gpu_mem_used_mb", 0.0) or 0.0)
    gpu_available_mb = gpu_total_mb - gpu_used_mb

    if gpu_total_mb <= 1.0 or gpu_available_mb <= 0:
        logger.warning("边端可用显存预算推导失败，已回退为默认 16GB: metrics=%s", edge_metrics)
        return 16.0

    return round(gpu_available_mb / 1024.0, 2)


async def process_schedule_task(task_id: str, openwebui_user_id: str, edge_session_id: str, trigger_payload: dict) -> None:
    db = SessionLocal()
    try:
        task = db.query(ScheduleTask).filter(ScheduleTask.task_id == task_id).first()
        if not task:
            logger.error("后台任务启动失败，未找到调度任务: %s", task_id)
            return

        model_type = trigger_payload["model_type"]
        model_type_key = model_type.lower()
        if model_type_key not in MODEL_REGISTRY:
            await fail_task_and_promote(db, task, "不支持的模型类型", f"不支持的模型类型: {model_type}")
            return

        update_task(
            db,
            task,
            status="running",
            phase="strategy",
            phase_progress=5,
            message="正在校验用户权限并准备采集环境指标",
            queue_status="running",
            queue_position=0,
            dispatched_at=datetime.utcnow(),
            edge_message="等待切分策略计算完成",
            cloud_message="等待切分策略计算完成",
        )

        edge_session = (
            db.query(EdgeSession)
            .filter(
                EdgeSession.session_id == edge_session_id,
                EdgeSession.openwebui_user_id == openwebui_user_id,
                EdgeSession.status == "active",
            )
            .first()
        )
        if not edge_session:
            await fail_task_and_promote(
                db,
                task,
                "初始化会话不存在",
                f"未找到 session_id={edge_session_id} 对应的有效会话",
            )
            return

        edge_device = db.query(Device).filter(Device.id == edge_session.edge_device_id).first()
        cloud_device = db.query(Device).filter(Device.id == edge_session.cloud_device_id).first()
        if not edge_device or not cloud_device:
            await fail_task_and_promote(db, task, "会话设备信息缺失", "边端或云端设备在资产表中不存在")
            return

        edge_device_id = edge_device.id
        cloud_device_id = cloud_device.id
        edge_ip = extract_ip(edge_device.value)
        cloud_ip = extract_ip(cloud_device.value)

        update_task(
            db,
            task,
            phase_progress=15,
            message="用户授权校验完成，正在采集边云环境指标",
            edge_device_id=edge_device_id,
            cloud_device_id=cloud_device_id,
        )

        if not cloud_ip or not edge_ip or not cloud_device_id or not edge_device_id:
            await fail_task_and_promote(
                db,
                task,
                "用户设备分配不完整",
                "触发失败：该用户分配的设备不完整，无法凑齐端云流水线 (需1云1边)",
            )
            return

        edge_metrics, cloud_metrics, network_metrics = await asyncio.gather(
            get_prometheus_metrics(edge_ip),
            get_prometheus_metrics(cloud_ip),
            get_network_metrics(edge_ip, cloud_ip),
        )
        logical_queue_metrics = build_logical_queue_metrics(
            db,
            edge_device_id=edge_device_id,
            cloud_device_id=cloud_device_id,
        )
        edge_metrics = {
            **edge_metrics,
            "queue_len": logical_queue_metrics["edge_queue_len"],
        }
        cloud_metrics = {
            **cloud_metrics,
            "queue_len": logical_queue_metrics["cloud_queue_len"],
        }
        edge_storage_limit_gb = derive_edge_storage_limit_gb_from_metrics(edge_metrics)

        logger.info(
            "边端可用显存预算采集完成: task_id=%s, edge_ip=%s, storage_limit_gb=%s, logical_queue_metrics=%s",
            task_id,
            edge_ip,
            edge_storage_limit_gb,
            logical_queue_metrics,
        )

        raw_input_json = {
            "model_type": model_type,
            "max_context_length": 64,
            "env": {
                "edge": {
                    "device": "cuda",
                    "model_spec": {**MODEL_REGISTRY[model_type_key], "model_type": model_type},
                    "metrics": edge_metrics,
                    "storage_limit_gb": edge_storage_limit_gb,
                },
                "cloud": {
                    "device": "cuda",
                    "model_spec": {**MODEL_REGISTRY[model_type_key], "model_type": model_type},
                    "metrics": cloud_metrics,
                },
                "network": network_metrics,
            },
        }

        logger.info(
            "任务触发: task_id=%s, openwebui_user_id=%s, model=%s, raw_input_json=%s",
            task_id,
            openwebui_user_id,
            model_type,
            json.dumps(raw_input_json, ensure_ascii=False, indent=2),
        )

        update_task(
            db,
            task,
            phase_progress=35,
            message="环境指标采集完成，正在编码状态向量",
        )

        state_vector = encode_state(
            model_type=raw_input_json["model_type"],
            env=raw_input_json["env"],
            max_context_length=raw_input_json["max_context_length"],
        )

        logger.info(
            "状态向量编码完成: task_id=%s, openwebui_user_id=%s, model=%s, state_vector=%s",
            task_id,
            openwebui_user_id,
            model_type,
            json.dumps(state_vector, ensure_ascii=False),
        )

        update_task(
            db,
            task,
            phase_progress=60,
            message="状态向量已生成，正在请求切分策略模型",
        )

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        PENDING_STRATEGY_TASKS[task_id] = future

        payload_to_algorithm = {
            "task_id": task_id,
            "model_type": model_type,
            "state_vector": state_vector,
        }

        logger.info(
            "发送策略计算请求: task_id=%s, payload=%s",
            task_id,
            json.dumps(payload_to_algorithm, ensure_ascii=False),
        )

        async with httpx.AsyncClient() as client:
            response = await client.post(settings.ALGORITHM_API_URL, json=payload_to_algorithm, timeout=2.0)
            response.raise_for_status()

        update_task(
            db,
            task,
            phase_progress=75,
            message="切分策略模型已受理请求，等待算法回调",
        )

        decision_result = await asyncio.wait_for(future, timeout=30.0)
        logger.info(
            "策略回调完成: task_id=%s, decision=%s",
            task_id,
            json.dumps(decision_result, ensure_ascii=False),
        )

        update_task(
            db,
            task,
            phase_progress=100,
            message="切分策略计算完成，正在定位边云推理节点",
            strategy_payload=json.dumps(decision_result, ensure_ascii=False),
        )

        edge_node = find_runtime_node(db, edge_device_id, model_type_key, "edge")
        cloud_node = find_runtime_node(db, cloud_device_id, model_type_key, "cloud")
        if not edge_node or not cloud_node:
            missing_parts = []
            if not edge_node:
                missing_parts.append(f"边端节点(device_id={edge_device_id})")
            if not cloud_node:
                missing_parts.append(f"云端节点(device_id={cloud_device_id})")
            await fail_task_and_promote(db, task, "未找到在线推理节点", "、".join(missing_parts) + " 未注册或不在线")
            return

        update_task(
            db,
            task,
            phase="loading",
            phase_progress=5,
            message="已找到边云推理节点，正在下发切分策略",
            edge_status="dispatching",
            cloud_status="dispatching",
            edge_progress=0,
            cloud_progress=0,
            edge_message="边端正在接收切分策略",
            cloud_message="云端正在接收切分策略",
        )

        runtime_decision_payload = {
            "layer_partitions": decision_result["layer_partitions"],
        }
        edge_dispatch_payload = {
            "task_id": task_id,
            "model_type": model_type,
            "decision": runtime_decision_payload,
        }
        cloud_dispatch_payload = {
            "task_id": task_id,
            "model_type": model_type,
            "decision": runtime_decision_payload,
        }

        logger.info(
            "开始向推理节点下发切分策略: task_id=%s, edge_target=%s:%s%s, cloud_target=%s:%s%s",
            task_id,
            edge_node.ip_address,
            edge_node.port,
            edge_node.control_path,
            cloud_node.ip_address,
            cloud_node.port,
            cloud_node.control_path,
        )

        results = await asyncio.gather(
            dispatch_strategy_to_runtime(edge_node, edge_dispatch_payload),
            dispatch_strategy_to_runtime(cloud_node, cloud_dispatch_payload),
            return_exceptions=True,
        )

        dispatch_errors = [str(result) for result in results if isinstance(result, Exception)]
        if dispatch_errors:
            await fail_task_and_promote(db, task, "切分策略下发失败", " | ".join(dispatch_errors))
            return

        update_task(
            db,
            task,
            phase="loading",
            phase_progress=15,
            message="切分策略已下发，等待边云推理节点完成模型加载",
            edge_status="loading",
            cloud_status="loading",
            edge_message="等待边端开始加载模型",
            cloud_message="等待云端开始加载模型",
        )

    except asyncio.TimeoutError:
        if task_id in PENDING_STRATEGY_TASKS:
            PENDING_STRATEGY_TASKS.pop(task_id, None)
        task = db.query(ScheduleTask).filter(ScheduleTask.task_id == task_id).first()
        if task:
            await fail_task_and_promote(db, task, "等待算法组计算切分策略超时", "等待算法组计算切分策略超时 (30s)")
    except Exception as exc:
        logger.exception("调度任务执行异常: task_id=%s", task_id)
        task = db.query(ScheduleTask).filter(ScheduleTask.task_id == task_id).first()
        if task:
            await fail_task_and_promote(db, task, "调度任务执行失败", str(exc))
    finally:
        PENDING_STRATEGY_TASKS.pop(task_id, None)
        db.close()


@router.post("/trigger", response_model=ScheduleTaskAcceptedResponse, status_code=202, summary="接收边端触发，异步启动调度任务")
async def collect_raw_json(
    request: EdgeTriggerRequest,
    current_openwebui_user_id: str = Depends(get_current_openwebui_user_id),
    edge_session: EdgeSession = Depends(get_current_edge_session),
    db: Session = Depends(get_db),
):
    if edge_session.cloud_device_id != "cloud":
        raise HTTPException(status_code=400, detail="当前阶段仅支持固定云端设备 cloud")

    edge_session.model_type = request.model_type
    edge_session.updated_at = datetime.utcnow()
    db.add(edge_session)
    db.commit()
    db.refresh(edge_session)

    task_id = str(uuid.uuid4())
    edge_device_id = edge_session.edge_device_id
    cloud_device_id = edge_session.cloud_device_id
    active_task = find_active_task_for_device_pair(
        db,
        edge_device_id=edge_device_id,
        cloud_device_id=cloud_device_id,
    )
    queued_count = count_queued_tasks_for_device_pair(
        db,
        edge_device_id=edge_device_id,
        cloud_device_id=cloud_device_id,
    )
    is_queued = active_task is not None

    task = ScheduleTask(
        task_id=task_id,
        openwebui_user_id=current_openwebui_user_id,
        edge_session_id=edge_session.session_id,
        model_type=request.model_type,
        status="accepted",
        phase="queued" if is_queued else "strategy",
        phase_progress=0,
        overall_progress=0,
        message="当前边云推理节点繁忙，任务已进入排队" if is_queued else "任务已受理，开始计算切分策略",
        edge_device_id=edge_device_id,
        cloud_device_id=cloud_device_id,
        edge_status="pending",
        cloud_status="pending",
        queue_status="queued" if is_queued else "running",
        queue_position=queued_count + 1 if is_queued else 0,
        edge_message="等待切分策略计算完成",
        cloud_message="等待切分策略计算完成",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(task)
    db.commit()

    if not is_queued:
        asyncio.create_task(
            process_schedule_task(
                task_id,
                current_openwebui_user_id,
                edge_session.session_id,
                request.model_dump(),
            )
        )

    return {
        "status": "accepted",
        "task_id": task_id,
        "phase": "queued" if is_queued else "strategy",
        "phase_progress": 0,
        "overall_progress": 0,
        "message": "当前边云推理节点繁忙，任务已进入排队" if is_queued else "任务已受理，开始计算切分策略",
    }


@router.get("/tasks/{task_id}", response_model=ScheduleTaskStatusResponse, summary="查询调度任务状态")
async def get_schedule_task_status(
    task_id: str,
    current_openwebui_user_id: str = Depends(get_current_openwebui_user_id),
    db: Session = Depends(get_db),
):
    task = (
        db.query(ScheduleTask)
        .filter(ScheduleTask.task_id == task_id, ScheduleTask.openwebui_user_id == current_openwebui_user_id)
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="未找到该调度任务")
    return serialize_task(task)


@router.get("/tasks/{task_id}/strategy", response_model=ScheduleTaskStrategyResponse, summary="获取调度任务的切分策略")
async def get_schedule_task_strategy(
    task_id: str,
    current_openwebui_user_id: str = Depends(get_current_openwebui_user_id),
    db: Session = Depends(get_db),
):
    task = (
        db.query(ScheduleTask)
        .filter(ScheduleTask.task_id == task_id, ScheduleTask.openwebui_user_id == current_openwebui_user_id)
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="未找到该调度任务")
    if not task.strategy_payload:
        raise HTTPException(status_code=409, detail="切分策略尚未生成，请在进入 loading 阶段后再拉取")

    try:
        decision = json.loads(task.strategy_payload)
    except json.JSONDecodeError as exc:
        logger.exception("任务策略反序列化失败: task_id=%s", task_id)
        raise HTTPException(status_code=500, detail="任务切分策略解析失败") from exc

    display_layers = build_strategy_display_layer_partitions(decision.get("layer_partitions", []))
    display_summary = build_strategy_display_summary(display_layers)

    return {
        "task_id": task.task_id,
        "model_type": task.model_type,
        "decision": {
            "layer_partitions": display_layers,
            "edge_head_count_total": display_summary["edge_head_count_total"],
            "cloud_head_count_total": display_summary["cloud_head_count_total"],
        },
    }


@router.get("/tasks/{task_id}/stream", summary="SSE 推送调度任务进度")
async def stream_schedule_task_status(task_id: str, token: str = Query(...)):
    current_openwebui_user_id = decode_query_token_to_openwebui_user_id(token)

    async def event_generator():
        while True:
            db = SessionLocal()
            try:
                task = (
                    db.query(ScheduleTask)
                    .filter(
                        ScheduleTask.task_id == task_id,
                        ScheduleTask.openwebui_user_id == current_openwebui_user_id,
                    )
                    .first()
                )
                if not task:
                    payload = json.dumps({"status": "error", "message": "未找到该调度任务"}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
                    break

                payload = json.dumps(serialize_task(task), ensure_ascii=False)
                yield f"data: {payload}\n\n"

                if task.status in TASK_TERMINAL_STATUSES:
                    break
            finally:
                db.close()

            await asyncio.sleep(1)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/strategy_callback", summary="【算法组专用】接收切分策略回调")
async def receive_strategy_decision(payload: StrategyCallbackRequest):
    task_id = payload.task_id
    if task_id not in PENDING_STRATEGY_TASKS:
        raise HTTPException(status_code=404, detail="未找到对应的任务ID，或任务已超时废弃")

    future = PENDING_STRATEGY_TASKS[task_id]
    if not future.done():
        future.set_result(payload.model_dump())

    return {"status": "success", "message": f"任务 {task_id} 切分策略已成功接收并交付"}


async def handle_runtime_progress(payload: RuntimeProgressCallbackRequest, callback_role: str | None = None):
    db = SessionLocal()
    try:
        task = db.query(ScheduleTask).filter(ScheduleTask.task_id == payload.task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="未找到对应的调度任务")
        if task.status in TASK_TERMINAL_STATUSES:
            return {"status": "success", "message": "任务已处于终态，忽略重复回调"}

        resolved_node_role = callback_role or payload.node_role
        if not resolved_node_role:
            raise HTTPException(status_code=400, detail="缺少 node_role，且未使用带角色的回调地址")

        node_role = resolved_node_role.lower()
        node_status = payload.status.lower()
        progress = clamp_progress(payload.progress)

        if node_role not in {"edge", "cloud"}:
            raise HTTPException(status_code=400, detail="node_role 仅支持 edge 或 cloud")

        if node_status == "failed":
            await fail_task_and_promote(db, task, payload.message, f"{node_role} runtime failed")
            return {"status": "success", "message": "失败状态已记录"}

        update_kwargs = {
            "status": "running",
            "phase": "loading",
            "message": payload.message,
        }
        if node_role == "edge":
            update_kwargs["edge_progress"] = progress
            update_kwargs["edge_status"] = node_status
            update_kwargs["edge_message"] = payload.message
        else:
            update_kwargs["cloud_progress"] = progress
            update_kwargs["cloud_status"] = node_status
            update_kwargs["cloud_message"] = payload.message

        task = update_task(db, task, **update_kwargs)

        if (
            task.edge_progress >= 100
            and task.cloud_progress >= 100
            and task.edge_status in {"ready", "completed"}
            and task.cloud_status in {"ready", "completed"}
        ):
            await complete_task_and_promote(db, task)

        return {"status": "success", "message": "加载进度已记录"}
    finally:
        db.close()


@router.post("/runtime_callback/edge", summary="【推理节点专用】接收边端模型加载进度回调")
async def receive_edge_runtime_progress(payload: RuntimeProgressCallbackRequest):
    return await handle_runtime_progress(payload, callback_role="edge")


@router.post("/runtime_callback/cloud", summary="【推理节点专用】接收云端模型加载进度回调")
async def receive_cloud_runtime_progress(payload: RuntimeProgressCallbackRequest):
    return await handle_runtime_progress(payload, callback_role="cloud")
