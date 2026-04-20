import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db.database import Base, engine
from app.models.models import init_db_data
from app.services.watchdog import health_check_watchdog

logger = logging.getLogger("AppLifespan")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    init_db_data()

    app.state.watchdog_task = asyncio.create_task(health_check_watchdog())
    logger.info("云端看门狗 (Health Check Watchdog) 已启动")

    try:
        yield
    finally:
        watchdog_task = getattr(app.state, "watchdog_task", None)
        if watchdog_task is not None:
            watchdog_task.cancel()
