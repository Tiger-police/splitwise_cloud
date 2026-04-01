import logging
import uvicorn
import asyncio
import httpx
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pathlib import Path


from app.db.database import SessionLocal
from app.models.models import ModelNode
from app.core.config import settings
from app.db.database import engine, Base
from app.models.models import init_db_data

# 导入我们的 4 大战区
from app.api.v1 import auth, users, devices, monitor, schedule

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")


async def health_check_watchdog():
    """后台巡检任务：每10秒探测一次在线节点的 /health 接口"""
    while True:
        await asyncio.sleep(10)  # 巡检间隔：10秒

        db = SessionLocal()  # 开启独立的数据库会话
        try:
            # 1. 查出当前数据库里所有自称 "online" 的节点
            online_nodes = db.query(ModelNode).filter(ModelNode.status == "online").all()

            if not online_nodes:
                continue  # 如果没有在线节点，直接进入下一次循环

            # 2. 并发或顺序去 ping 它们的 /health 接口
            async with httpx.AsyncClient(timeout=3.0) as client:
                for node in online_nodes:
                    health_url = f"http://{node.ip_address}:{node.port}/health"
                    try:
                        response = await client.get(health_url)
                        # 只要状态码在 200-299 之间，我们就认为服务正常存活，不再强求 JSON 里的特定字段！
                        if 200 <= response.status_code < 300:
                            node.last_heartbeat = datetime.utcnow()
                        else:
                            node.status = "offline"
                            logging.warning(
                                f"🚨 节点 {node.ip_address}:{node.port} 状态异常 (HTTP {response.status_code})，已踢下线")
                    except Exception as e:
                        node.status = "offline"
                        logging.warning(f"🚨 节点 {node.ip_address}:{node.port} 失去联络，已踢下线: {e}")

            # 3. 提交数据库更改
            db.commit()
        except Exception as e:
            logging.error(f"看门狗运行异常: {e}")
        finally:
            db.close()  # 务必关闭会话释放连接池


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)

    init_db_data()

    watchdog_task = asyncio.create_task(health_check_watchdog())
    logging.info("🐕 云端看门狗 (Health Check Watchdog) 已启动...")

    yield

    watchdog_task.cancel()

app = FastAPI(title="云边协同调度枢纽", version="3.0.0", lifespan=lifespan)

# 挂载跨域中间件
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# 像拼图一样，把 4 个战区挂载到总司令部，并统一加上 /api/v1 前缀
app.include_router(auth.router, prefix="/api/v1", tags=["认证"])
app.include_router(users.router, prefix="/api/v1/users", tags=["账号管理"])
app.include_router(devices.router, prefix="/api/v1/system/devices", tags=["设备管理"])
app.include_router(monitor.router, prefix="/api/v1", tags=["云边监控"])
app.include_router(schedule.router, prefix="/api/v1/schedule", tags=["协同调度"])

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"


@app.get("/", summary="访问监控大屏", tags=["前端界面"])
async def serve_dashboard():
    """
    当用户在浏览器直接访问 http://10.144.144.2:8000/ 时，
    直接将 dashboard.html 网页文件返回给浏览器解析。
    """
    html_path = FRONTEND_DIR / "dashboard.html"

    if not html_path.exists():
        return {"error": "🚨 未找到前端页面，请检查 splitwise_cloud/frontend/dashboard.html 是否存在！"}

    return FileResponse(html_path)

if __name__ == "__main__":
    # 终端启动方式：先在~/splitwise_cloud目录下使用source venv/bin/activate去激活环境,在backend目录下运行 python -m app.main
    # 脚本启动方式：先在~/splitwise_cloud目录下使用source venv/bin/activate去激活环境,后在任意目录下运行bash ~/splitwise_cloud/scripts/run_server.sh

    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=True)