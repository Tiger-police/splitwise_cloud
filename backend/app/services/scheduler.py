import logging
import asyncio
from typing import List, Dict

logger = logging.getLogger("SchedulerService")


def encode_state(model_type: str, env: dict, max_context_length: int) -> List[float]:
    """
    将环境状态编码为 26 维特征向量，严格遵循 API 文档第 4 节的归一化要求
    """
    state = [0.0] * 26

    edge = env.get("edge", {})
    cloud = env.get("cloud", {})
    network = env.get("network", {})
    mt_lower = model_type.lower()

    state[0] = max_context_length / 256.0

    if mt_lower == "gpt2":
        state[1] = 1.0
    elif mt_lower == "tinyllama":
        state[2] = 1.0

    edge_spec = edge.get("model_spec", {})
    state[3] = edge_spec.get("num_hidden_layers", 0) / 64.0
    state[4] = edge_spec.get("num_attention_heads", 0) / 64.0

    state[5] = 1.0 if edge.get("device", "").lower() == "cuda" else 0.0
    edge_m = edge.get("metrics", {})
    state[6] = edge_m.get("cpu_percent", 0) / 100.0
    state[7] = edge_m.get("memory_percent", 0) / 100.0
    state[8] = edge_m.get("gpu_util_percent", 0) / 100.0

    edge_gpu_tot = edge_m.get("gpu_mem_total_mb", 1.0)
    if edge_gpu_tot > 0:
        ratio = edge_m.get("gpu_mem_used_mb", 0) / edge_gpu_tot
        state[9] = min(max(ratio, 0.0), 1.0)

    state[10] = min(max(edge_m.get("queue_len", 0) / 64.0, 0.0), 1.0)

    cloud_spec = cloud.get("model_spec", {})
    state[11] = cloud_spec.get("num_hidden_layers", 0) / 64.0
    state[12] = cloud_spec.get("num_attention_heads", 0) / 64.0

    state[13] = 1.0 if cloud.get("device", "").lower() == "cuda" else 0.0
    cloud_m = cloud.get("metrics", {})
    state[14] = cloud_m.get("cpu_percent", 0) / 100.0
    state[15] = cloud_m.get("memory_percent", 0) / 100.0
    state[16] = cloud_m.get("gpu_util_percent", 0) / 100.0

    cloud_gpu_tot = cloud_m.get("gpu_mem_total_mb", 1.0)
    if cloud_gpu_tot > 0:
        ratio = cloud_m.get("gpu_mem_used_mb", 0) / cloud_gpu_tot
        state[17] = min(max(ratio, 0.0), 1.0)

    state[18] = min(max(cloud_m.get("queue_len", 0) / 64.0, 0.0), 1.0)

    state[19] = min(max(network.get("edge_rtt_ms", 0) / 500.0, 0.0), 1.0)
    state[20] = min(max(network.get("cloud_rtt_ms", 0) / 500.0, 0.0), 1.0)
    state[21] = min(max(network.get("edge_to_cloud_rtt_ms", 0) / 500.0, 0.0), 1.0)
    state[22] = min(max(network.get("estimated_bandwidth_mbps", 0) / 10000.0, 0.0), 1.0)
    state[23] = min(max(network.get("packet_loss", 0) / 100.0, 0.0), 1.0)

    edge_storage = edge.get("storage_limit_gb", 16.0) or 16.0
    state[24] = min(max(edge_storage / 32.0, 0.0), 1.0)

    req_storage = 8.0 if mt_lower == "gpt2" else 24.0
    state[25] = min(max(req_storage / edge_storage, 0.0), 1.0)

    return state


async def request_strategy_model_mock(state_vector: List[float], model_type: str, num_layers: int) -> Dict:
    """
    [Mock Client] 模拟将 26 维向量发送给算法组的策略模型接口
    """
    print("\n" + "🚀 " * 20)
    print("📡 正在向策略模型 API 发送 26 维降维特征向量...")
    print(f"📐 向量内容: {[round(v, 4) for v in state_vector]}")

    await asyncio.sleep(0.5)

    layer_partitions = []
    for i in range(num_layers):
        if i < num_layers // 2:
            layer_partitions.append(
                {"layer_id": i, "head_assignments": [0] * 12, "ffn_assignment": 0})
        else:
            layer_partitions.append(
                {"layer_id": i, "head_assignments": [1] * 12, "ffn_assignment": 1})

    print("✅ 成功接收到策略模型的细粒度切分方案！")
    print("🚀 " * 20 + "\n")

    return {
        "model_type": model_type,
        "layer_partitions": layer_partitions
    }
