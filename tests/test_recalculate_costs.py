"""
tests/test_recalculate_costs.py — scripts/recalculate_costs.py 동작 검증.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.recalculate_costs import recalculate_file


def _write_report_yaml(path: Path, *, cost_usd, quality, models_used, token_usage):
    data = {
        "task_id": path.stem,
        "title": "t",
        "status": "COMPLETED",
        "completed_at": "2026-04-10T00:00:00+00:00",
        "metrics": {
            "retry_count": 0,
            "total_tokens": 0,
            "cost_usd": cost_usd,
            "cost_estimation_quality": quality,
        },
        "pipeline_result": {},
        "models_used": models_used,
        "token_usage": token_usage,
    }
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def test_recalculate_creates_backup_before_overwrite(tmp_path):
    """덮어쓰기 직전에 동일 경로의 .bak 사본이 생성돼야 한다."""
    path = tmp_path / "task-042.yaml"
    _write_report_yaml(
        path,
        cost_usd=0.0,  # stale 값 — 재계산 대상
        quality="missing",
        models_used={"implementer": "openai/gpt-5"},
        token_usage={"implementer": {"input": 1_000_000, "output": 0, "cached_read": 0, "cached_write": 0}},
    )
    original = path.read_bytes()

    result = recalculate_file(path, dry_run=False, backup=True)

    assert result["changed"] is True
    backup_path = tmp_path / "task-042.yaml.bak"
    assert backup_path.exists()
    # 백업은 원본 그대로여야 한다
    assert backup_path.read_bytes() == original
    # 원본은 새 값으로 갱신돼 있어야 한다
    new_data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert new_data["metrics"]["cost_usd"] == 1.25
    assert new_data["metrics"]["cost_estimation_quality"] == "exact"


def test_recalculate_dry_run_touches_nothing(tmp_path):
    path = tmp_path / "task-099.yaml"
    _write_report_yaml(
        path,
        cost_usd=0.0,
        quality="missing",
        models_used={"implementer": "openai/gpt-5"},
        token_usage={"implementer": {"input": 1_000_000, "output": 0, "cached_read": 0, "cached_write": 0}},
    )
    original = path.read_bytes()

    result = recalculate_file(path, dry_run=True, backup=True)

    assert result["changed"] is True
    assert path.read_bytes() == original  # 원본 보존
    assert not (tmp_path / "task-099.yaml.bak").exists()  # 백업도 안 만듦


def test_recalculate_no_backup_option_skips_bak(tmp_path):
    path = tmp_path / "task-077.yaml"
    _write_report_yaml(
        path,
        cost_usd=0.0,
        quality="missing",
        models_used={"implementer": "openai/gpt-5"},
        token_usage={"implementer": {"input": 1_000_000, "output": 0, "cached_read": 0, "cached_write": 0}},
    )
    recalculate_file(path, dry_run=False, backup=False)
    assert not (tmp_path / "task-077.yaml.bak").exists()
