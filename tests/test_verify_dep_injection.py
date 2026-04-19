"""
tests/test_verify_dep_injection.py

scripts/verify_dep_injection.py 스모크 테스트.

검증 포인트:
  - depends_on 이 비어있고 dep_files_injected == 0 이면 clean 으로 본다
  - depends_on 이 non-empty 인데 dep_files_injected == 0 이면 버그로 집계된다
  - depends_on 이 non-empty 이고 dep_files_injected > 0 이면 clean
  - main() 종료 코드: clean → 0, 버그 있음 → 1
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts import verify_dep_injection as vdi


def _write_tasks(root: Path, tasks: list[dict]) -> Path:
    data_dir = root / "agent-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    tasks_path = data_dir / "tasks.yaml"
    tasks_path.write_text(yaml.safe_dump({"tasks": tasks}), encoding="utf-8")
    return tasks_path


def _write_report(
    root: Path,
    task_id: str,
    *,
    dep_files_injected: int,
) -> Path:
    reports_dir = root / "agent-data" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{task_id}.yaml"
    doc = {
        "task_id": task_id,
        "title": f"샘플 {task_id}",
        "status": "COMPLETED",
        "metrics": {"dep_files_injected": dep_files_injected},
    }
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return path


def test_scan_flags_task_with_deps_but_zero_injected(tmp_path: Path):
    _write_tasks(
        tmp_path,
        [
            {"id": "task-001", "depends_on": []},
            {"id": "task-002", "depends_on": ["task-001"]},
        ],
    )
    _write_report(tmp_path, "task-001", dep_files_injected=0)  # clean
    _write_report(tmp_path, "task-002", dep_files_injected=0)  # buggy

    deps = vdi.load_task_dependencies(tmp_path / "agent-data" / "tasks.yaml")
    buggy, scanned = vdi.scan_reports(tmp_path / "agent-data" / "reports", deps)

    assert scanned == 2
    assert len(buggy) == 1
    assert buggy[0].task_id == "task-002"
    assert buggy[0].depends_on == ["task-001"]


def test_scan_ignores_task_with_non_zero_injected(tmp_path: Path):
    _write_tasks(
        tmp_path,
        [
            {"id": "task-001", "depends_on": []},
            {"id": "task-002", "depends_on": ["task-001"]},
        ],
    )
    _write_report(tmp_path, "task-002", dep_files_injected=3)

    deps = vdi.load_task_dependencies(tmp_path / "agent-data" / "tasks.yaml")
    buggy, _ = vdi.scan_reports(tmp_path / "agent-data" / "reports", deps)
    assert buggy == []


def test_scan_ignores_task_with_empty_depends_on(tmp_path: Path):
    _write_tasks(tmp_path, [{"id": "task-001", "depends_on": []}])
    _write_report(tmp_path, "task-001", dep_files_injected=0)

    deps = vdi.load_task_dependencies(tmp_path / "agent-data" / "tasks.yaml")
    buggy, scanned = vdi.scan_reports(tmp_path / "agent-data" / "reports", deps)
    assert scanned == 1
    assert buggy == []


def test_main_exits_zero_when_clean(tmp_path: Path, capsys: pytest.CaptureFixture):
    _write_tasks(tmp_path, [{"id": "task-001", "depends_on": []}])
    _write_report(tmp_path, "task-001", dep_files_injected=0)

    rc = vdi.main(["--base", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "의심 리포트 없음" in out


def test_main_exits_one_when_buggy(tmp_path: Path, capsys: pytest.CaptureFixture):
    _write_tasks(
        tmp_path,
        [{"id": "task-002", "depends_on": ["task-001"]}],
    )
    _write_report(tmp_path, "task-002", dep_files_injected=0)

    rc = vdi.main(["--base", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "task-002" in out
    assert "dep_files_injected=0" in out


def test_real_repo_reports_are_clean(capsys: pytest.CaptureFixture):
    """현재 레포의 실제 리포트에 의심 건이 없어야 한다 (T4 착수 전제)."""
    repo_root = Path(__file__).resolve().parent.parent
    rc = vdi.main(["--base", str(repo_root)])
    out = capsys.readouterr().out
    assert rc == 0, f"verify_dep_injection 실패:\n{out}"


def test_scan_detects_buggy_task_when_injected_alongside_clean_task(tmp_path: Path):
    """'0건 확인' 이 진짜 검증이 되도록, depends_on != [] 인 태스크가 실제로
    포함된 혼합 시나리오에서 검사 경로 전체가 살아있음을 보장한다.

    회귀 가드: 현재 실제 레포에는 depends_on 이 비어있는 task-001 하나뿐이어서
    `test_real_repo_reports_are_clean` 만으로는 '리포트가 0건인지' 와 '진짜로
    의심 건이 감지되지 않는지' 를 구분할 수 없다. 여기서 fixture 로 3개 태스크
    혼합 (clean no-deps / ok with deps / buggy with deps) 를 구성해 scan 경로
    자체가 감지·무시·패스 세 갈래 모두 제대로 도는지 고정한다.
    """
    _write_tasks(
        tmp_path,
        [
            {"id": "task-A", "depends_on": []},                  # no deps → skip
            {"id": "task-B", "depends_on": ["task-A"]},          # deps & injected → ok
            {"id": "task-C", "depends_on": ["task-A", "task-B"]},  # deps & 0 injected → buggy
        ],
    )
    _write_report(tmp_path, "task-A", dep_files_injected=0)
    _write_report(tmp_path, "task-B", dep_files_injected=2)
    _write_report(tmp_path, "task-C", dep_files_injected=0)

    deps = vdi.load_task_dependencies(tmp_path / "agent-data" / "tasks.yaml")
    buggy, scanned = vdi.scan_reports(tmp_path / "agent-data" / "reports", deps)

    assert scanned == 3
    assert [b.task_id for b in buggy] == ["task-C"]
    assert set(buggy[0].depends_on) == {"task-A", "task-B"}
