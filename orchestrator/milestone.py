"""
orchestrator/milestone.py — 마일스톤 보고서 생성

파이프라인 1회 실행(run.py) 완료 시 전체 실행 결과를 Sonnet이 요약한다.
Task Report 데이터를 기반으로 마크다운 보고서를 생성한다.

저장 위치: agent-data/reports/milestones/YYYY-MM-DD-HHMMSS.md

사용 예시:
    from orchestrator.milestone import generate_milestone_report

    content, path = generate_milestone_report(
        reports=task_reports,
        llm_fn=my_llm_fn,
        run_label="유틸리티 모듈 5개",
    )
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from orchestrator.report import TaskReport

logger = logging.getLogger(__name__)

_MILESTONES_DIR = Path("agent-data/reports/milestones")

_SYSTEM_PROMPT = """\
당신은 소프트웨어 개발 파이프라인의 실행 결과를 분석하는 전문가입니다.
제공된 태스크 실행 데이터를 바탕으로 마일스톤 보고서를 작성하세요.

보고서는 다음 구조를 따르세요:
1. 실행 요약 (한 문단)
2. 태스크별 결과 표
3. 주요 지표
4. 패턴 및 인사이트 (성공/실패 패턴, 개선 포인트)
5. 다음 단계 제안

간결하고 실행 가능한 인사이트에 집중하세요.
"""


# ── 집계 ──────────────────────────────────────────────────────────────────────


def collect_run_stats(reports: list[TaskReport]) -> dict:
    """Task Report 목록에서 실행 통계를 계산한다."""
    total = len(reports)
    completed = sum(1 for r in reports if r.status == "COMPLETED")
    failed = total - completed
    success_rate = round(completed / total * 100) if total else 0

    first_try = sum(1 for r in reports if r.test_pass_first_try)
    first_try_rate = round(first_try / completed * 100) if completed else 0

    approved = sum(1 for r in reports if r.reviewer_verdict == "APPROVED")
    total_elapsed = sum(r.time_elapsed_seconds for r in reports)
    avg_elapsed = round(total_elapsed / total, 1) if total else 0
    total_tests = sum(r.test_count for r in reports)
    total_retries = sum(r.retry_count for r in reports)

    return {
        "total": total,
        "completed": completed,
        "failed": failed,
        "success_rate": success_rate,
        "first_try_rate": first_try_rate,
        "approved": approved,
        "total_elapsed_seconds": round(total_elapsed, 1),
        "avg_elapsed_seconds": avg_elapsed,
        "total_tests": total_tests,
        "total_retries": total_retries,
    }


# ── 프롬프트 빌더 ─────────────────────────────────────────────────────────────


def build_milestone_prompt(
    reports: list[TaskReport],
    stats: dict,
    run_label: str,
) -> str:
    """Sonnet에게 전달할 사용자 프롬프트를 생성한다."""
    lines = [
        f"## 파이프라인 실행: {run_label}",
        f"실행 일시: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "### 전체 통계",
        f"- 전체 태스크: {stats['total']}개",
        f"- 완료: {stats['completed']}개 / 실패: {stats['failed']}개 (성공률 {stats['success_rate']}%)",
        f"- 첫 시도 성공률: {stats['first_try_rate']}% (재시도 없이 통과)",
        f"- 리뷰 APPROVED: {stats['approved']}개",
        f"- 총 실행 시간: {stats['total_elapsed_seconds']}초 / 태스크 평균: {stats['avg_elapsed_seconds']}초",
        f"- 총 테스트 수: {stats['total_tests']}개",
        f"- 총 재시도 횟수: {stats['total_retries']}회",
        "",
        "### 태스크별 결과",
    ]

    for r in reports:
        status_icon = "✅" if r.status == "COMPLETED" else "❌"
        verdict = f" [{r.reviewer_verdict}]" if r.reviewer_verdict else ""
        retry = f" (재시도 {r.retry_count}회)" if r.retry_count > 0 else ""
        lines.append(
            f"- {status_icon} [{r.task_id}] {r.title}{verdict}{retry}"
            f" — {r.test_count}개 테스트, {r.time_elapsed_seconds}초"
        )
        if r.failure_reasons:
            lines.append(f"  실패 원인: {r.failure_reasons[0][:120]}")

    lines += [
        "",
        "위 데이터를 바탕으로 마일스톤 보고서를 작성하세요.",
    ]

    return "\n".join(lines)


# ── 생성 및 저장 ──────────────────────────────────────────────────────────────


def generate_milestone_report(
    reports: list[TaskReport],
    llm_fn: Callable[[str, str], str],
    run_label: str = "파이프라인 실행",
    milestones_dir: Path = _MILESTONES_DIR,
) -> tuple[str, Path]:
    """
    Task Report 목록으로 마일스톤 보고서를 생성하고 저장한다.

    Args:
        reports:       이번 실행의 TaskReport 목록
        llm_fn:        (system_prompt, user_prompt) → str 형태의 LLM 호출 함수
        run_label:     보고서 제목에 쓸 실행 라벨 (예: "유틸리티 모듈 5개")
        milestones_dir: 저장 디렉토리

    Returns:
        (markdown_content, saved_path)
    """
    if not reports:
        logger.warning("마일스톤 보고서: Task Report가 없습니다.")
        return "", Path()

    stats = collect_run_stats(reports)
    prompt = build_milestone_prompt(reports, stats, run_label)

    logger.info("마일스톤 보고서 생성 중 (태스크 %d개)...", len(reports))
    content = llm_fn(_SYSTEM_PROMPT, prompt)

    path = save_milestone_report(content, milestones_dir)
    return content, path


def save_milestone_report(
    content: str,
    milestones_dir: Path = _MILESTONES_DIR,
) -> Path:
    """마일스톤 보고서를 파일로 저장하고 경로를 반환한다."""
    milestones_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    path = milestones_dir / f"{timestamp}.md"
    path.write_text(content, encoding="utf-8")
    logger.info("마일스톤 보고서 저장: %s", path)
    return path


def load_milestone_reports(
    milestones_dir: Path = _MILESTONES_DIR,
) -> list[dict]:
    """저장된 마일스톤 보고서 목록을 최신순으로 반환한다."""
    if not milestones_dir.exists():
        return []
    result = []
    for path in sorted(milestones_dir.glob("*.md"), reverse=True):
        result.append({
            "filename": path.name,
            "path": str(path),
            "created_at": path.stem,  # YYYY-MM-DD-HHMMSS
        })
    return result
