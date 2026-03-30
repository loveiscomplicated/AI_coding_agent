"""TaskReport 데이터 모델 정의"""
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class TaskStatus(Enum):
    """태스크 상태"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TaskReport:
    """태스크 보고서"""
    task_id: str
    status: TaskStatus
    completed_at: datetime | None = None
    elapsed_seconds: float = 0.0
    retries: int = 0
    first_try: bool = True
