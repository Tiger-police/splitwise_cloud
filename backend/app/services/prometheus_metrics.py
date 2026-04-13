"""Prometheus 指标查询与缓存服务。"""
import asyncio
import json
import logging
import time
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger("PrometheusMetrics")


class PrometheusMetricsCache:
    """缓存按 IP 的 Prometheus 指标结果。"""

    def __init__(self, ttl_seconds: float = 15.0):
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, tuple[dict, float]] = {}

    def get(self, ip: str) -> Optional[dict]:
        entry = self._cache.get(ip)
        if not entry:
            return None
        metrics, ts = entry
        if time.monotonic() - ts > self.ttl_seconds:
            self._cache.pop(ip, None)
            return None
        return metrics

    def set(self, ip: str, metrics: dict) -> None:
        self._cache[ip] = (metrics, time.monotonic())

    def clear(self) -> None:
        self._cache.clear()


prometheus_metrics_cache = PrometheusMetricsCache(ttl_seconds=settings.PROMETHEUS_CACHE_SECONDS)


PROMETHEUS_QUERY_TEMPLATES = {
    "cpu": '100 - (avg(rate(node_cpu_seconds_total{{instance=~"{ip_regex}",mode="idle"}}[1m])) * 100)',
    "mem": '100 * (1 - node_memory_MemAvailable_bytes{{instance=~"{ip_regex}"}} / node_memory_MemTotal_bytes{{instance=~"{ip_regex}"}})',
    "gpu_util": 'avg(max_over_time(DCGM_FI_DEV_GPU_UTIL{{instance=~"{ip_regex}"}}[2m]))',
    "gpu_used": 'sum(DCGM_FI_DEV_FB_USED{{instance=~"{ip_regex}"}})',
    "gpu_free": 'sum(DCGM_FI_DEV_FB_FREE{{instance=~"{ip_regex}"}})',
}


def normalize_ip_regex(ip: str) -> str:
    return f"^{ip}:.*"


async def query_prom(client: httpx.AsyncClient, query: str, timeout: float | None = None) -> float:
    timeout = timeout if timeout is not None else settings.PROMETHEUS_QUERY_TIMEOUT
    try:
        resp = await client.get(
            f"{settings.PROMETHEUS_URL}/api/v1/query",
            params={"query": query},
            timeout=timeout,
        )
        data = resp.json()
        result = data.get("data", {}).get("result", [])
        if result:
            return float(result[0].get("value", [0, "0"])[1])
        logger.warning("Prometheus 查询结果为空，已回退为 0.0: %s", query)
    except Exception as exc:
        logger.warning("Prometheus 查询失败，已回退为 0.0: %s, error=%s", query, exc)
    return 0.0


async def fetch_metrics_from_prometheus(ip: str) -> dict:
    ip_regex = normalize_ip_regex(ip)
    async with httpx.AsyncClient() as client:
        queries = {
            name: template.format(ip_regex=ip_regex)
            for name, template in PROMETHEUS_QUERY_TEMPLATES.items()
        }
        values = await asyncio.gather(
            *(query_prom(client, q) for q in queries.values())
        )

    cpu, mem, g_util, g_used, g_free = values
    return {
        "cpu_percent": round(cpu, 2),
        "memory_percent": round(mem, 2),
        "gpu_util_percent": round(g_util, 2),
        "gpu_mem_used_mb": round(g_used, 2),
        "gpu_mem_total_mb": round(g_used + g_free, 2) if (g_used + g_free) > 0 else 1.0,
        "queue_len": 0.0,
    }


async def get_prometheus_metrics(ip: str) -> dict:
    cached = prometheus_metrics_cache.get(ip)
    if cached is not None:
        logger.debug("Prometheus 指标命中缓存: ip=%s", ip)
        return cached

    metrics = await fetch_metrics_from_prometheus(ip)
    prometheus_metrics_cache.set(ip, metrics)
    return metrics
