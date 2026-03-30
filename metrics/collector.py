"""
metrics.collector 모듈

TaskReport 데이터 클래스 정의
"""
from dataclasses import dataclass
from datetime import datetime


@dataclass
class TaskReport:
    """
    작업 실행 리포트
    
    Attributes:
        task_id: 작업 ID
        title: 작업 제목
        status: 작업 상태 ("COMPLETED" 또는 "FAILED")
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
