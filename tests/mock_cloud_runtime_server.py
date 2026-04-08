import asyncio
import os

import httpx
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Mock Cloud Runtime")

REGISTER_URL = "http://127.0.0.1:8010/api/v1/models/register"
UNREGISTER_URL = "http://127.0.0.1:8010/api/v1/models/unregister"
RUNTIME_IP = os.getenv("CLOUD_RUNTIME_IP", "127.0.0.1")
RUNTIME_PORT = 7002
REGISTERED_MODEL_KEY = os.getenv("CLOUD_RUNTIME_MODEL_KEY", "gpt2")

MODEL_PROFILES = {
    "gpt2": {
        "display_name": "GPT-2",
        "checkpoints": [
            (10, "云端已接收 GPT-2 策略，开始装载模型"),
            (35, "云端正在初始化 GPT-2"),
            (70, "云端正在加载 GPT-2 权重"),
            (100, "云端 GPT-2 加载完成"),
        ],
    },
    "tinyllama": {
        "display_name": "TinyLlama",
        "checkpoints": [
            (15, "云端已接收 TinyLlama 策略，开始装载模型"),
            (40, "云端正在初始化 TinyLlama"),
            (75, "云端正在加载 TinyLlama 权重"),
            (100, "云端 TinyLlama 加载完成"),
        ],
    },
    "llama-3.2-3b": {
        "display_name": "Llama 3.2 3B",
        "checkpoints": [
            (8, "云端已接收 Llama 3.2 3B 策略，开始分配显存"),
            (30, "云端正在加载 Llama 3.2 3B 权重"),
            (65, "云端正在初始化 Llama 3.2 3B 推理上下文"),
            (100, "云端 Llama 3.2 3B 加载完成"),
        ],
    },
}


class RuntimeDispatchPayload(BaseModel):
    task_id: str
    model_type: str
    callback_url: str
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
    print("☁️ Cloud runtime 已注册到云端")


@app.on_event("shutdown")
async def shutdown_event():
    await unregister_self()
    print("☁️ Cloud runtime 已从云端注销")


@app.post("/load_strategy")
async def load_strategy(payload: RuntimeDispatchPayload):
    print(
        f"☁️ [Cloud Runtime] 收到任务 {payload.task_id} 的切分策略，"
        f"目标模型 = {payload.model_type}，开始模拟加载..."
    )
    asyncio.create_task(simulate_loading(payload.task_id, payload.model_type, payload.callback_url))
    return {"status": "accepted", "message": "cloud runtime loading started"}


@app.get("/health")
async def health():
    return {"status": "ok", "node_role": "cloud"}


async def simulate_loading(task_id: str, model_type: str, callback_url: str):
    profile = MODEL_PROFILES.get(
        model_type.lower(),
        {
            "display_name": model_type,
            "checkpoints": [
                (10, f"云端已接收 {model_type} 策略，开始装载模型"),
                (35, f"云端正在初始化 {model_type}"),
                (70, f"云端正在加载 {model_type} 权重"),
                (100, f"云端 {model_type} 加载完成"),
            ],
        },
    )
    checkpoints = profile["checkpoints"]
    async with httpx.AsyncClient() as client:
        for progress, message in checkpoints:
            await asyncio.sleep(1)
            await client.post(
                callback_url,
                json={
                    "task_id": task_id,
                    "status": "ready" if progress == 100 else "loading",
                    "progress": progress,
                    "message": message,
                },
            )


if __name__ == "__main__":
    print("=========================================")
    print("☁️ Mock Cloud Runtime 已启动，监听 7002 端口...")
    print(f"☁️ 注册模型标识: {REGISTERED_MODEL_KEY}")
    print("=========================================")
    uvicorn.run(app, host="0.0.0.0", port=RUNTIME_PORT)
