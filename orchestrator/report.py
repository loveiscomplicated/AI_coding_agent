"""
orchestrator/report.py — Task Report 저장/로드

파이프라인 완료 후 실행 결과를 구조화된 YAML로 저장한다.
이 데이터가 Weekly Report, execution_brief, 시스템 자기 개선 루프의 기반이 된다.

저장 위치: agent-data/reports/task-{id}.yaml
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml

from orchestrator.pipeline import PipelineMetrics, PipelineResult, _assert_invariants
from orchestrator.task import Task, TaskStatus
from reports.task_report import TaskReport

logger = logging.getLogger(__name__)

_REPORTS_DIR = Path("agent-data/reports")

CostEstimationQuality = Literal["exact", "fallback", "missing"]

# ── LLM 모델별 단가 ($/1M tokens) ─────────────────────────────────────────────
# 각 엔트리: {"input": in_rate, "output": out_rate, "cached_read": cache_rate}
# cached_read 는 캐시 적중 토큰에 적용되는 별도 단가다.
#
# 공식 가격 출처:
#   - OpenAI:    https://openai.com/api/pricing/
#   - Anthropic: https://www.anthropic.com/pricing
#   - Google:    https://ai.google.dev/gemini-api/docs/pricing
#   - Z.AI:      https://docs.z.ai/guides/overview/pricing
#
# Google Gemini / Z.AI 는 가격표가 "1M tokens" 기준이며, cached_read 는 input 과
# 다른 별도 항목을 사용한다. `gemini-3-pro-preview` 키는 현재 공식 표기인
# `gemini-3.1-pro-preview` 의 레거시 alias 로 취급해 같은 단가를 적용한다.
_MODEL_PRICING: dict[str, dict[str, float]] = {
    # Anthropic Claude
    "claude-haiku-4-5": {"input": 0.80, "output": 4.00, "cached_read": 0.08},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00, "cached_read": 0.30},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cached_read": 0.30},
    "claude-opus-4": {"input": 15.00, "output": 75.00, "cached_read": 1.50},
    # OpenAI GPT-5 family — gpt-5: 1.25 / 0.125 / 10.00, gpt-5-mini: 0.25 /
    # 0.025 / 2.00, gpt-5-nano: 0.05 / 0.005 / 0.40.
    "gpt-5-mini": {"input": 0.25, "output": 2.00, "cached_read": 0.025},
    "gpt-5": {"input": 1.25, "output": 10.00, "cached_read": 0.125},
    "gpt-5-nano": {"input": 0.05, "output": 0.40, "cached_read": 0.005},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60, "cached_read": 0.10},
    "gpt-4.1": {"input": 2.00, "output": 8.00, "cached_read": 0.50},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60, "cached_read": 0.075},
    "gpt-4o": {"input": 2.50, "output": 10.00, "cached_read": 1.25},
    # Google Gemini standard pricing, <= 200k prompt tier.
    # gemini-3.1-pro-preview: 2.00 / 0.20 / 12.00.
    # gemini-2.5-pro: 1.25 / 0.125 / 10.00.
    # gemini-2.5-flash: 0.30 / 0.03 / 2.50.
    # gemini-2.5-flash-lite: 0.10 / 0.01 / 0.40.
    "gemini-3.1-pro-preview": {"input": 2.00, "output": 12.00, "cached_read": 0.20},
    "gemini-3-pro-preview": {"input": 2.00, "output": 12.00, "cached_read": 0.20},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00, "cached_read": 0.125},
    "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40, "cached_read": 0.01},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50, "cached_read": 0.03},
    # Z.AI GLM family — glm-5.1: 1.40 / 0.26 / 4.40, glm-4.6: 0.60 / 0.11 / 2.20.
    "glm-5.1": {"input": 1.40, "output": 4.40, "cached_read": 0.26},
    "glm-4.6": {"input": 0.60, "output": 2.20, "cached_read": 0.11},
    "glm-4-flash": {"input": 0.10, "output": 0.10, "cached_read": 0.025},
    "glm-4-plus": {"input": 0.70, "output": 0.70, "cached_read": 0.175},
}

# 긴 키가 먼저 매칭되도록 정렬 (예: 'gpt-5-mini' 가 'gpt-5' 보다 먼저)
_PRICING_LOOKUP_ORDER: list[str] = sorted(_MODEL_PRICING.keys(), key=len, reverse=True)

# 양쪽 경계 보장 (model ID 는 provider/model-name 또는 단독 model-name 형태):
# - 왼쪽 `(?:^|(?<=/))`: 키가 문자열 시작 또는 `/` 바로 뒤에 와야 매칭.
#   `chatgpt-5`, `foo/bar-gpt-5`, `xglm-4.6` 처럼 앞에 다른 토큰이 붙은
#   경우는 전부 거부된다 — 다른 모델이므로 과금하면 안 됨.
# - 오른쪽 `(?![.\d])`: 키 뒤에 `.` 또는 숫자가 오면 다른 계열로 보고 거부
#   (예: `gpt-5.4`, `glm-4.66`). `-20251001` 같은 date suffix 는 허용.
_PRICING_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?:^|(?<=/))" + re.escape(k) + r"(?![.\d])"), k)
    for k in _PRICING_LOOKUP_ORDER
]

_PRICING_MISSING_WARNED: set[str] = set()


def _model_rate(model_id: str) -> dict[str, float] | None:
    """model_id (예: 'anthropic/claude-haiku-4-5-20251001')에서 단가 dict을 반환한다.

    - substring 매칭이 아닌 "버전 suffix 경계" 매칭을 사용한다.
      `gpt-5` 키는 `openai/gpt-5` 나 `openai/gpt-5-2026-04-01` 는 잡지만,
      `openai/gpt-5.4` 나 `openai/gpt-50` 은 잡지 않는다.
    - 미등록 모델은 None 을 반환한다.
    """
    if not model_id:
        return None
    lm = model_id.lower()
    for pattern, key in _PRICING_PATTERNS:
        if pattern.search(lm):
            return _MODEL_PRICING[key]
    return None


def _calculate_cost(
    token_usage: dict,
    models_used: dict[str, str] | None,
) -> float | None:
    """역할별 토큰 사용량과 모델 정보로 총 USD 비용을 계산한다.

    단가 등록이 된 역할이 하나도 없으면 None 을 반환한다 (null-safe 집계용).
    일부 역할만 미등록이면 등록된 역할의 비용만 합산해 반환한다.
    미등록 모델에 대해서는 logging.warning 으로 경고한다.
    """
    cost, _quality, _missing = _calculate_cost_with_quality(token_usage, models_used)
    return cost


def _calculate_cost_with_quality(
    token_usage: dict,
    models_used: dict[str, str] | None,
) -> tuple[float | None, CostEstimationQuality, list[str]]:
    """비용과 추정 품질, 미등록 모델 목록을 함께 반환한다.

    Returns:
        (cost_usd, quality, missing_models)
        - quality == "exact":    models_used 의 모든 역할이 단가 등록됨
        - quality == "fallback": 일부 역할만 단가 등록됨 (등록된 것만 합산)
        - quality == "missing":  등록된 역할이 전무 (cost_usd 는 None)
    """
    if not models_used:
        return None, "missing", []

    total = 0.0
    registered = 0
    missing_models: list[str] = []

    for role, model in models_used.items():
        rate = _model_rate(model)
        if rate is None:
            if model and model not in missing_models:
                missing_models.append(model)
                if model not in _PRICING_MISSING_WARNED:
                    logger.warning("PRICING_MISSING model=%s", model)
                    _PRICING_MISSING_WARNED.add(model)
            continue
        registered += 1

        usage = token_usage.get(role)
        if isinstance(usage, (tuple, list)):
            inp = usage[0] if len(usage) > 0 else 0
            out = usage[1] if len(usage) > 1 else 0
            cr = usage[2] if len(usage) > 2 else 0
        elif isinstance(usage, dict):
            inp = usage.get("input", 0)
            out = usage.get("output", 0)
            cr = usage.get("cached_read", 0)
        else:
            inp, out, cr = 0, 0, 0

        total += (
            inp * rate["input"]
            + out * rate["output"]
            + cr * rate.get("cached_read", rate["input"])
        ) / 1_000_000

    total_roles = len(models_used)
    if registered == 0:
        return None, "missing", missing_models
    if registered < total_roles:
        return round(total, 6), "fallback", missing_models
    return round(total, 6), "exact", missing_models


def build_report(
    task: Task,
    result: PipelineResult,
    elapsed_seconds: float = 0.0,
    pr_url: str = "",
    orchestrator_attempts: int = 0,
    orchestrator_model: str = "",
    coding_agent_model: str = "",
    orchestrator_summary: str = "",
    models_used: dict[str, str] | None = None,
    call_logs_dir: Path | None = None,
) -> TaskReport:
    """PipelineResult → TaskReport 변환."""
    test_count = 0
    test_summary = ""
    if result.test_result:
        test_summary = result.test_result.summary
        # "7 passed in 1.2s" 형식에서 숫자 추출
        parts = test_summary.split()
        if parts and parts[0].isdigit():
            test_count = int(parts[0])

    reviewer_verdict = ""
    reviewer_feedback = ""
    if result.review:
        reviewer_verdict = result.review.verdict
        reviewer_feedback = result.review.details

    failure_reasons = []
    if result.failure_reason:
        failure_reasons = [result.failure_reason]
    elif result.test_result and result.test_result.failed_tests:
        failure_reasons = result.test_result.failed_tests

    pr_number: int | None = None
    if pr_url:
        try:
            pr_number = int(pr_url.rstrip("/").split("/")[-1])
        except (ValueError, IndexError):
            pass

    m = result.metrics
    _mu = models_used or result.models_used or {}

    # 4-tuple 호환 집계: (input, output, cached_read, cached_write)
    _total_input = 0
    _total_output = 0
    _total_cached_read = 0
    _total_cached_write = 0
    _token_usage: dict[str, dict[str, int]] = {}
    for _role in set(m.token_usage.keys()) | set(_mu.keys()):
        _t = m.token_usage.get(_role, (0, 0, 0, 0))
        if isinstance(_t, (tuple, list)):
            _inp = _t[0] if len(_t) > 0 else 0
            _out = _t[1] if len(_t) > 1 else 0
            _cr = _t[2] if len(_t) > 2 else 0
            _cw = _t[3] if len(_t) > 3 else 0
        else:
            _inp, _out, _cr, _cw = 0, 0, 0, 0
        _total_input += _inp
        _total_output += _out
        _total_cached_read += _cr
        _total_cached_write += _cw
        _token_usage[_role] = {
            "input": _inp, "output": _out,
            "cached_read": _cr, "cached_write": _cw,
        }

    _total_tokens = _total_input + _total_output + _total_cached_read
    _cache_hit_rate = (
        round(_total_cached_read / (_total_input + _total_cached_read), 4)
        if (_total_input + _total_cached_read) > 0 else 0.0
    )
    _cost_usd, _cost_quality, _missing_models = _calculate_cost_with_quality(
        m.token_usage, _mu
    )

    # JSONL per-call 로그 저장
    if m.call_logs:
        from core.token_log import write_call_log
        for _log_role, _entries in m.call_logs.items():
            write_call_log(task.id, _log_role, _entries, log_dir=call_logs_dir)

    # T2: iteration 시계열 요약 집계.
    # max_single_iteration_tokens 는 역할 무관 전체에서의 최대 단일 iteration 토큰.
    # iteration_count_by_role 은 compaction 이벤트를 제외한 실제 LLM 호출 수.
    _max_single_iter_tokens = 0
    _iter_count_by_role: dict[str, int] = {}
    for _log_role, _entries in (m.call_logs or {}).items():
        for _entry in _entries:
            # compaction 이벤트는 LLM chat 호출이 아니므로 iteration 카운트 제외.
            if _entry.get("event") == "compaction":
                continue
            _iter_count_by_role[_log_role] = _iter_count_by_role.get(_log_role, 0) + 1
            # iteration 당 토큰: input + output + cached_read (총 컨텍스트 소모량).
            _iter_tokens = (
                (_entry.get("input_tokens") or 0)
                + (_entry.get("output_tokens") or 0)
                + (_entry.get("cached_read_tokens") or 0)
            )
            if _iter_tokens > _max_single_iter_tokens:
                _max_single_iter_tokens = _iter_tokens
    report = TaskReport(
        task_id=task.id,
        title=task.title,
        status="COMPLETED" if result.succeeded else "FAILED",
        completed_at=datetime.now(timezone.utc).isoformat(),
        retry_count=task.retry_count,
        total_tokens=_total_tokens,
        cost_usd=_cost_usd,
        test_count=test_count,
        test_pass_first_try=(task.retry_count == 0 and result.succeeded),
        reviewer_verdict=reviewer_verdict,
        time_elapsed_seconds=round(elapsed_seconds, 1),
        failure_reasons=failure_reasons,
        test_output_summary=test_summary,
        reviewer_feedback=reviewer_feedback,
        pr_number=pr_number,
        branch=task.branch_name,
        orchestrator_attempts=orchestrator_attempts,
        orchestrator_model=orchestrator_model,
        coding_agent_model=coding_agent_model,
        orchestrator_summary=orchestrator_summary,
        quality_gate_rejections=m.quality_gate_rejections,
        quality_gate_reasons=m.quality_gate_reasons,
        quality_gate_verdict=m.quality_gate_verdict,
        quality_gate_rule_results=list(m.quality_gate_rule_results),
        test_red_to_green_first_try=m.test_red_to_green_first_try,
        impl_retries=m.impl_retries,
        review_retries=m.review_retries,
        dep_files_injected=m.dep_files_injected,
        failed_stage=m.failed_stage,
        models_used=_mu or None,
        total_cached_read_tokens=_total_cached_read,
        total_cached_write_tokens=_total_cached_write,
        cache_hit_rate=_cache_hit_rate,
        token_usage=_token_usage or None,
        cost_estimation_quality=_cost_quality,
        max_single_iteration_tokens=_max_single_iter_tokens,
        iteration_count_by_role=_iter_count_by_role,
    )

    # 불가능한 상태 조합 감지 — explicit raise (Python -O 에서도 활성화)
    _assert_invariants(report)

    return report


def save_report(report: TaskReport, reports_dir: Path = _REPORTS_DIR) -> Path:
    """Task Report를 YAML로 저장하고 경로를 반환한다."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{report.task_id}.yaml"
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(report.to_dict(), f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    logger.info("Task Report 저장: %s", path)
    return path


def load_report(task_id: str, reports_dir: Path = _REPORTS_DIR) -> TaskReport:
    """저장된 Task Report를 로드한다."""
    path = reports_dir / f"{task_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Task Report 없음: {path}")
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return TaskReport.from_dict(data)


def load_reports(
    since: datetime | None = None,
    reports_dir: Path = _REPORTS_DIR,
) -> list[TaskReport]:
    """reports_dir 내 모든 Task Report를 로드한다. since 지정 시 이후 항목만."""
    if not reports_dir.exists():
        return []

    reports = []
    for path in sorted(reports_dir.glob("task-*.yaml")):
        try:
            with path.open(encoding="utf-8") as f:
                data = yaml.safe_load(f)
            report = TaskReport.from_dict(data)
            if since is not None:
                completed = datetime.fromisoformat(report.completed_at)
                if completed.tzinfo is None:
                    completed = completed.replace(tzinfo=timezone.utc)
                if completed < since:
                    continue
            reports.append(report)
        except Exception as e:
            logger.warning("Task Report 로드 실패 (%s): %s", path, e)

    return reports
