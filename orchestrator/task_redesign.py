"""
orchestrator/task_redesign.py — 실패한 태스크를 LLM이 재설계하는 모듈

LLM이 spec.md + 전체 tasks.yaml을 컨텍스트로 받아 실패한 태스크를 분석하고,
다음 중 하나를 수행한다:
  - 태스크 단순화: 기존 태스크의 description/acceptance_criteria/target_files 수정
  - 태스크 분할: 기존 태스크를 2~3개의 더 작은 태스크로 분할

사용 예:
    from orchestrator.task_redesign import redesign_task, create_redesign_llm

    llm = create_redesign_llm(provider, model)
    result = redesign_task(task, all_tasks, spec_content, llm)
    if result.success:
        # result.tasks — 교체할 Task dict 목록
        # result.explanation — LLM 설명
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from llm import BaseLLMClient, LLMConfig, Message, create_client
from orchestrator.task import Task

logger = logging.getLogger(__name__)

_REDESIGN_SYSTEM = """\
당신은 AI 코딩 에이전트 파이프라인의 중앙 오케스트레이터입니다.
하위 에이전트가 최대 반복 횟수를 초과하거나 반복적으로 실패하여 태스크를 완수하지 못했습니다.
이는 태스크가 너무 크거나, 스펙이 모호하거나, 수락 기준이 구현하기 너무 복잡하다는 신호입니다.

spec.md(프로젝트 스펙), 전체 tasks.yaml(현재 태스크 목록), 실패한 태스크 정보를 분석하여
태스크를 재설계하세요.

[재설계 전략]
1. 분할(SPLIT): 태스크가 너무 많은 일을 한 번에 하려 할 때. 2~3개의 작은 태스크로 쪼갠다.
   - 인터페이스/모델 정의 → 구현 → 테스트 순으로 분리
   - 각 태스크는 target_files 3개 이하
2. 단순화(SIMPLIFY): 수락 기준이 너무 엄격하거나 구현 범위가 과도할 때.
   - 핵심 기능에만 집중하도록 acceptance_criteria 줄이기
   - 복잡한 엣지 케이스를 별도 태스크로 분리

[출력 형식 — 반드시 아래 JSON만 출력, 마크다운 코드블록 없이]
{
  "action": "split" | "simplify",
  "explanation": "재설계 이유와 전략 설명 (2~4문장)",
  "tasks": [
    {
      "id": "task-XXX",
      "title": "...",
      "description": "...",
      "acceptance_criteria": ["...", "..."],
      "target_files": ["..."],
      "depends_on": [],
      "task_type": "backend"
    }
  ]
}

[규칙]
- 분할 시: 원래 태스크 id는 첫 번째 새 태스크에 재사용하거나, task-XXX-a, task-XXX-b 형식 사용
- 분할 시: depends_on을 올바르게 설정 (분할된 태스크 간 순서 명시)
- 분할 시: 기존 태스크가 depends_on하던 태스크를 첫 번째 새 태스크가 이어받을 것
- 단순화 시: tasks 배열에 태스크 하나만 포함
- 기존 tasks.yaml의 다른 태스크 id 체계와 일관성 유지
- 컨텍스트 문서에 없는 기능 추가 금지
- 각 태스크의 acceptance_criteria: 3~5개, target_files: 1~3개
"""


@dataclass
class RedesignResult:
    success: bool
    action: str           # "split" | "simplify" | ""
    explanation: str
    tasks: list[dict] = field(default_factory=list)
    error: str = ""
    raw: str = ""


def create_redesign_llm(provider: str, model: str) -> BaseLLMClient:
    """재설계 LLM 클라이언트를 생성한다."""
    return create_client(
        provider,
        LLMConfig(
            model=model,
            system_prompt=_REDESIGN_SYSTEM,
            max_tokens=4096,
        ),
    )


def _extract_text(response) -> str:
    for block in response.content:
        if isinstance(block, dict) and block.get("type") == "text":
            return block["text"]
        if hasattr(block, "type") and block.type == "text":
            return block.text
    return ""


def redesign_task(
    task: Task,
    all_tasks: list[Task],
    spec_content: str,
    llm: BaseLLMClient,
    orch_report: str = "",
) -> RedesignResult:
    """
    실패한 태스크를 LLM이 분석하여 재설계한다.

    Args:
        task         : 실패한 태스크
        all_tasks    : tasks.yaml의 전체 태스크 목록
        spec_content : spec.md 등 컨텍스트 문서 내용 (없으면 빈 문자열)
        llm          : 재설계 LLM 클라이언트
        orch_report  : 오케스트레이터가 생성한 마크다운 실패 보고서 (있으면 raw 로그 대신 사용)

    Returns:
        RedesignResult — success=True면 tasks에 교체할 태스크 dict 목록
    """
    tasks_summary = "\n".join(
        f"  - {t.id}: {t.title} [{t.status.value}]"
        + (f" (depends_on: {t.depends_on})" if t.depends_on else "")
        for t in all_tasks
    )

    # 오케스트레이터 보고서가 있으면 그걸 사용, 없으면 raw 실패 정보로 폴백
    if orch_report:
        failure_section = f"""## 오케스트레이터 실패 분석 보고서
{orch_report[:4000]}"""
    else:
        failure_section = f"""## 실패 원인
{(task.failure_reason or '알 수 없음')[:1000]}

## 마지막 오류 로그
{(task.last_error or '(없음)')[:1500]}"""

    user_msg = f"""## 프로젝트 스펙 (spec.md)
{spec_content[:3000] if spec_content else "(없음)"}

## 현재 태스크 목록 (tasks.yaml 요약)
{tasks_summary}

## 실패한 태스크 상세
id: {task.id}
제목: {task.title}
설명: {task.description}
수락 기준:
{task.acceptance_criteria_text()}
대상 파일: {', '.join(task.target_files)}
depends_on: {task.depends_on}
task_type: {task.task_type}

{failure_section}

위 실패한 태스크를 재설계하여 JSON으로 반환하세요."""

    try:
        response = llm.chat([Message(role="user", content=user_msg)])
        raw = _extract_text(response)
    except Exception as e:
        logger.error("[%s] 태스크 재설계 LLM 호출 실패: %s", task.id, e)
        return RedesignResult(success=False, action="", explanation="", error=str(e))

    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error("[%s] 태스크 재설계 JSON 파싱 실패: %s\n%s", task.id, e, raw[:300])
        return RedesignResult(
            success=False, action="", explanation="",
            error=f"LLM 응답 파싱 실패: {e}", raw=raw,
        )

    action = data.get("action", "")
    explanation = data.get("explanation", "")
    tasks = data.get("tasks", [])

    if not isinstance(tasks, list) or not tasks:
        return RedesignResult(
            success=False, action=action, explanation=explanation,
            error="재설계 태스크 목록이 비어 있습니다.", raw=raw,
        )

    # 기본 유효성 검사
    for t in tasks:
        if not t.get("id") or not t.get("title"):
            return RedesignResult(
                success=False, action=action, explanation=explanation,
                error="재설계 태스크에 id 또는 title이 없습니다.", raw=raw,
            )

    logger.info(
        "[%s] 태스크 재설계 완료: action=%s, 새 태스크 %d개",
        task.id, action, len(tasks),
    )
    return RedesignResult(
        success=True, action=action, explanation=explanation,
        tasks=tasks, raw=raw,
    )
