"""
메트릭 수집기 모듈

TaskReport를 YAML 파일로 저장/로드하고, 여러 Report를 집계하는
순수 Python 모듈이다. 외부 라이브러리 없이 표준 라이브러리만 사용한다.
"""
from dataclasses import dataclass, asdict
from pathlib import Path
from datetime import datetime
import yaml
from typing import Optional


@dataclass
class TaskReport:
    """
    작업 리포트 데이터클래스
    
    Attributes:
        task_id: 작업 ID (str)
        title: 작업 제목 (str)
        status: 작업 상태 (str)
        completed_at: 완료 시간 (str 또는 None)
        retry_count: 재시도 횟수 (int)
        test_count: 테스트 개수 (int)
        test_pass_first_try: 첫 시도에 통과했는지 여부 (bool)
        reviewer_verdict: 리뷰어 판정 (str)
        time_elapsed_seconds: 경과 시간 (float)
        failure_reasons: 실패 이유 목록 (list[str])
        reviewer_feedback: 리뷰어 피드백 (str 또는 None)
    """
    task_id: str
    title: str
    status: str
    completed_at: Optional[str]
    retry_count: int
    test_count: int
    test_pass_first_try: bool
    reviewer_verdict: str
    time_elapsed_seconds: float
    failure_reasons: list
    reviewer_feedback: Optional[str]


def save_report(report: TaskReport, reports_dir: str = "data/reports") -> Path:
    """
    TaskReport를 YAML 파일로 저장한다.
    
    Args:
        report: 저장할 TaskReport 인스턴스
        reports_dir: 저장할 디렉토리 경로 (기본값: "data/reports")
    
    Returns:
        저장된 파일의 Path 객체
    """
    # 디렉토리 생성
    dir_path = Path(reports_dir)
    dir_path.mkdir(parents=True, exist_ok=True)
    
    # 파일명 생성: task-{task_id}.yaml
    file_path = dir_path / f"task-{report.task_id}.yaml"
    
    # TaskReport를 딕셔너리로 변환
    report_dict = asdict(report)
    
    # YAML 파일로 저장
    with open(file_path, "w", encoding="utf-8") as f:
        yaml.dump(report_dict, f, allow_unicode=True, default_flow_style=False)
    
    return file_path


def load_reports(
    reports_dir: str = "data/reports", 
    since: Optional[datetime] = None
) -> list[TaskReport]:
    """
    디렉토리 내 모든 YAML 파일을 TaskReport 리스트로 로드한다.
    
    Args:
        reports_dir: 로드할 디렉토리 경로 (기본값: "data/reports")
        since: 이 시간 이후의 리포트만 반환 (completed_at 기준)
    
    Returns:
        TaskReport 인스턴스 리스트
    """
    dir_path = Path(reports_dir)
    
    # 디렉토리가 없으면 빈 리스트 반환
    if not dir_path.exists():
        return []
    
    reports = []
    
    # 디렉토리 내 모든 .yaml 파일 찾기
    for yaml_file in sorted(dir_path.glob("*.yaml")):
        with open(yaml_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        
        # TaskReport 인스턴스 생성
        report = TaskReport(**data)
        
        # since 필터링
        if since is not None:
            # completed_at이 None이면 제외
            if report.completed_at is None:
                continue
            
            # completed_at을 datetime으로 변환하여 비교
            completed_dt = datetime.fromisoformat(report.completed_at)
            if completed_dt < since:
                continue
        
        reports.append(report)
    
    return reports


def aggregate(reports: list[TaskReport]) -> dict:
    """
    여러 TaskReport를 집계한다.
    
    Args:
        reports: TaskReport 인스턴스 리스트
    
    Returns:
        집계 결과 딕셔너리:
        - total: 전체 리포트 수
        - completed: status=="COMPLETED"인 리포트 수
        - failed: status=="FAILED"인 리포트 수
        - success_rate: completed/total*100 (정수 반올림)
        - first_try_rate: test_pass_first_try==True인 비율 (%)
        - avg_elapsed_seconds: 평균 경과 시간
        - total_retries: 총 재시도 횟수
        - reviewer_approved: reviewer_verdict=="APPROVED"인 리포트 수
    """
    total = len(reports)
    
    # 기본값 설정
    if total == 0:
        return {
            "total": 0,
            "completed": 0,
            "failed": 0,
            "success_rate": 0,
            "first_try_rate": 0,
            "avg_elapsed_seconds": 0,
            "total_retries": 0,
            "reviewer_approved": 0,
        }
    
    # 집계 계산
    completed = sum(1 for r in reports if r.status == "COMPLETED")
    failed = sum(1 for r in reports if r.status == "FAILED")
    first_try_count = sum(1 for r in reports if r.test_pass_first_try)
    total_elapsed = sum(r.time_elapsed_seconds for r in reports)
    total_retries = sum(r.retry_count for r in reports)
    reviewer_approved = sum(1 for r in reports if r.reviewer_verdict == "APPROVED")
    
    # 비율 계산
    success_rate = round(completed / total * 100) if total > 0 else 0
    first_try_rate = round(first_try_count / total * 100) if total > 0 else 0
    avg_elapsed_seconds = total_elapsed / total if total > 0 else 0
    
    return {
        "total": total,
        "completed": completed,
        "failed": failed,
        "success_rate": success_rate,
        "first_try_rate": first_try_rate,
        "avg_elapsed_seconds": avg_elapsed_seconds,
        "total_retries": total_retries,
        "reviewer_approved": reviewer_approved,
    }
