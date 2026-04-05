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
from dataclasses import dataclass
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
    LOGIC_ERROR = "logic_error"


def classify_failure(failure_reason: str, test_stdout: str = "") -> FailureType:
    """LLM 호출 없이 문자열 패턴으로 실패 유형을 분류한다."""
    combined = f"{failure_reason}\n{test_stdout}".lower()

    if "[unsupported_language]" in combined:
        return FailureType.UNSUPPORTED_LANGUAGE
    if "[dependency_missing]" in combined:
        return FailureType.DEPENDENCY_MISSING
    if "internalerror" in combined:
        return FailureType.ENV_ERROR
    if any(p in combined for p in ["importerror", "modulenotfounderror", "no module named"]):
        return FailureType.ENV_ERROR
    if "[max_iter]" in combined:
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

    # LOGIC_ERROR → 기존 LLM 분석
    return analyze(task, failure_reason, attempt, previous_hints=previous_hints, role_models=role_models)

logger = logging.getLogger(__name__)

_analyze_llm: BaseLLMClient | None = None
_report_llm: BaseLLMClient | None = None

# 역할별 모델 오버라이드 지원을 위한 모델 config 글로벌
_provider: str = "claude"
_model_fast: str = ""
_model_capable: str = ""
_provider_fast: str | None = None
_provider_capable: str | None = None


def set_llm(analyze_llm: BaseLLMClient, report_llm: BaseLLMClient) -> None:
    """LLM 클라이언트를 주입한다. 파이프라인 시작 시 run.py에서 호출."""
    global _analyze_llm, _report_llm
    _analyze_llm = analyze_llm
    _report_llm = report_llm


def set_model_config(
    provider: str,
    model_fast: str,
    model_capable: str,
    provider_fast: str | None = None,
    provider_capable: str | None = None,
) -> None:
    """모델 config를 주입한다. role_models 오버라이드 적용 시 사용."""
    global _provider, _model_fast, _model_capable, _provider_fast, _provider_capable
    _provider = provider
    _model_fast = model_fast
    _model_capable = model_capable
    _provider_fast = provider_fast
    _provider_capable = provider_capable


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
    # role_models에 "intervention" 오버라이드가 있으면 새 클라이언트 생성
    if role_models and _model_capable:
        int_provider, int_model = resolve_model_for_role(
            role=ROLE_INTERVENTION,
            role_models=role_models,
            provider=_provider,
            model_fast=_model_fast,
            model_capable=_model_capable,
            provider_fast=_provider_fast,
            provider_capable=_provider_capable,
        )
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

    raw_stripped = raw.strip()

    # RETRY: ... 파싱
    retry_match = re.match(r"^RETRY\s*:\s*(.+)", raw_stripped, re.IGNORECASE | re.DOTALL)
    if retry_match:
        hint = retry_match.group(1).strip()
        logger.info("[%s] 오케스트레이터 → RETRY 결정 (힌트: %s…)", task.id, hint[:80])
        return AnalysisResult(should_retry=True, hint=hint, raw=raw)

    # GIVE_UP: ... 파싱
    giveup_match = re.match(r"^GIVE_UP\s*:\s*(.+)", raw_stripped, re.IGNORECASE | re.DOTALL)
    if giveup_match:
        reason = giveup_match.group(1).strip()
        logger.warning("[%s] 오케스트레이터 → GIVE_UP: %s", task.id, reason[:120])
        return AnalysisResult(should_retry=False, hint=reason, raw=raw)

    # 형식 불명확 → 보수적으로 포기
    logger.warning("[%s] 오케스트레이터 응답 형식 불명확, 포기 처리:\n%s", task.id, raw[:300])
    return AnalysisResult(should_retry=False, hint="오케스트레이터 응답 파싱 실패", raw=raw)


def generate_report(
    task: Task,
    failure_reason: str,
    attempts: int,
    hints_tried: list[str],
    orchestrator_model: str = "",
    coding_agent_model: str = "",
) -> str:
    """
    최종 실패 보고서를 LLM으로 생성한다.

    Returns:
        마크다운 형식의 보고서 문자열
    """
    if _report_llm is None:
        logger.error("intervention.set_llm()이 호출되지 않았습니다.")
        return f"# 보고서 생성 실패\n\nLLM 미초기화\n\n## 최종 실패 원인\n{failure_reason}"

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
        response = _report_llm.chat([Message(role="user", content=user_msg)])
        report_body = _extract_text(response) or "보고서 생성 실패"
    except Exception as e:
        logger.error("오케스트레이터 보고서 생성 LLM 호출 실패: %s", e)
        report_body = f"# 보고서 생성 실패\n\n오류: {e}\n\n## 최종 실패 원인\n{failure_reason}"

    model_section = (
        f"\n\n---\n\n## 사용 모델\n\n"
        f"| 역할 | 모델 |\n"
        f"|------|------|\n"
        f"| 중앙 오케스트레이터 | `{orchestrator_model or '(미지정)'}` |\n"
        f"| 코딩 에이전트 | `{coding_agent_model or '(미지정)'}` |\n"
    )
    return report_body + model_section


def save_report(report_text: str, task_id: str, reports_dir: Path) -> Path:
    """보고서를 파일로 저장하고 경로를 반환한다."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{task_id}_orchestrator_report.md"
    path.write_text(report_text, encoding="utf-8")
    return path


