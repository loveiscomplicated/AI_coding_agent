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
    def test_extracts_passed_line(self):
        stdout = ".\n.\n.\n3 passed in 0.05s\n"
        assert "3 passed" in _parse_summary(stdout)

    def test_extracts_mixed_result(self):
        stdout = "..F\nFAILED tests/test_foo.py::test_bar\n5 passed, 1 failed in 0.12s"
        summary = _parse_summary(stdout)
        assert "5 passed" in summary
        assert "1 failed" in summary

    def test_falls_back_to_last_nonempty_line(self):
        stdout = "some unexpected output\nerror line"
        result = _parse_summary(stdout)
        assert result == "error line"

    def test_empty_stdout(self):
        assert _parse_summary("") == "(출력 없음)"

    def test_strips_pytest_decoration(self):
        stdout = "======= 3 passed in 0.05s =======\n"
        summary = _parse_summary(stdout)
        assert "=======" not in summary
        assert "3 passed" in summary


# ── _parse_failed_tests ───────────────────────────────────────────────────────


class TestParseFailedTests:
    def test_extracts_single_failure(self):
        stdout = "FAILED tests/test_foo.py::test_bar - AssertionError: ..."
        failed = _parse_failed_tests(stdout)
        assert failed == ["tests/test_foo.py::test_bar"]

    def test_extracts_multiple_failures(self):
        stdout = (
            "FAILED tests/test_foo.py::TestClass::test_one - Error\n"
            "FAILED tests/test_bar.py::test_two - Error\n"
        )
        failed = _parse_failed_tests(stdout)
        assert len(failed) == 2
        assert "tests/test_foo.py::TestClass::test_one" in failed
        assert "tests/test_bar.py::test_two" in failed

    def test_no_failures(self):
        stdout = "3 passed in 0.05s"
        assert _parse_failed_tests(stdout) == []

    def test_ignores_non_failed_lines(self):
        stdout = "PASSED tests/test_foo.py::test_ok\nFAILED tests/test_bar.py::test_bad"
        failed = _parse_failed_tests(stdout)
        assert failed == ["tests/test_bar.py::test_bad"]


# ── DockerTestRunner (모킹) ───────────────────────────────────────────────────


class TestDockerTestRunnerMocked:
    """Docker 데몬 없이 subprocess를 모킹해 로직을 검증한다."""

    def _make_runner(self):
        return DockerTestRunner(image="test-image", timeout=30)

    def _mock_docker_info_ok(self):
        """docker info 성공 응답 모킹."""
        return MagicMock(returncode=0, stdout="Server Version: 28.0", stderr="")

    def _mock_image_exists(self):
        return MagicMock(returncode=0)

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
        # docker info → ok, docker image inspect → 실패 (이미지 없음)
        mock_run.side_effect = [
            MagicMock(returncode=0),   # docker info
            MagicMock(returncode=1),   # docker image inspect
        ]
        runner = self._make_runner()
        result = runner.run(tmp_path)
        assert result.passed is False
        assert "이미지" in result.summary

    @patch("docker.runner.subprocess.run")
    def test_returns_passed_on_zero_returncode(self, mock_run, tmp_path):
        mock_run.side_effect = [
            MagicMock(returncode=0),                                    # docker info
            MagicMock(returncode=0),                                    # image inspect
            MagicMock(returncode=0, stdout="3 passed in 0.05s\n", stderr=""),  # docker run
        ]
        runner = self._make_runner()
        result = runner.run(tmp_path)
        assert result.passed is True
        assert result.returncode == 0
        assert "3 passed" in result.summary

    @patch("docker.runner.subprocess.run")
    def test_returns_failed_on_nonzero_returncode(self, mock_run, tmp_path):
        stdout = (
            "FAILED tests/test_foo.py::test_bar - AssertionError\n"
            "1 failed in 0.03s\n"
        )
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0),
            MagicMock(returncode=1, stdout=stdout, stderr=""),
        ]
        runner = self._make_runner()
        result = runner.run(tmp_path)
        assert result.passed is False
        assert result.failed_tests == ["tests/test_foo.py::test_bar"]
        assert "1 failed" in result.summary

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
            MagicMock(returncode=0),   # docker info
            MagicMock(returncode=1, stderr="build failed"),  # docker build
        ]
        runner = self._make_runner()
        with pytest.raises(RuntimeError, match="빌드 실패"):
            runner.build_image()


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
