from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from orchestrator.run import _catchup_merge, run_pipeline
from orchestrator.task import Task, TaskStatus


def _task(task_id: str, *, task_type: str = "backend", depends_on: list[str] | None = None) -> Task:
    return Task(
        id=task_id,
        title=f"title-{task_id}",
        description="desc",
        acceptance_criteria=["ok"],
        target_files=[],
        task_type=task_type,
        depends_on=depends_on or [],
        status=TaskStatus.PENDING,
    )


class _PauseCtrlPausedThenStopped:
    def __init__(self):
        self._stopped_calls = 0

    @property
    def is_paused(self) -> bool:
        return True

    @property
    def is_stopped(self) -> bool:
        self._stopped_calls += 1
        # pause→resume 경로를 타게 한 뒤 다음 체크에서 중단
        return self._stopped_calls >= 2

    def wait_if_paused(self) -> bool:
        return False


class _PauseCtrlStopped:
    @property
    def is_paused(self) -> bool:
        return False

    @property
    def is_stopped(self) -> bool:
        return True

    def wait_if_paused(self) -> bool:
        return True


def _noop_client(*args, **kwargs):
    return MagicMock()


def test_run_pipeline_emits_frontend_skipped_and_returns_early(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    tasks = [_task("task-fe", task_type="frontend")]
    events: list[dict] = []

    with patch("orchestrator.run.load_tasks", return_value=tasks):
        result = run_pipeline(
            tasks_path=tmp_path / "tasks.yaml",
            repo_path=repo,
            on_progress=events.append,
        )

    assert result["success"] == 0
    assert result["fail"] == 0
    assert any(e.get("type") == "frontend_skipped" for e in events)


def test_run_pipeline_pause_resume_then_pipeline_aborted(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    tasks = [_task("task-001")]
    pause_ctrl = _PauseCtrlPausedThenStopped()
    events: list[dict] = []

    with (
        patch("orchestrator.run.load_tasks", return_value=tasks),
        patch("orchestrator.run.resolve_execution_groups", return_value=[tasks]),
        patch("orchestrator.run.create_client", side_effect=_noop_client),
        patch("orchestrator.run.create_hotline_llms", return_value=(MagicMock(), MagicMock())),
        patch("orchestrator.run.create_intervention_llms", return_value=(MagicMock(), MagicMock())),
        patch("orchestrator.run.DockerTestRunner", return_value=MagicMock()),
        patch("orchestrator.run._ensure_gitignore"),
    ):
        result = run_pipeline(
            tasks_path=tmp_path / "tasks.yaml",
            repo_path=repo,
            pause_controller=pause_ctrl,
            on_progress=events.append,
        )

    assert result["success"] == 0
    assert result["fail"] == 0
    event_types = [e.get("type") for e in events]
    assert "paused" in event_types
    assert "resumed" in event_types
    assert "pipeline_aborted" in event_types
    assert "pipeline_aborted_summary" in event_types


def test_run_pipeline_stop_aborts_before_running_group(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    tasks = [_task("task-001")]
    events: list[dict] = []

    with (
        patch("orchestrator.run.load_tasks", return_value=tasks),
        patch("orchestrator.run.resolve_execution_groups", return_value=[tasks]),
        patch("orchestrator.run.create_client", side_effect=_noop_client),
        patch("orchestrator.run.create_hotline_llms", return_value=(MagicMock(), MagicMock())),
        patch("orchestrator.run.create_intervention_llms", return_value=(MagicMock(), MagicMock())),
        patch("orchestrator.run.DockerTestRunner", return_value=MagicMock()),
        patch("orchestrator.run._ensure_gitignore"),
    ):
        run_pipeline(
            tasks_path=tmp_path / "tasks.yaml",
            repo_path=repo,
            pause_controller=_PauseCtrlStopped(),
            on_progress=events.append,
        )

    event_types = [e.get("type") for e in events]
    assert "pipeline_aborted" in event_types
    assert "pipeline_aborted_summary" in event_types


def test_catchup_merge_emits_and_merges_in_dependency_order(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    t1 = _task("task-001")
    t2 = _task("task-002", depends_on=["task-001"])
    t1.status = TaskStatus.DONE
    t2.status = TaskStatus.DONE
    events: list[dict] = []

    with (
        patch("orchestrator.run._remote_branch_exists", return_value=True),
        patch("orchestrator.run._is_branch_merged", return_value=False),
        patch("orchestrator.run._auto_merge_group") as mock_auto_merge,
    ):
        _catchup_merge(
            all_tasks=[t1, t2],
            pending_ids=set(),
            base_branch="dev",
            repo_path=repo,
            merge_agent=MagicMock(),
            all_task_ids={t1.id, t2.id},
            runner=MagicMock(),
            notifier=None,
            emit=events.append,
        )

    assert any(e.get("type") == "catchup_merge_start" for e in events)
    kwargs = mock_auto_merge.call_args.kwargs
    assert kwargs["branches"] == [t1.branch_name, t2.branch_name]
