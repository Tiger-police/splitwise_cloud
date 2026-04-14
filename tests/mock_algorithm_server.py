import asyncio
import os

import httpx
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="Mock 算法切分服务")
ALGORITHM_DELAY_SECONDS = float(os.getenv("ALGORITHM_MOCK_DELAY_SECONDS", "6.0"))

REAL_LLAMA_LAYER_PARTITIONS = [
    {"layer_id": 0, "head_assignments": [0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1], "ffn_assignment": 0},
    {"layer_id": 1, "head_assignments": [1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0], "ffn_assignment": 1},
    {"layer_id": 2, "head_assignments": [0, 0, 1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 0, 0, 1, 1], "ffn_assignment": 0},
    {"layer_id": 3, "head_assignments": [1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 0, 0], "ffn_assignment": 1},
    {"layer_id": 4, "head_assignments": [0, 1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 0], "ffn_assignment": 0},
    {"layer_id": 5, "head_assignments": [1, 0, 0, 1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 0, 0, 1], "ffn_assignment": 1},
    {"layer_id": 6, "head_assignments": [0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0], "ffn_assignment": 0},
    {"layer_id": 7, "head_assignments": [1, 0, 1, 1, 0, 1, 1, 0, 1, 1, 0, 1, 1, 0, 1, 1, 0, 1, 1, 0, 1, 1, 0, 1], "ffn_assignment": 1},
    {"layer_id": 8, "head_assignments": [0, 0, 0, 1, 1, 1, 0, 0, 0, 1, 1, 1, 0, 0, 0, 1, 1, 1, 0, 0, 0, 1, 1, 1], "ffn_assignment": 0},
    {"layer_id": 9, "head_assignments": [1, 1, 1, 0, 0, 0, 1, 1, 1, 0, 0, 0, 1, 1, 1, 0, 0, 0, 1, 1, 1, 0, 0, 0], "ffn_assignment": 1},
    {"layer_id": 10, "head_assignments": [0, 1, 0, 1, 1, 0, 0, 1, 0, 1, 1, 0, 0, 1, 0, 1, 1, 0, 0, 1, 0, 1, 1, 0], "ffn_assignment": 0},
    {"layer_id": 11, "head_assignments": [1, 0, 1, 0, 0, 1, 1, 0, 1, 0, 0, 1, 1, 0, 1, 0, 0, 1, 1, 0, 1, 0, 0, 1], "ffn_assignment": 1},
    {"layer_id": 12, "head_assignments": [0, 1, 1, 1, 0, 0, 0, 1, 1, 1, 0, 0, 0, 1, 1, 1, 0, 0, 0, 1, 1, 1, 0, 0], "ffn_assignment": 0},
    {"layer_id": 13, "head_assignments": [1, 0, 0, 0, 1, 1, 1, 0, 0, 0, 1, 1, 1, 0, 0, 0, 1, 1, 1, 0, 0, 0, 1, 1], "ffn_assignment": 1},
    {"layer_id": 14, "head_assignments": [0, 0, 1, 0, 1, 1, 0, 0, 1, 0, 1, 1, 0, 0, 1, 0, 1, 1, 0, 0, 1, 0, 1, 1], "ffn_assignment": 0},
    {"layer_id": 15, "head_assignments": [1, 1, 0, 1, 0, 0, 1, 1, 0, 1, 0, 0, 1, 1, 0, 1, 0, 0, 1, 1, 0, 1, 0, 0], "ffn_assignment": 1},
    {"layer_id": 16, "head_assignments": [0, 1, 0, 1, 0, 0, 1, 0, 1, 0, 0, 1, 0, 1, 0, 1, 0, 0, 1, 0, 1, 0, 0, 1], "ffn_assignment": 0},
    {"layer_id": 17, "head_assignments": [1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 1, 0], "ffn_assignment": 1},
    {"layer_id": 18, "head_assignments": [0, 1, 1, 0, 1, 0, 0, 1, 1, 0, 1, 0, 0, 1, 1, 0, 1, 0, 0, 1, 1, 0, 1, 0], "ffn_assignment": 0},
    {"layer_id": 19, "head_assignments": [1, 0, 0, 1, 0, 1, 1, 0, 0, 1, 0, 1, 1, 0, 0, 1, 0, 1, 1, 0, 0, 1, 0, 1], "ffn_assignment": 1},
    {"layer_id": 20, "head_assignments": [0, 0, 1, 1, 1, 0, 0, 0, 1, 1, 1, 0, 0, 0, 1, 1, 1, 0, 0, 0, 1, 1, 1, 0], "ffn_assignment": 0},
    {"layer_id": 21, "head_assignments": [1, 1, 0, 0, 0, 1, 1, 1, 0, 0, 0, 1, 1, 1, 0, 0, 0, 1, 1, 1, 0, 0, 0, 1], "ffn_assignment": 1},
    {"layer_id": 22, "head_assignments": [0, 1, 0, 0, 0, 1, 0, 1, 0, 0, 0, 1, 0, 1, 0, 0, 0, 1, 0, 1, 0, 0, 0, 1], "ffn_assignment": 0},
    {"layer_id": 23, "head_assignments": [1, 0, 1, 1, 1, 0, 1, 0, 1, 1, 1, 0, 1, 0, 1, 1, 1, 0, 1, 0, 1, 1, 1, 0], "ffn_assignment": 1},
    {"layer_id": 24, "head_assignments": [0, 1, 1, 0, 0, 0, 1, 1, 0, 0, 0, 1, 0, 1, 1, 0, 0, 0, 1, 1, 0, 0, 0, 1], "ffn_assignment": 0},
    {"layer_id": 25, "head_assignments": [1, 0, 0, 1, 1, 1, 0, 0, 1, 1, 1, 0, 1, 0, 0, 1, 1, 1, 0, 0, 1, 1, 1, 0], "ffn_assignment": 1},
    {"layer_id": 26, "head_assignments": [0, 1, 0, 1, 1, 1, 0, 1, 0, 1, 1, 1, 0, 1, 0, 1, 1, 1, 0, 1, 0, 1, 1, 1], "ffn_assignment": 0},
    {"layer_id": 27, "head_assignments": [1, 0, 1, 0, 0, 0, 1, 0, 1, 0, 0, 0, 1, 0, 1, 0, 0, 0, 1, 0, 1, 0, 0, 0], "ffn_assignment": 1},
]


def build_strategy_payload(task_id: str, model_type: str) -> dict:
    return {
        "task_id": task_id,
        "model_type": model_type,
        "layer_partitions": REAL_LLAMA_LAYER_PARTITIONS,
    }

# 接收云端发来的26维向量
class CalcRequest(BaseModel):
    task_id: str
    model_type: str
    state_vector: list

@app.post("/api/calculate")
async def calculate_strategy(req: CalcRequest):
    print(f"\n🧠 [算法端] 收到任务 ID: {req.task_id}，模型: {req.model_type}")
    print(f"🧠 [算法端] 正在进行深度学习推理模拟 (预计耗时{ALGORITHM_DELAY_SECONDS:.1f}秒)...")

    # 启动后台协程去处理，立即给云端返回已受理，防止云端卡住
    asyncio.create_task(process_and_callback(req.task_id, req.model_type))
    return {"status": "accepted"}

async def process_and_callback(task_id: str, model_type: str):
    await asyncio.sleep(ALGORITHM_DELAY_SECONDS) # 模拟算法矩阵计算的时间
    strategy_payload = build_strategy_payload(task_id, model_type)

    print(f"🚀 [算法端] 计算完成！正在回调云端中枢的预留接口...")
    # 指向你云端刚刚写好的回调接口
    CLOUD_CALLBACK_URL = "http://127.0.0.1:8010/api/v1/schedule/strategy_callback"

    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(CLOUD_CALLBACK_URL, json=strategy_payload)
            print(f"✅ [算法端] 策略已成功送达云端！云端响应: {res.json()}\n")
    except Exception as e:
        print(f"❌ [算法端] 回调云端失败，请检查云端服务是否开启: {e}")

if __name__ == "__main__":
    print("=========================================")
    print("🤖 虚拟算法服务已启动，监听 5000 端口...")
    print("等待云端中枢发送向量数据...")
    print("=========================================")
    uvicorn.run(app, host="0.0.0.0", port=5000)
