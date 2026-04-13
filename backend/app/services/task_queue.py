"""
后台任务队列管理 - 替代内存 future 的方案
支持任务持久化、恢复和分布式处理
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional, Callable, Any
from enum import Enum

from sqlalchemy.orm import Session
from app.db.database import SessionLocal, session_scope
from app.models.models import ScheduleTask

logger = logging.getLogger("TaskQueue")


class TaskState(str, Enum):
    """任务状态机"""
    PENDING = "pending"              # 等待处理
    RUNNING = "running"              # 正在运行
    WAITING_CALLBACK = "waiting_callback"  # 等待外部回调
    COMPLETED = "completed"          # 已完成
    FAILED = "failed"                # 失败


class TaskQueue:
    """基于数据库的任务队列实现"""

    def __init__(self, db_session_factory: Callable[[], Session] = None):
        """
        初始化任务队列
        - db_session_factory: 数据库 session 工厂函数
        """
        self.db_session_factory = db_session_factory or SessionLocal
        self._task_callbacks: dict[str, asyncio.Event] = {}  # task_id -> Event，用于通知任务完成

    def submit_task(
        self,
        task_id: str,
        username: str,
        model_type: str,
        initial_message: str = "任务已受理",
    ) -> ScheduleTask:
        """
        提交新任务到队列
        """
        with session_scope() as db:
            task = ScheduleTask(
                task_id=task_id,
                username=username,
                model_type=model_type,
                status=TaskState.PENDING.value,
                phase="strategy",
                phase_progress=0,
                overall_progress=0,
                message=initial_message,
                edge_status="pending",
                cloud_status="pending",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.add(task)
            logger.info("📋 新任务已提交: task_id=%s, user=%s, model=%s", task_id, username, model_type)
            return task

    def get_task_status(self, task_id: str, username: str) -> Optional[ScheduleTask]:
        """
        获取任务状态（仅返回属于该用户的任务）
        """
        with session_scope() as db:
            task = (
                db.query(ScheduleTask)
                .filter(ScheduleTask.task_id == task_id, ScheduleTask.username == username)
                .first()
            )
            return task if task else None

    def update_task_status(
        self,
        task_id: str,
        status: Optional[str] = None,
        phase: Optional[str] = None,
        phase_progress: Optional[int] = None,
        message: Optional[str] = None,
        **kwargs,
    ) -> ScheduleTask:
        """
        更新任务状态
        """
        with session_scope() as db:
            task = db.query(ScheduleTask).filter(ScheduleTask.task_id == task_id).first()
            if not task:
                raise ValueError(f"Task {task_id} not found")

            if status:
                task.status = status
            if phase:
                task.phase = phase
            if phase_progress is not None:
                task.phase_progress = max(0, min(100, phase_progress))
            if message:
                task.message = message

            # 处理其他字段更新
            for key, value in kwargs.items():
                if hasattr(task, key) and value is not None:
                    setattr(task, key, value)

            task.updated_at = datetime.utcnow()
            db.add(task)

            logger.info(
                "🔄 任务已更新: task_id=%s, status=%s, phase=%s, progress=%d%%",
                task_id,
                status or task.status,
                phase or task.phase,
                task.phase_progress,
            )
            return task

    def mark_task_waiting_callback(self, task_id: str, callback_type: str = "strategy") -> None:
        """
        标记任务为"等待回调"状态，创建一个关键事件对象
        """
        self.update_task_status(
            task_id,
            status=TaskState.WAITING_CALLBACK.value,
            message=f"等待{callback_type}回调",
        )
        # 创建异步事件，用于通知任务有回调到达
        if task_id not in self._task_callbacks:
            self._task_callbacks[task_id] = asyncio.Event()

    def notify_task_callback(self, task_id: str, callback_data: dict = None) -> bool:
        """
        通知任务回调已到达，唤醒等待该任务的协程
        """
        import json

        if task_id not in self._task_callbacks:
            logger.warning("⚠️ 收到未知任务的回调: task_id=%s", task_id)
            return False

        # 如果有回调数据，存储到任务（转换为 JSON）
        if callback_data:
            self.update_task_status(
                task_id,
                strategy_payload=json.dumps(callback_data, ensure_ascii=False),
            )

        event = self._task_callbacks[task_id]
        event.set()
        logger.info("✅ 任务回调已通知: task_id=%s", task_id)
        return True

    async def wait_for_callback(self, task_id: str, timeout_seconds: float = 30.0) -> bool:
        """
        等待任务的回调到达（异步）
        返回 True 如果回调到达，False 如果超时
        """
        if task_id not in self._task_callbacks:
            self._task_callbacks[task_id] = asyncio.Event()

        event = self._task_callbacks[task_id]
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout_seconds)
            return True
        except asyncio.TimeoutError:
            logger.warning("⏱️ 任务回调超时: task_id=%s (timeout=%s秒)", task_id, timeout_seconds)
            self.update_task_status(
                task_id,
                status=TaskState.FAILED.value,
                message=f"等待回调超时（{timeout_seconds}秒）",
            )
            return False

    def mark_task_completed(self, task_id: str, message: str = "任务完成") -> ScheduleTask:
        """
        标记任务为完成状态
        """
        task = self.update_task_status(
            task_id,
            status=TaskState.COMPLETED.value,
            phase="completed",
            phase_progress=100,
            message=message,
        )
        # 清理临时回调事件
        self._task_callbacks.pop(task_id, None)
        logger.info("🎉 任务已完成: task_id=%s", task_id)
        return task

    def mark_task_failed(self, task_id: str, error_detail: str) -> ScheduleTask:
        """
        标记任务为失败状态
        """
        task = self.update_task_status(
            task_id,
            status=TaskState.FAILED.value,
            message=f"任务失败: {error_detail}",
            error_detail=error_detail,
        )
        # 清理临时回调事件
        self._task_callbacks.pop(task_id, None)
        logger.error("❌ 任务已失败: task_id=%s, error=%s", task_id, error_detail)
        return task

    def get_pending_tasks(self, limit: int = 10) -> list[ScheduleTask]:
        """
        获取所有待处理的任务（用于任务恢复）
        """
        with session_scope() as db:
            tasks = (
                db.query(ScheduleTask)
                .filter(ScheduleTask.status == TaskState.PENDING.value)
                .limit(limit)
                .all()
            )
            return tasks

    def get_unfinished_tasks(self) -> list[ScheduleTask]:
        """
        获取所有未完成的任务（用于应用重启后恢复）
        """
        with session_scope() as db:
            tasks = (
                db.query(ScheduleTask)
                .filter(
                    ScheduleTask.status.notin_([TaskState.COMPLETED.value, TaskState.FAILED.value])
                )
                .all()
            )
            return tasks

    def get_callback_data(self, task_id: str) -> Optional[dict]:
        """
        获取任务的回调数据（从 strategy_payload 字段）
        """
        import json

        with session_scope() as db:
            task = db.query(ScheduleTask).filter(ScheduleTask.task_id == task_id).first()
            if not task or not task.strategy_payload:
                return None

            try:
                return json.loads(task.strategy_payload)
            except (json.JSONDecodeError, TypeError):
                None
                return None


# 全局任务队列实例
task_queue = TaskQueue()
