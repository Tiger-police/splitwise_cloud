import requests
import json
import time
import os
import jwt
import uuid
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / "backend" / ".env")

EXCHANGE_URL = "http://127.0.0.1:8010/api/v1/auth/exchange"
TRIGGER_URL = "http://127.0.0.1:8010/api/v1/schedule/trigger"
TASK_URL_TEMPLATE = "http://127.0.0.1:8010/api/v1/schedule/tasks/{task_id}"
OPENWEBUI_JWT_SECRET = os.getenv("OPENWEBUI_JWT_SECRET", "")
OPENWEBUI_JWT_ALGORITHM = os.getenv("OPENWEBUI_JWT_ALGORITHM", "HS256")
OPENWEBUI_SKIP_SIGNATURE_VERIFY = os.getenv("OPENWEBUI_SKIP_SIGNATURE_VERIFY", "").strip().lower() in {"1", "true", "yes", "on"}
# 默认使用本地种子数据中已绑定到 userA 的 OpenWebUI ID。
# 如果需要联调真实 OpenWebUI 用户，可通过环境变量 OPENWEBUI_MOCK_USER_ID 覆盖。
OPENWEBUI_MOCK_USER_ID = os.getenv("OPENWEBUI_MOCK_USER_ID", "ow-userA")
OPENWEBUI_MOCK_EXPIRE_SECONDS = int(os.getenv("OPENWEBUI_MOCK_EXPIRE_SECONDS", "3600"))

payload = {
    "model_type": "gpt2"
}

def build_mock_openwebui_token(openwebui_user_id: str) -> str:
    claims = {
        "id": openwebui_user_id,
        "exp": int(time.time()) + OPENWEBUI_MOCK_EXPIRE_SECONDS,
        "jti": str(uuid.uuid4()),
    }

    if OPENWEBUI_SKIP_SIGNATURE_VERIFY:
        return jwt.encode(
            claims,
            "dev-openwebui-skip-verify",
            algorithm=OPENWEBUI_JWT_ALGORITHM,
        )

    if not OPENWEBUI_JWT_SECRET:
        raise RuntimeError("请先设置环境变量 OPENWEBUI_JWT_SECRET，再运行 token exchange 联调脚本")

    return jwt.encode(
        claims,
        OPENWEBUI_JWT_SECRET,
        algorithm=OPENWEBUI_JWT_ALGORITHM,
    )


print("🚀 边缘端正在使用 OpenWebUI token 进行 exchange，并发送推理触发请求...")
try:
    openwebui_token = build_mock_openwebui_token(OPENWEBUI_MOCK_USER_ID)

    exchange_response = requests.post(
        EXCHANGE_URL,
        json={"openwebui_token": openwebui_token},
        timeout=10,
    )
    exchange_response.raise_for_status()
    access_token = exchange_response.json()["access_token"]

    headers = {"Authorization": f"Bearer {access_token}"}

    response = requests.post(TRIGGER_URL, json=payload, headers=headers, timeout=10)
    response.raise_for_status()

    accepted = response.json()
    task_id = accepted["task_id"]
    print(f"📡 云端受理成功，task_id = {task_id}")
    print(json.dumps(accepted, indent=2, ensure_ascii=False))

    while True:
        task_response = requests.get(TASK_URL_TEMPLATE.format(task_id=task_id), headers=headers, timeout=10)
        task_response.raise_for_status()
        task_data = task_response.json()
        print(
            f"⏱️ 任务进度 | status={task_data['status']} | "
            f"phase={task_data['phase']} | "
            f"phase_progress={task_data['phase_progress']} | "
            f"overall_progress={task_data['overall_progress']} | "
            f"message={task_data['message']}"
        )

        if task_data["status"] in {"completed", "failed"}:
            print("📦 最终任务结果:")
            print(json.dumps(task_data, indent=2, ensure_ascii=False))
            break

        time.sleep(1)
except Exception as e:
    print(f"❌ 请求失败: {e}")
