import requests
import json

url = "http://127.0.0.1:8010/api/v1/schedule/trigger"

payload = {
    "model_type": "gpt2",
    "prompt": "你好，请帮我写一段关于人工智能的介绍。",
    "edge_device": "cuda",
    "edge_storage_limit_gb": 16.0,
    "edge_ip": "10.144.144.3",  # 假设边缘节点 A 的 IP
    "cloud_ip": "10.144.144.2", # 假设云端主节点的 IP
    "network_metrics": {
        "edge_rtt_ms": 4.84,
        "cloud_rtt_ms": 2.72,
        "edge_to_cloud_rtt_ms": 4.84,
        "estimated_bandwidth_mbps": 1000.0,
        "packet_loss": 0.0
    }
}

print("🚀 边缘端正在向云端发送推理触发请求...")
try:
    response = requests.post(url, json=payload)
    print(f"📡 云端返回状态码: {response.status_code}")
    print("📦 云端返回的组装结果:")
    print(json.dumps(response.json(), indent=2, ensure_ascii=False))
except Exception as e:
    print(f"❌ 请求失败: {e}")