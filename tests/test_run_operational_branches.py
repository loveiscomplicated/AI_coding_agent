from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from orchestrator.run import PauseController, _catchup_merge, run_pipeline
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
    def attach_notifier(self, notifier, after_message_id=None):
        return None

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


def _failed_result(task: Task, reason: str = "boom"):
    return SimpleNamespace(
        task=task,
        succeeded=False,
        failure_reason=reason,
        test_result=None,
        review=None,
        test_files=[],
        impl_files=[],
        metrics=MagicMock(),
        models_used={},
    )


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


def test_pause_controller_handle_command_transitions():
    ctrl = PauseController()

    assert ctrl.handle_command("멈춰") == "paused"
    assert ctrl.is_paused is True

    assert ctrl.handle_command("계속") == "resumed"
    assert ctrl.is_paused is False

    assert ctrl.handle_command("중단") == "stopped"
    assert ctrl.is_stopped is True


def test_pause_controller_direct_poll_detects_stop_keyword():
    class _NotifierStub:
        channel_id = 123
        _headers = {"Authorization": "Bot test"}

        def __init__(self):
            self.sent: list[str] = []

        def send(self, text: str):
            self.sent.append(text)

    notifier = _NotifierStub()
    ctrl = PauseController()
    ctrl.attach_notifier(notifier, after_message_id="100")

    fake_resp = MagicMock()
    fake_resp.is_success = True
    fake_resp.json.return_value = [
        {"id": "101", "content": "중단", "author": {"bot": False}},
    ]

    with (
        patch("orchestrator.run.time.monotonic", return_value=100.0),
        patch("httpx.Client") as MockClient,
    ):
        MockClient.return_value.__enter__.return_value.get.return_value = fake_resp
        assert ctrl.is_stopped is True

    assert notifier.sent, "중단 감지 후 Discord 확인 메시지를 보내야 함"


def test_run_pipeline_restarts_dead_listener_thread(tmp_path):
    class _NotifierStub:
        channel_id = 123
        _headers = {"Authorization": "Bot test"}

        def __init__(self):
            self.baselines = ["100", "200"]
            self.listen_calls = 0

        def get_latest_message_id(self):
            return self.baselines.pop(0) if self.baselines else "200"

        def listen_for_commands(self, **kwargs):
            self.listen_calls += 1

        def send(self, _text: str):
            return None

    class _DeadThread:
        created = 0

        def __init__(self, *args, **kwargs):
            self._target = kwargs.get("target")
            self._kwargs = kwargs.get("kwargs", {})
            _DeadThread.created += 1

        def start(self):
            # 실제 스레드를 돌리지 않고 타깃은 호출하지 않는다.
            return None

        def is_alive(self):
            return False

    repo = tmp_path / "repo"
    repo.mkdir()
    tasks = [_task("task-001")]
    events: list[dict] = []
    notifier_stub = _NotifierStub()

    with (
        patch("orchestrator.run.load_tasks", return_value=tasks),
        patch("orchestrator.run.resolve_execution_groups", return_value=[tasks]),
        patch("orchestrator.run.create_client", side_effect=_noop_client),
        patch("orchestrator.run.create_hotline_llms", return_value=(MagicMock(), MagicMock())),
        patch("orchestrator.run.create_intervention_llms", return_value=(MagicMock(), MagicMock())),
        patch("orchestrator.run.DockerTestRunner", return_value=MagicMock()),
        patch("orchestrator.run._ensure_gitignore"),
        patch("orchestrator.run.DiscordNotifier.from_env", return_value=notifier_stub),
        patch("orchestrator.run.threading.Thread", new=_DeadThread),
    ):
        run_pipeline(
            tasks_path=tmp_path / "tasks.yaml",
            repo_path=repo,
            pause_controller=_PauseCtrlStopped(),
            on_progress=events.append,
            discord_channel_id=123,
        )

    # 최초 시작 1회 + _check_listener_alive 재시작 1회 이상
    assert _DeadThread.created >= 2


def test_run_pipeline_task023_style_loop_injects_skeleton_then_auto_splits(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    task = Task(
        id="task-023",
        title="title-task-023",
        description="desc",
        acceptance_criteria=["ok"],
        target_files=["src/task023.py"],
        task_type="backend",
        status=TaskStatus.PENDING,
    )
    tasks = [task]
    events: list[dict] = []
    seen_file_contents: list[str] = []

    class _FakePipeline:
        def __init__(self):
            self.calls = 0

        def run(self, task, ws, on_progress=None, pause_ctrl=None, all_tasks=None, repo_path=None, tier=None):
            self.calls += 1
            target = ws.src_dir / "task023.py"
            seen_file_contents.append(target.read_text(encoding="utf-8"))
            return _failed_result(task, reason=f"boom-{self.calls}")

    fake_pipeline = _FakePipeline()

    def fake_analyze(task, failure_reason, attempt, test_stdout="", previous_hints=None, role_models=None, tier=None):
        skeletons = {}
        if attempt == 3:
            skeletons = {
                "src/task023.py": (
                    '"""task-023 skeleton."""\n'
                    "def foo() -> None:\n"
                    '    raise NotImplementedError("TODO: task task-023")\n'
                )
            }
        return SimpleNamespace(
            should_retry=True,
            hint=f"hint-{attempt}",
            raw=f"raw-{attempt}",
            token_usage=(0, 0, 0, 0),
            call_log=[],
            skeleton_files=skeletons,
        )

    def fake_split(task, all_tasks, spec_content, llm, tasks_yaml_path, orch_report=""):
        sub_a = Task(
            id="task-023-a",
            title="a",
            description="d",
            acceptance_criteria=["ok-a"],
            target_files=["src/task023.py"],
            depends_on=list(task.depends_on),
        )
        sub_b = Task(
            id="task-023-b",
            title="b",
            description="d",
            acceptance_criteria=["ok-b"],
            target_files=["src/task023.py"],
            depends_on=["task-023-a"],
        )
        task.status = TaskStatus.SUPERSEDED
        task.failure_reason = "[자동 분해: task-023-a, task-023-b]"
        orig_index = all_tasks.index(task)
        all_tasks.insert(orig_index + 1, sub_a)
        all_tasks.insert(orig_index + 2, sub_b)
        return [sub_a, sub_b]

    with (
        patch("orchestrator.run.load_tasks", return_value=tasks),
        patch("orchestrator.run.resolve_execution_groups", return_value=[tasks]),
        patch("orchestrator.run.create_client", side_effect=_noop_client),
        patch("orchestrator.run.create_hotline_llms", return_value=(MagicMock(), MagicMock())),
        patch("orchestrator.run.create_intervention_llms", return_value=(MagicMock(), MagicMock())),
        patch("orchestrator.run.DockerTestRunner", return_value=MagicMock()),
        patch("orchestrator.run.TDDPipeline", return_value=fake_pipeline),
        patch("orchestrator.run._ensure_gitignore"),
        patch("orchestrator.run._accumulate_external_tokens"),
        patch("orchestrator.run.orch_classify_and_analyze", side_effect=fake_analyze),
        patch("orchestrator.run.create_redesign_llm", return_value=MagicMock()),
        patch("orchestrator.run._split_task", side_effect=fake_split),
        patch("orchestrator.run._load_spec_content", return_value="spec"),
        patch("orchestrator.run.save_tasks"),
    ):
        result = run_pipeline(
            tasks_path=tmp_path / "tasks.yaml",
            repo_path=repo,
            on_progress=events.append,
            intervention_auto_split=True,
            max_orchestrator_retries=3,
        )

    assert result["success"] == 0
    assert result["fail"] == 0
    assert len(seen_file_contents) == 4
    assert seen_file_contents[0] == ""
    assert seen_file_contents[1] == ""
    assert seen_file_contents[2] == ""
    assert '"""task-023 skeleton."""' in seen_file_contents[3]
    assert 'raise NotImplementedError("TODO: task task-023")' in seen_file_contents[3]
    event_types = [e.get("type") for e in events]
    assert "orchestrator_retry" in event_types
    assert "orchestrator_skeleton_generated" in event_types
    assert "task_split" in event_types
    assert task.status == TaskStatus.SUPERSEDED
    assert [t.id for t in tasks] == ["task-023", "task-023-a", "task-023-b"]
