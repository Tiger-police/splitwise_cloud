from app.models.models import ScheduleTask


def clamp_progress(value: int) -> int:
    return max(0, min(100, int(value)))


def calc_overall_progress(phase: str, phase_progress: int) -> int:
    phase_progress = clamp_progress(phase_progress)
    if phase == "strategy":
        return phase_progress // 2
    if phase == "loading":
        return 50 + phase_progress // 2
    if phase == "completed":
        return 100
    return phase_progress


def serialize_task(task: ScheduleTask) -> dict:
    return {
        "task_id": task.task_id,
        "status": task.status,
        "phase": task.phase,
        "phase_progress": task.phase_progress,
        "overall_progress": task.overall_progress,
        "message": task.message,
        "edge_progress": task.edge_progress,
        "cloud_progress": task.cloud_progress,
        "edge_status": task.edge_status,
        "cloud_status": task.cloud_status,
        "edge_message": task.edge_message,
        "cloud_message": task.cloud_message,
        "error_detail": task.error_detail,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }


def build_strategy_display_layer_partitions(layer_partitions: list[dict]) -> list[dict]:
    display_layers = []
    for layer in layer_partitions:
        head_assignments = list(layer.get("head_assignments", []))
        edge_head_count = sum(1 for assignment in head_assignments if assignment == 0)
        cloud_head_count = sum(1 for assignment in head_assignments if assignment == 1)
        display_layers.append(
            {
                "layer_id": layer.get("layer_id"),
                "head_assignments": head_assignments,
                "ffn_assignment": layer.get("ffn_assignment"),
                "edge_head_count": edge_head_count,
                "cloud_head_count": cloud_head_count,
            }
        )
    return display_layers


def build_strategy_display_summary(display_layers: list[dict]) -> dict:
    return {
        "edge_head_count_total": sum(layer.get("edge_head_count", 0) for layer in display_layers),
        "cloud_head_count_total": sum(layer.get("cloud_head_count", 0) for layer in display_layers),
    }
