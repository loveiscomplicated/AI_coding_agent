"""
tests/test_dashboard.py — /api/dashboard/summary 엔드포인트 단위 테스트
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import dashboard as dashboard_router
from orchestrator.report import save_report
from reports.task_report import TaskReport


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(dashboard_router.router, prefix="/api")
    return TestClient(app)


def _save(reports_dir, task_id: str, **kwargs):
    report = TaskReport(
        task_id=task_id,
        title=kwargs.pop("title", ""),
        status=kwargs.pop("status", "COMPLETED"),
        completed_at=kwargs.pop("completed_at", "2026-04-10T00:00:00+00:00"),
        **kwargs,
    )
    save_report(report, reports_dir=reports_dir)


def test_summary_reports_missing_pricing_models(tmp_path, client):
    """단가 미등록 모델이 포함된 리포트가 있으면 summary 응답에 그대로 노출되어야 한다."""
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()

    # exact: 등록된 모델만 사용
    _save(
        reports_dir,
        "task-001",
        cost_usd=0.02,
        cost_estimation_quality="exact",
        models_used={"implementer": "openai/gpt-5"},
    )
    # fallback: 일부 모델 미등록
    _save(
        reports_dir,
        "task-002",
        cost_usd=0.01,
        cost_estimation_quality="fallback",
        models_used={
            "implementer": "openai/gpt-5",
            "reviewer": "unregistered/mystery-xl",
        },
    )
    # missing: 전부 미등록
    _save(
        reports_dir,
        "task-003",
        cost_usd=None,
        cost_estimation_quality="missing",
        models_used={"implementer": "another-unknown/wizard-3"},
    )

    res = client.get(
        "/api/dashboard/summary",
        params={"reports_dir": str(reports_dir), "tasks_path": str(tmp_path / "tasks.yaml")},
    )
    assert res.status_code == 200
    body = res.json()

    breakdown = body["cost_estimation_quality_breakdown"]
    assert breakdown == {"exact": 1, "fallback": 1, "missing": 1}

    missing = body["models_with_missing_pricing"]
    assert "unregistered/mystery-xl" in missing
    assert "another-unknown/wizard-3" in missing
    # 등록된 모델은 포함되지 않아야 함
    assert all("gpt-5" not in m for m in missing)

    # None 비용은 total_cost_usd 집계에서 null-safe 하게 스킵되어야 함
    assert body["metrics"]["total_cost_usd"] == pytest.approx(0.03)
    # missing 항목이 있으므로 client 가 "partial" 임을 인지할 수 있는 플래그가 있어야 함
    assert body["metrics"]["has_missing_costs"] is True


def test_summary_has_missing_costs_false_when_all_costed(tmp_path, client):
    """모든 리포트에 cost_usd 가 있으면 has_missing_costs 는 False."""
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    _save(
        reports_dir,
        "task-001",
        cost_usd=0.05,
        cost_estimation_quality="exact",
        models_used={"implementer": "openai/gpt-5"},
    )
    res = client.get(
        "/api/dashboard/summary",
        params={"reports_dir": str(reports_dir), "tasks_path": str(tmp_path / "tasks.yaml")},
    )
    body = res.json()
    assert body["metrics"]["has_missing_costs"] is False


def test_summary_empty_when_no_reports(tmp_path, client):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()

    res = client.get(
        "/api/dashboard/summary",
        params={"reports_dir": str(reports_dir), "tasks_path": str(tmp_path / "tasks.yaml")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["cost_estimation_quality_breakdown"] == {"exact": 0, "fallback": 0, "missing": 0}
    assert body["models_with_missing_pricing"] == []


def test_tasks_endpoint_preserves_null_cost(tmp_path, client):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()

    tasks_path = tmp_path / "tasks.yaml"
    tasks_path.write_text(
        """
tasks:
  - id: task-001
    title: null-cost report
    description: test
    acceptance_criteria: []
    status: done
    depends_on: []
""".strip(),
        encoding="utf-8",
    )

    _save(
        reports_dir,
        "task-001",
        cost_usd=None,
        cost_estimation_quality="missing",
        models_used={"implementer": "unknown/no-price"},
    )

    res = client.get(
        "/api/dashboard/tasks",
        params={"reports_dir": str(reports_dir), "tasks_path": str(tasks_path)},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["tasks"][0]["report"]["cost_usd"] is None
