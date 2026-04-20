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
from orchestrator.task import Task, TaskStatus, save_tasks

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


# ── 자동 분해 래퍼 (intervention 4회차 실패 후 호출) ─────────────────────────


class SplitTaskError(RuntimeError):
    """`split_task()` 이 LLM 응답을 받아들일 수 없을 때 발생."""


_WORD_RE = re.compile(r"[A-Za-z\uAC00-\uD7A3_][A-Za-z\uAC00-\uD7A3_0-9]{2,}")


def _content_words(text: str) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(text or "")}


def _validate_subtask_scope(
    parent: Task,
    payload: dict,
    subtask_idx: int,
    min_criterion_overlap: float = 0.75,
) -> None:
    """하위 태스크 payload 가 부모 범위를 벗어나지 않는지 검증한다.

    - `target_files` 는 엄격한 부분집합이어야 한다 (LLM 이 새 파일을 꾸며내지 못하도록).
    - 각 `acceptance_criteria` 항목은 부모의 "개별 acceptance criterion" 중 하나에
      매핑될 수 있어야 한다. 매핑 기준은:
        * 부모 criterion + title/description 단어 집합이 child 단어의
          `min_criterion_overlap` 이상을 덮어야 함
      이렇게 해야 parent 문구 일부를 앞에 복사한 뒤 새 스코프를 덧붙이는 패턴을
      막을 수 있다.

    Raises:
        SplitTaskError: 범위 위반 발견 시.
    """
    parent_files = set(parent.target_files)
    child_files = payload.get("target_files") or []
    if not isinstance(child_files, list):
        raise SplitTaskError(
            f"하위 태스크 {subtask_idx} target_files 형식 오류 (리스트 아님)"
        )
    for f in child_files:
        if f not in parent_files:
            raise SplitTaskError(
                f"하위 태스크 {subtask_idx} target_files 범위 이탈: "
                f"{f!r} ∉ {sorted(parent_files)}"
            )

    context_words = _content_words(" ".join([parent.title, parent.description]))
    parent_criterion_words = [
        _content_words(criterion) | context_words
        for criterion in parent.acceptance_criteria
        if criterion.strip()
    ]
    if not parent_criterion_words:
        parent_criterion_words = [context_words]

    child_criteria = payload.get("acceptance_criteria") or []
    if not isinstance(child_criteria, list):
        raise SplitTaskError(
            f"하위 태스크 {subtask_idx} acceptance_criteria 형식 오류 (리스트 아님)"
        )
    for c in child_criteria:
        c_text = str(c).strip()
        if not c_text:
            continue
        c_words = _content_words(c_text)
        if not c_words:
            continue
        best_overlap = max(
            len(c_words & parent_words) / len(c_words)
            for parent_words in parent_criterion_words
        )
        if best_overlap < min_criterion_overlap:
            raise SplitTaskError(
                f"하위 태스크 {subtask_idx} acceptance_criteria 범위 이탈 "
                f"(best_overlap={best_overlap:.2f} < {min_criterion_overlap}): {c_text!r}"
            )


def split_task(
    task: Task,
    all_tasks: list[Task],
    spec_content: str,
    llm: BaseLLMClient,
    tasks_yaml_path: str | Path,
    orch_report: str = "",
) -> list[Task]:
    """`redesign_task()` 를 호출해 분할(split) 결과만 수용하고 tasks.yaml 에 커밋한다.

    intervention 4회차 실패 직후 `orchestrator/run.py` 에서 호출되는 얇은 래퍼.
    LLM 프롬프트·JSON 계약은 `redesign_task()` 와 완전히 공유한다.

    동작:
      1) `redesign_task(...)` 호출 → action='split' + 2~3개 하위 태스크만 수용
      2) 하위 태스크 id 를 `{orig.id}-a`, `-b`, `-c` 로 정규화 (기존 id 충돌 시 ValueError)
      3) `depends_on` 을 순차 체인으로 구성 — a = orig.depends_on, b = [a], c = [b]
      4) 원 태스크 `status = SUPERSEDED` + `failure_reason` 보강
      5) `all_tasks` 의 원 태스크 인덱스 **직후** 에 하위 태스크 삽입
      6) `save_tasks(all_tasks, tasks_yaml_path)` 로 파일 커밋
      7) 하위 Task 객체 리스트 반환

    Raises:
        SplitTaskError: LLM 응답이 split 이 아니거나, 하위 태스크 개수가 2~3이 아니거나,
                        새 id 가 기존과 충돌할 때.
    """
    result = redesign_task(task, all_tasks, spec_content, llm, orch_report=orch_report)

    if not result.success:
        raise SplitTaskError(f"LLM 재설계 실패: {result.error or '(사유 없음)'}")
    if result.action != "split":
        raise SplitTaskError(
            f"split 이 아닌 재설계 결과 거부 (action={result.action!r})"
        )
    if not (2 <= len(result.tasks) <= 3):
        raise SplitTaskError(
            f"하위 태스크 개수 이상 (len={len(result.tasks)}, 허용: 2~3)"
        )

    for idx, payload in enumerate(result.tasks):
        _validate_subtask_scope(task, payload, idx)

    suffixes = ["a", "b", "c"][: len(result.tasks)]
    existing_ids = {t.id for t in all_tasks}
    sub_ids = [f"{task.id}-{s}" for s in suffixes]
    for sid in sub_ids:
        if sid in existing_ids:
            raise SplitTaskError(f"하위 태스크 id 충돌: {sid}")

    subtasks: list[Task] = []
    for idx, (sid, payload) in enumerate(zip(sub_ids, result.tasks)):
        if idx == 0:
            deps = list(task.depends_on)
        else:
            deps = [sub_ids[idx - 1]]
        subtask = Task(
            id=sid,
            title=str(payload.get("title") or f"{task.title} ({suffixes[idx]})"),
            description=str(payload.get("description") or task.description),
            acceptance_criteria=list(payload.get("acceptance_criteria") or task.acceptance_criteria),
            target_files=list(payload.get("target_files") or task.target_files),
            test_framework=str(payload.get("test_framework") or task.test_framework),
            depends_on=deps,
            task_type=str(payload.get("task_type") or task.task_type),
            language=str(payload.get("language") or task.language),
        )
        subtasks.append(subtask)

    task.status = TaskStatus.SUPERSEDED
    task.failure_reason = (
        f"{task.failure_reason} [자동 분해: {', '.join(sub_ids)}]".strip()
    )

    try:
        orig_index = next(i for i, t in enumerate(all_tasks) if t.id == task.id)
    except StopIteration as e:
        raise SplitTaskError(f"원 태스크 {task.id} 가 all_tasks 에 없음") from e
    for offset, sub in enumerate(subtasks, start=1):
        all_tasks.insert(orig_index + offset, sub)

    save_tasks(all_tasks, tasks_yaml_path)
    logger.info(
        "[%s] 자동 분해 완료 → %s (SUPERSEDED 보존)",
        task.id, ", ".join(sub_ids),
    )
    return subtasks
