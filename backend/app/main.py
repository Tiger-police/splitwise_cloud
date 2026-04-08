import logging
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.core.config import settings
from app.core.lifespan import lifespan
from app.web import dashboard

# 导入我们的 4 大战区
from app.api.v1 import auth, users, devices, monitor, schedule

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")


def create_app() -> FastAPI:
    app = FastAPI(title="云边协同调度枢纽", version="3.0.0", lifespan=lifespan)

    # 挂载跨域中间件
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 像拼图一样，把各战区路由挂载到总司令部
    app.include_router(auth.router, prefix="/api/v1", tags=["认证"])
    app.include_router(users.router, prefix="/api/v1/users", tags=["账号管理"])
    app.include_router(devices.router, prefix="/api/v1/system/devices", tags=["设备管理"])
    app.include_router(monitor.router, prefix="/api/v1", tags=["云边监控"])
    app.include_router(schedule.router, prefix="/api/v1/schedule", tags=["协同调度"])
    app.include_router(dashboard.router)
    app.mount("/static", StaticFiles(directory=settings.FRONTEND_DIR), name="static")

    return app


app = create_app()

if __name__ == "__main__":
    # 终端启动方式：先在~/splitwise_cloud目录下使用source venv/bin/activate去激活环境,在backend目录下运行 python -m app.main
    # 脚本启动方式：先在~/splitwise_cloud目录下使用source venv/bin/activate去激活环境,后在任意目录下运行bash ~/splitwise_cloud/scripts/run_server.sh

    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=True)
