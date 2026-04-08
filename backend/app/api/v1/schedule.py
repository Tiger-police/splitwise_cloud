import asyncio
import json
import logging
import re
import shutil
import uuid
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import decode_token_to_username, get_current_user, get_db
from app.core.config import settings
from app.db.database import SessionLocal
from app.models.models import Device, ModelNode, ScheduleTask, User
from app.schemas.schemas import (
    EdgeTriggerRequest,
    RuntimeProgressCallbackRequest,
    ScheduleTaskAcceptedResponse,
    ScheduleTaskStatusResponse,
    StrategyCallbackRequest,
)
from app.services.scheduler import encode_state

PROMETHEUS_URL = settings.PROMETHEUS_URL
TASK_TERMINAL_STATUSES = {"completed", "failed"}
PENDING_STRATEGY_TASKS = {}

router = APIRouter()
logger = logging.getLogger("ScheduleRouter")

MODEL_REGISTRY = {
    "gpt2": {
        "architecture": "gpt2",
        "num_hidden_layers": 12,
        "num_attention_heads": 12,
        "hidden_size": 768,
        "intermediate_size": 3072,
        "vocab_size": 50257,
    },
    "tinyllama": {
        "architecture": "llama",
        "num_hidden_layers": 22,
        "num_attention_heads": 32,
        "hidden_size": 2048,
        "intermediate_size": 5632,
        "vocab_size": 32000,
    },
    "llama-3.2-3b": {
        "architecture": "llama",
        "num_hidden_layers": 28,
        "num_attention_heads": 24,
        "hidden_size": 3072,
        "intermediate_size": 8192,
        "vocab_size": 128256,
    },
}


def clamp_progress(value: int) -> int:
    return max(0, min(100, int(value)))


def calc_overall_progress(phase: str, phase_progress: int) -> int:
    phase_progress = clamp_progress(phase_progress)
    if phase == "strategy":
        return phase_progress // 2
    if phase == "loading":
        return 50 + phase_progress // 2
    if phase == "completed":
        return 100
    return phase_progress


def serialize_task(task: ScheduleTask) -> dict:
    return {
        "task_id": task.task_id,
        "status": task.status,
        "phase": task.phase,
        "phase_progress": task.phase_progress,
        "overall_progress": task.overall_progress,
        "message": task.message,
        "edge_progress": task.edge_progress,
        "cloud_progress": task.cloud_progress,
        "edge_status": task.edge_status,
        "cloud_status": task.cloud_status,
        "error_detail": task.error_detail,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }


def update_task(
    db: Session,
    task: ScheduleTask,
    *,
    status: str | None = None,
    phase: str | None = None,
    phase_progress: int | None = None,
    message: str | None = None,
    edge_progress: int | None = None,
    cloud_progress: int | None = None,
    edge_status: str | None = None,
    cloud_status: str | None = None,
    strategy_payload: str | None = None,
    error_detail: str | None = None,
    edge_device_id: str | None = None,
    cloud_device_id: str | None = None,
) -> ScheduleTask:
    if status is not None:
        task.status = status
    if phase is not None:
        task.phase = phase
    if message is not None:
        task.message = message
    if edge_progress is not None:
        task.edge_progress = clamp_progress(edge_progress)
    if cloud_progress is not None:
        task.cloud_progress = clamp_progress(cloud_progress)
    if edge_status is not None:
        task.edge_status = edge_status
    if cloud_status is not None:
        task.cloud_status = cloud_status
    if strategy_payload is not None:
        task.strategy_payload = strategy_payload
    if error_detail is not None:
        task.error_detail = error_detail
    if edge_device_id is not None:
        task.edge_device_id = edge_device_id
    if cloud_device_id is not None:
        task.cloud_device_id = cloud_device_id

    if phase_progress is not None:
        task.phase_progress = clamp_progress(phase_progress)
    elif task.phase == "loading":
        task.phase_progress = clamp_progress((task.edge_progress + task.cloud_progress) // 2)

    if task.status == "completed":
        task.phase = "completed"
        task.phase_progress = 100
        task.overall_progress = 100
    else:
        task.overall_progress = calc_overall_progress(task.phase, task.phase_progress)

    task.updated_at = datetime.utcnow()
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def fail_task(db: Session, task: ScheduleTask, message: str, error_detail: str | None = None) -> None:
    update_task(
        db,
        task,
        status="failed",
        message=message,
        error_detail=error_detail or message,
    )


def extract_ip(device_value: str) -> str | None:
    ip_match = re.search(r"(?:\d{1,3}\.){3}\d{1,3}", device_value)
    return ip_match.group(0) if ip_match else None


def find_runtime_node(db: Session, device_id: str, model_key: str, node_role: str) -> ModelNode | None:
    candidates = (
        db.query(ModelNode)
        .filter(
            ModelNode.device_id == device_id,
            ModelNode.node_role == node_role,
            ModelNode.service_type == "runtime",
            ModelNode.status == "online",
        )
        .order_by(ModelNode.last_heartbeat.desc())
        .all()
    )

    for node in candidates:
        if (node.model_key or "").lower() == model_key:
            return node

    for node in candidates:
        node_model_key = (node.model_key or "").lower()
        if node_model_key in {"multi", "*", "all"}:
            return node

    return candidates[0] if len(candidates) == 1 else None


async def query_prom(client: httpx.AsyncClient, query: str) -> float:
    try:
        resp = await client.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": query}, timeout=3.0)
        data = resp.json()
        result = data.get("data", {}).get("result", [])
        if result:
            return float(result[0].get("value", [0, "0"])[1])
        logger.warning("Prometheus 查询结果为空，已回退为 0.0: %s", query)
    except Exception as exc:
        logger.warning("Prometheus 查询失败，已回退为 0.0: %s, error=%s", query, exc)
    return 0.0


async def fetch_metrics_from_prometheus(ip: str) -> dict:
    async with httpx.AsyncClient() as client:
        ip_regex = f"^{ip}:.*"

        q_cpu = f'100 - (avg(rate(node_cpu_seconds_total{{instance=~"{ip_regex}",mode="idle"}}[1m])) * 100)'
        q_mem = f'100 * (1 - node_memory_MemAvailable_bytes{{instance=~"{ip_regex}"}} / node_memory_MemTotal_bytes{{instance=~"{ip_regex}"}})'
        q_gpu_util = f'avg(max_over_time(DCGM_FI_DEV_GPU_UTIL{{instance=~"{ip_regex}"}}[2m]))'
        q_gpu_used = f'sum(DCGM_FI_DEV_FB_USED{{instance=~"{ip_regex}"}})'
        q_gpu_free = f'sum(DCGM_FI_DEV_FB_FREE{{instance=~"{ip_regex}"}})'

        cpu, mem, g_util, g_used, g_free = await asyncio.gather(
            query_prom(client, q_cpu),
            query_prom(client, q_mem),
            query_prom(client, q_gpu_util),
            query_prom(client, q_gpu_used),
            query_prom(client, q_gpu_free),
        )

    return {
        "cpu_percent": round(cpu, 2),
        "memory_percent": round(mem, 2),
        "gpu_util_percent": round(g_util, 2),
        "gpu_mem_used_mb": round(g_used, 2),
        "gpu_mem_total_mb": round(g_used + g_free, 2) if (g_used + g_free) > 0 else 1.0,
        "queue_len": 0.0,
    }


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


async def get_network_metrics(edge_ip: str, cloud_ip: str) -> dict:
    """
    低风险网络探测版本：
    1. 优先尝试通过 ping3 探测 RTT 与丢包。
    2. iperf3 带宽测试默认关闭，仅在显式开启并且本机安装了 iperf3 时执行。
    3. 任一探测不可用时，自动回退到配置里的默认值，不影响调度主流程。

    当前 edge_to_cloud_rtt_ms 仍是近似值，优先使用云端到边端的 RTT 作为估计。
    """

    async def ping_host_with_system_ping(host: str, count: int, timeout: float) -> tuple[float | None, float | None]:
        if not shutil.which("ping"):
            logger.info("未检测到系统 ping 命令，网络 RTT 探测将回退到默认值")
            return None, None

        cmd = [
            "ping",
            "-c",
            str(count),
            "-W",
            str(max(1, int(timeout))),
            host,
        ]
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=(count * timeout) + 2)
            output = (stdout or b"").decode("utf-8", errors="ignore")
            if process.returncode not in {0, 1}:
                logger.warning(
                    "系统 ping 执行失败，已回退到默认值: host=%s, returncode=%s, stderr=%s",
                    host,
                    process.returncode,
                    (stderr or b"").decode("utf-8", errors="ignore").strip(),
                )
                return None, None

            packet_loss_match = re.search(r"(\d+(?:\.\d+)?)%\s+packet loss", output)
            rtt_match = re.search(
                r"rtt min/avg/max(?:/mdev)? = \d+(?:\.\d+)?/(\d+(?:\.\d+)?)/",
                output,
            )
            if not packet_loss_match or not rtt_match:
                logger.warning("系统 ping 输出无法解析，已回退到默认值: host=%s, output=%s", host, output.strip())
                return None, None

            avg_rtt = round(float(rtt_match.group(1)), 2)
            packet_loss = round(float(packet_loss_match.group(1)), 2)
            return avg_rtt, packet_loss
        except asyncio.TimeoutError:
            logger.warning("系统 ping 超时，已回退到默认值: host=%s", host)
            if process and process.returncode is None:
                process.kill()
                await process.communicate()
            return None, None
        except Exception as exc:
            logger.warning("系统 ping 探测失败，已回退到默认值: host=%s, error=%s", host, exc)
            return None, None

    async def ping_host(host: str, count: int, timeout: float) -> tuple[float | None, float | None]:
        try:
            from ping3 import ping
        except ImportError:
            logger.info("未安装 ping3，将尝试使用系统 ping 命令探测 RTT")
            return await ping_host_with_system_ping(host, count, timeout)

        rtts: list[float] = []
        lost = 0
        hard_failure = False
        for _ in range(count):
            try:
                result = await asyncio.to_thread(ping, host, timeout=timeout, unit="ms")
                if result is None:
                    lost += 1
                else:
                    rtts.append(float(result))
            except PermissionError as exc:
                logger.warning("ping3 探测缺少权限，将尝试使用系统 ping: host=%s, error=%s", host, exc)
                hard_failure = True
                break
            except Exception as exc:
                logger.warning("ping 探测失败: host=%s, error=%s", host, exc)
                lost += 1

        if hard_failure:
            return await ping_host_with_system_ping(host, count, timeout)

        avg_rtt = round(sum(rtts) / len(rtts), 2) if rtts else 0.0
        packet_loss = round((lost / count) * 100.0, 2)
        return avg_rtt, packet_loss

    async def measure_bandwidth(target_ip: str) -> float:
        if not settings.NETWORK_ENABLE_IPERF3:
            return 0.0
        if not shutil.which("iperf3"):
            logger.info("未检测到 iperf3，可用带宽将回退到默认值")
            return 0.0

        cmd = [
            "iperf3",
            "-c",
            target_ip,
            "-f",
            "m",
            "-t",
            str(settings.NETWORK_IPERF3_DURATION_SECONDS),
            "--json",
        ]
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=settings.NETWORK_IPERF3_DURATION_SECONDS + 2,
            )
            if process.returncode != 0:
                return 0.0

            payload = json.loads(stdout.decode("utf-8"))
            bits_per_second = payload.get("end", {}).get("sum_received", {}).get("bits_per_second")
            if bits_per_second is None:
                bits_per_second = payload.get("end", {}).get("sum_sent", {}).get("bits_per_second")
            if bits_per_second is None:
                return 0.0

            return round(float(bits_per_second) / 1_000_000.0, 2)
        except asyncio.TimeoutError:
            logger.warning("iperf3 带宽探测超时，已回退到默认值: target_ip=%s", target_ip)
            if process and process.returncode is None:
                process.kill()
                await process.communicate()
            return 0.0
        except Exception as exc:
            logger.warning("iperf3 带宽探测失败，已回退到默认值: target_ip=%s, error=%s", target_ip, exc)
            return 0.0

    default_metrics = {
        "edge_rtt_ms": settings.NETWORK_DEFAULT_EDGE_RTT_MS,
        "cloud_rtt_ms": settings.NETWORK_DEFAULT_CLOUD_RTT_MS,
        "edge_to_cloud_rtt_ms": settings.NETWORK_DEFAULT_EDGE_RTT_MS,
        "estimated_bandwidth_mbps": settings.NETWORK_DEFAULT_BANDWIDTH_MBPS,
        "packet_loss": settings.NETWORK_DEFAULT_PACKET_LOSS,
    }

    edge_ping, cloud_ping, bandwidth = await asyncio.gather(
        ping_host(edge_ip, settings.NETWORK_PING_COUNT, settings.NETWORK_PING_TIMEOUT_SECONDS),
        ping_host(cloud_ip, settings.NETWORK_PING_COUNT, settings.NETWORK_PING_TIMEOUT_SECONDS),
        measure_bandwidth(edge_ip),
    )

    edge_rtt, edge_loss = edge_ping
    cloud_rtt, cloud_loss = cloud_ping

    resolved_edge_rtt = default_metrics["edge_rtt_ms"] if edge_rtt is None else edge_rtt
    resolved_cloud_rtt = default_metrics["cloud_rtt_ms"] if cloud_rtt is None else cloud_rtt
    resolved_bandwidth = bandwidth if bandwidth > 0 else default_metrics["estimated_bandwidth_mbps"]

    loss_candidates = [loss for loss in (edge_loss, cloud_loss) if loss is not None]
    resolved_packet_loss = round(max(loss_candidates), 2) if loss_candidates else default_metrics["packet_loss"]

    metrics = {
        "edge_rtt_ms": round(resolved_edge_rtt, 2),
        "cloud_rtt_ms": round(resolved_cloud_rtt, 2),
        "edge_to_cloud_rtt_ms": round(resolved_edge_rtt, 2),
        "estimated_bandwidth_mbps": round(resolved_bandwidth, 2),
        "packet_loss": resolved_packet_loss,
    }
    logger.info("网络状态采集完成: edge_ip=%s, cloud_ip=%s, metrics=%s", edge_ip, cloud_ip, metrics)
    return metrics


async def dispatch_strategy_to_runtime(node: ModelNode, payload: dict) -> None:
    control_path = node.control_path or "/load_strategy"
    runtime_url = f"http://{node.ip_address}:{node.port}{control_path}"
    async with httpx.AsyncClient() as client:
        response = await client.post(runtime_url, json=payload, timeout=5.0)
        response.raise_for_status()


async def process_schedule_task(task_id: str, username: str, trigger_payload: dict) -> None:
    db = SessionLocal()
    try:
        task = db.query(ScheduleTask).filter(ScheduleTask.task_id == task_id).first()
        if not task:
            logger.error("后台任务启动失败，未找到调度任务: %s", task_id)
            return

        model_type = trigger_payload["model_type"]
        model_type_key = model_type.lower()
        if model_type_key not in MODEL_REGISTRY:
            fail_task(db, task, "不支持的模型类型", f"不支持的模型类型: {model_type}")
            return

        update_task(
            db,
            task,
            status="running",
            phase="strategy",
            phase_progress=5,
            message="正在校验用户权限并准备采集环境指标",
        )

        user = db.query(User).filter(User.username == username).first()
        if not user or not user.allowed_devices:
            fail_task(db, task, "未找到用户设备授权", f"未找到用户 {username} 或该用户未分配设备权限")
            return

        allowed_keys = [device_id for device_id in user.allowed_devices.split(",") if device_id]
        devices = db.query(Device).filter(Device.id.in_(allowed_keys)).all()

        cloud_ip = None
        edge_ip = None
        cloud_device_id = None
        edge_device_id = None

        for device in devices:
            extracted_ip = extract_ip(device.value)
            if not extracted_ip:
                continue

            if device.device_type == "cloud" and not cloud_ip:
                cloud_ip = extracted_ip
                cloud_device_id = device.id
            elif device.device_type == "edge" and not edge_ip:
                edge_ip = extracted_ip
                edge_device_id = device.id

        update_task(
            db,
            task,
            phase_progress=15,
            message="用户授权校验完成，正在采集边云环境指标",
            edge_device_id=edge_device_id,
            cloud_device_id=cloud_device_id,
        )

        if not cloud_ip or not edge_ip or not cloud_device_id or not edge_device_id:
            fail_task(db, task, "用户设备分配不完整", "触发失败：该用户分配的设备不完整，无法凑齐端云流水线 (需1云1边)")
            return

        edge_metrics, cloud_metrics, network_metrics = await asyncio.gather(
            fetch_metrics_from_prometheus(edge_ip),
            fetch_metrics_from_prometheus(cloud_ip),
            get_network_metrics(edge_ip, cloud_ip),
        )
        edge_storage_limit_gb = derive_edge_storage_limit_gb_from_metrics(edge_metrics)

        logger.info(
            "边端可用显存预算采集完成: task_id=%s, edge_ip=%s, storage_limit_gb=%s",
            task_id,
            edge_ip,
            edge_storage_limit_gb,
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
            "任务触发: task_id=%s, user=%s, model=%s, raw_input_json=%s",
            task_id,
            username,
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
            "状态向量编码完成: task_id=%s, user=%s, model=%s, state_vector=%s",
            task_id,
            username,
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
            fail_task(db, task, "未找到在线推理节点", "、".join(missing_parts) + " 未注册或不在线")
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
        )

        edge_callback_url = f"{settings.PUBLIC_BASE_URL}/api/v1/schedule/runtime_callback/edge"
        cloud_callback_url = f"{settings.PUBLIC_BASE_URL}/api/v1/schedule/runtime_callback/cloud"
        runtime_decision_payload = {
            "layer_partitions": decision_result["layer_partitions"],
        }
        edge_dispatch_payload = {
            "task_id": task_id,
            "model_type": model_type,
            "callback_url": edge_callback_url,
            "decision": runtime_decision_payload,
        }
        cloud_dispatch_payload = {
            "task_id": task_id,
            "model_type": model_type,
            "callback_url": cloud_callback_url,
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
            fail_task(db, task, "切分策略下发失败", " | ".join(dispatch_errors))
            return

        update_task(
            db,
            task,
            phase="loading",
            phase_progress=15,
            message="切分策略已下发，等待边云推理节点完成模型加载",
            edge_status="loading",
            cloud_status="loading",
        )

    except asyncio.TimeoutError:
        if task_id in PENDING_STRATEGY_TASKS:
            PENDING_STRATEGY_TASKS.pop(task_id, None)
        task = db.query(ScheduleTask).filter(ScheduleTask.task_id == task_id).first()
        if task:
            fail_task(db, task, "等待算法组计算切分策略超时", "等待算法组计算切分策略超时 (30s)")
    except Exception as exc:
        logger.exception("调度任务执行异常: task_id=%s", task_id)
        task = db.query(ScheduleTask).filter(ScheduleTask.task_id == task_id).first()
        if task:
            fail_task(db, task, "调度任务执行失败", str(exc))
    finally:
        PENDING_STRATEGY_TASKS.pop(task_id, None)
        db.close()


@router.post("/trigger", response_model=ScheduleTaskAcceptedResponse, status_code=202, summary="接收边端触发，异步启动调度任务")
async def collect_raw_json(
    request: EdgeTriggerRequest,
    current_username: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task_id = str(uuid.uuid4())
    task = ScheduleTask(
        task_id=task_id,
        username=current_username,
        model_type=request.model_type,
        status="accepted",
        phase="strategy",
        phase_progress=0,
        overall_progress=0,
        message="任务已受理，开始计算切分策略",
        edge_status="pending",
        cloud_status="pending",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(task)
    db.commit()

    asyncio.create_task(process_schedule_task(task_id, current_username, request.model_dump()))

    return {
        "status": "accepted",
        "task_id": task_id,
        "phase": "strategy",
        "phase_progress": 0,
        "overall_progress": 0,
        "message": "任务已受理，开始计算切分策略",
    }


@router.get("/tasks/{task_id}", response_model=ScheduleTaskStatusResponse, summary="查询调度任务状态")
async def get_schedule_task_status(
    task_id: str,
    current_username: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = (
        db.query(ScheduleTask)
        .filter(ScheduleTask.task_id == task_id, ScheduleTask.username == current_username)
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="未找到该调度任务")
    return serialize_task(task)


@router.get("/tasks/{task_id}/stream", summary="SSE 推送调度任务进度")
async def stream_schedule_task_status(task_id: str, token: str = Query(...)):
    current_username = decode_token_to_username(token)

    async def event_generator():
        while True:
            db = SessionLocal()
            try:
                task = (
                    db.query(ScheduleTask)
                    .filter(ScheduleTask.task_id == task_id, ScheduleTask.username == current_username)
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

        resolved_node_role = callback_role or payload.node_role
        if not resolved_node_role:
            raise HTTPException(status_code=400, detail="缺少 node_role，且未使用带角色的回调地址")

        node_role = resolved_node_role.lower()
        node_status = payload.status.lower()
        progress = clamp_progress(payload.progress)

        if node_role not in {"edge", "cloud"}:
            raise HTTPException(status_code=400, detail="node_role 仅支持 edge 或 cloud")

        if node_status == "failed":
            fail_task(db, task, payload.message, f"{node_role} runtime failed")
            return {"status": "success", "message": "失败状态已记录"}

        update_kwargs = {
            "status": "running",
            "phase": "loading",
            "message": payload.message,
        }
        if node_role == "edge":
            update_kwargs["edge_progress"] = progress
            update_kwargs["edge_status"] = node_status
        else:
            update_kwargs["cloud_progress"] = progress
            update_kwargs["cloud_status"] = node_status

        task = update_task(db, task, **update_kwargs)

        if (
            task.edge_progress >= 100
            and task.cloud_progress >= 100
            and task.edge_status in {"ready", "completed"}
            and task.cloud_status in {"ready", "completed"}
        ):
            update_task(
                db,
                task,
                status="completed",
                message="边云模型均已加载完成，任务结束",
                edge_status="ready",
                cloud_status="ready",
                edge_progress=100,
                cloud_progress=100,
            )

        return {"status": "success", "message": "加载进度已记录"}
    finally:
        db.close()


@router.post("/runtime_callback", summary="【推理节点专用】接收边云模型加载进度回调（兼容旧版）")
async def receive_runtime_progress(payload: RuntimeProgressCallbackRequest):
    return await handle_runtime_progress(payload)


@router.post("/runtime_callback/edge", summary="【推理节点专用】接收边端模型加载进度回调")
async def receive_edge_runtime_progress(payload: RuntimeProgressCallbackRequest):
    return await handle_runtime_progress(payload, callback_role="edge")


@router.post("/runtime_callback/cloud", summary="【推理节点专用】接收云端模型加载进度回调")
async def receive_cloud_runtime_progress(payload: RuntimeProgressCallbackRequest):
    return await handle_runtime_progress(payload, callback_role="cloud")
