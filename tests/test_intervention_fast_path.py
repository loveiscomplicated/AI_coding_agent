from __future__ import annotations

from orchestrator.intervention import (
    FailureType,
    classify_and_analyze,
    classify_failure,
)
from orchestrator.task import Task


def _task() -> Task:
    return Task(
        id="t1",
        title="title",
        description="desc",
        acceptance_criteria=["c1"],
        target_files=[],
    )


def test_classify_failure_reviewer_changes_requested_is_logic_error():
    ft = classify_failure("Reviewer changes_requested: fix style", "ModuleNotFoundError: x")
    assert ft == FailureType.LOGIC_ERROR


def test_classify_and_analyze_env_error_returns_fast_give_up():
    result = classify_and_analyze(_task(), "ImportError: no module named foo", attempt=1)
    assert result.should_retry is False
    assert "[env_error]" in result.hint
    assert result.raw == "[fast-path] env_error"


def test_classify_and_analyze_max_iter_first_attempt_retries():
    result = classify_and_analyze(_task(), "[MAX_ITER] loop exceeded", attempt=1)
    assert result.should_retry is True
    assert "write_file" in result.hint
    assert result.raw == "[fast-path] max_iter_retry"


def test_classify_and_analyze_max_iter_second_attempt_give_up():
    result = classify_and_analyze(_task(), "[MAX_ITER] loop exceeded", attempt=2)
    assert result.should_retry is False
    assert "MAX_ITER" in result.hint
    assert result.raw == "[fast-path] max_iter_exceeded"
