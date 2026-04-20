import re

import httpx
from sqlalchemy.orm import Session

from app.models.models import ModelNode


def extract_ip(device_value: str) -> str | None:
    ip_match = re.search(r"(?:\d{1,3}\.){3}\d{1,3}", device_value)
    return ip_match.group(0) if ip_match else None


def find_runtime_node(db: Session, device_id: str, model_key: str, node_role: str) -> ModelNode | None:
    candidates = (
        db.query(ModelNode)
        .filter(
            ModelNode.device_id == device_id,
            ModelNode.node_role == node_role,
            ModelNode.service_type == "runtime",
            ModelNode.status == "online",
        )
        .order_by(ModelNode.last_heartbeat.desc())
        .all()
    )

    for node in candidates:
        if (node.model_key or "").lower() == model_key:
            return node

    for node in candidates:
        node_model_key = (node.model_key or "").lower()
        if node_model_key in {"multi", "*", "all"}:
            return node

    return candidates[0] if len(candidates) == 1 else None


async def dispatch_strategy_to_runtime(node: ModelNode, payload: dict) -> None:
    control_path = node.control_path or "/load_strategy"
    runtime_url = f"http://{node.ip_address}:{node.port}{control_path}"
    async with httpx.AsyncClient() as client:
        response = await client.post(runtime_url, json=payload, timeout=5.0)
        response.raise_for_status()
