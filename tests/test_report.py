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
    _calculate_cost,
    _calculate_cost_with_quality,
    _model_rate,
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
        # pipeline invariant 을 만족시키기 위해 succeeded=True 에 기본값 주입:
        # test_result 가 없으면 "3 passed", review 가 없으면 APPROVED 로 채운다.
        # 각 테스트가 명시적으로 test_result/review 를 넘기면 그대로 사용한다.
        if succeeded and test_result is None:
            test_result = _FakeTestResult()
        if succeeded and review is None:
            review = _FakeReview()
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
        assert report.cost_usd is None
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
        # test_count=0 + succeeded=True 는 invariant 위반이므로, 이 파서 동작은
        # 실패 경로(FAILED)에서만 관찰한다 — 실제로 "no tests ran" summary 는
        # 성공 경로에 도달하지 않는다.
        task = make_task(status=TaskStatus.FAILED)
        result = _FakePipelineResult(
            succeeded=False,
            test_result=_FakeTestResult(summary="no tests ran"),
        )
        report = build_report(task, result)
        assert report.test_count == 0

    def test_reviewer_info_extracted(self):
        # CHANGES_REQUESTED verdict 는 pipeline invariant 상 succeeded=False 와만
        # 공존 가능하므로 FAILED 경로에서 검증한다.
        task = make_task(status=TaskStatus.FAILED)
        result = _FakePipelineResult(
            succeeded=False,
            review=_FakeReview(verdict="CHANGES_REQUESTED", details="수정 필요"),
            test_result=_FakeTestResult(),
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

    def test_token_usage_includes_all_roles_from_models_used(self):
        task = make_task()
        result = _FakePipelineResult(succeeded=False, failure_reason="x")
        result.models_used = {
            "test_writer": "model/a",
            "implementer": "model/b",
            "reviewer": "model/c",
            "intervention": "model/d",
        }
        result.metrics.token_usage = {
            "test_writer": (100, 10, 0, 0),
            "intervention": (20, 5, 0, 0),
        }

        report = build_report(task, result)
        assert report.token_usage is not None
        assert report.token_usage["test_writer"]["input"] == 100
        assert report.token_usage["intervention"]["output"] == 5
        assert report.token_usage["implementer"] == {
            "input": 0, "output": 0, "cached_read": 0, "cached_write": 0,
        }
        assert report.token_usage["reviewer"] == {
            "input": 0, "output": 0, "cached_read": 0, "cached_write": 0,
        }


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


# ── 비용 계산: 단가 미등록 / 품질 지표 ─────────────────────────────────────────

class TestCostCalculation:
    def test_model_pricing_matches_current_official_sources(self):
        assert _model_rate("openai/gpt-5") == {
            "input": 1.25, "output": 10.00, "cached_read": 0.125,
        }
        assert _model_rate("openai/gpt-5-mini") == {
            "input": 0.25, "output": 2.00, "cached_read": 0.025,
        }
        assert _model_rate("google/gemini-2.5-flash") == {
            "input": 0.30, "output": 2.50, "cached_read": 0.03,
        }
        assert _model_rate("google/gemini-3.1-pro-preview") == {
            "input": 2.00, "output": 12.00, "cached_read": 0.20,
        }
        assert _model_rate("zai/glm-5.1") == {
            "input": 1.40, "output": 4.40, "cached_read": 0.26,
        }

    def test_gemini_legacy_alias_maps_to_current_pricing(self):
        assert _model_rate("google/gemini-3-pro-preview") == _model_rate(
            "google/gemini-3.1-pro-preview"
        )

    def test_model_rate_rejects_version_suffix_boundary(self):
        """미래의 `gpt-5.4` 처럼 dotted 버전은 `gpt-5` 단가로 오인식되면 안 된다.

        substring 매칭은 `openai/gpt-5.4` 를 `gpt-5` 로 잡아 과금 오류를 일으키므로,
        키 뒤에 `.` 또는 추가 숫자가 오면 매칭을 거부해야 한다.
        """
        assert _model_rate("openai/gpt-5.4") is None
        assert _model_rate("openai/gpt-50") is None
        assert _model_rate("zai/glm-4.66") is None
        # 정상 케이스는 계속 매칭돼야 한다 (regression guard)
        assert _model_rate("openai/gpt-5") is not None
        assert _model_rate("openai/gpt-5-2026-04-01") is not None
        assert _model_rate("anthropic/claude-haiku-4-5-20251001") is not None

    def test_model_rate_rejects_prefix_boundary(self):
        """`chatgpt-5` 나 `foo-bar-gpt-5` 는 `gpt-5` 단가로 해석되면 안 된다.

        왼쪽에 alnum 이 붙은 경우는 다른 모델이므로 매칭을 거부해야 한다.
        구분자(`/`, `-`, 문자열 시작)로 시작하는 경우만 허용.
        """
        assert _model_rate("openai/chatgpt-5") is None
        assert _model_rate("foo/bar-gpt-5") is None
        assert _model_rate("somegpt-5-mini") is None
        assert _model_rate("xglm-4.6") is None
        # 정상 케이스는 매칭 유지
        assert _model_rate("openai/gpt-5") is not None
        assert _model_rate("gpt-5") is not None
        assert _model_rate("openai/gpt-5-mini") is not None

    def test_calculate_cost_unregistered_model_returns_none(self, caplog):
        """단가 테이블에 없는 모델만 사용된 경우 None 을 반환하고 경고를 남겨야 한다."""
        token_usage = {"implementer": (1000, 500, 0, 0)}
        models_used = {"implementer": "some-fictional-provider/ultra-model-9000"}

        with caplog.at_level("WARNING"):
            result = _calculate_cost(token_usage, models_used)

        assert result is None
        assert any("PRICING_MISSING" in rec.message for rec in caplog.records)

    def test_calculate_cost_registered_model_includes_cached_read(self):
        """등록된 모델은 input/output/cached_read 단가 모두 반영되어야 한다."""
        token_usage = {"implementer": (1_000_000, 0, 0, 0)}
        models_used = {"implementer": "openai/gpt-5"}
        result = _calculate_cost(token_usage, models_used)
        # gpt-5 input rate: $1.25/1M
        assert result == pytest.approx(1.25)

    def test_calculate_cost_with_quality_fallback_when_mixed(self):
        token_usage = {
            "a": (1000, 0, 0, 0),
            "b": (1000, 0, 0, 0),
        }
        models_used = {"a": "openai/gpt-5", "b": "unknown/xyz"}
        cost, quality, missing = _calculate_cost_with_quality(token_usage, models_used)
        assert quality == "fallback"
        assert "unknown/xyz" in missing
        assert cost is not None and cost > 0

    def test_task_report_cost_estimation_quality_mixed(self):
        """build_report 는 등록/미등록 모델이 섞였을 때 'fallback' 으로 표기해야 한다."""
        task = make_task()
        result = _FakePipelineResult(succeeded=True)
        result.models_used = {
            "implementer": "openai/gpt-5",
            "reviewer": "unregistered/quantum-foo",
        }
        result.metrics.token_usage = {
            "implementer": (1000, 500, 0, 0),
            "reviewer": (500, 200, 0, 0),
        }
        report = build_report(task, result)
        assert report.cost_estimation_quality == "fallback"
        assert report.cost_usd is not None and report.cost_usd > 0

    def test_task_report_cost_estimation_quality_missing(self):
        task = make_task()
        result = _FakePipelineResult(succeeded=True)
        result.models_used = {"implementer": "unregistered/foo"}
        result.metrics.token_usage = {"implementer": (1000, 500, 0, 0)}
        report = build_report(task, result)
        assert report.cost_estimation_quality == "missing"
        assert report.cost_usd is None

    def test_legacy_yaml_without_quality_field_loads_as_missing(self, tmp_path):
        """기존 YAML (cost_estimation_quality 없음) 은 'missing' 으로 로드돼야 한다."""
        import yaml as _yaml
        legacy = {
            "task_id": "task-legacy",
            "title": "legacy",
            "status": "COMPLETED",
            "completed_at": "2026-01-01T00:00:00+00:00",
            "metrics": {
                "retry_count": 0,
                "total_tokens": 100,
                "cost_usd": 0.0,
            },
        }
        path = tmp_path / "task-legacy.yaml"
        path.write_text(_yaml.safe_dump(legacy), encoding="utf-8")
        loaded = load_report("task-legacy", reports_dir=tmp_path)
        assert loaded.cost_estimation_quality == "missing"


# ── T2: iteration 시계열 요약 집계 ──────────────────────────────────────────

class TestIterationTimeSeriesAggregation:
    """build_report 가 metrics.call_logs 에서 iteration 요약을 집계하는지 검증."""

    def test_iteration_count_by_role_aggregated(self):
        """role 별 LLM 호출 수를 정확히 집계한다. compaction 이벤트는 제외."""
        task = make_task()
        result = _FakePipelineResult(succeeded=True)
        # test_writer: 2회, implementer: 3회 + compaction 이벤트 1개
        result.metrics.call_logs = {
            "test_writer": [
                {"iteration": 1, "input_tokens": 100, "output_tokens": 10, "cached_read_tokens": 0},
                {"iteration": 2, "input_tokens": 120, "output_tokens": 15, "cached_read_tokens": 0},
            ],
            "implementer": [
                {"iteration": 1, "input_tokens": 200, "output_tokens": 20, "cached_read_tokens": 0},
                {"iteration": 2, "input_tokens": 300, "output_tokens": 30, "cached_read_tokens": 50},
                {"iteration": 2, "event": "compaction", "net_tokens_saved": 1000},
                {"iteration": 3, "input_tokens": 50, "output_tokens": 10, "cached_read_tokens": 500},
            ],
        }

        report = build_report(task, result)
        assert report.iteration_count_by_role == {
            "test_writer": 2,
            "implementer": 3,  # compaction 이벤트는 제외
        }

    def test_max_single_iteration_tokens_recorded(self):
        """역할 무관 최대 단일 iteration 토큰 (input+output+cached_read) 을 기록한다."""
        task = make_task()
        result = _FakePipelineResult(succeeded=True)
        result.metrics.call_logs = {
            "test_writer": [
                {"iteration": 1, "input_tokens": 100, "output_tokens": 10, "cached_read_tokens": 0},
            ],
            "implementer": [
                {"iteration": 1, "input_tokens": 5000, "output_tokens": 500, "cached_read_tokens": 1000},
                {"iteration": 2, "input_tokens": 200, "output_tokens": 20, "cached_read_tokens": 0},
            ],
        }

        report = build_report(task, result)
        # implementer iter 1: 5000 + 500 + 1000 = 6500
        assert report.max_single_iteration_tokens == 6500

    def test_zero_values_when_no_call_logs(self):
        task = make_task()
        result = _FakePipelineResult(succeeded=True)
        # call_logs 비어있음
        report = build_report(task, result)
        assert report.max_single_iteration_tokens == 0
        assert report.iteration_count_by_role == {}

    def test_roundtrip_preserves_iteration_metrics(self, tmp_path):
        report = TaskReport(
            task_id="task-007",
            title="시계열 테스트",
            status="COMPLETED",
            completed_at="2026-04-20T00:00:00+00:00",
            max_single_iteration_tokens=45000,
            iteration_count_by_role={"implementer": 47, "test_writer": 8},
        )
        save_report(report, reports_dir=tmp_path)
        loaded = load_report("task-007", reports_dir=tmp_path)
        assert loaded.max_single_iteration_tokens == 45000
        assert loaded.iteration_count_by_role == {"implementer": 47, "test_writer": 8}
