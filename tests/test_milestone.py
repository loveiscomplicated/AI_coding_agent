from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from reports.task_report import TaskReport

from orchestrator.milestone import (
    collect_run_stats,
    generate_milestone_report,
    save_milestone_report,
)


def _sample_reports() -> list[TaskReport]:
    return [
        TaskReport(
            task_id="task-1",
            title="first",
            status="COMPLETED",
            completed_at="2026-04-15T10:00:00+00:00",
            retry_count=0,
            test_count=3,
            test_pass_first_try=True,
            reviewer_verdict="APPROVED",
            time_elapsed_seconds=31.2,
        )
    ]


def test_generate_milestone_report_returns_empty_for_no_reports(tmp_path: Path):
    content, path = generate_milestone_report(
        reports=[],
        llm_fn=lambda _sys, _prompt: "SHOULD-NOT-BE-CALLED",
        milestones_dir=tmp_path,
    )
    assert content == ""
    assert path == Path()


def test_generate_milestone_report_saves_content_with_valid_path(tmp_path: Path):
    content, path = generate_milestone_report(
        reports=_sample_reports(),
        llm_fn=lambda _sys, _prompt: "# milestone report",
        run_label="run-1",
        milestones_dir=tmp_path,
    )

    assert content == "# milestone report"
    assert path.parent == tmp_path
    assert path.suffix == ".md"
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "# milestone report"


def _report(task_id: str, verdict: str) -> TaskReport:
    return TaskReport(
        task_id=task_id,
        title=task_id,
        status="COMPLETED",
        completed_at="2026-04-15T10:00:00+00:00",
        retry_count=0,
        test_count=1,
        test_pass_first_try=True,
        reviewer_verdict=verdict,
        time_elapsed_seconds=10.0,
    )


def test_collect_run_stats_counts_approved_with_suggestions_as_approved():
    """APPROVED_WITH_SUGGESTIONS 도 PR 생성 = '승인' 으로 집계된다."""
    stats = collect_run_stats([
        _report("t1", "APPROVED"),
        _report("t2", "APPROVED_WITH_SUGGESTIONS"),
        _report("t3", "CHANGES_REQUESTED"),
    ])
    assert stats["approved"] == 2


def test_save_milestone_report_timestamp_filename_format(monkeypatch, tmp_path: Path):
    fixed = datetime(2026, 4, 15, 12, 34, 56, tzinfo=timezone.utc)

    class _FixedDateTime:
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr("orchestrator.milestone.datetime", _FixedDateTime)

    path = save_milestone_report("hello", milestones_dir=tmp_path)

    assert path.name == "2026-04-15-123456.md"
    assert re.match(r"^\d{4}-\d{2}-\d{2}-\d{6}\.md$", path.name)
