import asyncio
import logging
from datetime import datetime

import httpx

from app.core.config import settings
from app.db.database import SessionLocal
from app.models.models import ModelNode

logger = logging.getLogger("HealthWatchdog")


CONSECUTIVE_HEALTHCHECK_FAILURES: dict[int, int] = {}


async def probe_health_with_retry(client: httpx.AsyncClient, health_url: str) -> tuple[bool, str]:
    """
    对单个节点做轻量重试，避免一次瞬时抖动就把节点判离线。
    返回 (是否成功, 最后一次失败原因)。
    """
    max_attempts = max(1, settings.HEALTHCHECK_RETRY_COUNT + 1)
    last_error = "unknown"

    for attempt in range(1, max_attempts + 1):
        try:
            response = await client.get(health_url)
            if 200 <= response.status_code < 300:
                return True, ""
            last_error = f"HTTP {response.status_code}"
        except httpx.TimeoutException:
            last_error = "timeout"
        except httpx.RequestError as exc:
            last_error = f"request_error: {exc}"
        except Exception as exc:
            last_error = f"unexpected_error: {exc}"

        if attempt < max_attempts:
            await asyncio.sleep(settings.HEALTHCHECK_RETRY_DELAY_SECONDS)

    return False, last_error


async def health_check_watchdog():
    """后台巡检任务：每 10 秒探测一次在线节点的 /health 接口。"""
    while True:
        await asyncio.sleep(settings.HEALTHCHECK_INTERVAL_SECONDS)

        db = SessionLocal()
        try:
            online_nodes = db.query(ModelNode).filter(ModelNode.status == "online").all()
            if not online_nodes:
                continue

            async with httpx.AsyncClient(timeout=settings.HEALTHCHECK_TIMEOUT_SECONDS) as client:
                for node in online_nodes:
                    health_url = f"http://{node.ip_address}:{node.port}/health"
                    success, error_reason = await probe_health_with_retry(client, health_url)
                    if success:
                        CONSECUTIVE_HEALTHCHECK_FAILURES.pop(node.id, None)
                        node.last_heartbeat = datetime.utcnow()
                        continue

                    failure_count = CONSECUTIVE_HEALTHCHECK_FAILURES.get(node.id, 0) + 1
                    CONSECUTIVE_HEALTHCHECK_FAILURES[node.id] = failure_count

                    if failure_count >= settings.HEALTHCHECK_FAILURE_THRESHOLD:
                        node.status = "offline"
                        logger.warning(
                            "节点 %s:%s 连续健康检查失败 %s 次，已标记离线: %s",
                            node.ip_address,
                            node.port,
                            failure_count,
                            error_reason,
                        )
                    else:
                        logger.warning(
                            "节点 %s:%s 健康检查失败 %s/%s，暂不离线: %s",
                            node.ip_address,
                            node.port,
                            failure_count,
                            settings.HEALTHCHECK_FAILURE_THRESHOLD,
                            error_reason,
                        )

            db.commit()
        except Exception as exc:
            logger.error("看门狗运行异常: %s", exc)
        finally:
            db.close()
