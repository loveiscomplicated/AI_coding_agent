from __future__ import annotations

from unittest.mock import MagicMock

from cli.config import default_role_models
from cli.read_only_runner import (
    READ_ONLY_TOOL_NAMES,
    ReadOnlyReactLoop,
    ReadOnlyRunner,
)
from llm.base import Message
from core.loop import ToolCall
from tools.shell_tools import execute_readonly_command


class _DummyLLM:
    def __init__(self) -> None:
        self.config = MagicMock(system_prompt="")


class _CodeBlockLLM:
    def __init__(self) -> None:
        self.config = MagicMock(system_prompt="")
        self.chat_calls = 0

    def build_messages(self, user_input, history=None):
        return [Message(role="user", content=user_input)]

    def chat(self, messages, **kwargs):
        self.chat_calls += 1
        return MagicMock(
            stop_reason="end_turn",
            content=[{"type": "text", "text": "```python\nprint('readonly')\n```"}],
        )


def test_execute_readonly_command_allows_git_diff():
    result = execute_readonly_command(["git", "diff", "--stat"])
    assert result.success is True


def test_execute_readonly_command_rejects_git_commit():
    result = execute_readonly_command(["git", "commit", "-m", "x"])
    assert result.success is False
    assert result.error is not None
    assert "READ_ONLY_POLICY_VIOLATION" in result.error


def test_execute_readonly_command_rejects_shell_tokens():
    result = execute_readonly_command(["rg", "TODO", "|", "wc"])
    assert result.success is False
    assert result.error is not None
    assert "READ_ONLY_POLICY_VIOLATION" in result.error


def test_readonly_loop_rejects_write_tool_before_execution():
    loop = ReadOnlyReactLoop(
        llm=_DummyLLM(),
        max_iterations=1,
        tools_schema=[],
        tool_caller=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not execute")),
        allowed_tools=READ_ONLY_TOOL_NAMES,
    )

    result = loop._execute_tool(ToolCall(id="1", name="write_file", input={"path": "a.py", "content": "x"}))

    assert result.is_error is True
    assert "READ_ONLY_POLICY_VIOLATION" in result.content


def test_readonly_runner_preserves_configured_max_iterations():
    runner = ReadOnlyRunner(default_role_models=default_role_models(), max_iterations=13)
    assert runner.max_iterations == 13


def test_readonly_loop_allows_code_block_answer_without_write_nudge():
    llm = _CodeBlockLLM()
    loop = ReadOnlyReactLoop(
        llm=llm,
        max_iterations=2,
        tools_schema=[{"name": "read_file"}],
        tool_caller=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not execute")),
        allowed_tools=READ_ONLY_TOOL_NAMES,
        allowed_tool_names=READ_ONLY_TOOL_NAMES,
    )

    result = loop.run("코드를 읽어서 설명해줘")

    assert result.succeeded is True
    assert result.answer == "```python\nprint('readonly')\n```"
    assert llm.chat_calls == 1
