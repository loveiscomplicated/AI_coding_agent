"""
project_paths.py

프로젝트 데이터 기본 경로를 해석한다.

정책:
- 기본 경로는 `agent-data/*`
- 다만 기존 운영 데이터가 `data/*`에만 있을 경우 자동으로 fallback
- 둘 다 없으면 `agent-data/*`를 기본으로 사용
"""

from __future__ import annotations

from pathlib import Path

_PRIMARY_DATA_DIR = "agent-data"
_LEGACY_DATA_DIR = "data"


def _normalize_rel(path: str | Path) -> str:
    """상대 경로 문자열을 비교 가능한 형태로 정규화한다."""
    return Path(path).as_posix().lstrip("./")


def resolve_data_dir(base: str | Path = ".") -> Path:
    """
    기본 데이터 디렉토리를 반환한다.

    우선순위:
      1) {base}/agent-data 가 있으면 사용
      2) {base}/data 가 있으면 사용
      3) 기본값으로 {base}/agent-data 사용
    """
    base_path = Path(base)
    primary = base_path / _PRIMARY_DATA_DIR
    legacy = base_path / _LEGACY_DATA_DIR
    if primary.exists():
        return primary
    if legacy.exists():
        return legacy
    return primary


def resolve_tasks_path(tasks_path: str | Path, base: str | Path = ".") -> Path:
    """
    tasks 경로를 해석한다.

    - 기본 경로(`agent-data/tasks.yaml` 또는 `data/tasks.yaml`)가 들어오면
      실제 존재 디렉토리에 맞춰 자동 해석한다.
    - 그 외 사용자 지정 경로는 그대로 사용한다.
    """
    raw = Path(tasks_path)
    if raw.is_absolute():
        return raw

    rel = _normalize_rel(tasks_path)
    if rel in {"agent-data/tasks.yaml", "data/tasks.yaml"}:
        return resolve_data_dir(base) / "tasks.yaml"
    return raw


def resolve_reports_dir(reports_dir: str | Path, base: str | Path = ".") -> Path:
    """
    reports 디렉토리 경로를 해석한다.

    - 기본 경로(`agent-data/reports` 또는 `data/reports`)가 들어오면
      실제 존재 디렉토리에 맞춰 자동 해석한다.
    - 그 외 사용자 지정 경로는 그대로 사용한다.
    """
    raw = Path(reports_dir)
    if raw.is_absolute():
        return raw

    rel = _normalize_rel(reports_dir)
    if rel in {"agent-data/reports", "data/reports"}:
        return resolve_data_dir(base) / "reports"
    return raw
