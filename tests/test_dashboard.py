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


def test_summary_approved_counts_approved_with_suggestions(tmp_path, client):
    """dashboard summary.metrics.approved 가 APPROVED_WITH_SUGGESTIONS 도 포함해야 한다."""
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    _save(
        reports_dir, "task-001",
        reviewer_verdict="APPROVED",
        cost_usd=0.01, cost_estimation_quality="exact",
        models_used={"implementer": "openai/gpt-5"},
    )
    _save(
        reports_dir, "task-002",
        reviewer_verdict="APPROVED_WITH_SUGGESTIONS",
        cost_usd=0.01, cost_estimation_quality="exact",
        models_used={"implementer": "openai/gpt-5"},
    )
    _save(
        reports_dir, "task-003",
        reviewer_verdict="CHANGES_REQUESTED",
        status="FAILED",
        cost_usd=0.01, cost_estimation_quality="exact",
        models_used={"implementer": "openai/gpt-5"},
    )
    res = client.get(
        "/api/dashboard/summary",
        params={"reports_dir": str(reports_dir), "tasks_path": str(tmp_path / "tasks.yaml")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["metrics"]["approved"] == 2


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


# ── T2: outlier 태스크 탐지 ─────────────────────────────────────────────────

def test_outlier_tasks_detected_high_iteration(tmp_path, client):
    """역할별 iteration 평균 + 2σ 를 초과하는 태스크가 high_iteration_count 로 잡힌다.

    분포: [5,5,5,5,5,5,5,5,5,60] → mean=10.5, σ≈16.5, threshold≈43.5 → 60 이 outlier.
    정상값을 여러 개 두어 σ 가 outlier 하나에 지배되지 않도록 한다.
    """
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    for i, count in enumerate([5, 5, 5, 5, 5, 5, 5, 5, 5, 60], start=1):
        _save(
            reports_dir, f"task-{i:03d}",
            cost_usd=0.01, cost_estimation_quality="exact",
            models_used={"implementer": "openai/gpt-5"},
            iteration_count_by_role={"implementer": count},
        )

    res = client.get(
        "/api/dashboard/summary",
        params={"reports_dir": str(reports_dir), "tasks_path": str(tmp_path / "tasks.yaml")},
    )
    body = res.json()
    outliers = body["outlier_tasks"]
    assert len(outliers) == 1
    assert outliers[0]["task_id"] == "task-010"
    assert outliers[0]["reason"] == "high_iteration_count"
    assert outliers[0]["value"] == 60
    assert outliers[0]["role"] == "implementer"


def test_outlier_tasks_detected_high_single_iteration_tokens(tmp_path, client):
    """max_single_iteration_tokens > 50000 이면 outlier 로 잡힌다 (평균과 무관)."""
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    # 모두 iteration count 는 비슷하지만 task-002 는 단일 iter 토큰이 폭주.
    _save(
        reports_dir, "task-001",
        cost_usd=0.01, cost_estimation_quality="exact",
        models_used={"implementer": "openai/gpt-5"},
        iteration_count_by_role={"implementer": 5},
        max_single_iteration_tokens=10_000,
    )
    _save(
        reports_dir, "task-002",
        cost_usd=0.01, cost_estimation_quality="exact",
        models_used={"implementer": "openai/gpt-5"},
        iteration_count_by_role={"implementer": 5},
        max_single_iteration_tokens=60_000,
    )

    res = client.get(
        "/api/dashboard/summary",
        params={"reports_dir": str(reports_dir), "tasks_path": str(tmp_path / "tasks.yaml")},
    )
    body = res.json()
    outliers = body["outlier_tasks"]
    assert len(outliers) == 1
    assert outliers[0]["task_id"] == "task-002"
    assert outliers[0]["reason"] == "high_single_iteration_tokens"
    assert outliers[0]["value"] == 60_000


def test_outlier_tasks_empty_when_all_normal(tmp_path, client):
    """모든 태스크가 정상 범위면 outlier_tasks 는 빈 리스트."""
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    for i, count in enumerate([5, 6, 7, 5, 6], start=1):
        _save(
            reports_dir, f"task-10{i}",
            cost_usd=0.01, cost_estimation_quality="exact",
            models_used={"implementer": "openai/gpt-5"},
            iteration_count_by_role={"implementer": count},
            max_single_iteration_tokens=10_000,
        )
    res = client.get(
        "/api/dashboard/summary",
        params={"reports_dir": str(reports_dir), "tasks_path": str(tmp_path / "tasks.yaml")},
    )
    body = res.json()
    assert body["outlier_tasks"] == []


def test_outlier_tasks_empty_when_no_reports(tmp_path, client):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    res = client.get(
        "/api/dashboard/summary",
        params={"reports_dir": str(reports_dir), "tasks_path": str(tmp_path / "tasks.yaml")},
    )
    body = res.json()
    assert body["outlier_tasks"] == []


def test_outlier_small_sample_uses_absolute_fallback(tmp_path, client):
    """표본 N<10 에서는 mean+2σ 대신 절대 임계(30)를 사용해 runaway 를 잡는다.

    재현: [5, 100] → μ=52.5, σ=47.5, μ+2σ=147.5. 통계만으로는 100 이 outlier
    로 잡히지 않아 초기 프로젝트에서 가장 중요한 탈선을 놓치게 된다. 절대
    fallback 이 걸리면 100 > 30 으로 잡혀야 한다.
    """
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    for i, count in enumerate([5, 100], start=1):
        _save(
            reports_dir, f"task-{i:03d}",
            cost_usd=0.01, cost_estimation_quality="exact",
            models_used={"implementer": "openai/gpt-5"},
            iteration_count_by_role={"implementer": count},
        )
    res = client.get(
        "/api/dashboard/summary",
        params={"reports_dir": str(reports_dir), "tasks_path": str(tmp_path / "tasks.yaml")},
    )
    body = res.json()
    outliers = body["outlier_tasks"]
    assert len(outliers) == 1
    assert outliers[0]["task_id"] == "task-002"
    assert outliers[0]["reason"] == "high_iteration_count"
    assert outliers[0]["value"] == 100
    assert outliers[0]["role"] == "implementer"


def test_outlier_small_sample_normal_values_not_flagged(tmp_path, client):
    """표본 N<10 에서도 절대 임계(30) 아래는 outlier 가 아니어야 한다."""
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    for i, count in enumerate([5, 10, 15, 25], start=1):
        _save(
            reports_dir, f"task-{i:03d}",
            cost_usd=0.01, cost_estimation_quality="exact",
            models_used={"implementer": "openai/gpt-5"},
            iteration_count_by_role={"implementer": count},
        )
    res = client.get(
        "/api/dashboard/summary",
        params={"reports_dir": str(reports_dir), "tasks_path": str(tmp_path / "tasks.yaml")},
    )
    assert res.json()["outlier_tasks"] == []


def test_outlier_picks_role_with_largest_exceedance_not_raw_count(tmp_path, client):
    """두 역할이 동시에 초과할 때 raw count 가 아닌 exceedance 로 대표 역할 선택.

    분포 설계:
      - implementer counts (N=10): [3]*9 + [30] → μ=5.7, σ≈8.1, threshold≈21.9
        → 한 태스크에서 count=30 → exceedance ≈ 8.1
      - reviewer counts    (N=10): [50]*9 + [60] → μ=51, σ=3, threshold=57
        → 같은 태스크에서 count=60 → exceedance = 3

    raw count 비교이면 60(reviewer) 이 30(implementer) 보다 크므로 reviewer 가
    대표로 잘못 뽑힌다. exceedance 비교이면 implementer(8.1) > reviewer(3) 이므로
    implementer 가 올바른 대표가 된다.
    """
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    # 배경 분포 (9건씩)
    for i in range(1, 10):
        _save(
            reports_dir, f"task-{i:03d}",
            cost_usd=0.01, cost_estimation_quality="exact",
            models_used={"implementer": "openai/gpt-5", "reviewer": "openai/gpt-5"},
            iteration_count_by_role={"implementer": 3, "reviewer": 50},
        )
    # 두 역할 모두 임계를 초과하는 문제 태스크
    _save(
        reports_dir, "task-010",
        cost_usd=0.01, cost_estimation_quality="exact",
        models_used={"implementer": "openai/gpt-5", "reviewer": "openai/gpt-5"},
        iteration_count_by_role={"implementer": 30, "reviewer": 60},
    )
    res = client.get(
        "/api/dashboard/summary",
        params={"reports_dir": str(reports_dir), "tasks_path": str(tmp_path / "tasks.yaml")},
    )
    outliers = res.json()["outlier_tasks"]
    task_010 = [o for o in outliers if o["task_id"] == "task-010"]
    assert len(task_010) == 1
    assert task_010[0]["role"] == "implementer"
    assert task_010[0]["value"] == 30


def test_outlier_prefers_iteration_count_over_token_threshold(tmp_path, client):
    """같은 태스크가 두 기준 모두 해당할 때 high_iteration_count 로만 1건 보고.

    분포는 위 high_iteration 테스트와 동일 ([5×9, 60]) — 정상값 풍부해서 60 이 outlier.
    60 번째 태스크만 단일 iter 토큰도 초과시킨다.
    """
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    counts = [5, 5, 5, 5, 5, 5, 5, 5, 5, 60]
    for i, count in enumerate(counts, start=1):
        _save(
            reports_dir, f"task-{i:03d}",
            cost_usd=0.01, cost_estimation_quality="exact",
            models_used={"implementer": "openai/gpt-5"},
            iteration_count_by_role={"implementer": count},
            max_single_iteration_tokens=80_000 if count == 60 else 5_000,
        )
    res = client.get(
        "/api/dashboard/summary",
        params={"reports_dir": str(reports_dir), "tasks_path": str(tmp_path / "tasks.yaml")},
    )
    body = res.json()
    outliers = body["outlier_tasks"]
    task_010_entries = [o for o in outliers if o["task_id"] == "task-010"]
    # 두 기준 모두 해당해도 1건만 보고
    assert len(task_010_entries) == 1
    assert task_010_entries[0]["reason"] == "high_iteration_count"
