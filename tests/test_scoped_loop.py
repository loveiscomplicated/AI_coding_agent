"""
tests/test_scoped_loop.py

agents/scoped_loop.py 단위 테스트.
LLM 호출 없이 도구 제약·경로 검증 로직만 검증한다.

실행:
    pytest tests/test_scoped_loop.py -v
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from agents.roles import TEST_WRITER, IMPLEMENTER, REVIEWER, RoleConfig
from agents.scoped_loop import ScopedReactLoop, _infer_provider
from core.loop import ToolCall


# ── 픽스처 ────────────────────────────────────────────────────────────────────


def _make_mock_llm(model_name: str = "ClaudeClient"):
    """ClaudeClient 처럼 행동하는 mock LLM."""
    llm = MagicMock()
    llm.config = MagicMock()
    llm.config.system_prompt = "original prompt"
    type(llm).__name__ = model_name
    return llm


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "src").mkdir()
    (ws / "tests").mkdir()
    return ws


@pytest.fixture
def test_writer_loop(workspace):
    llm = _make_mock_llm()
    return ScopedReactLoop(llm=llm, role=TEST_WRITER, workspace_dir=workspace)


@pytest.fixture
def reviewer_loop(workspace):
    llm = _make_mock_llm()
    return ScopedReactLoop(llm=llm, role=REVIEWER, workspace_dir=workspace)


# ── _infer_provider ───────────────────────────────────────────────────────────


class TestInferProvider:
    def test_claude_client(self):
        llm = _make_mock_llm("ClaudeClient")
        assert _infer_provider(llm) == "anthropic"

    def test_openai_client(self):
        llm = _make_mock_llm("OpenaiClient")
        assert _infer_provider(llm) == "openai"

    def test_ollama_client(self):
        llm = _make_mock_llm("OllamaClient")
        assert _infer_provider(llm) == "ollama"

    def test_unknown_defaults_to_anthropic(self):
        llm = _make_mock_llm("SomeOtherClient")
        assert _infer_provider(llm) == "anthropic"


# ── 스키마 필터링 ──────────────────────────────────────────────────────────────


class TestScopedSchema:
    def test_test_writer_schema_excludes_shell(self, workspace):
        llm = _make_mock_llm()
        loop = ScopedReactLoop(llm=llm, role=TEST_WRITER, workspace_dir=workspace)
        tool_names = {t["name"] for t in loop.TOOLS_SCHEMA}
        # TEST_WRITER 는 execute_command, git_* 를 허용하지 않음
        assert "execute_command" not in tool_names
        assert "git_commit" not in tool_names

    def test_test_writer_schema_includes_write_file(self, workspace):
        llm = _make_mock_llm()
        loop = ScopedReactLoop(llm=llm, role=TEST_WRITER, workspace_dir=workspace)
        tool_names = {t["name"] for t in loop.TOOLS_SCHEMA}
        assert "write_file" in tool_names

    def test_test_writer_schema_excludes_edit_file(self, workspace):
        llm = _make_mock_llm()
        loop = ScopedReactLoop(llm=llm, role=TEST_WRITER, workspace_dir=workspace)
        tool_names = {t["name"] for t in loop.TOOLS_SCHEMA}
        # TEST_WRITER 는 edit_file 불필요
        assert "edit_file" not in tool_names

    def test_reviewer_schema_excludes_all_write_tools(self, workspace):
        llm = _make_mock_llm()
        loop = ScopedReactLoop(llm=llm, role=REVIEWER, workspace_dir=workspace)
        tool_names = {t["name"] for t in loop.TOOLS_SCHEMA}
        assert "write_file" not in tool_names
        assert "edit_file" not in tool_names
        assert "append_to_file" not in tool_names

    def test_implementer_schema_includes_all_write_tools(self, workspace):
        llm = _make_mock_llm()
        loop = ScopedReactLoop(llm=llm, role=IMPLEMENTER, workspace_dir=workspace)
        tool_names = {t["name"] for t in loop.TOOLS_SCHEMA}
        assert "write_file" in tool_names
        assert "edit_file" in tool_names
        assert "append_to_file" in tool_names


# ── _execute_tool: 허용 목록 ─────────────────────────────────────────────────


class TestAllowedToolsEnforcement:
    def test_blocks_disallowed_tool(self, reviewer_loop):
        tc = ToolCall(id="1", name="write_file", input={"path": "x.py", "content": ""})
        result = reviewer_loop._execute_tool(tc)
        assert result.is_error is True
        assert "역할 제약" in result.content

    def test_allows_read_file_for_reviewer(self, reviewer_loop, workspace):
        # read_file 은 허용 — 실제 파일 접근이 아닌 도구 레지스트리 호출 검증
        (workspace / "src" / "foo.py").write_text("x = 1")
        tc = ToolCall(
            id="2",
            name="read_file",
            input={"path": str(workspace / "src" / "foo.py")},
        )
        result = reviewer_loop._execute_tool(tc)
        # 역할 제약 에러는 없어야 함 (파일 존재하면 성공, 없으면 파일 에러)
        assert "역할 제약" not in result.content

    def test_blocks_git_commit_for_test_writer(self, test_writer_loop):
        tc = ToolCall(id="3", name="git_commit", input={"message": "test"})
        result = test_writer_loop._execute_tool(tc)
        assert result.is_error is True
        assert "역할 제약" in result.content


# ── _execute_tool: workspace 경로 격리 ───────────────────────────────────────


class TestWorkspaceIsolation:
    def test_blocks_write_outside_workspace(self, workspace):
        llm = _make_mock_llm()
        loop = ScopedReactLoop(llm=llm, role=IMPLEMENTER, workspace_dir=workspace)
        tc = ToolCall(
            id="4",
            name="write_file",
            input={"path": "/etc/passwd", "content": "bad"},
        )
        result = loop._execute_tool(tc)
        assert result.is_error is True
        assert "workspace 격리" in result.content

    def test_allows_write_inside_workspace(self, workspace):
        llm = _make_mock_llm()
        loop = ScopedReactLoop(llm=llm, role=IMPLEMENTER, workspace_dir=workspace)
        target = workspace / "src" / "new_file.py"
        tc = ToolCall(
            id="5",
            name="write_file",
            input={"path": str(target), "content": "x = 1\n"},
        )
        result = loop._execute_tool(tc)
        assert "workspace 격리" not in result.content

    def test_allows_relative_path_inside_workspace(self, workspace):
        llm = _make_mock_llm()
        loop = ScopedReactLoop(llm=llm, role=IMPLEMENTER, workspace_dir=workspace)
        # 상대 경로는 workspace 기준으로 해석
        tc = ToolCall(
            id="6",
            name="write_file",
            input={"path": "src/relative.py", "content": "pass\n"},
        )
        result = loop._execute_tool(tc)
        assert "workspace 격리" not in result.content

    def test_blocks_path_traversal(self, workspace):
        llm = _make_mock_llm()
        loop = ScopedReactLoop(llm=llm, role=IMPLEMENTER, workspace_dir=workspace)
        tc = ToolCall(
            id="7",
            name="write_file",
            input={"path": str(workspace / ".." / ".." / "evil.py"), "content": ""},
        )
        result = loop._execute_tool(tc)
        assert result.is_error is True
        assert "workspace 격리" in result.content

    def test_read_tools_bypass_path_check(self, workspace):
        """read_file 은 workspace 밖이어도 경로 검사 대상이 아니다."""
        llm = _make_mock_llm()
        loop = ScopedReactLoop(llm=llm, role=IMPLEMENTER, workspace_dir=workspace)
        tc = ToolCall(
            id="8",
            name="read_file",
            input={"path": "/etc/hosts"},
        )
        result = loop._execute_tool(tc)
        # workspace 격리 에러가 아닌 일반 파일 에러여야 함
        assert "workspace 격리" not in result.content


# ── RoleConfig ────────────────────────────────────────────────────────────────


class TestRoleConfig:
    def test_test_writer_allows_write_file(self):
        assert TEST_WRITER.allows("write_file") is True

    def test_test_writer_disallows_edit_file(self):
        assert TEST_WRITER.allows("edit_file") is False

    def test_reviewer_disallows_all_write(self):
        assert REVIEWER.allows("write_file") is False
        assert REVIEWER.allows("edit_file") is False
        assert REVIEWER.allows("append_to_file") is False

    def test_reviewer_can_write_is_false(self):
        assert REVIEWER.can_write() is False

    def test_implementer_can_write_is_true(self):
        assert IMPLEMENTER.can_write() is True

    def test_prompts_are_non_empty(self):
        assert len(TEST_WRITER.system_prompt) > 100
        assert len(IMPLEMENTER.system_prompt) > 100
        assert len(REVIEWER.system_prompt) > 100


# ── 쓰기 의도 카운터 (경로·역할 거절과 무관하게 intent 를 기록) ─────────────


class TestWriteIntentCounter:
    """경로·역할 검사로 조기 반환돼도 write_file 호출 횟수는 집계돼야 한다.

    회귀 가드: 이전에는 early-return 전에 카운터 업데이트가 없어, workspace 밖
    쓰기 시도가 `[NO_WRITE]` 로 오분류됐다.
    """

    def test_workspace_violation_still_increments_write_counter(self, workspace):
        llm = _make_mock_llm()
        loop = ScopedReactLoop(llm=llm, role=IMPLEMENTER, workspace_dir=workspace)
        tc = ToolCall(
            id="w1", name="write_file",
            input={"path": "/etc/passwd", "content": "bad"},
        )
        result = loop._execute_tool(tc)
        assert result.is_error is True
        assert "workspace 격리" in result.content
        # 의도 집계: 경로가 막혔어도 1회 호출로 기록
        assert loop.write_file_count == 1

    def test_role_violation_still_increments_write_counter(self, reviewer_loop):
        # REVIEWER 는 write_file 금지. 호출이 거절돼도 의도는 기록.
        tc = ToolCall(
            id="w2", name="write_file",
            input={"path": "out.py", "content": "x"},
        )
        result = reviewer_loop._execute_tool(tc)
        assert result.is_error is True
        assert reviewer_loop.write_file_count == 1

    def test_edit_file_violation_still_increments_edit_counter(self, workspace):
        llm = _make_mock_llm()
        loop = ScopedReactLoop(llm=llm, role=IMPLEMENTER, workspace_dir=workspace)
        tc = ToolCall(
            id="e1", name="edit_file",
            input={"path": "/etc/hosts", "old_str": "a", "new_str": "b"},
        )
        _ = loop._execute_tool(tc)
        assert loop.edit_file_count == 1

    def test_read_tool_path_collected_in_explored_paths(self, workspace):
        llm = _make_mock_llm()
        loop = ScopedReactLoop(llm=llm, role=IMPLEMENTER, workspace_dir=workspace)
        (workspace / "src" / "foo.py").write_text("x = 1")
        tc = ToolCall(
            id="r1", name="read_file",
            input={"path": str(workspace / "src" / "foo.py")},
        )
        _ = loop._execute_tool(tc)
        # 경로는 절대 경로로 정규화된 후 explored_paths 에 들어간다
        assert any("src/foo.py" in p for p in loop.explored_paths)
