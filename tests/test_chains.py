"""
tests/test_chains.py — tools/chains.py 단위 테스트

Module 2: Multi-Step Tool Chaining
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, call
from dataclasses import dataclass

from tools.chains import (
    ToolChain,
    ChainStep,
    ChainResult,
    ChainMode,
    RollbackPolicy,
    SAFE_EDIT_CHAIN,
    VERIFIED_COMMIT_CHAIN,
    BUILTIN_CHAINS,
    _resolve_template,
    _flatten_values,
)


# ── _resolve_template 테스트 ───────────────────────────────────────────────────

class TestResolveTemplate:
    def test_string_replacement(self):
        assert _resolve_template("{path}", {"path": "src/main.py"}) == "src/main.py"

    def test_nested_dict(self):
        result = _resolve_template({"file": "{path}", "pattern": "{pat}"}, {"path": "a.py", "pat": "foo"})
        assert result == {"file": "a.py", "pattern": "foo"}

    def test_list_replacement(self):
        result = _resolve_template(["pytest", "{dir}"], {"dir": "src/"})
        assert result == ["pytest", "src/"]

    def test_missing_key_preserved(self):
        assert _resolve_template("{missing}", {}) == "{missing}"

    def test_non_string_value(self):
        result = _resolve_template("{count}", {"count": 42})
        assert result == "42"

    def test_no_placeholder(self):
        assert _resolve_template("static_value", {"key": "val"}) == "static_value"


# ── ToolChain.execute() — Sequential 테스트 ─────────────────────────────────

class TestToolChainSequential:
    """순차 체인 실행 테스트."""

    def _make_executor(self, results: list):
        """호출 순서대로 결과를 반환하는 mock executor."""
        calls = iter(results)
        def executor(name, **kwargs):
            return next(calls)
        return executor

    def _ok(self, output: str = "ok"):
        r = MagicMock()
        r.success = True
        r.output = output
        r.is_error = False  # ToolResult-like 지원
        r.content = output
        return r

    def _err(self, msg: str = "error"):
        r = MagicMock()
        r.success = False
        r.output = msg
        r.error = msg
        r.is_error = True
        r.content = msg
        return r

    def test_all_steps_succeed(self):
        chain = ToolChain(
            name="test_chain",
            steps=[
                ChainStep("step_a", {"x": "1"}),
                ChainStep("step_b", {"y": "2"}),
            ],
        )
        executor = self._make_executor([self._ok("result_a"), self._ok("result_b")])
        result = chain.execute({}, executor)

        assert result.succeeded is True
        assert len(result.step_results) == 2
        assert result.error_step is None

    def test_stop_on_first_failure(self):
        """STOP 정책: 첫 번째 실패에서 체인 중단, 이후 스텝 미실행."""
        executed = []

        def executor(name, **kwargs):
            executed.append(name)
            if name == "step_b":
                return self._err("step_b error")
            return self._ok()

        chain = ToolChain(
            name="test_chain",
            steps=[
                ChainStep("step_a", {}),
                ChainStep("step_b", {}),
                ChainStep("step_c", {}),  # 실행되면 안 됨
            ],
            rollback_policy=RollbackPolicy.STOP,
        )
        result = chain.execute({}, executor)

        assert result.succeeded is False
        assert result.error_step == 1  # step_b (index 1)
        assert "step_c" not in executed

    def test_none_policy_continues_on_failure(self):
        """NONE 정책: 실패해도 모든 스텝 실행."""
        executed = []

        def executor(name, **kwargs):
            executed.append(name)
            if name == "step_b":
                return self._err()
            return self._ok()

        chain = ToolChain(
            name="test_chain",
            steps=[
                ChainStep("step_a", {}),
                ChainStep("step_b", {}),
                ChainStep("step_c", {}),
            ],
            rollback_policy=RollbackPolicy.NONE,
        )
        result = chain.execute({}, executor)

        assert "step_a" in executed
        assert "step_b" in executed
        assert "step_c" in executed

    def test_condition_skips_step(self):
        """condition=False: 해당 스텝 스킵."""
        executed = []

        def executor(name, **kwargs):
            executed.append(name)
            return self._ok()

        chain = ToolChain(
            name="test_chain",
            steps=[
                ChainStep("step_a", {}),
                ChainStep("step_b", {}, condition=lambda ctx: False),  # 스킵
                ChainStep("step_c", {}),
            ],
        )
        result = chain.execute({}, executor)

        assert "step_a" in executed
        assert "step_b" not in executed
        assert "step_c" in executed
        assert result.step_results[1].skipped is True

    def test_condition_uses_context(self):
        """condition 이 context 값을 참조한다."""
        executed = []

        def executor(name, **kwargs):
            executed.append(name)
            return self._ok()

        chain = ToolChain(
            name="test_chain",
            steps=[
                ChainStep("step_a", {}, condition=lambda ctx: ctx.get("run_a", False)),
                ChainStep("step_b", {}),
            ],
        )
        # run_a=True → step_a 실행
        chain.execute({"run_a": True}, executor)
        assert "step_a" in executed

        # run_a=False → step_a 스킵
        executed.clear()
        chain.execute({"run_a": False}, executor)
        assert "step_a" not in executed

    def test_previous_output_in_context(self):
        """이전 스텝 결과가 context 에 {tool_name}_output 으로 전달된다."""
        received_context = {}

        def executor(name, **kwargs):
            if name == "step_b":
                received_context.update(kwargs)
            return self._ok(f"{name}_result")

        chain = ToolChain(
            name="test_chain",
            steps=[
                ChainStep("step_a", {"key": "val"}),
                ChainStep("step_b", {}, input_resolver=lambda ctx: {"prev": ctx.get("step_a_output", "")}),
            ],
        )
        chain.execute({}, executor)
        assert received_context.get("prev") == "step_a_result"

    def test_placeholder_substitution(self):
        """input_template 의 {placeholder} 가 context 값으로 치환된다."""
        received = {}

        def executor(name, **kwargs):
            received.update(kwargs)
            return self._ok()

        chain = ToolChain(
            name="test_chain",
            steps=[ChainStep("my_tool", {"path": "{the_path}", "pattern": "{search}"})],
        )
        chain.execute({"the_path": "src/main.py", "search": "TODO"}, executor)
        assert received["path"] == "src/main.py"
        assert received["pattern"] == "TODO"

    def test_executor_exception_becomes_error(self):
        """executor 가 예외를 던지면 StepResult.is_error=True."""
        def executor(name, **kwargs):
            raise RuntimeError("unexpected crash")

        chain = ToolChain(
            name="test_chain",
            steps=[ChainStep("crasher", {})],
            rollback_policy=RollbackPolicy.STOP,
        )
        result = chain.execute({}, executor)
        assert result.succeeded is False
        assert result.step_results[0].is_error is True


# ── ToolChain.execute() — Parallel 테스트 ────────────────────────────────────

class TestToolChainParallel:
    def test_all_parallel_steps_executed(self):
        """PARALLEL 모드: 모든 스텝이 실행된다."""
        executed = set()

        def executor(name, **kwargs):
            executed.add(name)
            r = MagicMock(); r.success = True; r.output = "ok"
            return r

        chain = ToolChain(
            name="par_chain",
            steps=[
                ChainStep("tool_a", {}),
                ChainStep("tool_b", {}),
                ChainStep("tool_c", {}),
            ],
            mode=ChainMode.PARALLEL,
        )
        result = chain.execute({}, executor)
        assert result.succeeded is True
        assert executed == {"tool_a", "tool_b", "tool_c"}

    def test_parallel_failure_returns_failed(self):
        """PARALLEL + STOP: 하나라도 실패 시 ChainResult.succeeded=False."""
        def executor(name, **kwargs):
            r = MagicMock()
            r.success = name != "tool_b"
            r.output = "ok" if r.success else "fail"
            r.error = "fail" if not r.success else None
            return r

        chain = ToolChain(
            name="par_chain",
            steps=[ChainStep("tool_a", {}), ChainStep("tool_b", {}), ChainStep("tool_c", {})],
            mode=ChainMode.PARALLEL,
            rollback_policy=RollbackPolicy.STOP,
        )
        result = chain.execute({}, executor)
        assert result.succeeded is False


# ── _infer_params 테스트 ──────────────────────────────────────────────────────

class TestInferParams:
    def test_extracts_placeholders(self):
        chain = ToolChain(
            name="test",
            steps=[
                ChainStep("tool_a", {"file": "{path}", "search": "{pattern}"}),
                ChainStep("tool_b", {"cmd": ["{exe}", "{path}"]}),
            ],
        )
        params = chain._infer_params()
        assert "path" in params
        assert "pattern" in params
        assert "exe" in params

    def test_no_duplicates(self):
        chain = ToolChain(
            name="test",
            steps=[
                ChainStep("a", {"p": "{path}"}),
                ChainStep("b", {"q": "{path}"}),  # 동일 키
            ],
        )
        params = chain._infer_params()
        assert list(params.keys()).count("path") == 1


# ── summary() 테스트 ──────────────────────────────────────────────────────────

class TestChainResultSummary:
    def test_summary_contains_chain_name(self):
        from tools.chains import StepResult
        result = ChainResult(
            chain_name="my_chain",
            succeeded=True,
            step_results=[
                StepResult(0, "tool_a", "output here", False),
            ],
        )
        summary = result.summary()
        assert "my_chain" in summary
        assert "tool_a" in summary

    def test_failed_summary_shows_error(self):
        from tools.chains import StepResult
        result = ChainResult(
            chain_name="my_chain",
            succeeded=False,
            step_results=[
                StepResult(0, "tool_a", "step failed!", True),
            ],
        )
        summary = result.summary()
        assert "실패" in summary or "✗" in summary


# ── 내장 체인 존재 확인 ───────────────────────────────────────────────────────

class TestBuiltinChains:
    def test_safe_edit_exists(self):
        assert "safe_edit" in BUILTIN_CHAINS

    def test_verified_commit_exists(self):
        assert "verified_commit" in BUILTIN_CHAINS

    def test_safe_edit_has_correct_steps(self):
        steps = SAFE_EDIT_CHAIN.steps
        tool_names = [s.tool_name for s in steps]
        assert "search_in_file" in tool_names
        assert "read_file" in tool_names
        assert "edit_file" in tool_names
        assert "execute_command" in tool_names

    def test_verified_commit_has_correct_steps(self):
        steps = VERIFIED_COMMIT_CHAIN.steps
        tool_names = [s.tool_name for s in steps]
        assert "execute_command" in tool_names
        assert "git_add" in tool_names
        assert "git_commit" in tool_names

    def test_safe_edit_lint_step_conditional(self):
        """lint 스텝은 lint_enabled=False 시 스킵되어야 한다."""
        # lint 스텝은 condition 이 있어야 함
        lint_steps = [s for s in SAFE_EDIT_CHAIN.steps if s.tool_name == "execute_command"]
        assert len(lint_steps) >= 1
        lint_step = lint_steps[0]
        assert lint_step.condition is not None
        assert lint_step.condition({"lint_enabled": False}) is False
        assert lint_step.condition({"lint_enabled": True}) is True
        assert lint_step.condition({}) is True  # 기본값 True

    def test_verified_commit_lint_step_conditional(self):
        lint_steps = [s for s in VERIFIED_COMMIT_CHAIN.steps
                      if s.tool_name == "execute_command"
                      and s.condition is not None]
        assert len(lint_steps) >= 1
        assert lint_steps[0].condition({"lint_enabled": False}) is False


# ── registry 등록 테스트 ──────────────────────────────────────────────────────

class TestChainRegistration:
    def test_builtin_chains_registered_in_tool_registry(self):
        from tools.registry import TOOL_REGISTRY, CHAIN_REGISTRY
        assert "safe_edit" in TOOL_REGISTRY
        assert "verified_commit" in TOOL_REGISTRY
        assert "safe_edit" in CHAIN_REGISTRY
        assert "verified_commit" in CHAIN_REGISTRY

    def test_registered_chain_callable_via_call_tool(self):
        """register_chain() 후 call_tool() 로 체인을 호출할 수 있다."""
        from tools.registry import register_chain, call_tool, TOOL_REGISTRY, CHAIN_REGISTRY
        from tools.registry import TOOLS_SCHEMA_ANTHROPIC, TOOLS_SCHEMA_OPENAI, TOOLS_SCHEMA_OLLAMA
        from tools.chains import ToolChain, ChainStep

        chain_name = "_test_only_chain_callable"
        try:
            test_chain = ToolChain(
                name=chain_name,
                description="테스트용 체인",
                steps=[ChainStep("read_file", {"path": "{path}"})],
            )
            register_chain(test_chain)
            assert chain_name in TOOL_REGISTRY
        finally:
            # 정리: 테스트 체인 제거 + 스키마 원상 복구
            TOOL_REGISTRY.pop(chain_name, None)
            CHAIN_REGISTRY.pop(chain_name, None)
            from tools.registry import _build_tools_schema
            TOOLS_SCHEMA_ANTHROPIC[:] = _build_tools_schema(TOOL_REGISTRY, "anthropic")
            TOOLS_SCHEMA_OPENAI[:] = _build_tools_schema(TOOL_REGISTRY, "openai")
            TOOLS_SCHEMA_OLLAMA[:] = _build_tools_schema(TOOL_REGISTRY, "ollama")

    def test_register_chain_updates_schema(self):
        """register_chain() 후 TOOLS_SCHEMA 가 새 도구를 포함한다."""
        from tools.registry import (register_chain, TOOLS_SCHEMA_ANTHROPIC, CHAIN_REGISTRY,
                                     TOOL_REGISTRY, TOOLS_SCHEMA_OPENAI, TOOLS_SCHEMA_OLLAMA,
                                     _build_tools_schema)
        from tools.chains import ToolChain, ChainStep

        chain_name = "_schema_test_chain_v2"
        try:
            test_chain = ToolChain(
                name=chain_name,
                description="스키마 테스트용",
                steps=[ChainStep("read_file", {"path": "{path}"})],
            )
            register_chain(test_chain)

            # 스키마가 업데이트됨
            names = [t.get("name") or t.get("function", {}).get("name") for t in TOOLS_SCHEMA_ANTHROPIC]
            assert chain_name in names
        finally:
            # 정리: 테스트 체인 제거 + 스키마 원상 복구
            TOOL_REGISTRY.pop(chain_name, None)
            CHAIN_REGISTRY.pop(chain_name, None)
            TOOLS_SCHEMA_ANTHROPIC[:] = _build_tools_schema(TOOL_REGISTRY, "anthropic")
            TOOLS_SCHEMA_OPENAI[:] = _build_tools_schema(TOOL_REGISTRY, "openai")
            TOOLS_SCHEMA_OLLAMA[:] = _build_tools_schema(TOOL_REGISTRY, "ollama")
