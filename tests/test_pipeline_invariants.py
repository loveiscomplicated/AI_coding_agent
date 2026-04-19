"""
tests/test_pipeline_invariants.py

orchestrator.pipeline._assert_invariants 단위 테스트.

"COMPLETED" 상태와 양립 불가한 조합 (BLOCKED QG, test_count=0,
CHANGES_REQUESTED verdict) 에서 explicit RuntimeError 가 발생해야 한다.
Python ``-O`` 에서도 활성화되도록 assert 가 아닌 raise 를 사용한다.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from orchestrator.pipeline import _assert_invariants
from reports.task_report import TaskReport


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────


def _make_report(
    *,
    status: str,
    qg_verdict: str | None = "PASS",
    test_count: int = 3,
    reviewer_verdict: str = "APPROVED",
) -> TaskReport:
    return TaskReport(
        task_id="task-invariant",
        title="test",
        status=status,
        completed_at="2026-04-19T00:00:00+00:00",
        test_count=test_count,
        reviewer_verdict=reviewer_verdict,
        quality_gate_verdict=qg_verdict,  # type: ignore[arg-type]
    )


# ── COMPLETED + 위반 조합 → RuntimeError ──────────────────────────────────────


class TestInvariantRaises:
    def test_invariant_completed_with_blocked_qg_raises(self):
        report = _make_report(status="COMPLETED", qg_verdict="BLOCKED")
        with pytest.raises(RuntimeError, match="quality_gate_verdict is BLOCKED"):
            _assert_invariants(report)

    def test_invariant_completed_with_zero_tests_raises(self):
        report = _make_report(status="COMPLETED", test_count=0)
        with pytest.raises(RuntimeError, match="test_count is 0"):
            _assert_invariants(report)

    def test_invariant_completed_with_changes_requested_raises(self):
        report = _make_report(
            status="COMPLETED",
            reviewer_verdict="CHANGES_REQUESTED",
        )
        with pytest.raises(RuntimeError, match="reviewer_verdict is CHANGES_REQUESTED"):
            _assert_invariants(report)

    def test_invariant_completed_with_error_verdict_raises(self):
        report = _make_report(
            status="COMPLETED",
            reviewer_verdict="ERROR",
        )
        with pytest.raises(RuntimeError, match="reviewer_verdict is ERROR"):
            _assert_invariants(report)


# ── 정상 조합 → 통과 ──────────────────────────────────────────────────────────


class TestInvariantPasses:
    def test_invariant_completed_with_approved_passes(self):
        report = _make_report(
            status="COMPLETED",
            qg_verdict="PASS",
            test_count=3,
            reviewer_verdict="APPROVED",
        )
        _assert_invariants(report)  # no raise

    def test_invariant_completed_with_approved_with_suggestions_passes(self):
        report = _make_report(
            status="COMPLETED",
            qg_verdict="PASS",
            test_count=5,
            reviewer_verdict="APPROVED_WITH_SUGGESTIONS",
        )
        _assert_invariants(report)

    def test_invariant_completed_with_warning_qg_passes(self):
        # WARNING 은 BLOCKED 가 아니므로 허용
        report = _make_report(
            status="COMPLETED",
            qg_verdict="WARNING",
            test_count=2,
            reviewer_verdict="APPROVED",
        )
        _assert_invariants(report)

    def test_invariant_completed_with_none_qg_passes(self):
        # 레거시 태스크(QG verdict 미기록) — None 은 BLOCKED 아님 → 통과
        report = _make_report(
            status="COMPLETED",
            qg_verdict=None,
            test_count=1,
            reviewer_verdict="APPROVED",
        )
        _assert_invariants(report)

    def test_invariant_completed_with_empty_verdict_passes(self):
        # reviewer 단계 이전 실패로 verdict="" 인 경우 — 하지만 status=COMPLETED
        # 와 공존할 수 있도록 "" 는 허용 (실제로 pipeline 이 COMPLETED 로
        # 끝날 수 없지만, 레거시 데이터 호환).
        report = _make_report(
            status="COMPLETED",
            qg_verdict="PASS",
            test_count=1,
            reviewer_verdict="",
        )
        _assert_invariants(report)


# ── FAILED 상태는 모든 조합 허용 ───────────────────────────────────────────────


class TestInvariantFailedPassThrough:
    def test_invariant_failed_tasks_pass_through(self):
        # FAILED 는 어떠한 QG/reviewer 조합이어도 raise 하지 않는다.
        report = _make_report(
            status="FAILED",
            qg_verdict="BLOCKED",
            test_count=0,
            reviewer_verdict="CHANGES_REQUESTED",
        )
        _assert_invariants(report)

    def test_invariant_unknown_status_pass_through(self):
        report = _make_report(
            status="IN_PROGRESS",  # COMPLETED 가 아닌 어떤 상태
            qg_verdict="BLOCKED",
            test_count=0,
        )
        _assert_invariants(report)


# ── 오류 메시지 포함 정보 ─────────────────────────────────────────────────────


class TestInvariantMessage:
    def test_error_message_includes_task_id(self):
        report = _make_report(status="COMPLETED", qg_verdict="BLOCKED")
        report.task_id = "task-xyz-42"
        with pytest.raises(RuntimeError) as exc:
            _assert_invariants(report)
        assert "task-xyz-42" in str(exc.value)

    def test_task_009_regression_mentioned_in_zero_tests_error(self):
        report = _make_report(status="COMPLETED", test_count=0)
        with pytest.raises(RuntimeError) as exc:
            _assert_invariants(report)
        # task-009 회귀 방지 메시지가 포함되어야 한다
        assert "task-009" in str(exc.value)
