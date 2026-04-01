import httpx
import asyncio

API_BASE = "http://127.0.0.1:8010/api/v1"

nodes_to_simulate = [
    {"role": "☁️ 云端解码", "ip": "10.144.144.2", "port": 8022, "model": "meta-llama/Llama-3.2-3B-Instruct"},
    {"role": "☁️ 云端解码", "ip": "10.144.144.2", "port": 8023, "model": "Qwen/Qwen2.5-7B-Instruct"},
    {"role": "📱 边缘预填充", "ip": "10.144.144.3", "port": 8021, "model": "meta-llama/Llama-3.2-3B-Instruct"},
    {"role": "📱 边缘预填充", "ip": "10.144.144.3", "port": 8024, "model": "microsoft/Phi-3-mini-4k-instruct"},
    {"role": "📱 边缘预填充", "ip": "10.144.144.4", "port": 8021, "model": "Qwen/Qwen2.5-7B-Instruct"}
]


async def register_node(client, node):
    """向云端发送注册/心跳包"""
    try:
        await client.post(f"{API_BASE}/models/register", json={
            "model_name": node["model"],
            "ip_address": node["ip"],
            "port": node["port"]
        })
        print(f"✅ [{node['role']}] 心跳 -> {node['ip']}:{node['port']} ({node['model']})")
    except Exception as e:
        print(f"❌ [{node['role']}] 连接失败: {e}")


async def unregister_node(client, node):
    """向云端发送注销包 (主动下线)"""
    try:
        await client.post(f"{API_BASE}/models/unregister", json={
            "ip_address": node["ip"],
            "port": node["port"]
        })
        print(f"🛑 [{node['role']}] 已主动注销 -> {node['ip']}:{node['port']}")
    except Exception as e:
        print(f"❌ 注销失败: {e}")


async def main():
    print("====== 🚀 边云协同多物理节点 模拟集群启动 ======")
    print("提示：按 Ctrl+C 将触发优雅下线，瞬间通知云端摘除节点。\n")

    async with httpx.AsyncClient() as client:
        try:
            while True:
                tasks = [register_node(client, node) for node in nodes_to_simulate]
                await asyncio.gather(*tasks)
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            # 捕获退出信号，执行下线操作
            print("\n\n⚠️ 收到中断信号，正在向云端发送 [主动下线] 请求...")
            shutdown_tasks = [unregister_node(client, node) for node in nodes_to_simulate]
            await asyncio.gather(*shutdown_tasks)
            print("👋 所有节点已成功从大盘摘除，模拟器退出。")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass