"""
tests/test_report.py — orchestrator/report.py 단위 테스트
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pytest

from orchestrator.report import (
    TaskReport,
    build_report,
    save_report,
    load_report,
    load_reports,
)
from orchestrator.task import Task, TaskStatus


# ── 픽스처 ───────────────────────────────────────────────────────────────────

def make_task(**kwargs) -> Task:
    defaults = dict(
        id="task-001",
        title="테스트 태스크",
        description="설명",
        acceptance_criteria=["조건1"],
        target_files=["src/foo.py"],
        status=TaskStatus.DONE,
        retry_count=0,
    )
    defaults.update(kwargs)
    return Task(**defaults)


class _FakeTestResult:
    def __init__(self, summary="3 passed in 0.5s", failed_tests=None):
        self.summary = summary
        self.failed_tests = failed_tests or []


class _FakeReview:
    def __init__(self, approved=True, verdict="APPROVED", details=""):
        self.approved = approved
        self.verdict = verdict
        self.details = details


class _FakePipelineResult:
    def __init__(
        self,
        succeeded=True,
        failure_reason="",
        test_result=None,
        review=None,
    ):
        self.succeeded = succeeded
        self.failure_reason = failure_reason
        self.test_result = test_result
        self.review = review
        from orchestrator.pipeline import PipelineMetrics
        self.metrics = PipelineMetrics()
        self.models_used = {}


# ── TaskReport 직렬화 ─────────────────────────────────────────────────────────

class TestTaskReportSerialization:
    def test_to_dict_has_expected_keys(self):
        report = TaskReport(
            task_id="t1", title="제목", status="COMPLETED",
            completed_at="2026-01-01T00:00:00+00:00",
        )
        d = report.to_dict()
        assert "task_id" in d
        assert "metrics" in d
        assert "pipeline_result" in d

    def test_from_dict_roundtrip(self):
        report = TaskReport(
            task_id="t1", title="제목", status="COMPLETED",
            completed_at="2026-01-01T00:00:00+00:00",
            retry_count=2, test_count=5, reviewer_verdict="APPROVED",
            time_elapsed_seconds=12.3,
        )
        restored = TaskReport.from_dict(report.to_dict())
        assert restored.task_id == "t1"
        assert restored.retry_count == 2
        assert restored.test_count == 5
        assert restored.reviewer_verdict == "APPROVED"
        assert restored.time_elapsed_seconds == pytest.approx(12.3)

    def test_from_dict_defaults_on_missing_keys(self):
        minimal = {"task_id": "t2", "title": "", "status": "FAILED", "completed_at": ""}
        report = TaskReport.from_dict(minimal)
        assert report.retry_count == 0
        assert report.failure_reasons == []
        assert report.pr_number is None


# ── build_report ──────────────────────────────────────────────────────────────

class TestBuildReport:
    def test_success_sets_completed_status(self):
        task = make_task()
        result = _FakePipelineResult(succeeded=True)
        report = build_report(task, result, elapsed_seconds=1.0)
        assert report.status == "COMPLETED"

    def test_failure_sets_failed_status(self):
        task = make_task(status=TaskStatus.FAILED)
        result = _FakePipelineResult(succeeded=False, failure_reason="타임아웃")
        report = build_report(task, result)
        assert report.status == "FAILED"
        assert "타임아웃" in report.failure_reasons

    def test_test_count_parsed_from_summary(self):
        task = make_task()
        result = _FakePipelineResult(
            succeeded=True,
            test_result=_FakeTestResult(summary="7 passed in 1.2s"),
        )
        report = build_report(task, result)
        assert report.test_count == 7

    def test_non_numeric_summary_gives_zero_count(self):
        task = make_task()
        result = _FakePipelineResult(
            succeeded=True,
            test_result=_FakeTestResult(summary="no tests ran"),
        )
        report = build_report(task, result)
        assert report.test_count == 0

    def test_reviewer_info_extracted(self):
        task = make_task()
        result = _FakePipelineResult(
            succeeded=True,
            review=_FakeReview(verdict="CHANGES_REQUESTED", details="수정 필요"),
        )
        report = build_report(task, result)
        assert report.reviewer_verdict == "CHANGES_REQUESTED"
        assert report.reviewer_feedback == "수정 필요"

    def test_pr_number_extracted_from_url(self):
        task = make_task()
        result = _FakePipelineResult(succeeded=True)
        report = build_report(task, result, pr_url="https://github.com/owner/repo/pull/42")
        assert report.pr_number == 42

    def test_pr_number_none_when_no_url(self):
        task = make_task()
        result = _FakePipelineResult(succeeded=True)
        report = build_report(task, result, pr_url="")
        assert report.pr_number is None

    def test_elapsed_seconds_rounded(self):
        task = make_task()
        result = _FakePipelineResult(succeeded=True)
        report = build_report(task, result, elapsed_seconds=3.14159)
        assert report.time_elapsed_seconds == pytest.approx(3.1, abs=0.05)

    def test_first_try_pass_when_retry_zero_and_succeeded(self):
        task = make_task(retry_count=0)
        result = _FakePipelineResult(succeeded=True)
        report = build_report(task, result)
        assert report.test_pass_first_try is True

    def test_not_first_try_when_retry_nonzero(self):
        task = make_task(retry_count=1)
        result = _FakePipelineResult(succeeded=True)
        report = build_report(task, result)
        assert report.test_pass_first_try is False

    def test_failure_reasons_from_failed_tests(self):
        task = make_task(status=TaskStatus.FAILED)
        result = _FakePipelineResult(
            succeeded=False,
            test_result=_FakeTestResult(
                summary="1 failed in 0.3s",
                failed_tests=["test_foo", "test_bar"],
            ),
        )
        report = build_report(task, result)
        assert "test_foo" in report.failure_reasons


# ── save_report / load_report ─────────────────────────────────────────────────

class TestSaveLoadReport:
    def test_save_and_load_roundtrip(self, tmp_path):
        report = TaskReport(
            task_id="task-999",
            title="저장 테스트",
            status="COMPLETED",
            completed_at="2026-03-01T00:00:00+00:00",
            retry_count=1,
            test_count=3,
        )
        save_report(report, reports_dir=tmp_path)
        loaded = load_report("task-999", reports_dir=tmp_path)
        assert loaded.task_id == "task-999"
        assert loaded.retry_count == 1
        assert loaded.test_count == 3

    def test_save_creates_yaml_file(self, tmp_path):
        report = TaskReport(
            task_id="task-abc",
            title="파일 생성 테스트",
            status="FAILED",
            completed_at="2026-03-01T00:00:00+00:00",
        )
        path = save_report(report, reports_dir=tmp_path)
        assert path.exists()
        assert path.suffix == ".yaml"

    def test_load_missing_report_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_report("nonexistent-task", reports_dir=tmp_path)


# ── load_reports ──────────────────────────────────────────────────────────────

class TestLoadReports:
    def _save(self, tmp_path, task_id, completed_at):
        report = TaskReport(
            task_id=task_id, title="", status="COMPLETED", completed_at=completed_at
        )
        save_report(report, reports_dir=tmp_path)

    def test_returns_all_reports(self, tmp_path):
        self._save(tmp_path, "task-001", "2026-01-01T00:00:00+00:00")
        self._save(tmp_path, "task-002", "2026-01-02T00:00:00+00:00")
        reports = load_reports(reports_dir=tmp_path)
        assert len(reports) == 2

    def test_since_filter(self, tmp_path):
        self._save(tmp_path, "task-001", "2026-01-01T00:00:00+00:00")
        self._save(tmp_path, "task-002", "2026-03-01T00:00:00+00:00")
        since = datetime(2026, 2, 1, tzinfo=timezone.utc)
        reports = load_reports(since=since, reports_dir=tmp_path)
        assert len(reports) == 1
        assert reports[0].task_id == "task-002"

    def test_empty_dir_returns_empty_list(self, tmp_path):
        assert load_reports(reports_dir=tmp_path) == []

    def test_missing_dir_returns_empty_list(self, tmp_path):
        nonexistent = tmp_path / "nonexistent"
        assert load_reports(reports_dir=nonexistent) == []
