import asyncio
import logging
from datetime import datetime

import httpx

from app.db.database import SessionLocal
from app.models.models import ModelNode

logger = logging.getLogger("HealthWatchdog")


async def health_check_watchdog():
    """后台巡检任务：每 10 秒探测一次在线节点的 /health 接口。"""
    while True:
        await asyncio.sleep(10)

        db = SessionLocal()
        try:
            online_nodes = db.query(ModelNode).filter(ModelNode.status == "online").all()
            if not online_nodes:
                continue

            async with httpx.AsyncClient(timeout=3.0) as client:
                for node in online_nodes:
                    health_url = f"http://{node.ip_address}:{node.port}/health"
                    try:
                        response = await client.get(health_url)
                        if 200 <= response.status_code < 300:
                            node.last_heartbeat = datetime.utcnow()
                        else:
                            node.status = "offline"
                            logger.warning(
                                "节点 %s:%s 状态异常 (HTTP %s)，已标记离线",
                                node.ip_address,
                                node.port,
                                response.status_code,
                            )
                    except Exception as exc:
                        node.status = "offline"
                        logger.warning(
                            "节点 %s:%s 失去联络，已标记离线: %s",
                            node.ip_address,
                            node.port,
                            exc,
                        )

            db.commit()
        except Exception as exc:
            logger.error("看门狗运行异常: %s", exc)
        finally:
            db.close()
