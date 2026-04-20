from datetime import datetime

from sqlalchemy.orm import Session

from app.models.models import ScheduleTask


def find_active_task_for_device_pair(
    db: Session,
    *,
    edge_device_id: str,
    cloud_device_id: str,
) -> ScheduleTask | None:
    return (
        db.query(ScheduleTask)
        .filter(
            ScheduleTask.edge_device_id == edge_device_id,
            ScheduleTask.cloud_device_id == cloud_device_id,
            ScheduleTask.queue_status == "running",
            ScheduleTask.status.in_(["accepted", "running"]),
            ScheduleTask.phase.in_(["strategy", "loading"]),
        )
        .order_by(ScheduleTask.created_at.asc(), ScheduleTask.task_id.asc())
        .first()
    )


def count_queued_tasks_for_device_pair(
    db: Session,
    *,
    edge_device_id: str,
    cloud_device_id: str,
) -> int:
    return (
        db.query(ScheduleTask)
        .filter(
            ScheduleTask.edge_device_id == edge_device_id,
            ScheduleTask.cloud_device_id == cloud_device_id,
            ScheduleTask.queue_status == "queued",
            ScheduleTask.status == "accepted",
            ScheduleTask.phase == "queued",
        )
        .count()
    )


def build_logical_queue_metrics(
    db: Session,
    *,
    edge_device_id: str,
    cloud_device_id: str,
) -> dict:
    queued_count = count_queued_tasks_for_device_pair(
        db,
        edge_device_id=edge_device_id,
        cloud_device_id=cloud_device_id,
    )
    return {
        "edge_queue_len": float(queued_count),
        "cloud_queue_len": float(queued_count),
    }


def recalculate_queue_positions_for_device_pair(
    db: Session,
    *,
    edge_device_id: str,
    cloud_device_id: str,
) -> None:
    queued_tasks = (
        db.query(ScheduleTask)
        .filter(
            ScheduleTask.edge_device_id == edge_device_id,
            ScheduleTask.cloud_device_id == cloud_device_id,
            ScheduleTask.queue_status == "queued",
            ScheduleTask.status == "accepted",
            ScheduleTask.phase == "queued",
        )
        .order_by(ScheduleTask.created_at.asc(), ScheduleTask.task_id.asc())
        .all()
    )
    for index, queued_task in enumerate(queued_tasks, start=1):
        queued_task.queue_position = index
        queued_task.updated_at = datetime.utcnow()
        db.add(queued_task)
    db.commit()
