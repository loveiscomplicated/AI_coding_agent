from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from orchestrator.report import save_report
from orchestrator.task import Task, TaskStatus
from reports.task_report import TaskReport

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

_spec = importlib.util.spec_from_file_location(
    "benchmark_thresholds", _REPO / "scripts" / "benchmark_thresholds.py",
)
bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bt)


def _report(task_id: str, *, status: str, total_tokens: int, cache_hit_rate: float, elapsed: float) -> TaskReport:
    return TaskReport(
        task_id=task_id,
        title=task_id,
        status=status,
        completed_at="2026-04-20T00:00:00+00:00",
        total_tokens=total_tokens,
        cache_hit_rate=cache_hit_rate,
        time_elapsed_seconds=elapsed,
    )


def test_load_log_metrics_aggregates_compaction_rows(tmp_path):
    path = tmp_path / "task-001_a.jsonl"
    rows = [
        {"task_id": "task-001", "iteration": 1, "role": "implementer"},
        {"task_id": "task-001", "iteration": 1, "event": "compaction", "net_tokens_saved": 1200},
        {"task_id": "task-001", "iteration": 2, "role": "implementer"},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    metrics = bt.load_log_metrics(tmp_path)
    assert metrics["task-001"] == {
        "llm_calls": 2,
        "compaction_events": 1,
        "net_tokens_saved": 1200,
    }


def test_compare_threshold_runs_computes_common_task_deltas(tmp_path):
    baseline_reports = tmp_path / "baseline-reports"
    candidate_reports = tmp_path / "candidate-reports"
    baseline_logs = tmp_path / "baseline-logs"
    candidate_logs = tmp_path / "candidate-logs"
    baseline_reports.mkdir()
    candidate_reports.mkdir()
    baseline_logs.mkdir()
    candidate_logs.mkdir()

    save_report(_report("task-001", status="COMPLETED", total_tokens=10_000, cache_hit_rate=0.10, elapsed=10), reports_dir=baseline_reports)
    save_report(_report("task-001", status="COMPLETED", total_tokens=8_000, cache_hit_rate=0.20, elapsed=9), reports_dir=candidate_reports)
    save_report(_report("task-002", status="COMPLETED", total_tokens=20_000, cache_hit_rate=0.30, elapsed=20), reports_dir=baseline_reports)
    save_report(_report("task-002", status="FAILED", total_tokens=25_000, cache_hit_rate=0.15, elapsed=30), reports_dir=candidate_reports)

    (baseline_logs / "baseline.jsonl").write_text(
        "\n".join([
            json.dumps({"task_id": "task-001", "iteration": 1}),
            json.dumps({"task_id": "task-002", "iteration": 1}),
        ]),
        encoding="utf-8",
    )
    (candidate_logs / "candidate.jsonl").write_text(
        "\n".join([
            json.dumps({"task_id": "task-001", "iteration": 1}),
            json.dumps({"task_id": "task-001", "iteration": 1, "event": "compaction", "net_tokens_saved": 2000}),
            json.dumps({"task_id": "task-002", "iteration": 1}),
            json.dumps({"task_id": "task-002", "iteration": 1, "event": "compaction", "net_tokens_saved": 500}),
        ]),
        encoding="utf-8",
    )

    cmp = bt.compare_threshold_runs(
        baseline_reports,
        candidate_reports,
        baseline_logs_dir=baseline_logs,
        candidate_logs_dir=candidate_logs,
    )
    assert cmp["common_tasks"] == ["task-001", "task-002"]
    assert cmp["summary"]["samples"] == 2
    assert cmp["summary"]["outcome_changes"] == {"improved": 0, "regressed": 1, "stable": 1}

    row1 = next(row for row in cmp["task_rows"] if row["task_id"] == "task-001")
    assert row1["delta_total_tokens"] == -2000
    assert row1["delta_compaction_events"] == 1
    assert row1["delta_net_tokens_saved"] == 2000

    row2 = next(row for row in cmp["task_rows"] if row["task_id"] == "task-002")
    assert row2["outcome"] == "regressed"


def _task(
    task_id: str,
    *,
    status: TaskStatus = TaskStatus.PENDING,
    task_type: str = "backend",
    target_files: list[str] | None = None,
    depends_on: list[str] | None = None,
) -> Task:
    return Task(
        id=task_id,
        title=task_id,
        description=f"desc {task_id}",
        acceptance_criteria=["works"],
        target_files=[f"src/{task_id}.py"] if target_files is None else target_files,
        test_framework="pytest",
        depends_on=depends_on or [],
        task_type=task_type,
        status=status,
    )


def test_select_runnable_tasks_filters_frontend_and_unsatisfied_dependencies(tmp_path):
    done_dep = _task("task-001", status=TaskStatus.DONE)
    file_dep = _task("task-002")
    blocked_dep = _task("task-003")
    frontend = _task("task-004", task_type="frontend")
    runnable_by_done = _task("task-005", depends_on=["task-001"])
    runnable_by_files = _task("task-006", depends_on=["task-002"])
    blocked = _task("task-007", depends_on=["task-003"])
    failed = _task("task-008", status=TaskStatus.FAILED)
    no_targets = _task("task-009", target_files=[])

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "task-002.py").write_text("# generated", encoding="utf-8")

    selected = bt.select_runnable_tasks(
        [
            done_dep,
            file_dep,
            blocked_dep,
            frontend,
            runnable_by_done,
            runnable_by_files,
            blocked,
            failed,
            no_targets,
        ],
        tmp_path,
        limit=10,
    )

    selected_ids = [task.id for task in selected]
    assert "task-004" not in selected_ids
    assert "task-007" not in selected_ids
    assert "task-009" not in selected_ids
    assert selected_ids == ["task-002", "task-003", "task-005", "task-006", "task-008"]


def test_make_subset_tasks_resets_target_and_marks_satisfied_dependencies_done(tmp_path):
    done_dep = _task("task-001", status=TaskStatus.DONE, target_files=["src/done.py"])
    file_dep = _task("task-002", target_files=["src/file.py"])
    target = _task("task-003", status=TaskStatus.FAILED, depends_on=["task-001", "task-002"])

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "file.py").write_text("# exists", encoding="utf-8")

    subset = bt.make_subset_tasks([done_dep, file_dep, target], tmp_path, "task-003")
    subset_by_id = {task.id: task for task in subset}

    assert [task.id for task in subset] == ["task-001", "task-002", "task-003"]
    assert subset_by_id["task-001"].status == TaskStatus.DONE
    assert subset_by_id["task-002"].status == TaskStatus.DONE
    assert subset_by_id["task-003"].status == TaskStatus.PENDING
    assert subset_by_id["task-003"].failure_reason == ""
    assert subset_by_id["task-003"].last_error == ""
