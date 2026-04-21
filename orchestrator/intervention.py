"""
orchestrator/intervention.py — 태스크 실패 시 중앙 오케스트레이터 개입 로직

흐름:
  1. 에이전트 파이프라인이 태스크 실패 반환
  2. analyze() 호출 → Sonnet이 실패 원인 분석 → RETRY(힌트) or GIVE_UP 결정
  3. RETRY면 힌트를 task.last_error에 주입 후 파이프라인 재시도
  4. max_retries 초과 또는 GIVE_UP → generate_report() 호출
  5. 상세 보고서를 파일로 저장 + Discord/대시보드에 전달
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from agents.roles import RoleModelConfig, resolve_model_for_role, ROLE_INTERVENTION
from llm import BaseLLMClient, LLMConfig, Message, create_client

from orchestrator.task import Task


# ── 실패 유형 분류 (LLM 미호출) ──────────────────────────────────────────────


class FailureType(Enum):
    ENV_ERROR = "env_error"
    UNSUPPORTED_LANGUAGE = "unsupported_language"
    DEPENDENCY_MISSING = "dependency_missing"
    MAX_ITER_EXCEEDED = "max_iter_exceeded"
    REVIEWER_INFRA_ERROR = "reviewer_infra_error"
    # pytest --collect-only 게이트가 import/syntax 오류를 감지한 경우.
    # 원인이 대부분 코드 파일이므로 analyze() LLM 이 파일별 수정 힌트 생성.
    COLLECTION_ERROR = "collection_error"
    # 0개 수집: TestWriter 가 테스트 파일 자체를 못 썼거나 경로/파일명이 틀림.
    # write-like 계열처럼 고정 힌트 재시도 후 미해결 시 포기.
    NO_TESTS_COLLECTED = "no_tests_collected"
    LOGIC_ERROR = "logic_error"


def classify_failure(failure_reason: str, test_stdout: str = "") -> FailureType:
    """LLM 호출 없이 문자열 패턴으로 실패 유형을 분류한다."""
    lower_reason = failure_reason.lower()

    # Reviewer 인프라 장애(LLM 호출 실패 / 파싱 불가)는 코드 문제가 아니므로
    # LOGIC_ERROR 와 구분한다. Implementer 를 다시 돌리는 건 토큰 낭비.
    if "[reviewer_infra_error]" in lower_reason:
        return FailureType.REVIEWER_INFRA_ERROR

    # Reviewer 거부는 test_stdout 내용(pytest 수집 오류 등)과 무관하게 LOGIC_ERROR로 처리.
    # test_stdout에 섞인 ModuleNotFoundError 등으로 ENV_ERROR로 오분류되는 것을 방지.
    if "reviewer changes_requested" in lower_reason:
        return FailureType.LOGIC_ERROR

    combined = f"{failure_reason}\n{test_stdout}".lower()

    if "[unsupported_language]" in combined:
        return FailureType.UNSUPPORTED_LANGUAGE
    if "[dependency_missing]" in combined:
        return FailureType.DEPENDENCY_MISSING
    # [COLLECTION_ERROR] 는 구체적 원인이 ImportError/ModuleNotFound 일 수도, 단순
    # SyntaxError 일 수도 있다. import 계열이면 ENV_ERROR 분기(아래)에 먼저 잡혀
    # GIVE_UP 경로로 가고, 그 외 문법 오류는 COLLECTION_ERROR → analyze() 로 간다.
    if "[no_tests_collected]" in combined:
        return FailureType.NO_TESTS_COLLECTED
    if "internalerror" in combined:
        return FailureType.ENV_ERROR
    if any(p in combined for p in ["importerror", "modulenotfounderror", "no module named"]):
        return FailureType.ENV_ERROR
    if "[collection_error]" in combined:
        return FailureType.COLLECTION_ERROR
    if "[max_iter]" in combined:
        return FailureType.MAX_ITER_EXCEEDED
    # 쓰기 미수행 / 산출물 누락 계열 — 모두 MAX_ITER_EXCEEDED 로 묶어
    # "바로 write_file 호출하라" 고정 힌트 + 2회차 give_up 흐름을 재사용한다.
    #   [TARGET_MISSING]    Implementer 가 target_files 를 채우지 않음
    #   [NO_WRITE]          TestWriter 가 write_file/edit_file 을 한 번도 호출하지 않음
    #   [TEST_MISSING]      TestWriter 가 tests/ 에 파일을 쓰지 못함 (잘못된 경로 포함)
    #   [TEST_SKELETON_ONLY] 선주입 스켈레톤을 그대로 두고 종료
    #   [NO_TEST_FUNCTIONS] test_* 함수 없이 종료
    # (TEST_SYNTAX_ERROR 는 LOGIC_ERROR 로 떨어져 analyze() LLM 이 파일별 수정
    #  힌트를 생성한다 — 문법 오류는 "write 를 더 하라" 가 아니라 "어디를 고치라"
    #  가 필요하다.)
    _WRITE_LIKE_PREFIXES = (
        "[target_missing]", "[no_write]", "[test_missing]",
        "[test_skeleton_only]", "[no_test_functions]",
    )
    if any(p in combined for p in _WRITE_LIKE_PREFIXES):
        return FailureType.MAX_ITER_EXCEEDED
    return FailureType.LOGIC_ERROR


def classify_and_analyze(
    task: Task,
    failure_reason: str,
    attempt: int,
    test_stdout: str = "",
    previous_hints: list[str] | None = None,
    role_models: dict[str, RoleModelConfig] | None = None,
) -> AnalysisResult:
    """
    실패 유형을 먼저 분류하고, LOGIC_ERROR만 LLM analyze()로 넘긴다.

    ENV_ERROR / UNSUPPORTED_LANGUAGE / DEPENDENCY_MISSING → 즉시 GIVE_UP
    MAX_ITER_EXCEEDED → 첫 재시도는 RETRY(고정 힌트), 이후 GIVE_UP
    LOGIC_ERROR → 기존 analyze() 호출
    """
    ft = classify_failure(failure_reason, test_stdout)
    logger.info("[%s] 실패 유형 분류: %s (시도 %d)", task.id, ft.value, attempt)

    if ft in (FailureType.ENV_ERROR, FailureType.UNSUPPORTED_LANGUAGE, FailureType.DEPENDENCY_MISSING):
        reason = f"[{ft.value}] 재시도 불가 — {failure_reason[:200]}"
        logger.warning("[%s] 즉시 GIVE_UP: %s", task.id, reason[:120])
        return AnalysisResult(should_retry=False, hint=reason, raw=f"[fast-path] {ft.value}")

    if ft == FailureType.REVIEWER_INFRA_ERROR:
        # 코드가 아니라 Reviewer LLM 자체가 실패한 경우.
        # Implementer 재실행은 토큰 낭비이므로 GIVE_UP, 사용자에게 원인 통보.
        reason = (
            "[reviewer_infra_error] Reviewer 모델 호출 자체가 실패했습니다. "
            "모델 설정(API 키·모델명·SDK 호환성)을 확인한 뒤 재실행하세요.\n"
            f"상세: {failure_reason[:300]}"
        )
        logger.warning("[%s] Reviewer 인프라 장애 → GIVE_UP", task.id)
        return AnalysisResult(should_retry=False, hint=reason, raw="[fast-path] reviewer_infra_error")

    if ft == FailureType.MAX_ITER_EXCEEDED:
        if attempt >= 2:  # 이미 1회 이상 재시도했으면 포기
            reason = "MAX_ITER 2회 이상 반복 — 태스크 스펙 재검토 필요"
            logger.warning("[%s] MAX_ITER GIVE_UP (시도 %d)", task.id, attempt)
            return AnalysisResult(should_retry=False, hint=reason, raw="[fast-path] max_iter_exceeded")
        hint = (
            "이전 시도에서 파일을 생성하지 못했습니다. "
            "반드시 write_file을 호출하여 코드를 작성하세요. "
            "탐색을 최소화하고 즉시 구현을 시작하세요."
        )
        logger.info("[%s] MAX_ITER RETRY (고정 힌트, 시도 %d)", task.id, attempt)
        return AnalysisResult(should_retry=True, hint=hint, raw="[fast-path] max_iter_retry")

    if ft == FailureType.NO_TESTS_COLLECTED:
        # pytest/go/jest/gradle 모두: 테스트 파일이 없거나 discovery 규칙과
        # 파일명/위치가 안 맞음. TestWriter 가 다시 올바른 경로에 쓰는 것이
        # 해결책. write-like 와 같은 재시도 패턴 사용.
        if attempt >= 2:
            reason = "[NO_TESTS_COLLECTED] 2회 재시도 후에도 테스트 미수집 — 스펙·경로 재검토 필요"
            logger.warning("[%s] NO_TESTS_COLLECTED GIVE_UP (시도 %d)", task.id, attempt)
            return AnalysisResult(should_retry=False, hint=reason, raw="[fast-path] no_tests_collected_give_up")
        hint = (
            "pytest --collect-only 가 0개 수집했습니다. 원인 후보:\n"
            " 1) 테스트 파일이 아예 없음 → tests/ 밑에 test_*.py 생성\n"
            " 2) 파일명/함수명이 discovery 규칙과 불일치 → test_ 접두사 확인\n"
            " 3) 테스트가 src/ 나 다른 경로에 잘못 배치됨 → tests/ 로 이동\n"
            "반드시 write_file 로 tests/ 아래에 test_*.py 를 생성하세요."
        )
        logger.info("[%s] NO_TESTS_COLLECTED RETRY (고정 힌트, 시도 %d)", task.id, attempt)
        return AnalysisResult(should_retry=True, hint=hint, raw="[fast-path] no_tests_collected_retry")

    if ft == FailureType.COLLECTION_ERROR:
        # import/syntax 오류 — ENV_ERROR 로 이미 걸러지지 않은 경우는 문법 오류 가능성.
        # LLM analyze() 에 파일별 수정 힌트 생성을 맡긴다 (LOGIC_ERROR 와 동일 흐름).
        logger.info("[%s] COLLECTION_ERROR → LLM analyze (시도 %d)", task.id, attempt)
        result = analyze(task, failure_reason, attempt, previous_hints=previous_hints, role_models=role_models)
    else:
        # LOGIC_ERROR → 기존 LLM 분석
        result = analyze(task, failure_reason, attempt, previous_hints=previous_hints, role_models=role_models)

    # 3회차 재시도에서는 텍스트 힌트에 더해 스켈레톤 파일을 생성한다.
    # run.py 가 AnalysisResult.skeleton_files 를 다음 WorkspaceManager 에 주입.
    if result.should_retry and attempt >= 3 and task.target_files:
        failure_context = f"{failure_reason[:500]}\n\n현재 힌트:\n{result.hint}"
        skeletons, sk_usage, sk_log = generate_skeleton_files(
            task, failure_context, role_models=role_models,
        )
        if skeletons:
            result.skeleton_files = skeletons
            # token_usage 는 tuple[int,int,int,int] — 요소별 합산
            result.token_usage = tuple(  # type: ignore[assignment]
                a + b for a, b in zip(result.token_usage, sk_usage)
            )
            result.call_log = result.call_log + sk_log

    return result

logger = logging.getLogger(__name__)

_analyze_llm: BaseLLMClient | None = None
_report_llm: BaseLLMClient | None = None

# 역할별 모델 오버라이드 지원을 위한 기본 모델 config 글로벌
_default_role_models: dict[str, RoleModelConfig | dict[str, str]] = {}

# 복잡도 기반 자동 선택 config 글로벌
_auto_select_by_complexity: bool = False
_complexity_map: dict[str, dict[str, RoleModelConfig | dict[str, str]]] = {}


def set_llm(analyze_llm: BaseLLMClient, report_llm: BaseLLMClient) -> None:
    """LLM 클라이언트를 주입한다. 파이프라인 시작 시 run.py에서 호출."""
    global _analyze_llm, _report_llm
    _analyze_llm = analyze_llm
    _report_llm = report_llm


def set_model_config(
    default_role_models: dict[str, RoleModelConfig | dict[str, str]] | None,
) -> None:
    """모델 config를 주입한다. role_models 오버라이드 적용 시 사용."""
    global _default_role_models
    _default_role_models = default_role_models or {}


def set_complexity_routing(
    auto_select_by_complexity: bool,
    complexity_map: dict[str, dict[str, RoleModelConfig | dict[str, str]]] | None,
) -> None:
    """복잡도 기반 자동 선택 플래그와 매핑을 주입한다.

    파이프라인 시작 시 run.py에서 호출한다. True이면 analyze()가 task.complexity에
    따라 per-task capable 모델을 선택한다 (role_models 무시).
    """
    global _auto_select_by_complexity, _complexity_map
    _auto_select_by_complexity = auto_select_by_complexity
    _complexity_map = complexity_map or {}


def create_intervention_llms(provider: str, model: str) -> tuple[BaseLLMClient, BaseLLMClient]:
    """
    intervention 모듈에서 사용할 LLM 클라이언트 쌍을 생성한다.

    Returns:
        (analyze_llm, report_llm) — 각각 올바른 시스템 프롬프트로 설정됨
    """
    analyze_llm = create_client(
        provider, LLMConfig(model=model, system_prompt=_ANALYZE_SYSTEM, max_tokens=1024)
    )
    report_llm = create_client(
        provider, LLMConfig(model=model, system_prompt=_REPORT_SYSTEM, max_tokens=4096)
    )
    return analyze_llm, report_llm


def _resolve_intervention_provider_model(
    task: Task,
    role_models: dict[str, RoleModelConfig] | None,
) -> tuple[str, str] | None:
    """intervention 역할의 최종 provider/model을 해석한다."""
    from agents.roles import compose_role_override, resolve_complexity_model, resolve_model_for_role

    intervention_override = (role_models or {}).get(ROLE_INTERVENTION)

    if _auto_select_by_complexity and _complexity_map:
        base_provider, base_model = resolve_complexity_model(
            ROLE_INTERVENTION, task.complexity, _complexity_map
        )
    elif _default_role_models:
        base_provider, base_model = resolve_model_for_role(
            role=ROLE_INTERVENTION,
            role_models=None,
            default_role_models=_default_role_models,
        )
    else:
        if intervention_override and intervention_override.provider and intervention_override.model:
            return intervention_override.provider, intervention_override.model
        return None

    return compose_role_override(intervention_override, base_provider, base_model)

_ANALYZE_SYSTEM = """\
당신은 AI 코딩 에이전트 파이프라인의 중앙 오케스트레이터입니다.
하위 에이전트가 태스크를 처리하다 실패했습니다.
실패 원인을 분석하고, 재시도 가능성 여부와 구체적인 힌트를 결정하세요.

[응답 형식 — 반드시 아래 두 가지 중 하나만 출력]

재시도 가능할 때:
RETRY: <에이전트에게 전달할 구체적 수정 힌트 (1~3문장)>

재시도가 의미 없을 때 (구조적 문제, 스펙 불명확, 반복 동일 오류 등):
GIVE_UP: <포기 이유 한 줄>

힌트 작성 원칙:
- 이전 시도에서 뭐가 잘못됐는지 명확히 지적할 것
- "더 잘 해봐"처럼 모호한 지시는 금지
- 파일 경로, 함수명, 구체적 수정 방향을 포함할 것

오류 우선순위 (위에서부터 먼저 해결):
1. ModuleNotFoundError / ImportError → 파일이 올바른 경로에 존재하는지, 모듈 이름이 테스트의 import문과 일치하는지 먼저 확인. 로직 오류보다 항상 우선.
2. SyntaxError → 파일은 존재하지만 문법 오류. 해당 파일의 구문 수정 지시.
3. AttributeError / NameError → 클래스·함수·변수 이름 불일치. 테스트가 기대하는 이름을 정확히 명시.
4. AssertionError → 로직 오류. 기대값과 실제값 차이를 분석해서 수정 방향 제시.

상위 오류가 있으면 하위 오류는 언급하지 마세요 (파일이 없으면 로직을 고쳐봤자 의미 없음).
"""

_SKELETON_SYSTEM = """\
당신은 다언어 스켈레톤 파일 생성기입니다.
태스크 설명과 수락 기준, 실패 원인을 읽고 target_files 각각에 대해
**구현 없는 스텁**을 생성하세요.

규칙 (엄수):
- 함수/클래스 signature 는 task 설명과 acceptance_criteria 에서 합리적으로 유추
- 타입 힌트 / 타입 어노테이션 필수, 필요한 import 포함
- 각 함수/클래스/메소드에는 짧은 docstring 또는 언어 관용의 문서 주석을 포함
  (한 줄 요약이면 충분하며, 구현 설명은 금지)
- 모든 함수/메소드 body 는 사용자 메시지의 "파일별 STUB 라인 표"에서
  해당 파일에 지정된 라인 **한 줄**만 사용
- 각 파일은 자신의 확장자에 맞는 문법을 유지해야 하며, 다른 파일의 STUB 라인을
  재사용하면 안 된다
- 실제 로직·예시·주석 설명 금지 (에이전트가 구현함)
- target_files 에 없는 경로는 절대 생성하지 말 것
- 파일 확장자에 맞는 언어 문법 유지 (ex. `.py` 는 Python, `.ts` 는 TypeScript)

응답 형식 (엄수):
각 파일을 다음 블록으로 구분하여 출력하세요. 코드블록 마커(```) 금지.

===FILE: <relative_path>===
<파일 전체 내용>
===END===

파일 여러 개면 위 블록을 반복하세요. 블록 외 설명 텍스트 금지.
"""


_STUB_BY_LANGUAGE: dict[str, str] = {
    "python": 'raise NotImplementedError("TODO: task {task_id}")',
    "kotlin": 'TODO("TODO: task {task_id}")',
    "typescript": 'throw new Error("TODO: task {task_id}");',
    "javascript": 'throw new Error("TODO: task {task_id}");',
    "go": 'panic("TODO: task {task_id}")',
    "java": 'throw new UnsupportedOperationException("TODO: task {task_id}");',
    "rust": 'unimplemented!("TODO: task {task_id}")',
}


def _stub_line_for_language(language: str, task_id: str) -> str:
    """task.language 에 해당하는 단일 라인 스텁을 반환. 미지원 언어는 python 폴백."""
    normalized = (language or "python").strip().lower()
    template = _STUB_BY_LANGUAGE.get(normalized, _STUB_BY_LANGUAGE["python"])
    return template.format(task_id=task_id)


_LANGUAGE_BY_EXTENSION: dict[str, str] = {
    ".py": "python",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
    ".java": "java",
    ".rs": "rust",
}


def _stub_line_for_target_file(rel_path: str, task_language: str, task_id: str) -> str:
    """파일 확장자를 우선으로 스텁 라인을 고른다. 모를 때만 task.language 로 폴백."""
    suffix = Path(rel_path).suffix.lower()
    language = _LANGUAGE_BY_EXTENSION.get(suffix, task_language)
    return _stub_line_for_language(language, task_id)

_REPORT_SYSTEM = """\
당신은 소프트웨어 개발 프로젝트 관리자입니다.
AI 코딩 에이전트가 태스크를 여러 번 시도했지만 실패했습니다.
담당자에게 제출할 실패 분석 보고서를 작성하세요.

[출력 형식 — 마크다운]

## 태스크 정보
(id, 제목, 설명)

## 실패 원인 분석
(근본 원인을 명확히)

## 시도된 해결 접근법
(각 시도에서 무엇을 했는지)

## 권장 해결 방안
(사람이 직접 취해야 할 조치, 구체적으로)

## 태스크 재설계 제안
(태스크를 더 작게 나누거나 스펙을 수정하는 방안)
"""


@dataclass
class AnalysisResult:
    should_retry: bool
    hint: str          # RETRY일 때 힌트, GIVE_UP일 때 이유
    raw: str
    token_usage: tuple[int, int, int, int] = (0, 0, 0, 0)
    call_log: list[dict] = field(default_factory=list)
    # 3회차 이상 재시도 시 LLM이 생성한 target_files 스켈레톤. {relative_path: content}.
    # run.py 가 다음 WorkspaceManager 에 전달하여 빈 placeholder 파일에만 주입한다.
    skeleton_files: dict[str, str] = field(default_factory=dict)


@dataclass
class ReportGenerationResult:
    text: str
    token_usage: tuple[int, int, int, int] = (0, 0, 0, 0)
    call_log: list[dict] = field(default_factory=list)


def _usage_from_response(
    response,
    iteration: int = 1,
) -> tuple[tuple[int, int, int, int], list[dict]]:
    """LLMResponse에서 intervention 토큰/로그 메타데이터를 추출한다."""
    if response is None:
        return (0, 0, 0, 0), []

    input_tokens = getattr(response, "input_tokens", 0) or 0
    output_tokens = getattr(response, "output_tokens", 0) or 0
    cached_read_tokens = getattr(response, "cached_read_tokens", 0) or 0
    cached_write_tokens = getattr(response, "cached_write_tokens", 0) or 0
    content = getattr(response, "content", None) or []
    tool_names = [
        block.get("name", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "tool_use"
    ]
    call_log = [{
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "iteration": iteration,
        "model": getattr(response, "model", ""),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_read_tokens": cached_read_tokens,
        "cached_write_tokens": cached_write_tokens,
        "tool_calls": tool_names,
    }]
    return (input_tokens, output_tokens, cached_read_tokens, cached_write_tokens), call_log


def _extract_text(response) -> str:
    """LLMResponse에서 텍스트를 추출한다."""
    for block in response.content:
        if isinstance(block, dict) and block.get("type") == "text":
            return block["text"]
        if hasattr(block, "type") and block.type == "text":
            return block.text
    return ""


def analyze(
    task: Task,
    failure_reason: str,
    attempt: int,
    previous_hints: list[str] | None = None,
    role_models: dict[str, RoleModelConfig] | None = None,
) -> AnalysisResult:
    """
    LLM에게 실패 원인을 분석시키고 RETRY/GIVE_UP 결정을 받는다.

    Args:
        task     : 실패한 태스크 (task.last_error 포함)
        failure_reason : PipelineResult.failure_reason
        attempt  : 오케스트레이터 재시도 횟수 (1-based)
        previous_hints : 이전 시도에서 제공한 힌트 목록 (중복 방지용)
        role_models: 역할별 모델 오버라이드. None이면 글로벌 _analyze_llm 사용.
    """
    # 우선순위 (TDDPipeline._llm_for_role와 동일 계약):
    #  1) role_models['intervention'] override — 부분 지정 시 base와 합성
    #  2) auto_select_by_complexity=True → task.complexity 기반 per-task intervention 모델
    #  3) 전역 _analyze_llm (파이프라인 시작 시 주입된 기본 LLM)
    resolved = _resolve_intervention_provider_model(task, role_models)
    use_resolved_model = bool((role_models or {}).get(ROLE_INTERVENTION)) or _auto_select_by_complexity

    if use_resolved_model:
        if not resolved:
            logger.error("intervention 모델 해석 실패")
            return AnalysisResult(should_retry=False, hint="오케스트레이터 모델 해석 실패", raw="")
        int_provider, int_model = resolved
        llm: BaseLLMClient = create_client(
            int_provider, LLMConfig(model=int_model, system_prompt=_ANALYZE_SYSTEM, max_tokens=1024)
        )
    elif _analyze_llm is None:
        logger.error("intervention.set_llm()이 호출되지 않았습니다.")
        return AnalysisResult(should_retry=False, hint="오케스트레이터 LLM 미초기화", raw="")
    else:
        llm = _analyze_llm

    last_error_snippet = (task.last_error or "")[:2000]

    hints_section = ""
    if previous_hints:
        hints_list = "\n".join(f"  - 시도 {i+1}: {h[:300]}" for i, h in enumerate(previous_hints))
        hints_section = (
            f"\n## 이전 시도에서 제공한 힌트 (이미 적용했으나 실패)\n"
            f"{hints_list}\n"
            f"위 힌트들은 이미 적용했으나 실패했다. 같은 방향을 다시 제안하지 마라.\n"
        )

    user_msg = f"""## 태스크
id: {task.id}
제목: {task.title}
설명: {task.description}

## 수락 기준
{task.acceptance_criteria_text()}

## 실패 원인 (시도 {attempt}회차)
{failure_reason[:1000]}

## 마지막 오류 로그
{last_error_snippet if last_error_snippet else "(없음)"}
{hints_section}"""
    try:
        response = llm.chat([Message(role="user", content=user_msg)])
        raw = _extract_text(response)
    except Exception as e:
        logger.error("오케스트레이터 분석 LLM 호출 실패: %s", e)
        return AnalysisResult(should_retry=False, hint=str(e), raw="")

    token_usage, call_log = _usage_from_response(response)

    raw_stripped = raw.strip()

    # RETRY: ... 파싱
    retry_match = re.match(r"^RETRY\s*:\s*(.+)", raw_stripped, re.IGNORECASE | re.DOTALL)
    if retry_match:
        hint = retry_match.group(1).strip()
        logger.info("[%s] 오케스트레이터 → RETRY 결정 (힌트: %s…)", task.id, hint[:80])
        return AnalysisResult(
            should_retry=True,
            hint=hint,
            raw=raw,
            token_usage=token_usage,
            call_log=call_log,
        )

    # GIVE_UP: ... 파싱
    giveup_match = re.match(r"^GIVE_UP\s*:\s*(.+)", raw_stripped, re.IGNORECASE | re.DOTALL)
    if giveup_match:
        reason = giveup_match.group(1).strip()
        logger.warning("[%s] 오케스트레이터 → GIVE_UP: %s", task.id, reason[:120])
        return AnalysisResult(
            should_retry=False,
            hint=reason,
            raw=raw,
            token_usage=token_usage,
            call_log=call_log,
        )

    # 형식 불명확 → 보수적으로 포기
    logger.warning("[%s] 오케스트레이터 응답 형식 불명확, 포기 처리:\n%s", task.id, raw[:300])
    return AnalysisResult(
        should_retry=False,
        hint="오케스트레이터 응답 파싱 실패",
        raw=raw,
        token_usage=token_usage,
        call_log=call_log,
    )


# ── 스켈레톤 파일 생성 (3회차 재시도 전용) ───────────────────────────────────

_SKELETON_BLOCK_RE = re.compile(
    r"===FILE:\s*(.+?)\s*===\s*\n(.*?)\n===END===",
    re.DOTALL,
)


def _parse_skeleton_response(raw: str, allowed_paths: set[str]) -> dict[str, str]:
    """LLM 응답에서 `===FILE: path===\\n...\\n===END===` 블록을 추출해 dict로 반환.

    `allowed_paths` 에 없는 경로는 경고 로그 후 스킵 (LLM 환각 방지).
    """
    result: dict[str, str] = {}
    for match in _SKELETON_BLOCK_RE.finditer(raw):
        path = match.group(1).strip()
        content = match.group(2)
        if path not in allowed_paths:
            logger.warning("스켈레톤 응답 경로가 target_files 에 없음, 스킵: %s", path)
            continue
        result[path] = content
    if not result:
        logger.warning("스켈레톤 파싱 결과 비어있음. raw[:300]=%s", raw[:300])
    return result


def _ensure_full_skeleton_coverage(
    skeletons: dict[str, str],
    expected_paths: list[str],
) -> dict[str, str]:
    """target_files 전체를 덮지 못한 스켈레톤 응답은 보수적으로 폐기한다."""
    missing = [path for path in expected_paths if path not in skeletons]
    if not missing:
        return skeletons
    logger.warning(
        "스켈레톤 응답 일부 누락 — 전체 폐기: missing=%s produced=%s",
        missing,
        sorted(skeletons.keys()),
    )
    return {}


def _resolve_intervention_llm_for_skeleton(
    task: Task,
    role_models: dict[str, RoleModelConfig] | None,
) -> BaseLLMClient | None:
    """skeleton 생성용 LLM 클라이언트를 선택한다.

    선택 우선순위는 `analyze()` 와 동일:
      1) role_models['intervention'] override
      2) auto_select_by_complexity + task.complexity
      3) 전역 default role model (_analyze_llm 은 system prompt 가 달라 재사용 불가)
    """
    resolved = _resolve_intervention_provider_model(task, role_models)
    if not resolved:
        logger.error("스켈레톤 LLM 해석 실패")
        return None
    provider, model = resolved
    if not (provider and model):
        logger.error("스켈레톤 LLM 해석 실패: provider=%r model=%r", provider, model)
        return None

    return create_client(
        provider,
        LLMConfig(model=model, system_prompt=_SKELETON_SYSTEM, max_tokens=2048),
    )


def generate_skeleton_files(
    task: Task,
    failure_context: str,
    role_models: dict[str, RoleModelConfig] | None = None,
) -> tuple[dict[str, str], tuple[int, int, int, int], list[dict]]:
    """3회차 재시도 전에 target_files 각각의 스텁을 LLM으로 생성한다.

    Returns:
        (skeletons, token_usage, call_log)
        skeletons: {relative_path: file_content}. 파싱 실패 시 빈 dict.
        token_usage: (input, output, cached_read, cached_write)
        call_log: 모델 호출 메타데이터 (_accumulate_external_tokens 에 누적 가능)
    """
    if not task.target_files:
        return {}, (0, 0, 0, 0), []

    llm = _resolve_intervention_llm_for_skeleton(task, role_models)
    if llm is None:
        return {}, (0, 0, 0, 0), []

    target_list = "\n".join(f"- {p}" for p in task.target_files)
    stub_table = "\n".join(
        f"- {path}: {_stub_line_for_target_file(path, task.language, task.id)}"
        for path in task.target_files
    )
    user_msg = f"""## 태스크
id: {task.id}
제목: {task.title}
설명: {task.description}
language: {task.language}

## 수락 기준
{task.acceptance_criteria_text()}

## target_files
{target_list}

## 직전 실패 컨텍스트
{failure_context[:1500]}

## 파일별 STUB 라인 (각 파일의 모든 함수/메소드 body 로 해당 라인 한 줄만 사용)
{stub_table}

위 target_files 각각에 대해 스텁을 생성하세요.
각 파일은 위 표에서 자기 경로에 대응하는 STUB 라인만 사용해야 합니다.
모든 함수/클래스/메소드에는 짧은 docstring 또는 언어별 문서 주석을 포함하세요.
target_files 에 있는 모든 경로를 빠짐없이 생성해야 하며, 하나라도 누락하면 실패입니다.
"""

    try:
        response = llm.chat([Message(role="user", content=user_msg)])
    except Exception as e:
        logger.error("[%s] 스켈레톤 LLM 호출 실패: %s", task.id, e)
        return {}, (0, 0, 0, 0), []

    raw = _extract_text(response)
    token_usage, call_log = _usage_from_response(response)
    skeletons = _parse_skeleton_response(raw, allowed_paths=set(task.target_files))
    skeletons = _ensure_full_skeleton_coverage(skeletons, task.target_files)
    logger.info("[%s] 스켈레톤 생성: %d/%d 파일",
                task.id, len(skeletons), len(task.target_files))
    return skeletons, token_usage, call_log


def generate_report_with_metrics(
    task: Task,
    failure_reason: str,
    attempts: int,
    hints_tried: list[str],
    orchestrator_model: str = "",
    coding_agent_model: str = "",
    models_used: dict[str, str] | None = None,
    role_models: dict[str, RoleModelConfig] | None = None,
) -> ReportGenerationResult:
    """
    최종 실패 보고서를 LLM으로 생성한다.

    우선순위 (analyze()와 동일):
      1) role_models['intervention'] override
      2) auto_select_by_complexity=True → task.complexity 기반 per-task capable 모델
      3) 전역 _report_llm (파이프라인 시작 시 주입된 기본)

    Returns:
        생성된 보고서와 LLM 메타데이터
    """
    # analyze()와 동일한 우선순위: override > complexity > 전역 _report_llm
    resolved = _resolve_intervention_provider_model(task, role_models)
    use_resolved_model = bool((role_models or {}).get(ROLE_INTERVENTION)) or _auto_select_by_complexity

    report_llm: BaseLLMClient | None
    if use_resolved_model:
        if not resolved:
            logger.error("intervention 보고서 모델 해석 실패")
            return ReportGenerationResult(
                text=f"# 보고서 생성 실패\n\n모델 해석 실패\n\n## 최종 실패 원인\n{failure_reason}"
            )
        int_provider, int_model = resolved
        report_llm = create_client(
            int_provider, LLMConfig(model=int_model, system_prompt=_REPORT_SYSTEM, max_tokens=4096)
        )
    else:
        report_llm = _report_llm

    if report_llm is None:
        logger.error("intervention.set_llm()이 호출되지 않았습니다.")
        return ReportGenerationResult(
            text=f"# 보고서 생성 실패\n\nLLM 미초기화\n\n## 최종 실패 원인\n{failure_reason}"
        )

    hints_text = "\n".join(
        f"  시도 {i+1}: {h[:300]}" for i, h in enumerate(hints_tried)
    ) or "  (없음)"

    user_msg = f"""## 태스크 정보
id: {task.id}
제목: {task.title}
설명: {task.description}

## 수락 기준
{task.acceptance_criteria_text()}

## 최종 실패 원인
{failure_reason[:1000]}

## 오케스트레이터 시도 횟수
{attempts}회

## 시도된 힌트 목록
{hints_text}

## 마지막 오류 로그
{(task.last_error or '')[:2000]}

위 정보를 바탕으로 실패 분석 보고서를 작성하세요.
"""
    try:
        response = report_llm.chat([Message(role="user", content=user_msg)])
        report_body = _extract_text(response) or "보고서 생성 실패"
        token_usage, call_log = _usage_from_response(response)
    except Exception as e:
        logger.error("오케스트레이터 보고서 생성 LLM 호출 실패: %s", e)
        report_body = f"# 보고서 생성 실패\n\n오류: {e}\n\n## 최종 실패 원인\n{failure_reason}"
        token_usage, call_log = (0, 0, 0, 0), []

    role_labels = {
        "test_writer": "테스트 작성 에이전트",
        "implementer": "구현 에이전트",
        "reviewer": "리뷰 에이전트",
    }
    rows = (
        f"| 중앙 오케스트레이터 | `{orchestrator_model or '(미지정)'}` |\n"
        f"| 코딩 에이전트 | `{coding_agent_model or '(미지정)'}` |\n"
    )
    if models_used:
        for role_key, model_str in models_used.items():
            label = role_labels.get(role_key, role_key)
            rows += f"| {label} | `{model_str}` |\n"

    model_section = (
        f"\n\n---\n\n## 사용 모델\n\n"
        f"| 역할 | 모델 |\n"
        f"|------|------|\n"
        + rows
    )
    return ReportGenerationResult(
        text=report_body + model_section,
        token_usage=token_usage,
        call_log=call_log,
    )


def generate_report(
    task: Task,
    failure_reason: str,
    attempts: int,
    hints_tried: list[str],
    orchestrator_model: str = "",
    coding_agent_model: str = "",
    models_used: dict[str, str] | None = None,
) -> str:
    """기존 API 호환용 래퍼: 보고서 본문만 반환한다."""
    return generate_report_with_metrics(
        task,
        failure_reason,
        attempts,
        hints_tried,
        orchestrator_model=orchestrator_model,
        coding_agent_model=coding_agent_model,
        models_used=models_used,
    ).text


def save_report(report_text: str, task_id: str, reports_dir: Path) -> Path:
    """보고서를 파일로 저장하고 경로를 반환한다."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{task_id}_orchestrator_report.md"
    path.write_text(report_text, encoding="utf-8")
    return path
