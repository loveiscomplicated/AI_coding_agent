"""
tests/test_weekly.py — orchestrator.weekly 단위 테스트
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.report import TaskReport
from orchestrator.weekly import (
    _prev_week,
    build_weekly_prompt,
    collect_week_stats,
    current_iso_week,
    generate_weekly_report,
    get_week_range,
    list_weekly_reports,
    load_weekly_report,
)


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

def make_report(**kwargs) -> TaskReport:
    defaults = dict(
        task_id="task-001",
        title="테스트 태스크",
        status="COMPLETED",
        completed_at="2026-01-05T10:00:00+00:00",
        retry_count=0,
        test_count=3,
        test_pass_first_try=True,
        reviewer_verdict="APPROVED",
        time_elapsed_seconds=120.0,
        failure_reasons=[],
        reviewer_feedback="",
    )
    defaults.update(kwargs)
    return TaskReport(**defaults)


# ── get_week_range ────────────────────────────────────────────────────────────

class TestGetWeekRange:
    def test_returns_monday_to_sunday(self):
        monday, sunday = get_week_range(2026, 1)
        assert monday.weekday() == 0  # 월요일
        assert sunday.weekday() == 6  # 일요일

    def test_sunday_is_6_days_after_monday(self):
        monday, sunday = get_week_range(2026, 15)
        delta = sunday - monday
        assert delta.days == 6

    def test_times_are_utc(self):
        monday, sunday = get_week_range(2026, 1)
        assert monday.tzinfo == timezone.utc
        assert sunday.tzinfo == timezone.utc

    def test_monday_is_start_of_day(self):
        monday, _ = get_week_range(2026, 10)
        assert monday.hour == 0
        assert monday.minute == 0
        assert monday.second == 0

    def test_sunday_is_end_of_day(self):
        _, sunday = get_week_range(2026, 10)
        assert sunday.hour == 23
        assert sunday.minute == 59
        assert sunday.second == 59


# ── current_iso_week ──────────────────────────────────────────────────────────

class TestCurrentIsoWeek:
    def test_returns_tuple_of_ints(self):
        year, week = current_iso_week()
        assert isinstance(year, int)
        assert isinstance(week, int)

    def test_week_in_valid_range(self):
        _, week = current_iso_week()
        assert 1 <= week <= 53


# ── collect_week_stats ────────────────────────────────────────────────────────

class TestCollectWeekStats:
    def test_empty_list(self):
        stats = collect_week_stats([])
        assert stats["total"] == 0
        assert stats["success_rate"] == 0
        assert stats["first_try_rate"] == 0
        assert stats["avg_elapsed_seconds"] == 0

    def test_all_completed(self):
        reports = [make_report(task_id=f"task-{i:03d}") for i in range(4)]
        stats = collect_week_stats(reports)
        assert stats["total"] == 4
        assert stats["completed"] == 4
        assert stats["failed"] == 0
        assert stats["success_rate"] == 100

    def test_mixed_status(self):
        reports = [
            make_report(task_id="task-001", status="COMPLETED"),
            make_report(task_id="task-002", status="FAILED"),
        ]
        stats = collect_week_stats(reports)
        assert stats["completed"] == 1
        assert stats["failed"] == 1
        assert stats["success_rate"] == 50

    def test_first_try_rate(self):
        reports = [
            make_report(task_id="task-001", test_pass_first_try=True),
            make_report(task_id="task-002", test_pass_first_try=False),
            make_report(task_id="task-003", test_pass_first_try=True),
            make_report(task_id="task-004", test_pass_first_try=False),
        ]
        stats = collect_week_stats(reports)
        assert stats["first_try_rate"] == 50

    def test_avg_elapsed(self):
        reports = [
            make_report(task_id="task-001", time_elapsed_seconds=100.0),
            make_report(task_id="task-002", time_elapsed_seconds=200.0),
        ]
        stats = collect_week_stats(reports)
        assert stats["avg_elapsed_seconds"] == 150.0

    def test_reviewer_approved_count(self):
        reports = [
            make_report(task_id="task-001", reviewer_verdict="APPROVED"),
            make_report(task_id="task-002", reviewer_verdict="REJECTED"),
            make_report(task_id="task-003", reviewer_verdict="APPROVED"),
        ]
        stats = collect_week_stats(reports)
        assert stats["reviewer_approved"] == 2


# ── build_weekly_prompt ───────────────────────────────────────────────────────

class TestBuildWeeklyPrompt:
    def test_contains_year_and_week(self):
        reports = [make_report()]
        prompt = build_weekly_prompt(2026, 15, reports)
        assert "2026" in prompt
        assert "15" in prompt

    def test_contains_stats(self):
        reports = [make_report()]
        prompt = build_weekly_prompt(2026, 15, reports)
        assert "성공률" in prompt
        assert "태스크" in prompt

    def test_empty_reports_message(self):
        prompt = build_weekly_prompt(2026, 15, [])
        assert "태스크 없음" in prompt

    def test_includes_prev_content(self):
        reports = [make_report()]
        prompt = build_weekly_prompt(2026, 15, reports, prev_content="이전 주 보고서 내용")
        assert "이전 주 보고서 내용" in prompt

    def test_no_prev_content_by_default(self):
        reports = [make_report()]
        prompt = build_weekly_prompt(2026, 15, reports)
        assert "전주 보고서" not in prompt


# ── _prev_week ────────────────────────────────────────────────────────────────

class TestPrevWeek:
    def test_week_2_returns_week_1(self):
        year, week = _prev_week(2026, 2)
        assert year == 2026
        assert week == 1

    def test_week_1_crosses_year_boundary(self):
        year, week = _prev_week(2026, 1)
        # 2025년의 마지막 ISO 주차
        assert year == 2025
        assert week >= 52

    def test_week_decrements_by_one(self):
        year, week = _prev_week(2026, 20)
        assert year == 2026
        assert week == 19


# ── generate_weekly_report ────────────────────────────────────────────────────

class TestGenerateWeeklyReport:
    def test_saves_file_to_weekly_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reports_dir = Path(tmpdir) / "reports"
            reports_dir.mkdir()
            weekly_dir = Path(tmpdir) / "weekly"

            mock_llm = MagicMock(return_value="# 주간 보고서\n내용")

            with patch("orchestrator.weekly.load_reports", return_value=[]):
                content, path = generate_weekly_report(
                    llm_fn=mock_llm,
                    year=2026,
                    week=15,
                    reports_dir=reports_dir,
                    weekly_dir=weekly_dir,
                )

            assert path.exists()
            assert path.name == "2026-W15.md"
            assert content == "# 주간 보고서\n내용"

    def test_llm_called_with_system_and_user(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reports_dir = Path(tmpdir) / "reports"
            reports_dir.mkdir()
            weekly_dir = Path(tmpdir) / "weekly"
            mock_llm = MagicMock(return_value="보고서 내용")

            with patch("orchestrator.weekly.load_reports", return_value=[]):
                generate_weekly_report(
                    llm_fn=mock_llm,
                    year=2026,
                    week=15,
                    reports_dir=reports_dir,
                    weekly_dir=weekly_dir,
                )

            assert mock_llm.called
            call_args = mock_llm.call_args[0]
            system_prompt, user_prompt = call_args
            assert "보고서" in system_prompt
            assert "2026" in user_prompt

    def test_uses_current_week_when_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reports_dir = Path(tmpdir) / "reports"
            reports_dir.mkdir()
            weekly_dir = Path(tmpdir) / "weekly"
            mock_llm = MagicMock(return_value="내용")

            with patch("orchestrator.weekly.load_reports", return_value=[]):
                with patch("orchestrator.weekly.current_iso_week", return_value=(2026, 20)):
                    content, path = generate_weekly_report(
                        llm_fn=mock_llm,
                        year=None,
                        week=None,
                        reports_dir=reports_dir,
                        weekly_dir=weekly_dir,
                    )

            assert path.name == "2026-W20.md"

    def test_loads_prev_week_report_if_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reports_dir = Path(tmpdir) / "reports"
            reports_dir.mkdir()
            weekly_dir = Path(tmpdir) / "weekly"
            weekly_dir.mkdir()

            # 전주 보고서 미리 생성
            prev_path = weekly_dir / "2026-W14.md"
            prev_path.write_text("전주 보고서 내용", encoding="utf-8")

            captured_user = {}
            def mock_llm(system, user):
                captured_user["prompt"] = user
                return "이번 주 보고서"

            with patch("orchestrator.weekly.load_reports", return_value=[]):
                generate_weekly_report(
                    llm_fn=mock_llm,
                    year=2026,
                    week=15,
                    reports_dir=reports_dir,
                    weekly_dir=weekly_dir,
                )

            assert "전주 보고서 내용" in captured_user["prompt"]


# ── load_weekly_report / list_weekly_reports ──────────────────────────────────

class TestLoadAndList:
    def test_load_returns_none_when_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = load_weekly_report(2026, 15, weekly_dir=Path(tmpdir))
        assert result is None

    def test_load_returns_content_when_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "2026-W15.md"
            p.write_text("주간 보고서 내용", encoding="utf-8")
            result = load_weekly_report(2026, 15, weekly_dir=Path(tmpdir))
        assert result == "주간 보고서 내용"

    def test_list_returns_empty_when_dir_missing(self):
        result = list_weekly_reports(weekly_dir=Path("/nonexistent/path"))
        assert result == []

    def test_list_returns_sorted_descending(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "2026-W10.md").write_text("a")
            (d / "2026-W15.md").write_text("b")
            (d / "2025-W52.md").write_text("c")

            result = list_weekly_reports(weekly_dir=d)

        names = [f"{r['year']}-W{r['week']:02d}" for r in result]
        assert names == sorted(names, reverse=True)

    def test_list_ignores_non_matching_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "2026-W10.md").write_text("a")
            (d / "README.md").write_text("b")
            (d / "invalid.md").write_text("c")

            result = list_weekly_reports(weekly_dir=d)

        assert len(result) == 1
        assert result[0]["week"] == 10
