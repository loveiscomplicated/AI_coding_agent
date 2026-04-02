"""
tests/test_docker_runner.py

docker/runner.py 단위 테스트.

DockerTestRunner 는 실제 Docker 데몬이 필요한 통합 테스트와
subprocess를 모킹한 단위 테스트로 분리한다.

실행:
    pytest tests/test_docker_runner.py -v
    pytest tests/test_docker_runner.py -v -m "not docker"  # Docker 없이
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from docker.runner import (
    DockerTestRunner,
    RunResult,
    _parse_failed_tests,
    _parse_summary,
)


# ── _parse_summary ────────────────────────────────────────────────────────────


class TestParseSummary:
    # pytest
    def test_pytest_passed(self):
        stdout = ".\n.\n.\n3 passed in 0.05s\n"
        assert "3 passed" in _parse_summary(stdout)

    def test_pytest_mixed(self):
        stdout = "..F\nFAILED tests/test_foo.py::test_bar\n5 passed, 1 failed in 0.12s"
        summary = _parse_summary(stdout)
        assert "5 passed" in summary
        assert "1 failed" in summary

    def test_pytest_strips_decoration(self):
        stdout = "======= 3 passed in 0.05s =======\n"
        summary = _parse_summary(stdout)
        assert "=======" not in summary
        assert "3 passed" in summary

    # jest / vitest
    def test_jest_passed(self):
        stdout = (
            "PASS tests/egg.test.js\n"
            "  getEggStage\n"
            "    ✓ returns raw egg (3ms)\n\n"
            "Tests:       3 passed, 3 total\n"
            "Test Suites: 1 passed, 1 total\n"
            "Time:        1.5s\n"
        )
        summary = _parse_summary(stdout)
        assert "Tests:" in summary
        assert "3 passed" in summary

    def test_jest_failed(self):
        stdout = (
            "FAIL tests/egg.test.js\n"
            "Tests:       1 failed, 2 passed, 3 total\n"
        )
        summary = _parse_summary(stdout)
        assert "Tests:" in summary
        assert "1 failed" in summary

    # go test
    def test_go_passed(self):
        stdout = (
            "=== RUN   TestGetEggStage\n"
            "--- PASS: TestGetEggStage (0.00s)\n"
            "ok  \tmodule/egg\t0.123s\n"
        )
        summary = _parse_summary(stdout)
        assert summary.startswith("ok")
        assert "egg" in summary

    def test_go_failed(self):
        stdout = (
            "--- FAIL: TestGetEggStage (0.00s)\n"
            "FAIL\tmodule/egg\t0.005s\n"
        )
        summary = _parse_summary(stdout)
        assert summary.startswith("FAIL")

    # rspec
    def test_rspec_passed(self):
        stdout = (
            "....\n\n"
            "Finished in 0.01234 seconds (files took 0.5s to load)\n"
            "4 examples, 0 failures\n"
        )
        summary = _parse_summary(stdout)
        assert "examples" in summary
        assert "0 failures" in summary

    def test_rspec_failed(self):
        stdout = (
            "..F.\n\n"
            "3 examples, 1 failure\n"
        )
        summary = _parse_summary(stdout)
        assert "1 failure" in summary

    # 공통
    def test_falls_back_to_last_nonempty_line(self):
        stdout = "some unexpected output\nerror line"
        assert _parse_summary(stdout) == "error line"

    def test_empty_stdout(self):
        assert _parse_summary("") == "(출력 없음)"


# ── _parse_failed_tests ───────────────────────────────────────────────────────


class TestParseFailedTests:
    # pytest
    def test_pytest_single_failure(self):
        stdout = "FAILED tests/test_foo.py::test_bar - AssertionError: ..."
        assert _parse_failed_tests(stdout) == ["tests/test_foo.py::test_bar"]

    def test_pytest_multiple_failures(self):
        stdout = (
            "FAILED tests/test_foo.py::TestClass::test_one - Error\n"
            "FAILED tests/test_bar.py::test_two - Error\n"
        )
        failed = _parse_failed_tests(stdout)
        assert len(failed) == 2
        assert "tests/test_foo.py::TestClass::test_one" in failed
        assert "tests/test_bar.py::test_two" in failed

    def test_pytest_no_failures(self):
        assert _parse_failed_tests("3 passed in 0.05s") == []

    def test_pytest_ignores_passed_lines(self):
        stdout = "PASSED tests/test_foo.py::test_ok\nFAILED tests/test_bar.py::test_bad"
        assert _parse_failed_tests(stdout) == ["tests/test_bar.py::test_bad"]

    # jest
    def test_jest_failure_with_checkmark(self):
        stdout = (
            "  ✕ returns raw egg for 0 seconds (5ms)\n"
            "  ✓ returns cracked for 999 seconds (2ms)\n"
        )
        failed = _parse_failed_tests(stdout)
        assert failed == ["returns raw egg for 0 seconds"]

    def test_jest_failure_with_cross(self):
        # × (U+00D7) 변형도 처리
        stdout = "  × getEggStage returns correct stage (3ms)\n"
        failed = _parse_failed_tests(stdout)
        assert "getEggStage returns correct stage" in failed

    def test_jest_no_failures(self):
        stdout = "  ✓ all tests passed (2ms)\nTests: 3 passed, 3 total\n"
        assert _parse_failed_tests(stdout) == []

    # go test
    def test_go_failure(self):
        stdout = (
            "--- FAIL: TestGetEggStage (0.00s)\n"
            "--- FAIL: TestStartTimer (0.01s)\n"
            "--- PASS: TestStopTimer (0.00s)\n"
        )
        failed = _parse_failed_tests(stdout)
        assert "TestGetEggStage" in failed
        assert "TestStartTimer" in failed
        assert "TestStopTimer" not in failed

    def test_go_no_failures(self):
        stdout = "--- PASS: TestGetEggStage (0.00s)\nok  \tmodule/egg\t0.005s\n"
        assert _parse_failed_tests(stdout) == []

    # rspec
    def test_rspec_failure(self):
        stdout = (
            "  1) EggStage#getEggStage returns raw egg for 0 seconds\n"
            "     Failure/Error: expect(get_egg_stage(0)).to eq('날계란')\n"
            "  2) EggStage#getEggStage returns cracked for large value\n"
        )
        failed = _parse_failed_tests(stdout)
        assert len(failed) == 2
        assert any("raw egg" in f for f in failed)

    def test_rspec_no_failures(self):
        stdout = "4 examples, 0 failures\n"
        assert _parse_failed_tests(stdout) == []


# ── DockerTestRunner (모킹) ───────────────────────────────────────────────────


class TestDockerTestRunnerMocked:
    """Docker 데몬 없이 subprocess를 모킹해 로직을 검증한다."""

    def _make_runner(self):
        return DockerTestRunner(image="test-image", timeout=30)

    def _mock_docker_info_ok(self):
        return MagicMock(returncode=0, stdout="Server Version: 28.0", stderr="")

    @patch("docker.runner.subprocess.run")
    def test_returns_failed_when_workspace_missing(self, mock_run, tmp_path):
        mock_run.return_value = self._mock_docker_info_ok()
        runner = self._make_runner()
        result = runner.run(tmp_path / "nonexistent")
        assert result.passed is False
        assert result.returncode == -1
        assert "없음" in result.summary

    @patch("docker.runner.subprocess.run")
    def test_returns_failed_when_image_not_built(self, mock_run, tmp_path):
        mock_run.side_effect = [
            MagicMock(returncode=0),                           # docker info (run)
            MagicMock(returncode=1),                           # docker image inspect → 없음
            MagicMock(returncode=0),                           # docker info (build_image 내부)
            MagicMock(returncode=1, stderr="build failed"),    # docker build → 실패
        ]
        runner = self._make_runner()
        result = runner.run(tmp_path)
        assert result.passed is False
        assert "이미지" in result.summary

    @patch("docker.runner.subprocess.run")
    def test_returns_passed_on_zero_returncode(self, mock_run, tmp_path):
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="3 passed in 0.05s\n", stderr=""),
        ]
        runner = self._make_runner()
        result = runner.run(tmp_path)
        assert result.passed is True
        assert "3 passed" in result.summary

    @patch("docker.runner.subprocess.run")
    def test_returns_failed_on_nonzero_returncode(self, mock_run, tmp_path):
        stdout = "FAILED tests/test_foo.py::test_bar - AssertionError\n1 failed in 0.03s\n"
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0),
            MagicMock(returncode=1, stdout=stdout, stderr=""),
        ]
        runner = self._make_runner()
        result = runner.run(tmp_path)
        assert result.passed is False
        assert result.failed_tests == ["tests/test_foo.py::test_bar"]

    @patch("docker.runner.subprocess.run")
    def test_handles_timeout(self, mock_run, tmp_path):
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0),
            subprocess.TimeoutExpired(cmd="docker run", timeout=30),
        ]
        runner = self._make_runner()
        result = runner.run(tmp_path)
        assert result.passed is False
        assert "타임아웃" in result.summary

    @patch("docker.runner.subprocess.run")
    def test_raises_when_docker_not_running(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=1, stderr="Cannot connect")
        runner = self._make_runner()
        with pytest.raises(RuntimeError, match="Docker 데몬"):
            runner.run(tmp_path)

    @patch("docker.runner.subprocess.run")
    def test_build_image_raises_on_failure(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=1, stderr="build failed"),
        ]
        runner = self._make_runner()
        with pytest.raises(RuntimeError, match="빌드 실패"):
            runner.build_image()


# ── test_framework 파라미터 전달 검증 ─────────────────────────────────────────


class TestTestFrameworkParameter:
    """test_framework 값이 Docker 명령의 -e TEST_FRAMEWORK=... 로 전달되는지 검증."""

    def _run_with_framework(self, mock_run, tmp_path, framework: str):
        mock_run.side_effect = [
            MagicMock(returncode=0),                                          # docker info
            MagicMock(returncode=0),                                          # image inspect
            MagicMock(returncode=0, stdout="3 passed in 0.05s\n", stderr=""), # docker run
        ]
        runner = DockerTestRunner(image="test-image", timeout=30)
        runner.run(tmp_path, test_framework=framework)
        # 세 번째 호출(docker run)의 args 반환
        return mock_run.call_args_list[2][0][0]  # positional args[0] = command list

    @patch("docker.runner.subprocess.run")
    def test_default_framework_is_pytest(self, mock_run, tmp_path):
        cmd = self._run_with_framework(mock_run, tmp_path, "pytest")
        assert "-e" in cmd
        env_idx = cmd.index("-e", cmd.index("--rm"))
        # TEST_FRAMEWORK=pytest 가 포함되어 있어야 함
        env_args = " ".join(cmd)
        assert "TEST_FRAMEWORK=pytest" in env_args

    @patch("docker.runner.subprocess.run")
    def test_jest_framework_passed(self, mock_run, tmp_path):
        cmd = self._run_with_framework(mock_run, tmp_path, "jest")
        assert "TEST_FRAMEWORK=jest" in " ".join(cmd)

    @patch("docker.runner.subprocess.run")
    def test_go_framework_passed(self, mock_run, tmp_path):
        cmd = self._run_with_framework(mock_run, tmp_path, "go")
        assert "TEST_FRAMEWORK=go" in " ".join(cmd)

    @patch("docker.runner.subprocess.run")
    def test_arbitrary_command_passed(self, mock_run, tmp_path):
        """알 수 없는 프레임워크(직접 명령)도 그대로 전달된다."""
        cmd = self._run_with_framework(mock_run, tmp_path, "cargo test")
        assert "TEST_FRAMEWORK=cargo test" in " ".join(cmd)

    @patch("docker.runner.subprocess.run")
    def test_rspec_framework_passed(self, mock_run, tmp_path):
        cmd = self._run_with_framework(mock_run, tmp_path, "rspec")
        assert "TEST_FRAMEWORK=rspec" in " ".join(cmd)


# ── RunResult 데이터클래스 ────────────────────────────────────────────────────


class TestRunResultDataclass:
    def test_default_failed_tests_is_empty_list(self):
        result = RunResult(passed=True, returncode=0, stdout="ok", summary="1 passed")
        assert result.failed_tests == []

    def test_passed_false_with_failures(self):
        result = RunResult(
            passed=False,
            returncode=1,
            stdout="FAILED tests/test_x.py::test_y",
            summary="1 failed",
            failed_tests=["tests/test_x.py::test_y"],
        )
        assert result.passed is False
        assert len(result.failed_tests) == 1
