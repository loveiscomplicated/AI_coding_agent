"""
cli/read_only_runner.py — CLI read-only 분석 모드 러너
"""

from __future__ import annotations

import asyncio
from typing import Iterable

from core.loop import ReactLoop, ToolCall, ToolResult
from llm import LLMConfig, create_client
from agents.roles import ROLE_INTERVENTION, resolve_model_for_role
from tools.registry import get_tools_schema, make_tool_caller

READ_ONLY_TOOL_NAMES: tuple[str, ...] = (
    "read_file",
    "read_file_lines",
    "list_directory",
    "search_in_file",
    "search_files",
    "get_imports",
    "get_outline",
    "get_function_src",
    "hashline_read",
    "execute_readonly_command",
)

READ_ONLY_DENIED_TOOL_NAMES: frozenset[str] = frozenset({
    "write_file",
    "edit_file",
    "append_to_file",
    "delete_file",
    "delete_directory",
    "hashline_edit",
    "execute_command",
    "git_add",
    "git_commit",
    "stage_group",
    "commit_group",
    "verified_commit",
    "ask_user",
})

_READ_ONLY_SYSTEM_PROMPT = (
    "당신은 코드베이스 읽기/분석 전용 에이전트다.\n"
    "절대로 파일 수정, 커밋, 쓰기 명령 수행, 사용자 승인 요청을 시도하지 마라.\n"
    "사용자가 구현을 요청해도 실행 대신 현재 구조와 구현 방향을 분석해서 답하라.\n"
    "답변은 체계적으로 작성하되 과장하지 말고, 근거가 되는 파일 경로와 함수/심볼 이름을 포함하라.\n"
    "도구 호출이 정책에 막히면 다른 읽기 전용 도구로 재탐색하라."
)


def _tool_schema_provider(provider: str) -> str:
    if provider in {"openai", "glm", "gemini"}:
        return "openai"
    if provider == "claude":
        return "anthropic"
    if provider == "ollama":
        return "ollama"
    raise ValueError(f"지원하지 않는 provider: {provider}")


class ReadOnlyReactLoop(ReactLoop):
    def __init__(self, *args, allowed_tools: Iterable[str] = READ_ONLY_TOOL_NAMES, **kwargs):
        super().__init__(*args, **kwargs)
        self._allowed_tools = frozenset(allowed_tools)

    def _execute_tool(self, tc: ToolCall) -> ToolResult:
        if tc.name not in self._allowed_tools or tc.name in READ_ONLY_DENIED_TOOL_NAMES:
            return ToolResult(
                tool_use_id=tc.id,
                content=(
                    "READ_ONLY_POLICY_VIOLATION: "
                    f"'{tc.name}' 도구는 ReadOnly 모드에서 허용되지 않습니다. "
                    f"허용 도구: {sorted(self._allowed_tools)}"
                ),
                is_error=True,
            )
        return super()._execute_tool(tc)


class ReadOnlyRunner:
    def __init__(
        self,
        default_role_models: dict[str, dict[str, str]],
        *,
        max_iterations: int = 8,
    ) -> None:
        self.default_role_models = default_role_models
        self.max_iterations = max_iterations

    async def run(self, user_input: str) -> str:
        provider, model = resolve_model_for_role(
            role=ROLE_INTERVENTION,
            role_models=None,
            default_role_models=self.default_role_models,
        )
        client = create_client(
            provider,
            LLMConfig(model=model, max_tokens=4096, system_prompt=_READ_ONLY_SYSTEM_PROMPT),
        )
        loop = ReadOnlyReactLoop(
            llm=client,
            max_iterations=self.max_iterations,
            on_tool_call=None,
            on_tool_result=None,
            on_tool_approval=None,
            tools_schema=get_tools_schema(
                _tool_schema_provider(provider),
                allowed_names=READ_ONLY_TOOL_NAMES,
            ),
            tool_caller=make_tool_caller(READ_ONLY_TOOL_NAMES),
            allowed_tool_names=READ_ONLY_TOOL_NAMES,
            role_name="read_only",
        )
        result = await asyncio.to_thread(
            loop.run,
            (
                "저장소를 읽고 분석해 사용자의 요청에 답하라.\n"
                "구현/수정 요청이면 실행하지 말고 현재 상태와 구현 방향을 분석하라.\n"
                "가능하면 답변을 다음 순서로 구성하라: 요약, 근거, 관련 파일, 구현 시 고려사항.\n\n"
                f"# 사용자 요청\n{user_input}"
            ),
        )
        return result.answer
