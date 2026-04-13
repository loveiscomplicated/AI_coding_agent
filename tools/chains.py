"""
tools/chains.py — Multi-Step Tool Chaining

ToolChain 은 여러 도구 호출을 순차(SEQUENTIAL) 또는 병렬(PARALLEL)로
묶어 하나의 '가상 도구'로 LLM에게 제공하는 체인 실행 엔진이다.

내장 체인:
    safe_edit        : 의존성 확인 → 파일 읽기 → 수정 → lint 검증
    verified_commit  : 테스트 → lint → git add → git commit

사용 예 (직접):
    result = SAFE_EDIT_CHAIN.execute(
        context={"path": "src/main.py", "old_str": "foo", "new_str": "bar"},
        tool_executor=call_tool,
    )

사용 예 (가상 도구로):
    # tools/registry.py 에서 자동 등록됨.
    # LLM은 safe_edit 도구를 일반 도구처럼 호출할 수 있다.
"""

from __future__ import annotations

import concurrent.futures
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


# ── 열거형 ────────────────────────────────────────────────────────────────────

class ChainMode(str, Enum):
    SEQUENTIAL = "sequential"  # 순서대로 실행, 이전 결과를 다음 입력으로 전달
    PARALLEL = "parallel"      # 모든 스텝 동시 실행 (서로 독립적인 스텝만)


class RollbackPolicy(str, Enum):
    STOP = "stop"    # 실패 시 체인 중단, 에러 반환 (기본)
    NONE = "none"    # 실패를 무시하고 계속 진행


# ── 데이터 클래스 ─────────────────────────────────────────────────────────────

@dataclass
class ChainStep:
    """
    체인 내 단일 스텝.

    input_template 의 "{key}" 플레이스홀더는 실행 시 context[key] 값으로 교체된다.

    Parameters
    ----------
    tool_name       : TOOL_REGISTRY 에 등록된 도구 이름
    input_template  : 도구 인수 템플릿 (플레이스홀더 포함 가능)
    condition       : Callable[[context], bool]. None 이면 항상 실행.
                      False 반환 시 이 스텝을 스킵 (오류 없이 다음으로 진행).
    input_resolver  : Callable[[context], dict]. 정적 템플릿 대신 동적으로
                      인수를 계산할 때 사용. 지정 시 input_template 무시.
    """

    tool_name: str
    input_template: dict[str, Any] = field(default_factory=dict)
    condition: Callable[[dict], bool] | None = None
    input_resolver: Callable[[dict], dict] | None = None


@dataclass
class StepResult:
    """단일 스텝 실행 결과."""

    step_index: int
    tool_name: str
    output: str
    is_error: bool
    skipped: bool = False


@dataclass
class ChainResult:
    """ToolChain.execute() 반환값."""

    chain_name: str
    succeeded: bool
    step_results: list[StepResult] = field(default_factory=list)
    error_step: int | None = None      # 실패가 발생한 스텝 인덱스 (0-indexed)
    error_message: str = ""

    def summary(self, max_chars_per_step: int = 300) -> str:
        """LLM이 읽기 좋은 1-줄 요약 형식."""
        lines = [f"[Chain: {self.chain_name}] {'성공' if self.succeeded else '실패'}"]
        for sr in self.step_results:
            prefix = "  ✓" if not sr.is_error and not sr.skipped else ("  ↷" if sr.skipped else "  ✗")
            snippet = sr.output[:max_chars_per_step].replace("\n", " ")
            lines.append(f"{prefix} [{sr.tool_name}] {snippet}")
        return "\n".join(lines)


# ── 플레이스홀더 치환 헬퍼 ────────────────────────────────────────────────────

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def _resolve_template(template: Any, context: dict[str, Any]) -> Any:
    """
    dict/list/str 안의 "{key}" 패턴을 context[key] 로 재귀 교체한다.

    예:
        _resolve_template({"command": ["pytest", "{path}"]}, {"path": "src/"})
        → {"command": ["pytest", "src/"]}
    """
    if isinstance(template, str):
        def _replace(m: re.Match) -> str:
            key = m.group(1)
            val = context.get(key, m.group(0))  # 키 없으면 원본 유지
            return str(val) if not isinstance(val, str) else val
        return _PLACEHOLDER_RE.sub(_replace, template)
    if isinstance(template, list):
        return [_resolve_template(item, context) for item in template]
    if isinstance(template, dict):
        return {k: _resolve_template(v, context) for k, v in template.items()}
    return template


# ── ToolChain ─────────────────────────────────────────────────────────────────

class ToolChain:
    """
    도구 체인 정의 및 실행 엔진.

    Parameters
    ----------
    name             : 체인 이름 (TOOL_REGISTRY 가상 도구 이름으로도 사용)
    steps            : ChainStep 목록
    description      : LLM에게 표시할 도구 설명
    mode             : SEQUENTIAL(기본) | PARALLEL
    rollback_policy  : STOP(기본) | NONE
    """

    def __init__(
        self,
        name: str,
        steps: list[ChainStep],
        description: str = "",
        mode: ChainMode = ChainMode.SEQUENTIAL,
        rollback_policy: RollbackPolicy = RollbackPolicy.STOP,
    ):
        self.name = name
        self.steps = steps
        self.description = description
        self.mode = mode
        self.rollback_policy = rollback_policy

    # ── 실행 ──────────────────────────────────────────────────────────────────

    def execute(
        self,
        context: dict[str, Any],
        tool_executor: Callable[..., Any],
    ) -> ChainResult:
        """
        체인을 실행한다.

        Args:
            context       : 초기 입력값 (플레이스홀더 치환에 사용).
                            각 스텝 결과는 "{tool_name}_output" 키로 context에 추가된다.
            tool_executor : call_tool-호환 callable — (name: str, **kwargs) → ToolResult-like

        Returns:
            ChainResult
        """
        if self.mode == ChainMode.PARALLEL:
            return self._execute_parallel(context, tool_executor)
        return self._execute_sequential(context, tool_executor)

    def _execute_sequential(
        self,
        context: dict[str, Any],
        tool_executor: Callable,
    ) -> ChainResult:
        ctx = dict(context)  # 로컬 복사본 (context 오염 방지)
        step_results: list[StepResult] = []

        for idx, step in enumerate(self.steps):
            # 조건 검사
            if step.condition is not None and not step.condition(ctx):
                step_results.append(
                    StepResult(step_index=idx, tool_name=step.tool_name,
                               output="[스텝 스킵 — 조건 미충족]", is_error=False, skipped=True)
                )
                continue

            # 인수 해석
            kwargs = self._resolve_inputs(step, ctx)

            # 도구 실행
            sr = self._run_step(idx, step, kwargs, tool_executor)
            step_results.append(sr)

            # context 업데이트 (다음 스텝에서 참조 가능)
            ctx[f"{step.tool_name}_output"] = sr.output

            # 실패 처리
            if sr.is_error and self.rollback_policy == RollbackPolicy.STOP:
                return ChainResult(
                    chain_name=self.name,
                    succeeded=False,
                    step_results=step_results,
                    error_step=idx,
                    error_message=sr.output,
                )

        return ChainResult(
            chain_name=self.name,
            succeeded=True,
            step_results=step_results,
        )

    def _execute_parallel(
        self,
        context: dict[str, Any],
        tool_executor: Callable,
    ) -> ChainResult:
        """모든 스텝을 ThreadPoolExecutor 로 동시 실행."""
        ctx = dict(context)
        step_results: list[StepResult] = [None] * len(self.steps)  # type: ignore[list-item]

        def _run(idx: int, step: ChainStep) -> StepResult:
            if step.condition is not None and not step.condition(ctx):
                return StepResult(step_index=idx, tool_name=step.tool_name,
                                  output="[스텝 스킵 — 조건 미충족]", is_error=False, skipped=True)
            kwargs = self._resolve_inputs(step, ctx)
            return self._run_step(idx, step, kwargs, tool_executor)

        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = {executor.submit(_run, i, s): i for i, s in enumerate(self.steps)}
            for future in concurrent.futures.as_completed(futures):
                idx = futures[future]
                try:
                    step_results[idx] = future.result()
                except Exception as exc:
                    step_results[idx] = StepResult(
                        step_index=idx,
                        tool_name=self.steps[idx].tool_name,
                        output=f"병렬 실행 예외: {exc}",
                        is_error=True,
                    )

        failed = [sr for sr in step_results if sr.is_error]
        if failed and self.rollback_policy == RollbackPolicy.STOP:
            return ChainResult(
                chain_name=self.name,
                succeeded=False,
                step_results=step_results,
                error_step=failed[0].step_index,
                error_message=failed[0].output,
            )

        return ChainResult(
            chain_name=self.name,
            succeeded=True,
            step_results=step_results,
        )

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────────────

    def _resolve_inputs(self, step: ChainStep, ctx: dict) -> dict:
        """input_resolver 우선, 없으면 input_template 플레이스홀더 치환."""
        if step.input_resolver is not None:
            return step.input_resolver(ctx)
        return _resolve_template(step.input_template, ctx)

    def _run_step(
        self,
        idx: int,
        step: ChainStep,
        kwargs: dict,
        tool_executor: Callable,
    ) -> StepResult:
        """단일 스텝을 실행하고 StepResult 로 래핑한다."""
        try:
            result = tool_executor(step.tool_name, **kwargs)
            # result 는 ToolResult-like (success, output, error) 또는 str
            if hasattr(result, "success"):
                is_error = not result.success
                output = result.output if result.success else (result.error or result.output)
            elif hasattr(result, "is_error"):
                is_error = result.is_error
                output = result.content
            else:
                is_error = False
                output = str(result)
        except Exception as exc:
            is_error = True
            output = f"스텝 실행 예외 [{step.tool_name}]: {type(exc).__name__}: {exc}"

        return StepResult(
            step_index=idx,
            tool_name=step.tool_name,
            output=output,
            is_error=is_error,
        )

    def _infer_params(self) -> dict:
        """
        모든 스텝의 input_template 에서 "{key}" 플레이스홀더를 추출해
        TOOL_REGISTRY params 형식의 dict 로 반환한다.

        Returns:
            {"param_name": ("string", description, required, None), ...}
        """
        params: dict[str, tuple] = {}
        for step in self.steps:
            for val in _flatten_values(step.input_template):
                if isinstance(val, str):
                    for m in _PLACEHOLDER_RE.finditer(val):
                        key = m.group(1)
                        if key not in params:
                            params[key] = (
                                "string",
                                f"{self.name} 체인 파라미터: {key}",
                                True,
                                None,
                            )
        return params


def _flatten_values(obj: Any):
    """dict/list 를 재귀 순회하며 리프 값을 yield."""
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _flatten_values(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _flatten_values(item)
    else:
        yield obj


# ── 내장 체인 정의 ────────────────────────────────────────────────────────────

SAFE_EDIT_CHAIN = ToolChain(
    name="safe_edit",
    description=(
        "파일을 안전하게 수정한다: "
        "① 변경할 패턴이 파일에 존재하는지 확인 "
        "② 현재 파일 내용 읽기 "
        "③ 문자열 교체 "
        "④ flake8 lint 검증. "
        "필수 파라미터: path, old_str, new_str."
    ),
    steps=[
        ChainStep(
            tool_name="search_in_file",
            input_template={"path": "{path}", "pattern": "{old_str}"},
        ),
        ChainStep(
            tool_name="read_file",
            input_template={"path": "{path}"},
        ),
        ChainStep(
            tool_name="edit_file",
            input_template={"path": "{path}", "old_str": "{old_str}", "new_str": "{new_str}"},
        ),
        ChainStep(
            tool_name="execute_command",
            input_template={"command": ["python", "-m", "flake8", "{path}", "--max-line-length=120"]},
            condition=lambda ctx: ctx.get("lint_enabled", True),
        ),
    ],
    rollback_policy=RollbackPolicy.STOP,
)

VERIFIED_COMMIT_CHAIN = ToolChain(
    name="verified_commit",
    description=(
        "테스트와 lint 통과 후에만 커밋한다: "
        "① 테스트 실행 ({test_command}) "
        "② lint 검증 ({lint_path}) "
        "③ git add ({paths}) "
        "④ git commit ({message}). "
        "필수 파라미터: test_command, lint_path, repo_path, paths, message."
    ),
    steps=[
        ChainStep(
            tool_name="execute_command",
            input_template={"command": ["{test_command}"]},
            # test_command 가 공백 포함 시 리스트로 처리하도록 input_resolver 사용
            input_resolver=lambda ctx: {
                "command": ctx.get("test_command", "pytest").split()
            },
        ),
        ChainStep(
            tool_name="execute_command",
            input_template={"command": ["python", "-m", "flake8", "{lint_path}", "--max-line-length=120"]},
            condition=lambda ctx: ctx.get("lint_enabled", True),
        ),
        ChainStep(
            tool_name="git_add",
            input_template={"repo_path": "{repo_path}", "paths": "{paths}"},
        ),
        ChainStep(
            tool_name="git_commit",
            input_template={"repo_path": "{repo_path}", "message": "{message}"},
        ),
    ],
    rollback_policy=RollbackPolicy.STOP,
)

# 모듈 로드 시 참조용 내장 체인 dict
BUILTIN_CHAINS: dict[str, ToolChain] = {
    SAFE_EDIT_CHAIN.name: SAFE_EDIT_CHAIN,
    VERIFIED_COMMIT_CHAIN.name: VERIFIED_COMMIT_CHAIN,
}
