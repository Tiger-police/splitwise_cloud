import asyncio
import os

import httpx
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Mock Edge Runtime")

BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL", "http://127.0.0.1:8010")
REGISTER_URL = f"{BACKEND_BASE_URL}/api/v1/models/register"
UNREGISTER_URL = f"{BACKEND_BASE_URL}/api/v1/models/unregister"
RUNTIME_CALLBACK_URL = f"{BACKEND_BASE_URL}/api/v1/schedule/runtime_callback/edge"
RUNTIME_IP = os.getenv("EDGE_RUNTIME_IP", "127.0.0.1")
RUNTIME_PORT = 7001
REGISTERED_MODEL_KEY = os.getenv("EDGE_RUNTIME_MODEL_KEY", "gpt2")
STEP_DELAY_SECONDS = float(os.getenv("EDGE_RUNTIME_STEP_DELAY_SECONDS", "2.5"))

MODEL_PROFILES = {
    "gpt2": {
        "display_name": "GPT-2",
        "checkpoints": [
            (10, "边端已接收 GPT-2 策略，开始准备加载"),
            (25, "边端正在校验 GPT-2 切分配置"),
            (40, "边端正在加载 GPT-2 权重"),
            (60, "边端正在初始化 GPT-2 推理上下文"),
            (80, "边端正在预热 GPT-2 运行环境"),
            (92, "边端 GPT-2 即将就绪"),
            (100, "边端 GPT-2 加载完成"),
        ],
    },
    "tinyllama": {
        "display_name": "TinyLlama",
        "checkpoints": [
            (12, "边端已接收 TinyLlama 策略，开始准备加载"),
            (28, "边端正在校验 TinyLlama 切分配置"),
            (45, "边端正在加载 TinyLlama 权重"),
            (62, "边端正在初始化 TinyLlama 推理上下文"),
            (82, "边端正在预热 TinyLlama 运行环境"),
            (94, "边端 TinyLlama 即将就绪"),
            (100, "边端 TinyLlama 加载完成"),
        ],
    },
    "llama-3.2-3b": {
        "display_name": "Llama 3.2 3B",
        "checkpoints": [
            (8, "边端已接收 Llama 3.2 3B 策略，开始准备加载"),
            (18, "边端正在分配 Llama 3.2 3B 显存"),
            (32, "边端正在校验 Llama 3.2 3B 切分配置"),
            (50, "边端正在加载 Llama 3.2 3B 权重"),
            (72, "边端正在初始化 Llama 3.2 3B 推理上下文"),
            (90, "边端正在预热 Llama 3.2 3B 运行环境"),
            (100, "边端 Llama 3.2 3B 加载完成"),
        ],
    },
}


class RuntimeDispatchPayload(BaseModel):
    task_id: str
    model_type: str
    decision: dict


async def register_self():
    payload = {
        "model_key": REGISTERED_MODEL_KEY,
        "ip_address": RUNTIME_IP,
        "port": RUNTIME_PORT,
    }
    async with httpx.AsyncClient() as client:
        await client.post(REGISTER_URL, json=payload)


async def unregister_self():
    payload = {
        "ip_address": RUNTIME_IP,
        "port": RUNTIME_PORT,
    }
    async with httpx.AsyncClient() as client:
        await client.post(UNREGISTER_URL, json=payload)


@app.on_event("startup")
async def startup_event():
    await register_self()
    print("📱 Edge runtime 已注册到云端")


@app.on_event("shutdown")
async def shutdown_event():
    await unregister_self()
    print("📱 Edge runtime 已从云端注销")


@app.post("/load_strategy")
async def load_strategy(payload: RuntimeDispatchPayload):
    print(
        f"📱 [Edge Runtime] 收到任务 {payload.task_id} 的切分策略，"
        f"目标模型 = {payload.model_type}，开始模拟加载..."
    )
    asyncio.create_task(simulate_loading(payload.task_id, payload.model_type))
    return {"status": "accepted", "message": "edge runtime loading started"}


@app.get("/health")
async def health():
    return {"status": "ok", "node_role": "edge"}


async def simulate_loading(task_id: str, model_type: str):
    profile = MODEL_PROFILES.get(
        model_type.lower(),
        {
            "display_name": model_type,
            "checkpoints": [
                (10, f"边端已接收 {model_type} 策略，开始准备加载"),
                (25, f"边端正在校验 {model_type} 切分配置"),
                (40, f"边端正在加载 {model_type} 权重"),
                (60, f"边端正在初始化 {model_type} 推理上下文"),
                (80, f"边端正在预热 {model_type} 运行环境"),
                (92, f"边端 {model_type} 即将就绪"),
                (100, f"边端 {model_type} 加载完成"),
            ],
        },
    )
    checkpoints = profile["checkpoints"]
    async with httpx.AsyncClient() as client:
        for progress, message in checkpoints:
            await asyncio.sleep(STEP_DELAY_SECONDS)
            await client.post(
                RUNTIME_CALLBACK_URL,
                json={
                    "task_id": task_id,
                    "status": "ready" if progress == 100 else "loading",
                    "progress": progress,
                    "message": message,
                },
            )


if __name__ == "__main__":
    print("=========================================")
    print("📱 Mock Edge Runtime 已启动，监听 7001 端口...")
    print(f"📱 注册模型标识: {REGISTERED_MODEL_KEY}")
    print(f"📱 单步进度间隔: {STEP_DELAY_SECONDS:.1f} 秒")
    print("=========================================")
    uvicorn.run(app, host="0.0.0.0", port=RUNTIME_PORT)
