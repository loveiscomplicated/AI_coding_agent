"""
backend/routers/reports.py — 실행 요약(execution_brief) 생성 API + 주간 보고서 API

POST /api/execution-brief
  마지막 회의 이후의 Task Report들을 Sonnet이 요약하여 반환한다.
  회의 시작 시 Opus 시스템 프롬프트에 주입하기 위한 용도.

GET /api/project-structure
  PROJECT_STRUCTURE.md 내용을 반환한다.
  파일이 없으면 빈 문자열을 반환한다.

POST /api/reports/weekly
  지정한 ISO 주차의 주간 보고서를 생성(또는 재생성)하여 저장한다.

GET /api/reports/weekly
  저장된 주간 보고서 목록을 반환한다.

GET /api/reports/weekly/{year}/{week}
  특정 주의 주간 보고서 마크다운을 반환한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import anthropic
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import ANTHROPIC_API_KEY
from orchestrator.report import load_reports
from orchestrator.weekly import (
    current_iso_week,
    generate_weekly_report,
    list_weekly_reports,
    load_weekly_report,
)

router = APIRouter()

_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

_BRIEF_SYSTEM = """\
당신은 소프트웨어 개발 에이전트 시스템의 성능 분석가입니다.

주어진 Task Report YAML 데이터를 분석하여 주간 회의에 필요한 핵심 요약을 작성하세요.

요약 형식 (마크다운):
## 실행 요약

**완료된 태스크** (N개)
- task-xxx: [제목] — 성공/실패, 소요: Xs, 재시도: N회

**핵심 지표**
- 전체 성공률: N%
- 첫 시도 성공률: N%
- 평균 소요 시간: Xs

**이슈 & 패턴**
- [발견된 패턴이나 반복 이슈]

**논의 제안**
- [회의에서 다룰 만한 사항]

없는 정보는 해당 항목을 생략하세요. 간결하게 유지하세요."""


class ExecutionBriefRequest(BaseModel):
    since: str | None = None        # ISO 8601 datetime (마지막 회의 시각)
    reports_dir: str = "data/reports"


@router.post("/execution-brief")
async def generate_execution_brief(body: ExecutionBriefRequest) -> dict[str, Any]:
    """
    Task Report들을 수집하여 Sonnet이 요약한 execution_brief를 반환한다.
    보고서가 없으면 빈 문자열을 반환한다.
    """
    from datetime import datetime, timezone
    since_dt = None
    if body.since:
        try:
            since_dt = datetime.fromisoformat(body.since)
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    reports = load_reports(since=since_dt, reports_dir=Path(body.reports_dir))
    if not reports:
        return {"brief": ""}

    # 보고서 데이터를 텍스트로 직렬화
    lines = []
    for r in reports:
        lines.append(
            f"task_id: {r.task_id}\n"
            f"title: {r.title}\n"
            f"status: {r.status}\n"
            f"completed_at: {r.completed_at}\n"
            f"retry_count: {r.retry_count}\n"
            f"test_count: {r.test_count}\n"
            f"test_pass_first_try: {r.test_pass_first_try}\n"
            f"reviewer_verdict: {r.reviewer_verdict}\n"
            f"time_elapsed_seconds: {r.time_elapsed_seconds}\n"
            f"failure_reasons: {r.failure_reasons}\n"
        )
    report_text = "\n---\n".join(lines)

    response = await _client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=_BRIEF_SYSTEM,
        messages=[{"role": "user", "content": report_text}],
    )
    brief = response.content[0].text.strip() if response.content else ""
    return {"brief": brief}


@router.get("/project-structure")
def get_project_structure(path: str = "PROJECT_STRUCTURE.md") -> dict[str, Any]:
    """PROJECT_STRUCTURE.md 내용을 반환한다. 파일이 없으면 빈 문자열."""
    p = Path(path)
    if not p.exists():
        return {"content": "", "exists": False}
    return {"content": p.read_text(encoding="utf-8"), "exists": True}


# ── 주간 보고서 ───────────────────────────────────────────────────────────────

class WeeklyReportRequest(BaseModel):
    year: int | None = None   # None이면 현재 주
    week: int | None = None
    reports_dir: str = "data/reports"


def _make_llm_fn():
    """동기 Anthropic 호출 래퍼 (generate_weekly_report가 동기 llm_fn을 기대함)."""
    import anthropic as _anthropic
    sync_client = _anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    def llm_fn(system: str, user: str) -> str:
        resp = sync_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return resp.content[0].text.strip() if resp.content else ""

    return llm_fn


@router.post("/reports/weekly")
def create_weekly_report(body: WeeklyReportRequest) -> dict[str, Any]:
    """ISO 주차 주간 보고서를 생성하고 저장한다."""
    try:
        content, save_path = generate_weekly_report(
            llm_fn=_make_llm_fn(),
            year=body.year,
            week=body.week,
            reports_dir=Path(body.reports_dir),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    from orchestrator.weekly import current_iso_week
    year = body.year
    week = body.week
    if year is None or week is None:
        year, week = current_iso_week()

    return {
        "year": year,
        "week": week,
        "path": str(save_path),
        "content": content,
    }


@router.get("/reports/weekly")
def list_weekly() -> dict[str, Any]:
    """저장된 주간 보고서 목록을 반환한다."""
    return {"reports": list_weekly_reports()}


@router.get("/reports/weekly/{year}/{week}")
def get_weekly_report(year: int, week: int) -> dict[str, Any]:
    """특정 주의 주간 보고서를 반환한다."""
    content = load_weekly_report(year, week)
    if content is None:
        raise HTTPException(
            status_code=404,
            detail=f"{year}-W{week:02d} 주간 보고서가 없습니다.",
        )
    return {"year": year, "week": week, "content": content}
