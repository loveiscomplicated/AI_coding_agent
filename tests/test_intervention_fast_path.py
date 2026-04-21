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


# ── TestWriter 가드 실패 분류 (write-like 계열 → MAX_ITER_EXCEEDED) ─────────
#
# 이 prefix 들은 모두 "에이전트가 쓰기를 제대로 수행하지 못했다" 로 의미가 같으므로
# 첫 재시도는 고정 힌트(write_file 을 바로 호출) 로 자동화하고, 2회차에서는
# deterministic 하게 GIVE_UP 한다.


def test_classify_failure_no_write_is_max_iter_category():
    ft = classify_failure("[NO_WRITE] TestWriter 가 write_file/edit_file 을 호출하지 않았습니다.")
    assert ft == FailureType.MAX_ITER_EXCEEDED


def test_classify_failure_test_missing_is_max_iter_category():
    ft = classify_failure("[TEST_MISSING] workspace/tests/ 에 테스트 파일이 없습니다.")
    assert ft == FailureType.MAX_ITER_EXCEEDED


def test_classify_failure_test_skeleton_only_is_max_iter_category():
    ft = classify_failure("[TEST_SKELETON_ONLY] test_auth.py 이 스켈레톤 그대로입니다.")
    assert ft == FailureType.MAX_ITER_EXCEEDED


def test_classify_failure_no_test_functions_is_max_iter_category():
    ft = classify_failure("[NO_TEST_FUNCTIONS] test_auth.py 에 test_* 함수가 없습니다.")
    assert ft == FailureType.MAX_ITER_EXCEEDED


def test_classify_failure_test_syntax_error_stays_logic_error():
    # 문법 오류는 "어디를 고치라" 힌트가 필요 — MAX_ITER 의 고정 힌트와 부적합.
    ft = classify_failure("[TEST_SYNTAX_ERROR] test_auth.py: invalid syntax")
    assert ft == FailureType.LOGIC_ERROR


def test_test_skeleton_only_second_attempt_gives_up_deterministically():
    """회귀 가드(#5): 같은 가드 실패가 2회 반복되면 LLM 개입 없이 GIVE_UP 한다."""
    result = classify_and_analyze(
        _task(), "[TEST_SKELETON_ONLY] 스켈레톤 그대로", attempt=2,
    )
    assert result.should_retry is False
    assert result.raw == "[fast-path] max_iter_exceeded"


def test_no_write_first_attempt_retries_with_write_hint():
    result = classify_and_analyze(
        _task(), "[NO_WRITE] 탐색만 함", attempt=1,
    )
    assert result.should_retry is True
    assert "write_file" in result.hint
    assert result.raw == "[fast-path] max_iter_retry"


# ── collect-only 게이트 실패 분류 ([NO_TESTS_COLLECTED] / [COLLECTION_ERROR]) ─


def test_classify_failure_no_tests_collected_is_own_category():
    ft = classify_failure("[NO_TESTS_COLLECTED] No tests were collected")
    assert ft == FailureType.NO_TESTS_COLLECTED


def test_classify_failure_collection_error_is_own_category():
    ft = classify_failure("[COLLECTION_ERROR] pytest collection failed (syntax)")
    assert ft == FailureType.COLLECTION_ERROR


def test_classify_failure_collection_error_with_importerror_goes_env_error():
    """
    [COLLECTION_ERROR] 라도 test_stdout 이 ImportError 면 ENV_ERROR 로 분류.
    ENV_ERROR 체크가 우선하여 즉시 GIVE_UP 흐름에 태움.
    """
    ft = classify_failure(
        "[COLLECTION_ERROR] collection failed",
        test_stdout="ImportError: No module named foo",
    )
    assert ft == FailureType.ENV_ERROR


def test_no_tests_collected_first_attempt_retries_with_testwriter_hint():
    result = classify_and_analyze(
        _task(),
        "[NO_TESTS_COLLECTED] No tests were collected",
        attempt=1,
    )
    assert result.should_retry is True
    assert "write_file" in result.hint
    assert "tests/" in result.hint
    assert result.raw == "[fast-path] no_tests_collected_retry"


def test_no_tests_collected_second_attempt_gives_up():
    result = classify_and_analyze(
        _task(),
        "[NO_TESTS_COLLECTED] No tests were collected",
        attempt=2,
    )
    assert result.should_retry is False
    assert result.raw == "[fast-path] no_tests_collected_give_up"


def test_collection_error_tag_routes_to_analyze_llm(monkeypatch):
    """
    [COLLECTION_ERROR] (ImportError/ModuleNotFound 없는 경우, 즉 SyntaxError 등)
    는 analyze() LLM 분석 경로로 간다. monkeypatch 로 analyze 를 가로채서
    확인.
    """
    from orchestrator import intervention as iv

    called = {"count": 0}

    def fake_analyze(task, reason, attempt, previous_hints=None, role_models=None, tier=None):
        called["count"] += 1
        return iv.AnalysisResult(
            should_retry=True, hint="fake", raw="fake",
        )

    monkeypatch.setattr(iv, "analyze", fake_analyze)
    result = classify_and_analyze(
        _task(),
        "[COLLECTION_ERROR] SyntaxError in tests/test_x.py line 3",
        attempt=1,
    )
    assert called["count"] == 1
    assert result.hint == "fake"
