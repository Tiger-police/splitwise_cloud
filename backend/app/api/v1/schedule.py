import re  # 引入正则库用于提取IP
from fastapi import APIRouter, HTTPException, Depends  # 新增 Depends
from sqlalchemy.orm import Session  # 新增数据库 Session
from app.api.deps import get_db  # 新增获取数据库连接的依赖
from app.models.models import User, Device  # 新增数据库模型
from app.schemas.schemas import EdgeTriggerRequest
from app.services.scheduler import encode_state, request_strategy_model_mock
import json
import shutil
import asyncio
import httpx

PROMETHEUS_URL = "http://10.144.144.2:9090"
router = APIRouter()

MODEL_REGISTRY = {
    "gpt2": {
        "architecture": "gpt2",
        "num_hidden_layers": 12,
        "num_attention_heads": 12,
        "hidden_size": 768,
        "intermediate_size": 3072,
        "vocab_size": 50257
    },
    "tinyllama": {
        "architecture": "llama",
        "num_hidden_layers": 22,
        "num_attention_heads": 32,
        "hidden_size": 2048,
        "intermediate_size": 5632,
        "vocab_size": 32000
    },
    "llama-3.2-3b": {
        "architecture": "llama",
        "num_hidden_layers": 28,      # Llama 3.2 3B 标准层数
        "num_attention_heads": 24,    # 注意力头数 (Query Heads)
        "hidden_size": 3072,          # 隐藏层维度
        "intermediate_size": 8192,    # FFN 层的中间维度
        "vocab_size": 128256          # Llama 3 系列的标准词表大小
    }
}

async def query_prom(client: httpx.AsyncClient, query: str) -> float:
    """向 Prometheus 发起 PromQL 查询并解析单点浮点值"""
    try:
        resp = await client.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": query}, timeout=3.0)
        data = resp.json()
        result = data.get("data", {}).get("result", [])
        if result:
            # Prometheus 的返回值结构通常为: "value": [1682838383.123, "45.5"]
            return float(result[0].get("value", [0, "0"])[1])
    except Exception as e:
        print(f"⚠️ Prometheus 查询失败 [{query}]: {e}")
    return 0.0  # 如果查不到或报错，安全回退返回 0.0

async def fetch_metrics_from_prometheus(ip: str) -> dict:
    """根据传入的 IP 地址，向 Prometheus 提取 5 大真实硬件指标"""
    async with httpx.AsyncClient() as client:
        # 正则匹配该 IP 下所有端口的 exporter
        ip_regex = f"^{ip}:.*"

        q_cpu = f'100 - (avg(rate(node_cpu_seconds_total{{instance=~"{ip_regex}",mode="idle"}}[1m])) * 100)'
        q_mem = f'100 * (1 - node_memory_MemAvailable_bytes{{instance=~"{ip_regex}"}} / node_memory_MemTotal_bytes{{instance=~"{ip_regex}"}})'
        q_gpu_util = f'avg(DCGM_FI_DEV_GPU_UTIL{{instance=~"{ip_regex}"}})'
        q_gpu_used = f'sum(DCGM_FI_DEV_FB_USED{{instance=~"{ip_regex}"}})'
        q_gpu_free = f'sum(DCGM_FI_DEV_FB_FREE{{instance=~"{ip_regex}"}})'

        # 并发执行 5 个查询，极大提高响应速度
        cpu, mem, g_util, g_used, g_free = await asyncio.gather(
            query_prom(client, q_cpu),
            query_prom(client, q_mem),
            query_prom(client, q_gpu_util),
            query_prom(client, q_gpu_used),
            query_prom(client, q_gpu_free)
        )

    return {
        "cpu_percent": round(cpu, 2),
        "memory_percent": round(mem, 2),
        "gpu_util_percent": round(g_util, 2),
        "gpu_mem_used_mb": round(g_used, 2),
        "gpu_mem_total_mb": round(g_used + g_free, 2) if (g_used + g_free) > 0 else 1.0,
        "queue_len": 0.0  # 暂留占位
    }


async def ping_host(host: str, count: int = 4, timeout: float = 1.0) -> tuple[float, float]:
    """使用 ping3 测量主机 RTT 和丢包率。"""
    try:
        from ping3 import ping
    except ImportError:
        return 0.0, 0.0

    rtts: list[float] = []
    lost = 0
    for _ in range(count):
        try:
            result = await asyncio.to_thread(ping, host, timeout=timeout, unit="ms")
            if result is None:
                lost += 1
            else:
                rtts.append(float(result))
        except Exception:
            lost += 1

    avg_rtt = round(sum(rtts) / len(rtts), 2) if rtts else 0.0
    packet_loss = round((lost / count) * 100.0, 2)
    return avg_rtt, packet_loss


async def measure_bandwidth(edge_ip: str) -> float:
    """若本机上安装了 iperf3，并且 edge 端有 iperf3 server，可测量带宽。"""
    if not shutil.which("iperf3"):
        return 0.0

    cmd = ["iperf3", "-c", edge_ip, "-f", "m", "-t", "3", "--json"]
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            return 0.0

        payload = json.loads(stdout.decode("utf-8"))
        bits_per_second = payload.get("end", {}).get("sum_received", {}).get("bits_per_second")
        if bits_per_second is None:
            bits_per_second = payload.get("end", {}).get("sum_sent", {}).get("bits_per_second")
        if bits_per_second is None:
            return 0.0

        return round(float(bits_per_second) / 1_000_000.0, 2)
    except Exception:
        return 0.0


async def get_network_metrics(edge_ip: str, cloud_ip: str) -> dict:
    """
    云端主动收集端到端的网络状态指标。

    真实采集方案建议：
    1. RTT 与丢包：通过 ping 或 ICMP 测试获取 edge -> cloud 和 cloud -> edge 的延迟与丢包。
    2. 带宽：如果 edge 端能运行 iperf3 server，可由 cloud 端发起 iperf3 测试；
       否则可从 Prometheus 的网卡流量速率指标估算。
    3. 如果只有单向可测量，则将 edge_to_cloud_rtt_ms 设为可测 RTT 的近似值，
       并在后续设计中补充 edge 端主动上报。
    """
    edge_rtt, edge_loss = await ping_host(edge_ip)
    cloud_rtt, cloud_loss = await ping_host(cloud_ip)
    bandwidth = await measure_bandwidth(edge_ip)

    estimated_bandwidth_mbps = bandwidth if bandwidth > 0 else 1000.0
    packet_loss = round(max(edge_loss, cloud_loss), 2)

    return {
        "edge_rtt_ms": edge_rtt,
        "cloud_rtt_ms": cloud_rtt,
        "edge_to_cloud_rtt_ms": edge_rtt,
        "estimated_bandwidth_mbps": estimated_bandwidth_mbps,
        "packet_loss": packet_loss
    }

@router.post("/trigger", summary="接收边端触发，获取调度策略")
async def collect_raw_json(request: EdgeTriggerRequest, db: Session = Depends(get_db)):
    model_type_key = request.model_type.lower()
    if model_type_key not in MODEL_REGISTRY:
        raise HTTPException(status_code=400, detail=f"不支持的模型类型: {request.model_type}")

    model_spec = MODEL_REGISTRY[model_type_key].copy()
    model_spec["model_type"] = request.model_type

    prompt_len = 64

    user = db.query(User).filter(User.username == request.username).first()
    if not user or not user.allowed_devices:
        raise HTTPException(status_code=404, detail=f"未找到用户 {request.username} 或该用户未分配设备权限")

    allowed_keys = user.allowed_devices.split(",")
    devices = db.query(Device).filter(Device.id.in_(allowed_keys)).all()

    cloud_ip = None
    edge_ip = None

    import re
    for d in devices:
        ip_match = re.search(r'(?:\d{1,3}\.){3}\d{1,3}', d.value)
        if not ip_match:
            continue
        extracted_ip = ip_match.group(0)

        if d.device_type == "cloud" and not cloud_ip:
            cloud_ip = extracted_ip
        elif d.device_type == "edge" and not edge_ip:
            edge_ip = extracted_ip

    if not cloud_ip or not edge_ip:
        raise HTTPException(status_code=400, detail="触发失败：该用户分配的设备不完整，无法凑齐端云流水线 (需1云1边)")

    edge_metrics = await fetch_metrics_from_prometheus(edge_ip)
    cloud_metrics = await fetch_metrics_from_prometheus(cloud_ip)

    network_metrics = await get_network_metrics(edge_ip, cloud_ip)

    raw_input_json = {
        "model_type": request.model_type,
        "prompt_len": prompt_len,
        "env": {
            "edge": {
                "device": request.edge_device,
                "model_spec": model_spec,
                "metrics": edge_metrics,
                "storage_limit_gb": request.edge_storage_limit_gb
            },
            "cloud": {
                "device": "cuda",
                "model_spec": model_spec,
                "metrics": cloud_metrics
            },
            "network": network_metrics  # 👇 使用后端自己拿到的网络数据
        }
    }

    state_vector = encode_state(
        model_type=raw_input_json["model_type"],
        env=raw_input_json["env"],
        prompt_len=raw_input_json["prompt_len"]
    )

    num_layers = model_spec.get("num_hidden_layers", 12)
    decision_result = await request_strategy_model_mock(
        state_vector=state_vector,
        model_type=request.model_type,
        num_layers=num_layers
    )

    return {
        "status": "success",
        "message": f"策略下发完毕 (识别到云IP:{cloud_ip}, 边IP:{edge_ip})",
        "raw_json_dump": raw_input_json,
        "decision": decision_result
    }