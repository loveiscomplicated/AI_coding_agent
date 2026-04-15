from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from reports.task_report import TaskReport

from orchestrator.weekly import generate_weekly_report, get_week_range


def _report(task_id: str, completed_at: str | None) -> TaskReport:
    return TaskReport(
        task_id=task_id,
        title=f"title-{task_id}",
        status="COMPLETED",
        completed_at=completed_at,
        retry_count=0,
        test_count=1,
        test_pass_first_try=True,
        reviewer_verdict="APPROVED",
        time_elapsed_seconds=12.3,
    )


def test_generate_weekly_report_filters_by_sunday_and_uses_prev_report(monkeypatch, tmp_path: Path):
    reports = [
        _report("task-in", "2026-01-06T10:00:00+00:00"),   # 2026-W02 범위 내
        _report("task-out", "2026-01-12T00:00:00+00:00"),  # 다음 주
    ]
    weekly_dir = tmp_path / "weekly"
    weekly_dir.mkdir(parents=True, exist_ok=True)
    (weekly_dir / "2026-W01.md").write_text("전주 요약", encoding="utf-8")

    monkeypatch.setattr("orchestrator.weekly.load_reports", lambda since, reports_dir: reports)

    captured = {"prompt": ""}

    def _llm(_system: str, user_prompt: str) -> str:
        captured["prompt"] = user_prompt
        return "WEEKLY-CONTENT"

    content, path = generate_weekly_report(
        llm_fn=_llm,
        year=2026,
        week=2,
        reports_dir=tmp_path / "reports",
        weekly_dir=weekly_dir,
    )

    assert content == "WEEKLY-CONTENT"
    assert path == weekly_dir / "2026-W02.md"
    assert path.read_text(encoding="utf-8") == "WEEKLY-CONTENT"
    assert "task-in" in captured["prompt"]
    assert "task-out" not in captured["prompt"]
    assert "전주 보고서" in captured["prompt"]
    assert "전주 요약" in captured["prompt"]


def test_generate_weekly_report_passes_monday_as_since(monkeypatch, tmp_path: Path):
    seen_since: list[datetime] = []

    def _fake_load_reports(since, reports_dir):
        seen_since.append(since)
        return []

    monkeypatch.setattr("orchestrator.weekly.load_reports", _fake_load_reports)

    generate_weekly_report(
        llm_fn=lambda _sys, _user: "OK",
        year=2026,
        week=2,
        reports_dir=tmp_path / "reports",
        weekly_dir=tmp_path / "weekly",
    )

    monday, _ = get_week_range(2026, 2)
    assert seen_since and seen_since[0] == monday


def test_generate_weekly_report_without_prev_report_omits_prev_section(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "orchestrator.weekly.load_reports",
        lambda since, reports_dir: [_report("task-1", "2026-01-06T00:00:00+00:00")],
    )
    captured = {"prompt": ""}

    def _llm(_system: str, user_prompt: str) -> str:
        captured["prompt"] = user_prompt
        return "OK"

    generate_weekly_report(
        llm_fn=_llm,
        year=2026,
        week=2,
        reports_dir=tmp_path / "reports",
        weekly_dir=tmp_path / "weekly",
    )

    assert "전주 보고서" not in captured["prompt"]
