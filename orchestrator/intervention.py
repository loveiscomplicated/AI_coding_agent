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
from pathlib import Path

from llm import BaseLLMClient, LLMConfig, Message, create_client

from orchestrator.task import Task

logger = logging.getLogger(__name__)

_analyze_llm: BaseLLMClient | None = None
_report_llm: BaseLLMClient | None = None


def set_llm(analyze_llm: BaseLLMClient, report_llm: BaseLLMClient) -> None:
    """LLM 클라이언트를 주입한다. 파이프라인 시작 시 run.py에서 호출."""
    global _analyze_llm, _report_llm
    _analyze_llm = analyze_llm
    _report_llm = report_llm


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


def analyze(task: Task, failure_reason: str, attempt: int) -> AnalysisResult:
    """
    LLM에게 실패 원인을 분석시키고 RETRY/GIVE_UP 결정을 받는다.

    Args:
        task     : 실패한 태스크 (task.last_error 포함)
        failure_reason : PipelineResult.failure_reason
        attempt  : 오케스트레이터 재시도 횟수 (1-based)
    """
    if _analyze_llm is None:
        logger.error("intervention.set_llm()이 호출되지 않았습니다.")
        return AnalysisResult(should_retry=False, hint="오케스트레이터 LLM 미초기화", raw="")

    last_error_snippet = (task.last_error or "")[:2000]
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
"""
    try:
        response = _analyze_llm.chat([Message(role="user", content=user_msg)])
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


