"""
tests/test_verification.py — core/verification.py 단위 테스트

Module 4: Verification-Driven Commit
"""

from __future__ import annotations

import subprocess
import pytest
from unittest.mock import MagicMock, patch, call
from dataclasses import dataclass

from core.verification import (
    VerificationGate,
    VerificationResult,
    GateResult,
)


# ── GateResult.failure_summary 테스트 ─────────────────────────────────────────

class TestGateResultFailureSummary:
    def test_all_passed_returns_success_message(self):
        gate = GateResult(all_passed=True)
        summary = gate.failure_summary
        assert "FAILED" not in summary

    def test_failed_contains_header(self):
        gate = GateResult(
            all_passed=False,
            results=[VerificationResult(passed=False, command="pytest", output="FAILED 2", returncode=1)],
        )
        assert "[VERIFICATION GATE FAILED]" in gate.failure_summary

    def test_failed_contains_command(self):
        gate = GateResult(
            all_passed=False,
            results=[VerificationResult(passed=False, command="pytest -q", output="error", returncode=1)],
        )
        assert "pytest -q" in gate.failure_summary

    def test_failed_contains_returncode(self):
        gate = GateResult(
            all_passed=False,
            results=[VerificationResult(passed=False, command="flake8", output="E501", returncode=1)],
        )
        assert "1" in gate.failure_summary

    def test_long_output_truncated(self):
        long_output = "X" * 5000
        gate = GateResult(
            all_passed=False,
            results=[VerificationResult(passed=False, command="pytest", output=long_output, returncode=1)],
        )
        # 요약이 너무 길지 않아야 함
        assert len(gate.failure_summary) < 3000

    def test_multiple_failures_listed(self):
        gate = GateResult(
            all_passed=False,
            results=[
                VerificationResult(passed=False, command="pytest", output="fail1", returncode=1),
                VerificationResult(passed=False, command="flake8", output="fail2", returncode=1),
            ],
        )
        summary = gate.failure_summary
        assert "pytest" in summary
        assert "flake8" in summary


# ── VerificationGate 기본 동작 ─────────────────────────────────────────────────

class TestVerificationGateBasic:
    """실제 subprocess 를 mock 해서 게이트 동작 검증."""

    def _gate_with_mock(self, returncode: int = 0, **gate_kwargs) -> VerificationGate:
        gate = VerificationGate(**gate_kwargs)
        mock_result = VerificationResult(
            passed=(returncode == 0),
            command="pytest",
            output="output",
            error="",
            returncode=returncode,
        )
        gate._run_command = MagicMock(return_value=mock_result)
        return gate

    def test_all_pass_returns_true(self):
        gate = self._gate_with_mock(returncode=0, verification_commands=["pytest"])
        result = gate.check("/tmp/repo")
        assert result.all_passed is True

    def test_failure_returns_false(self):
        gate = self._gate_with_mock(returncode=1, verification_commands=["pytest"])
        result = gate.check("/tmp/repo")
        assert result.all_passed is False

    def test_disabled_gate_always_passes(self):
        gate = VerificationGate(enabled=False)
        result = gate.check("/tmp/repo")
        assert result.all_passed is True
        # _run_command 는 호출되지 않아야 함

    def test_fail_fast_stops_at_first_failure(self):
        """fail_fast=True: 첫 번째 실패에서 나머지 커맨드 실행 안 함."""
        gate = VerificationGate(
            verification_commands=["pytest", "flake8 ."],
            fail_fast=True,
        )
        # pytest → 실패, flake8 → 정상(호출 안 됨)
        call_count = {"n": 0}
        def mock_run(tokens, cwd):
            call_count["n"] += 1
            return VerificationResult(passed=False, command=" ".join(tokens), output="fail", returncode=1)

        gate._run_command = mock_run
        result = gate.check("/tmp/repo")
        assert result.all_passed is False
        assert call_count["n"] == 1  # pytest 만 실행

    def test_no_fail_fast_runs_all(self):
        """fail_fast=False: 실패해도 모든 커맨드 실행."""
        gate = VerificationGate(
            verification_commands=["pytest", "flake8 ."],
            fail_fast=False,
        )
        call_count = {"n": 0}
        def mock_run(tokens, cwd):
            call_count["n"] += 1
            return VerificationResult(passed=False, command=" ".join(tokens), output="fail", returncode=1)

        gate._run_command = mock_run
        gate.check("/tmp/repo")
        assert call_count["n"] == 2

    def test_working_dir_none_uses_repo_path(self):
        """working_dir=None 이면 repo_path 를 cwd 로 사용."""
        received_cwd = {}

        gate = VerificationGate(
            verification_commands=["pytest"],
            working_dir=None,
        )
        def mock_run(tokens, cwd):
            received_cwd["cwd"] = cwd
            return VerificationResult(passed=True, command=" ".join(tokens), output="ok", returncode=0)

        gate._run_command = mock_run
        gate.check("/my/repo")
        assert received_cwd["cwd"] == "/my/repo"

    def test_working_dir_overrides_repo_path(self):
        """working_dir 가 설정되면 repo_path 무시."""
        received_cwd = {}

        gate = VerificationGate(
            verification_commands=["pytest"],
            working_dir="/fixed/dir",
        )
        def mock_run(tokens, cwd):
            received_cwd["cwd"] = cwd
            return VerificationResult(passed=True, command=" ".join(tokens), output="ok", returncode=0)

        gate._run_command = mock_run
        gate.check("/other/repo")
        assert received_cwd["cwd"] == "/fixed/dir"

    def test_multiple_commands_all_pass(self):
        gate = VerificationGate(verification_commands=["cmd_a", "cmd_b", "cmd_c"])
        gate._run_command = MagicMock(
            return_value=VerificationResult(passed=True, command="x", output="ok", returncode=0)
        )
        result = gate.check("/repo")
        assert result.all_passed is True
        assert gate._run_command.call_count == 3


# ── _run_command 서브프로세스 테스트 ─────────────────────────────────────────

class TestRunCommand:
    """_run_command 의 실제 subprocess 처리 로직 (subprocess.run mock)."""

    def test_success_exit_0(self):
        gate = VerificationGate()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="all good", stderr="")
            result = gate._run_command(["pytest", "-q"], "/tmp")
        assert result.passed is True
        assert result.returncode == 0
        assert result.output == "all good"

    def test_failure_nonzero_exit(self):
        gate = VerificationGate()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=2, stdout="2 failed", stderr="")
            result = gate._run_command(["pytest"], "/tmp")
        assert result.passed is False
        assert result.returncode == 2

    def test_timeout_returns_failed(self):
        gate = VerificationGate(verification_timeout=10.0)
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd=["pytest"], timeout=10)
            result = gate._run_command(["pytest"], "/tmp")
        assert result.passed is False
        assert result.returncode == -1
        assert "타임아웃" in result.error or "timeout" in result.error.lower()

    def test_command_not_found_returns_failed(self):
        gate = VerificationGate()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError
            result = gate._run_command(["nonexistent_cmd"], "/tmp")
        assert result.passed is False
        assert result.returncode == -1

    def test_shell_false_for_injection_safety(self):
        """subprocess.run 이 shell=False 로 호출되는지 확인."""
        gate = VerificationGate()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            gate._run_command(["pytest"], "/tmp")
        _, kwargs = mock_run.call_args
        # shell 파라미터가 없거나 False
        assert kwargs.get("shell", False) is False


# ── ReactLoop 통합 테스트 ─────────────────────────────────────────────────────

class TestVerificationGateInReactLoop:
    """VerificationGate 가 ReactLoop._execute_tool() 에 올바르게 통합되는지."""

    def _make_loop(self, **kwargs):
        """get_tools_schema() 를 patch 해서 LLM 타입 검사를 우회한다."""
        from core.loop import ReactLoop
        mock_llm = MagicMock()
        mock_llm.config = MagicMock(system_prompt="test")
        with patch.object(ReactLoop, "get_tools_schema", return_value=[]):
            loop = ReactLoop(llm=mock_llm, **kwargs)
        return loop

    def test_gate_blocks_git_commit_on_failure(self):
        """검증 실패 시 git_commit ToolResult.is_error=True."""
        from core.loop import ToolCall

        gate = VerificationGate(enabled=True)
        gate._run_command = MagicMock(
            return_value=VerificationResult(
                passed=False, command="pytest", output="2 FAILED", returncode=1
            )
        )
        gate.verification_commands = ["pytest"]

        loop = self._make_loop(verification_gate=gate)
        tc = ToolCall(id="t1", name="git_commit", input={"repo_path": ".", "message": "feat: done"})

        result = loop._execute_tool(tc)
        assert result.is_error is True
        assert "[VERIFICATION GATE FAILED]" in result.content

    def test_gate_allows_git_commit_on_pass(self):
        """검증 통과 시 실제 git_commit 도구가 호출된다."""
        from core.loop import ToolCall

        gate = VerificationGate(enabled=True)
        gate._run_command = MagicMock(
            return_value=VerificationResult(passed=True, command="pytest", output="ok", returncode=0)
        )
        gate.verification_commands = ["pytest"]

        loop = self._make_loop(verification_gate=gate)
        tc = ToolCall(id="t1", name="git_commit", input={"repo_path": ".", "message": "feat: done"})

        with patch("core.loop.call_tool") as mock_call:
            mock_schema = MagicMock()
            mock_schema.success = True
            mock_schema.output = "commit ok"
            mock_call.return_value = mock_schema

            result = loop._execute_tool(tc)

        # call_tool 이 실제로 호출되어야 함
        mock_call.assert_called_once_with("git_commit", repo_path=".", message="feat: done")
        assert result.is_error is False

    def test_gate_not_triggered_for_other_tools(self):
        """git_commit 이 아닌 도구에는 게이트가 동작하지 않는다."""
        from core.loop import ToolCall

        gate = MagicMock()  # check() 호출 여부 추적용
        loop = self._make_loop(verification_gate=gate)
        tc = ToolCall(id="t1", name="read_file", input={"path": "main.py"})

        with patch("core.loop.call_tool") as mock_call:
            mock_schema = MagicMock()
            mock_schema.success = True
            mock_schema.output = "file content"
            mock_call.return_value = mock_schema
            loop._execute_tool(tc)

        gate.check.assert_not_called()

    def test_no_gate_git_commit_proceeds_normally(self):
        """verification_gate=None: 게이트 없이 git_commit 직접 실행."""
        from core.loop import ToolCall

        loop = self._make_loop(verification_gate=None)
        tc = ToolCall(id="t1", name="git_commit", input={"repo_path": ".", "message": "msg"})

        with patch("core.loop.call_tool") as mock_call:
            mock_schema = MagicMock()
            mock_schema.success = True
            mock_schema.output = "committed"
            mock_call.return_value = mock_schema
            result = loop._execute_tool(tc)

        mock_call.assert_called_once()
        assert result.is_error is False

    def test_duck_typed_gate_works(self):
        """duck-typing: check(repo_path) → GateResult 프로토콜을 구현한 객체면 동작."""
        from core.loop import ToolCall

        class _AlwaysPassGate:
            def check(self, repo_path: str) -> GateResult:
                return GateResult(all_passed=True)

        loop = self._make_loop(verification_gate=_AlwaysPassGate())
        tc = ToolCall(id="t1", name="git_commit", input={"repo_path": ".", "message": "ok"})

        with patch("core.loop.call_tool") as mock_call:
            mock_schema = MagicMock()
            mock_schema.success = True
            mock_schema.output = "committed"
            mock_call.return_value = mock_schema
            result = loop._execute_tool(tc)

        assert result.is_error is False
