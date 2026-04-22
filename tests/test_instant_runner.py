"""
tests/test_instant_runner.py — InstantRunner 단위 테스트

TDDPipeline, WorkspaceManager, GitWorkflow는 모두 mock.
TaskConverter, PipelineConfirmManager, RetryPrompt는 mock 인스턴스 직접 주입.
_build_pipeline() 은 lambda 오버라이드로 mock pipeline을 주입한다.
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if not os.environ.get("ANTHROPIC_API_KEY"):
    os.environ["ANTHROPIC_API_KEY"] = "test-key"

from cli.instant_runner import InstantRunner, InstantRunResult, RunMode
from cli.pipeline_confirm import ConfirmType, PipelineConfirmManager
from cli.retry_prompt import RetryDecision, RetryPrompt
from cli.task_converter import ConversionResult, TaskConverter
from llm import LLMConfig
from orchestrator.pipeline import PipelineMetrics, PipelineResult, ReviewResult
from orchestrator.task import Task


# ── 공통 픽스처 ────────────────────────────────────────────────────────────────


def _make_task(target_files: list[str] | None = None) -> Task:
    return Task(
        id="T001",
        title="테스트 태스크",
        description="태스크 설명",
        acceptance_criteria=["요구사항 1"],
        target_files=target_files or ["src/foo.py"],
    )


def _make_conversion(task: Task | None = None, aborted: bool = False) -> ConversionResult:
    return ConversionResult(
        task=task or (_make_task() if not aborted else None),
        aborted=aborted,
    )


def _approved_review() -> ReviewResult:
    return ReviewResult(
        verdict="APPROVED",
        summary="승인",
        details="",
        raw="VERDICT: APPROVED\nSUMMARY: 승인\nDETAILS:\n",
    )


def _changes_requested_review() -> ReviewResult:
    return ReviewResult(
        verdict="CHANGES_REQUESTED",
        summary="변경 요청",
        details="수정이 필요합니다.",
        raw="VERDICT: CHANGES_REQUESTED\nSUMMARY: 변경 요청\nDETAILS:\n수정이 필요합니다.",
    )


def _make_success_result(task: Task, test_files: list[str] | None = None) -> PipelineResult:
    return PipelineResult(
        task=task,
        succeeded=True,
        review=_approved_review(),
        test_files=test_files or [],
        impl_files=["src/foo.py"],
        metrics=PipelineMetrics(impl_retries=0),
    )


def _make_failure_result(task: Task, reason: str = "테스트 실패") -> PipelineResult:
    return PipelineResult(
        task=task,
        succeeded=False,
        failure_reason=reason,
        metrics=PipelineMetrics(impl_retries=2),
    )


def _make_runner(
    *,
    converter: TaskConverter | None = None,
    confirm: PipelineConfirmManager | None = None,
    retry: RetryPrompt | None = None,
    mode: RunMode = RunMode.FULL_TDD,
) -> InstantRunner:
    cfg = LLMConfig(model="claude-haiku-4-5-20251001", max_tokens=100)
    return InstantRunner(
        repo_path="/tmp/repo",
        converter=converter or MagicMock(spec=TaskConverter),
        confirm=confirm or MagicMock(spec=PipelineConfirmManager),
        retry=retry or MagicMock(spec=RetryPrompt),
        llm_config_fast=cfg,
        llm_config_capable=cfg,
        mode=mode,
    )


def _inject_pipeline(runner: InstantRunner, mock_pipeline) -> None:
    """_build_pipeline을 mock pipeline 반환하도록 오버라이드한다."""
    runner._build_pipeline = lambda: mock_pipeline


def _stub_workspace_cls(src_files=None, test_files=None, src_snapshots=None):
    """WorkspaceManager 클래스 mock과 인스턴스를 반환한다."""
    ws_instance = MagicMock()
    ws_instance.list_src_files.return_value = src_files or ["src/foo.py"]
    ws_instance.list_test_files.return_value = test_files or []
    if src_snapshots is None:
        default_snapshot = {
            rel_path: f"digest:{rel_path}"
            for rel_path in (src_files or ["src/foo.py"])
        }
        ws_instance.snapshot_src_files.return_value = default_snapshot
    else:
        ws_instance.snapshot_src_files.side_effect = src_snapshots

    ws_cls = MagicMock()
    ws_cls.return_value.__enter__ = MagicMock(return_value=ws_instance)
    ws_cls.return_value.__exit__ = MagicMock(return_value=False)
    return ws_cls, ws_instance


def _run(coro):
    return asyncio.run(coro)


# ── 테스트 1: FULL_TDD 성공 ────────────────────────────────────────────────────


def test_full_tdd_success():
    task = _make_task()
    converter = MagicMock(spec=TaskConverter)
    converter.convert = AsyncMock(return_value=_make_conversion(task=task))

    confirm = MagicMock(spec=PipelineConfirmManager)
    confirm.confirm.return_value = True

    success_result = _make_success_result(task)
    mock_pipeline = MagicMock()
    mock_pipeline.run.return_value = success_result

    ws_cls, _ = _stub_workspace_cls()
    mock_git = MagicMock()
    mock_git.run.return_value = ""

    runner = _make_runner(converter=converter, confirm=confirm)
    _inject_pipeline(runner, mock_pipeline)

    with patch("cli.instant_runner.WorkspaceManager", ws_cls), \
         patch("cli.instant_runner.GitWorkflow", return_value=mock_git), \
         patch("cli.instant_runner.print_pipeline_result"), \
         patch("cli.instant_runner.print_task_summary"):
        result = _run(runner.run("foo 기능 구현"))

    assert result.success is True
    assert result.user_aborted is False
    assert result.retry_count == 0


# ── 테스트 2: NO_TDD 모드 → skip_test_writer=True 확인 ────────────────────────


@patch("cli.instant_runner.create_client")
@patch("cli.instant_runner.GitWorkflow")
@patch("cli.instant_runner.WorkspaceManager")
@patch("cli.instant_runner.TDDPipeline")
def test_no_tdd_skips_test_writer(mock_pipeline_cls, mock_ws_cls, mock_git_cls, mock_create_client):
    task = _make_task()
    converter = MagicMock(spec=TaskConverter)
    converter.convert = AsyncMock(return_value=_make_conversion(task=task))

    confirm = MagicMock(spec=PipelineConfirmManager)
    confirm.confirm.return_value = True

    success_result = _make_success_result(task)
    mock_pipeline_cls.return_value.run.return_value = success_result

    ws_instance = MagicMock()
    ws_instance.list_src_files.return_value = ["src/foo.py"]
    ws_instance.list_test_files.return_value = []
    mock_ws_cls.return_value.__enter__ = MagicMock(return_value=ws_instance)
    mock_ws_cls.return_value.__exit__ = MagicMock(return_value=False)
    mock_git_cls.return_value.run.return_value = ""

    runner = _make_runner(converter=converter, confirm=confirm, mode=RunMode.NO_TDD)

    with patch("cli.instant_runner.print_pipeline_result"), \
         patch("cli.instant_runner.print_task_summary"):
        _run(runner.run("foo 구현"))

    _, kwargs = mock_pipeline_cls.call_args
    assert kwargs.get("skip_test_writer") is True


# ── 테스트 3: 미니 회의 중단 → user_aborted=True ─────────────────────────────


def test_mini_meeting_abort():
    converter = MagicMock(spec=TaskConverter)
    converter.convert = AsyncMock(return_value=_make_conversion(aborted=True))

    mock_pipeline = MagicMock()
    runner = _make_runner(converter=converter)
    _inject_pipeline(runner, mock_pipeline)

    result = _run(runner.run(""))

    assert result.user_aborted is True
    assert result.task is None
    mock_pipeline.run.assert_not_called()


# ── 테스트 4: 자동 재시도 소진 → RetryPrompt 호출 → retry → 성공 ──────────────


def test_auto_retry_then_user_retry():
    task = _make_task()
    converter = MagicMock(spec=TaskConverter)
    converter.convert = AsyncMock(return_value=_make_conversion(task=task))

    confirm = MagicMock(spec=PipelineConfirmManager)
    confirm.confirm.return_value = True

    failure_result = _make_failure_result(task)
    success_result = _make_success_result(task)
    mock_pipeline = MagicMock()
    mock_pipeline.run.side_effect = [failure_result, success_result]

    ws_cls, _ = _stub_workspace_cls()
    mock_git = MagicMock()
    mock_git.run.return_value = ""

    retry = MagicMock(spec=RetryPrompt)
    retry.ask_on_test_failure.return_value = RetryDecision(action="retry")

    runner = _make_runner(converter=converter, confirm=confirm, retry=retry)
    _inject_pipeline(runner, mock_pipeline)

    with patch("cli.instant_runner.WorkspaceManager", ws_cls), \
         patch("cli.instant_runner.GitWorkflow", return_value=mock_git), \
         patch("cli.instant_runner.print_pipeline_result"), \
         patch("cli.instant_runner.print_task_summary"):
        result = _run(runner.run("foo"))

    assert result.success is True
    assert result.retry_count == 1
    retry.ask_on_test_failure.assert_called_once()


# ── 테스트 5: retry_with_hint → task.description에 힌트 주입 ─────────────────


def test_user_hint_injected():
    task = _make_task()
    converter = MagicMock(spec=TaskConverter)
    converter.convert = AsyncMock(return_value=_make_conversion(task=task))

    confirm = MagicMock(spec=PipelineConfirmManager)
    confirm.confirm.return_value = True

    failure_result = _make_failure_result(task)
    success_result = _make_success_result(task)
    mock_pipeline = MagicMock()
    mock_pipeline.run.side_effect = [failure_result, success_result]

    ws_cls, _ = _stub_workspace_cls()
    mock_git = MagicMock()
    mock_git.run.return_value = ""

    retry = MagicMock(spec=RetryPrompt)
    retry.ask_on_test_failure.return_value = RetryDecision(
        action="retry_with_hint", hint="힌트텍스트"
    )

    runner = _make_runner(converter=converter, confirm=confirm, retry=retry)
    _inject_pipeline(runner, mock_pipeline)

    with patch("cli.instant_runner.WorkspaceManager", ws_cls), \
         patch("cli.instant_runner.GitWorkflow", return_value=mock_git), \
         patch("cli.instant_runner.print_pipeline_result"), \
         patch("cli.instant_runner.print_task_summary"):
        _run(runner.run("foo"))

    assert "### 사용자 힌트" in task.description
    assert "힌트텍스트" in task.description


# ── 테스트 6: quit → user_aborted=True ───────────────────────────────────────


def test_user_quit_aborts():
    task = _make_task()
    converter = MagicMock(spec=TaskConverter)
    converter.convert = AsyncMock(return_value=_make_conversion(task=task))

    confirm = MagicMock(spec=PipelineConfirmManager)
    confirm.confirm.return_value = True

    failure_result = _make_failure_result(task)
    mock_pipeline = MagicMock()
    mock_pipeline.run.return_value = failure_result

    ws_cls, _ = _stub_workspace_cls()

    retry = MagicMock(spec=RetryPrompt)
    retry.ask_on_test_failure.return_value = RetryDecision(action="quit")

    runner = _make_runner(converter=converter, confirm=confirm, retry=retry)
    _inject_pipeline(runner, mock_pipeline)

    with patch("cli.instant_runner.WorkspaceManager", ws_cls), \
         patch("cli.instant_runner.print_task_summary"):
        result = _run(runner.run("foo"))

    assert result.user_aborted is True
    assert result.success is False


# ── 테스트 7: 범위 밖 파일 수정 → confirm(OUT_OF_SCOPE_FILE) → False → 커밋 안 함


def test_out_of_scope_file_blocks():
    task = _make_task(target_files=["src/foo.py"])
    converter = MagicMock(spec=TaskConverter)
    converter.convert = AsyncMock(return_value=_make_conversion(task=task))

    # TASK_REVIEW → True, OUT_OF_SCOPE_FILE → False
    confirm = MagicMock(spec=PipelineConfirmManager)
    confirm.confirm.side_effect = lambda ct, msg, detail=None: ct != ConfirmType.OUT_OF_SCOPE_FILE

    success_result = _make_success_result(task)
    mock_pipeline = MagicMock()
    mock_pipeline.run.return_value = success_result

    # 파이프라인 실행 전에는 foo.py만 있고, 실행 후 bar.py가 새로 생성된다.
    ws_cls, _ = _stub_workspace_cls(
        src_snapshots=[
            {"src/foo.py": "digest:foo:v1"},
            {"src/foo.py": "digest:foo:v1", "src/bar.py": "digest:bar:v1"},
        ],
    )
    mock_git = MagicMock()

    runner = _make_runner(converter=converter, confirm=confirm)
    _inject_pipeline(runner, mock_pipeline)

    with patch("cli.instant_runner.WorkspaceManager", ws_cls), \
         patch("cli.instant_runner.GitWorkflow", return_value=mock_git), \
         patch("cli.instant_runner.print_pipeline_result"), \
         patch("cli.instant_runner.print_task_summary"):
        result = _run(runner.run("foo"))

    confirm_calls = [c.args[0] for c in confirm.confirm.call_args_list]
    assert ConfirmType.OUT_OF_SCOPE_FILE in confirm_calls
    mock_git.run.assert_not_called()


# ── 테스트 8: target_files 5개 → TASK_TOO_LARGE 확인 트리거 ──────────────────


def test_task_too_large_warning():
    task = _make_task(target_files=[f"src/f{i}.py" for i in range(5)])
    converter = MagicMock(spec=TaskConverter)
    converter.convert = AsyncMock(return_value=_make_conversion(task=task))

    # TASK_REVIEW → True, TASK_TOO_LARGE → False (취소)
    confirm = MagicMock(spec=PipelineConfirmManager)
    confirm.confirm.side_effect = lambda ct, msg, detail=None: ct != ConfirmType.TASK_TOO_LARGE

    mock_pipeline = MagicMock()
    runner = _make_runner(converter=converter, confirm=confirm)
    _inject_pipeline(runner, mock_pipeline)

    with patch("cli.instant_runner.print_task_summary"):
        result = _run(runner.run("대형 작업"))

    confirm_calls = [c.args[0] for c in confirm.confirm.call_args_list]
    assert ConfirmType.TASK_TOO_LARGE in confirm_calls
    mock_pipeline.run.assert_not_called()


# ── 테스트 9: CHANGES_REQUESTED + proceed → 커밋 진행 ────────────────────────


def test_changes_requested_ignore():
    task = _make_task()
    converter = MagicMock(spec=TaskConverter)
    converter.convert = AsyncMock(return_value=_make_conversion(task=task))

    confirm = MagicMock(spec=PipelineConfirmManager)
    confirm.confirm.return_value = True

    cr_result = PipelineResult(
        task=task,
        succeeded=True,
        review=_changes_requested_review(),
        test_files=[],
        impl_files=["src/foo.py"],
        metrics=PipelineMetrics(impl_retries=0),
    )
    mock_pipeline = MagicMock()
    mock_pipeline.run.return_value = cr_result

    ws_cls, _ = _stub_workspace_cls()
    mock_git = MagicMock()
    mock_git.run.return_value = ""

    runner = _make_runner(converter=converter, confirm=confirm)
    _inject_pipeline(runner, mock_pipeline)

    with patch("cli.instant_runner.WorkspaceManager", ws_cls), \
         patch("cli.instant_runner.GitWorkflow", return_value=mock_git), \
         patch("cli.instant_runner.print_pipeline_result"), \
         patch("cli.instant_runner.print_task_summary"):
        result = _run(runner.run("foo"))

    confirm_calls = [c.args[0] for c in confirm.confirm.call_args_list]
    assert ConfirmType.COMMIT_CHANGES_REQUESTED in confirm_calls
    mock_git.run.assert_called_once()


# ── 테스트 10: 기존 테스트 깨짐 감지 → confirm(EXISTING_TEST_BROKEN) → False ──


def test_existing_test_broken_blocks():
    task = _make_task()
    converter = MagicMock(spec=TaskConverter)
    converter.convert = AsyncMock(return_value=_make_conversion(task=task))

    # TASK_REVIEW → True, EXISTING_TEST_BROKEN → False
    confirm = MagicMock(spec=PipelineConfirmManager)
    confirm.confirm.side_effect = lambda ct, msg, detail=None: ct != ConfirmType.EXISTING_TEST_BROKEN

    # TestWriter가 생성한 파일은 test_new.py 하나지만
    # workspace에는 test_old.py(기존 파일)도 존재
    success_result = PipelineResult(
        task=task,
        succeeded=True,
        review=_approved_review(),
        test_files=["tests/test_new.py"],   # TestWriter 생성 파일
        impl_files=["src/foo.py"],
        metrics=PipelineMetrics(impl_retries=0),
    )
    mock_pipeline = MagicMock()
    mock_pipeline.run.return_value = success_result

    # workspace에 기존 테스트 파일도 포함
    ws_cls, _ = _stub_workspace_cls(test_files=["tests/test_old.py", "tests/test_new.py"])
    mock_git = MagicMock()

    runner = _make_runner(converter=converter, confirm=confirm)
    _inject_pipeline(runner, mock_pipeline)

    with patch("cli.instant_runner.WorkspaceManager", ws_cls), \
         patch("cli.instant_runner.GitWorkflow", return_value=mock_git), \
         patch("cli.instant_runner.print_pipeline_result"), \
         patch("cli.instant_runner.print_task_summary"):
        result = _run(runner.run("foo"))

    confirm_calls = [c.args[0] for c in confirm.confirm.call_args_list]
    assert ConfirmType.EXISTING_TEST_BROKEN in confirm_calls
    mock_git.run.assert_not_called()


# ── 테스트 11: 파이프라인 전부터 있던 비대상 파일이 unchanged면 허용 ───────────


def test_preexisting_out_of_scope_file_is_ignored_when_unchanged():
    """비대상 파일이 원래 있었더라도 변경이 없으면 out-of-scope로 보지 않는다."""
    task = _make_task(target_files=["src/foo.py"])
    converter = MagicMock(spec=TaskConverter)
    converter.convert = AsyncMock(return_value=_make_conversion(task=task))

    confirm = MagicMock(spec=PipelineConfirmManager)
    confirm.confirm.return_value = True

    success_result = _make_success_result(task)
    mock_pipeline = MagicMock()
    mock_pipeline.run.return_value = success_result

    ws_cls, _ = _stub_workspace_cls(
        src_snapshots=[
            {
                "src/foo.py": "digest:foo:v1",
                "src/bar.py": "digest:bar:v1",
            },
            {
                "src/foo.py": "digest:foo:v2",
                "src/bar.py": "digest:bar:v1",
            },
        ],
    )
    mock_git = MagicMock()
    mock_git.run.return_value = ""

    runner = _make_runner(converter=converter, confirm=confirm)
    _inject_pipeline(runner, mock_pipeline)

    with patch("cli.instant_runner.WorkspaceManager", ws_cls), \
         patch("cli.instant_runner.GitWorkflow", return_value=mock_git), \
         patch("cli.instant_runner.print_pipeline_result"), \
         patch("cli.instant_runner.print_task_summary"):
        result = _run(runner.run("foo"))

    confirm_calls = [c.args[0] for c in confirm.confirm.call_args_list]
    assert ConfirmType.OUT_OF_SCOPE_FILE not in confirm_calls
    mock_git.run.assert_called_once()
    assert result.success is True


def test_modified_preexisting_out_of_scope_file_blocks():
    """기존 비대상 파일의 내용이 바뀌면 out-of-scope 확인이 트리거된다."""
    task = _make_task(target_files=["src/foo.py"])
    converter = MagicMock(spec=TaskConverter)
    converter.convert = AsyncMock(return_value=_make_conversion(task=task))

    confirm = MagicMock(spec=PipelineConfirmManager)
    confirm.confirm.side_effect = lambda ct, msg, detail=None: ct != ConfirmType.OUT_OF_SCOPE_FILE

    success_result = _make_success_result(task)
    mock_pipeline = MagicMock()
    mock_pipeline.run.return_value = success_result

    ws_cls, _ = _stub_workspace_cls(
        src_snapshots=[
            {
                "src/foo.py": "digest:foo:v1",
                "src/bar.py": "digest:bar:v1",
            },
            {
                "src/foo.py": "digest:foo:v2",
                "src/bar.py": "digest:bar:v2",
            },
        ],
    )
    mock_git = MagicMock()

    runner = _make_runner(converter=converter, confirm=confirm)
    _inject_pipeline(runner, mock_pipeline)

    with patch("cli.instant_runner.WorkspaceManager", ws_cls), \
         patch("cli.instant_runner.GitWorkflow", return_value=mock_git), \
         patch("cli.instant_runner.print_pipeline_result"), \
         patch("cli.instant_runner.print_task_summary"):
        _run(runner.run("foo"))

    confirm_calls = [c.args[0] for c in confirm.confirm.call_args_list]
    assert ConfirmType.OUT_OF_SCOPE_FILE in confirm_calls
    mock_git.run.assert_not_called()


# ── 테스트 12: 파일 삭제 감지 (workspace diff 기준) ───────────────────────────────


def test_deleted_file_blocks():
    """초기에 있던 파일이 파이프라인 실행 후 사라지면 FILE_DELETION 확인이 트리거된다."""
    task = _make_task(target_files=["src/foo.py"])
    converter = MagicMock(spec=TaskConverter)
    converter.convert = AsyncMock(return_value=_make_conversion(task=task))

    confirm = MagicMock(spec=PipelineConfirmManager)
    confirm.confirm.side_effect = lambda ct, msg, detail=None: ct != ConfirmType.FILE_DELETION

    success_result = _make_success_result(task)
    mock_pipeline = MagicMock()
    mock_pipeline.run.return_value = success_result

    ws_cls, _ = _stub_workspace_cls(
        src_snapshots=[
            {"src/foo.py": "digest:foo:v1"},
            {},
        ],
    )
    mock_git = MagicMock()

    runner = _make_runner(converter=converter, confirm=confirm)
    _inject_pipeline(runner, mock_pipeline)

    with patch("cli.instant_runner.WorkspaceManager", ws_cls), \
         patch("cli.instant_runner.GitWorkflow", return_value=mock_git), \
         patch("cli.instant_runner.print_pipeline_result"), \
         patch("cli.instant_runner.print_task_summary"):
        _run(runner.run("foo"))

    confirm_calls = [c.args[0] for c in confirm.confirm.call_args_list]
    assert ConfirmType.FILE_DELETION in confirm_calls
    mock_git.run.assert_not_called()


def test_real_workspace_python_target_does_not_flag_auto_init(tmp_path):
    """real WorkspaceManager가 만든 src/__init__.py 는 unchanged면 out-of-scope가 아니다."""
    from orchestrator.workspace import WorkspaceManager

    repo_src = tmp_path / "src"
    repo_src.mkdir()
    (repo_src / "foo.py").write_text("def foo():\n    return 1\n", encoding="utf-8")

    task = _make_task(target_files=["src/foo.py"])
    runner = _make_runner()

    ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
    ws.create()
    try:
        initial_src_snapshot = ws.snapshot_src_files()
        current_src_snapshot = ws.snapshot_src_files()

        assert sorted(initial_src_snapshot) == ["src/__init__.py", "src/foo.py"]
        assert runner._get_out_of_scope_files(
            task,
            initial_src_snapshot,
            current_src_snapshot,
        ) == []
    finally:
        ws.cleanup()


# ── 테스트 13: [ABORTED] 결과 → user_aborted=True ────────────────────────────────


def test_aborted_result_returns_user_aborted():
    """pipeline.run()이 [ABORTED] 결과를 반환하면 user_aborted=True로 즉시 반환된다."""
    task = _make_task()
    converter = MagicMock(spec=TaskConverter)
    converter.convert = AsyncMock(return_value=_make_conversion(task=task))

    confirm = MagicMock(spec=PipelineConfirmManager)
    confirm.confirm.return_value = True

    aborted_result = PipelineResult(
        task=task,
        succeeded=False,
        failure_reason="[ABORTED] 사용자 즉시 중단 요청",
        metrics=PipelineMetrics(impl_retries=0),
    )
    mock_pipeline = MagicMock()
    mock_pipeline.run.return_value = aborted_result

    ws_cls, _ = _stub_workspace_cls()
    retry = MagicMock(spec=RetryPrompt)

    runner = _make_runner(converter=converter, confirm=confirm, retry=retry)
    _inject_pipeline(runner, mock_pipeline)

    with patch("cli.instant_runner.WorkspaceManager", ws_cls), \
         patch("cli.instant_runner.print_task_summary"):
        result = _run(runner.run("foo"))

    assert result.user_aborted is True
    assert result.success is False
    # RetryPrompt는 호출되지 않아야 한다
    retry.ask_on_test_failure.assert_not_called()
    retry.ask_on_pipeline_error.assert_not_called()


# ── 테스트 14: [REVIEWER_INFRA_ERROR] → ask_on_pipeline_error 라우팅 ─────────────


def test_infra_error_routes_to_pipeline_error_prompt():
    """[REVIEWER_INFRA_ERROR] 실패는 ask_on_test_failure가 아닌 ask_on_pipeline_error로 간다."""
    task = _make_task()
    converter = MagicMock(spec=TaskConverter)
    converter.convert = AsyncMock(return_value=_make_conversion(task=task))

    confirm = MagicMock(spec=PipelineConfirmManager)
    confirm.confirm.return_value = True

    infra_result = PipelineResult(
        task=task,
        succeeded=False,
        failure_reason="[REVIEWER_INFRA_ERROR] Reviewer 실행 실패: timeout",
        metrics=PipelineMetrics(impl_retries=0),
    )
    mock_pipeline = MagicMock()
    mock_pipeline.run.return_value = infra_result

    ws_cls, _ = _stub_workspace_cls()
    retry = MagicMock(spec=RetryPrompt)
    retry.ask_on_pipeline_error.return_value = RetryDecision(action="quit")

    runner = _make_runner(converter=converter, confirm=confirm, retry=retry)
    _inject_pipeline(runner, mock_pipeline)

    with patch("cli.instant_runner.WorkspaceManager", ws_cls), \
         patch("cli.instant_runner.print_task_summary"):
        result = _run(runner.run("foo"))

    retry.ask_on_pipeline_error.assert_called_once()
    retry.ask_on_test_failure.assert_not_called()
    assert result.user_aborted is True
