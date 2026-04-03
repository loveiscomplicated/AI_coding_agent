"""
agents/scoped_loop.py — 역할별 제약이 적용된 ReactLoop 래퍼

ScopedReactLoop 은 기존 ReactLoop 을 상속하며 다음 세 가지 제약을 추가한다:

1. **시스템 프롬프트 교체**: 역할 전용 지시문을 LLM 에 주입
2. **도구 허용 목록**: 역할에 허용된 도구만 스키마에 포함, 미허용 도구 호출 시 차단
3. **workspace 격리**: 파일 쓰기 경로가 workspace_dir 밖이면 차단

사용 예:
    from agents.roles import TEST_WRITER
    from agents.scoped_loop import ScopedReactLoop

    loop = ScopedReactLoop(llm=haiku_client, role=TEST_WRITER, workspace_dir=ws.path)
    result = loop.run(task_prompt)
    print(result.answer)
    print(result.workspace_files)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from agents.roles import RoleConfig, WRITE_TOOLS
from core.loop import ReactLoop, ToolCall, ToolResult, LoopResult
from tools.registry import TOOL_REGISTRY, _build_tools_schema

logger = logging.getLogger(__name__)

# 경로 검증이 필요한 쓰기 도구 (input 의 "path" 키를 검사)
_PATH_ARG_WRITE_TOOLS = frozenset(WRITE_TOOLS)

# 상대 경로를 workspace_dir 기준으로 정규화해야 하는 읽기 도구
# (절대 경로면 그대로, 상대 경로면 workspace_dir / path 로 변환)
_PATH_ARG_READ_TOOLS = frozenset([
    "read_file", "read_file_lines", "list_directory",
    "search_in_file", "get_outline", "get_function_src", "get_imports",
])


@dataclass
class ScopedResult:
    """ScopedReactLoop.run() 의 반환값."""

    answer: str
    succeeded: bool
    workspace_files: list[str] = field(default_factory=list)
    loop_result: LoopResult | None = None


class ScopedReactLoop(ReactLoop):
    """
    역할(RoleConfig)과 workspace 경로 제약이 적용된 ReactLoop.

    ReactLoop 의 _execute_tool 을 오버라이드해서:
    - 미허용 도구 호출 → 에러 반환 (LLM 이 스스로 수정하도록)
    - workspace 밖 쓰기 → 에러 반환
    """

    def __init__(
        self,
        llm,
        role: RoleConfig,
        workspace_dir: str | Path,
        max_iterations: int = 20,
        on_progress=None,
        write_deadline: int | None = None,
        stop_check=None,
        **kwargs,
    ):
        if on_progress is not None:
            kwargs["on_iteration"] = lambda data: on_progress(
                {"type": "agent_iteration", **data}
            )
        super().__init__(llm=llm, max_iterations=max_iterations, write_deadline=write_deadline, stop_check=stop_check, **kwargs)
        self._role = role
        self._workspace_dir = Path(workspace_dir).resolve()

        # 읽기 전용 도구 연속 호출 감지 (탐색 루프 방지)
        self._consecutive_readonly = 0
        self._readonly_tool_names = frozenset({"read_file", "list_directory", "search_files"})
        self._write_tool_names = frozenset({"write_file", "edit_file"})
        self._readonly_warn_threshold = 5  # TODO: RoleConfig에서 설정 가능하게 확장

        # 허용 도구만 포함한 스키마로 교체
        self.TOOLS_SCHEMA = self._build_scoped_schema()

    # ── 공개 인터페이스 ───────────────────────────────────────────────────────

    def run(
        self,
        user_message: str,
        history=None,
    ) -> ScopedResult:
        """
        역할 시스템 프롬프트를 적용해 루프를 실행하고 ScopedResult 를 반환한다.
        """
        original_prompt = self.llm.config.system_prompt
        self.llm.config.system_prompt = self._role.system_prompt
        try:
            loop_result: LoopResult = super().run(user_message, history=history)
        finally:
            self.llm.config.system_prompt = original_prompt

        workspace_files = self._scan_workspace()
        return ScopedResult(
            answer=loop_result.answer,
            succeeded=loop_result.succeeded,
            workspace_files=workspace_files,
            loop_result=loop_result,
        )

    # ── 오버라이드: 도구 실행 전 제약 검사 ───────────────────────────────────

    def _execute_tool(self, tc: ToolCall) -> ToolResult:
        # 1. 허용 목록 검사
        if not self._role.allows(tc.name):
            msg = (
                f"[역할 제약] '{self._role.name}' 역할에서는 '{tc.name}' 도구를 "
                f"사용할 수 없습니다. 허용된 도구: {list(self._role.allowed_tools)}"
            )
            logger.warning(msg)
            return ToolResult(tool_use_id=tc.id, content=msg, is_error=True)

        # 2. workspace 경로 검사 + 상대 경로 정규화
        if tc.name in _PATH_ARG_WRITE_TOOLS:
            path_str = tc.input.get("path", "")
            if path_str:
                violation = self._check_workspace_path(path_str)
                if violation:
                    return ToolResult(
                        tool_use_id=tc.id, content=violation, is_error=True
                    )
                # 검증은 workspace 기준으로 통과했지만, 실제 write_file은 CWD 기준으로
                # Path(path)를 해석한다. 상대 경로를 절대 경로로 정규화해서 일치시킨다.
                p = Path(path_str)
                if not p.is_absolute():
                    absolute_path = str((self._workspace_dir / path_str).resolve())
                    tc = ToolCall(id=tc.id, name=tc.name, input={**tc.input, "path": absolute_path})

        elif tc.name in _PATH_ARG_READ_TOOLS:
            # 읽기 도구: 상대 경로를 workspace_dir 기준으로 정규화
            # (절대 경로는 그대로 — 에이전트가 명시적으로 지정한 경우)
            path_str = tc.input.get("path", "")
            if path_str and not Path(path_str).is_absolute():
                absolute_path = str((self._workspace_dir / path_str).resolve())
                tc = ToolCall(id=tc.id, name=tc.name, input={**tc.input, "path": absolute_path})

        result = super()._execute_tool(tc)

        # ── 읽기 전용 도구 연속 호출 카운터 업데이트 ──────────────────────────
        if tc.name in self._write_tool_names:
            self._consecutive_readonly = 0
        elif tc.name in self._readonly_tool_names:
            self._consecutive_readonly += 1
        # 그 외 도구(ask_user 등): 카운터 변경 없음

        # 임계값 도달 시 경고를 tool result에 주입 (LLM이 user 메시지로 수신)
        if self._consecutive_readonly >= self._readonly_warn_threshold:
            warning = (
                f"\n\n⚠️ [SYSTEM WARNING] 읽기 전용 도구를 {self._consecutive_readonly}회 연속 호출했습니다. "
                f"탐색을 멈추고 반드시 write_file 또는 edit_file을 호출하여 코드를 작성하세요. "
                f"필요한 정보가 부족해도 현재 가진 정보로 최선의 코드를 작성하세요."
            )
            logger.warning(
                "[readonly_guard] 읽기 전용 도구 %d회 연속 — 경고 주입",
                self._consecutive_readonly,
            )
            result = ToolResult(
                tool_use_id=result.tool_use_id,
                content=result.content + warning,
                is_error=result.is_error,
            )

        return result

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────────────

    def _build_scoped_schema(self) -> list[dict]:
        """허용된 도구만 포함한 tools 스키마를 생성한다."""
        filtered_registry = {
            name: meta
            for name, meta in TOOL_REGISTRY.items()
            if name in self._role.allowed_tools
        }
        provider = _infer_provider(self.llm)
        return _build_tools_schema(filtered_registry, provider)

    def _check_workspace_path(self, path_str: str) -> str | None:
        """
        경로가 workspace_dir 밖이거나 역할의 blocked_write_dirs 안이면 오류를 반환한다.

        상대 경로는 workspace_dir 기준으로 해석한다.
        """
        try:
            path = Path(path_str)
            if not path.is_absolute():
                path = self._workspace_dir / path
            resolved = path.resolve()
        except Exception:
            return f"[경로 오류] 잘못된 경로: {path_str!r}"

        # workspace 범위 검사
        try:
            resolved.relative_to(self._workspace_dir)
        except ValueError:
            return (
                f"[workspace 격리] workspace 밖 경로 접근 금지.\n"
                f"  요청 경로: {resolved}\n"
                f"  허용 범위: {self._workspace_dir}\n"
                f"  파일은 반드시 workspace 안에 저장하세요."
            )

        # 역할별 쓰기 금지 디렉토리 검사
        for blocked_rel in self._role.blocked_write_dirs:
            blocked_abs = (self._workspace_dir / blocked_rel).resolve()
            try:
                resolved.relative_to(blocked_abs)
                return (
                    f"[역할 제약] '{self._role.name}' 역할에서는 "
                    f"'{blocked_rel}/' 디렉토리에 쓸 수 없습니다.\n"
                    f"  요청 경로: {resolved}\n"
                    f"  이 역할의 쓰기 금지 디렉토리: {list(self._role.blocked_write_dirs)}"
                )
            except ValueError:
                pass  # 이 blocked_dir 안에 없음, 다음 검사로

        return None  # 정상

    def _scan_workspace(self) -> list[str]:
        """workspace 안의 모든 파일을 상대 경로 목록으로 반환한다."""
        if not self._workspace_dir.exists():
            return []
        return [
            str(p.relative_to(self._workspace_dir))
            for p in sorted(self._workspace_dir.rglob("*"))
            if p.is_file()
        ]


# ── 모듈 수준 헬퍼 ────────────────────────────────────────────────────────────


def _infer_provider(llm) -> str:
    """LLM 클라이언트 타입에서 provider 이름을 추론한다."""
    name = type(llm).__name__
    mapping = {
        "ClaudeClient": "anthropic",
        "OpenaiClient": "openai",
        "GlmClient": "openai",
        "OllamaClient": "ollama",
    }
    return mapping.get(name, "anthropic")
