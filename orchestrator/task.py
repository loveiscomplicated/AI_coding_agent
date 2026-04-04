"""
orchestrator/task.py — 태스크 데이터 모델

Task        : 파이프라인이 처리하는 단일 작업 단위
TaskStatus  : 파이프라인 상태 머신의 상태값
load_tasks  : YAML 파일 → List[Task]
save_tasks  : List[Task] → YAML 파일 (상태 저장용)

YAML 형식 예시:
    tasks:
      - id: task-001
        title: "사용자 인증 구현"
        description: "JWT 기반 로그인/로그아웃 API를 구현한다."
        acceptance_criteria:
          - "올바른 자격증명으로 로그인 시 JWT 토큰 반환"
          - "잘못된 자격증명으로 로그인 시 401 반환"
          - "토큰 없이 보호 엔드포인트 접근 시 401 반환"
        target_files:
          - "src/auth.py"
          - "src/models/user.py"
        test_framework: pytest
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


# 언어 → 기본 테스트 프레임워크 매핑
LANGUAGE_TEST_FRAMEWORK_MAP: dict[str, str] = {
    "python":     "pytest",
    "go":         "go",
    "kotlin":     "gradle",
    "javascript": "jest",
    "typescript": "jest",
    "ruby":       "rspec",
    "java":       "gradle",
    "rust":       "cargo",
    "c":          "c",
    "cpp":        "cpp",
}


class TaskStatus(Enum):
    PENDING        = "pending"
    WRITING_TESTS  = "writing_tests"
    IMPLEMENTING   = "implementing"
    RUNNING_TESTS  = "running_tests"
    REVIEWING      = "reviewing"
    COMMITTING     = "committing"
    DONE           = "done"
    FAILED         = "failed"


@dataclass
class Task:
    # ── 필수 필드 ─────────────────────────────────────────────────────────────
    id: str
    title: str
    description: str
    acceptance_criteria: list[str]
    target_files: list[str]
    test_framework: str = "pytest"

    # ── 선택 필드 ─────────────────────────────────────────────────────────────
    depends_on: list[str] = field(default_factory=list)  # 선행 태스크 ID 목록
    task_type: str = "backend"  # "backend" | "frontend" — frontend는 멀티 에이전트 파이프라인 제외
    language: str = "python"    # 태스크 구현 언어 (DockerTestRunner 실행 환경 결정)

    # ── 런타임 상태 (YAML 저장/복원 가능) ────────────────────────────────────
    status: TaskStatus = TaskStatus.PENDING
    retry_count: int = 0
    last_error: str = ""         # 직전 테스트 실패 stdout → Implementer 재시도 컨텍스트
    pr_url: str = ""             # 생성된 PR URL
    failure_reason: str = ""     # FAILED 상태일 때 원인

    # ── 편의 프로퍼티 ─────────────────────────────────────────────────────────

    @property
    def is_done(self) -> bool:
        return self.status in (TaskStatus.DONE, TaskStatus.FAILED)

    @property
    def branch_name(self) -> str:
        return f"agent/{self.id}"

    def acceptance_criteria_text(self) -> str:
        """수락 기준을 번호 목록 문자열로 반환 (프롬프트 주입용)."""
        return "\n".join(
            f"{i+1}. {criterion}"
            for i, criterion in enumerate(self.acceptance_criteria)
        )

    # ── 직렬화 ────────────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "acceptance_criteria": self.acceptance_criteria,
            "target_files": self.target_files,
            "test_framework": self.test_framework,
            "depends_on": self.depends_on,
            "task_type": self.task_type,
            "language": self.language,
            "status": self.status.value,
            "retry_count": self.retry_count,
            "last_error": self.last_error,
            "pr_url": self.pr_url,
            "failure_reason": self.failure_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Task":
        return cls(
            id=data["id"],
            title=data["title"],
            description=data["description"],
            acceptance_criteria=data.get("acceptance_criteria", []),
            target_files=data.get("target_files", []),
            test_framework=data.get("test_framework", "pytest"),
            depends_on=data.get("depends_on", []),
            task_type=data.get("task_type", "backend"),
            language=data.get("language", "python"),
            status=TaskStatus(data.get("status", TaskStatus.PENDING.value)),
            retry_count=data.get("retry_count", 0),
            last_error=data.get("last_error", ""),
            pr_url=data.get("pr_url", ""),
            failure_reason=data.get("failure_reason", ""),
        )

    def __repr__(self) -> str:
        return f"Task(id={self.id!r}, title={self.title!r}, status={self.status.value})"


# ── YAML I/O ──────────────────────────────────────────────────────────────────


def load_tasks(path: str | Path) -> list[Task]:
    """
    YAML 파일에서 Task 목록을 로드한다.

    Args:
        path: tasks.yaml 파일 경로

    Returns:
        List[Task] — 파일 내 tasks 키 아래의 항목들

    Raises:
        FileNotFoundError: 파일이 없을 때
        KeyError: YAML에 'tasks' 키가 없을 때
        ValueError: 필수 필드 누락 시
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"태스크 파일 없음: {path}")

    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict) or "tasks" not in data:
        raise KeyError(f"YAML 파일에 'tasks' 키가 없습니다: {path}")

    tasks = []
    for i, item in enumerate(data["tasks"]):
        _validate_task_dict(item, index=i)
        tasks.append(Task.from_dict(item))

    return tasks


def save_tasks(tasks: list[Task], path: str | Path) -> None:
    """
    Task 목록을 YAML 파일로 저장한다 (상태 체크포인트용).

    Args:
        tasks: 저장할 Task 목록
        path:  저장 경로 (없으면 생성)
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = {"tasks": [t.to_dict() for t in tasks]}
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)


def _validate_task_dict(data: dict[str, Any], index: int) -> None:
    """필수 필드 존재 여부를 확인한다."""
    required = ("id", "title", "description", "acceptance_criteria")
    missing = [key for key in required if key not in data]
    if missing:
        raise ValueError(
            f"tasks[{index}] 에 필수 필드 누락: {missing}\n"
            f"  (받은 키: {list(data.keys())})"
        )
    if not isinstance(data.get("acceptance_criteria"), list):
        raise ValueError(
            f"tasks[{index}].acceptance_criteria 는 리스트여야 합니다."
        )
