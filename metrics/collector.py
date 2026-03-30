"""
TaskReport 정의 및 메트릭 수집 관련 모듈
"""
from dataclasses import dataclass
from datetime import datetime


@dataclass
class TaskReport:
    """
    태스크 실행 보고서
    
    Attributes:
        task_id: 태스크 ID
        title: 태스크 제목
        status: 태스크 상태 (COMPLETED, FAILED 등)
        time_elapsed_seconds: 소요 시간 (초)
        retry_count: 재시도 횟수
        completed_at: 완료 시간
    """
    task_id: str
    title: str
    status: str
    time_elapsed_seconds: int
    retry_count: int
    completed_at: datetime
