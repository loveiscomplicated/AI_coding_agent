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
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from docker.runner import (
    DockerTestRunner,
    RunResult,
    _LANGUAGE_DOCKERFILE,
    _LANGUAGE_IMAGE,
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

    # ctest
    def test_ctest_passed(self):
        stdout = (
            "Test project /tmp/ws/_build\n"
            "    Start 1: TestCoordinate\n"
            "1/2 Test #1: TestCoordinate ...............   Passed    0.01 sec\n"
            "    Start 2: TestPlace\n"
            "2/2 Test #2: TestPlace ...................   Passed    0.01 sec\n"
            "\n"
            "100% tests passed, 0 tests failed out of 2\n"
        )
        summary = _parse_summary(stdout)
        assert "tests passed" in summary
        assert "out of 2" in summary

    def test_ctest_failed(self):
        stdout = (
            "1/2 Test #1: TestCoordinate ...............   Passed    0.01 sec\n"
            "2/2 Test #2: TestPlace ...................***Failed    0.01 sec\n"
            "\n"
            "50% tests passed, 1 tests failed out of 2\n"
        )
        summary = _parse_summary(stdout)
        assert "1 tests failed" in summary

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

    # ctest
    def test_ctest_failure(self):
        stdout = (
            "1/2 Test #1: TestCoordinate ...............   Passed    0.01 sec\n"
            "2/2 Test #2: TestPlace ...................***Failed    0.01 sec\n"
            "  2 - TestPlace (Failed)\n"
        )
        failed = _parse_failed_tests(stdout)
        assert "TestPlace" in failed

    def test_ctest_no_failures(self):
        stdout = "100% tests passed, 0 tests failed out of 2\n"
        assert _parse_failed_tests(stdout) == []


# ── 언어 → 이미지 매핑 테이블 ──────────────────────────────────────────────────


class TestLanguageImageMapping:
    """_LANGUAGE_IMAGE / _LANGUAGE_DOCKERFILE 테이블 정합성 검증."""

    def test_python_maps_to_agent_runner_python(self):
        assert _LANGUAGE_IMAGE["python"] == "agent-runner-python"

    def test_go_maps_to_agent_runner_go(self):
        assert _LANGUAGE_IMAGE["go"] == "agent-runner-go"

    def test_kotlin_maps_to_agent_runner_kotlin(self):
        assert _LANGUAGE_IMAGE["kotlin"] == "agent-runner-kotlin"

    def test_javascript_maps_to_agent_runner_javascript(self):
        assert _LANGUAGE_IMAGE["javascript"] == "agent-runner-javascript"

    def test_typescript_reuses_javascript_image(self):
        assert _LANGUAGE_IMAGE["typescript"] == _LANGUAGE_IMAGE["javascript"]

    def test_java_reuses_kotlin_image(self):
        assert _LANGUAGE_IMAGE["java"] == _LANGUAGE_IMAGE["kotlin"]

    def test_c_maps_to_agent_runner_c(self):
        assert _LANGUAGE_IMAGE["c"] == "agent-runner-c"

    def test_cpp_maps_to_agent_runner_cpp(self):
        assert _LANGUAGE_IMAGE["cpp"] == "agent-runner-cpp"

    def test_all_image_names_follow_agent_runner_prefix(self):
        for lang, image in _LANGUAGE_IMAGE.items():
            assert image.startswith("agent-runner-"), (
                f"언어 '{lang}'의 이미지 '{image}'가 'agent-runner-' 접두사를 갖지 않음"
            )

    def test_every_image_has_matching_dockerfile(self):
        """_LANGUAGE_IMAGE에 등록된 언어는 _LANGUAGE_DOCKERFILE에도 있어야 한다."""
        for lang in _LANGUAGE_IMAGE:
            assert lang in _LANGUAGE_DOCKERFILE, (
                f"언어 '{lang}'에 대한 Dockerfile 매핑 없음"
            )


# ── DockerTestRunner (모킹) ───────────────────────────────────────────────────


class TestDockerTestRunnerMocked:
    """Docker 데몬 없이 subprocess를 모킹해 로직을 검증한다."""

    def _make_runner(self):
        return DockerTestRunner(timeout=30)

    def _mock_ok(self, stdout="", stderr=""):
        return MagicMock(returncode=0, stdout=stdout, stderr=stderr)

    def _mock_fail(self, stderr=""):
        return MagicMock(returncode=1, stdout="", stderr=stderr)

    @patch("docker.runner.subprocess.run")
    def test_returns_failed_when_workspace_missing(self, mock_run, tmp_path):
        mock_run.return_value = self._mock_ok(stdout="Server Version: 28.0")
        runner = self._make_runner()
        result = runner.run(tmp_path / "nonexistent")
        assert result.passed is False
        assert result.returncode == -1
        assert "없음" in result.summary

    @patch("docker.runner.subprocess.run")
    def test_returns_failed_when_image_not_built(self, mock_run, tmp_path):
        mock_run.side_effect = [
            self._mock_ok(),                              # docker info
            self._mock_fail(),                            # docker image inspect → 없음
            self._mock_ok(),                              # docker info (build_image 내부)
            self._mock_fail(stderr="build failed"),       # docker build → 실패
        ]
        runner = self._make_runner()
        result = runner.run(tmp_path)
        assert result.passed is False
        assert "이미지" in result.summary

    @patch("docker.runner.subprocess.run")
    def test_returns_passed_on_zero_returncode(self, mock_run, tmp_path):
        mock_run.side_effect = [
            self._mock_ok(),
            self._mock_ok(),
            self._mock_ok(stdout="3 passed in 0.05s\n"),
        ]
        runner = self._make_runner()
        result = runner.run(tmp_path)
        assert result.passed is True
        assert "3 passed" in result.summary

    @patch("docker.runner.subprocess.run")
    def test_returns_failed_on_nonzero_returncode(self, mock_run, tmp_path):
        stdout = "FAILED tests/test_foo.py::test_bar - AssertionError\n1 failed in 0.03s\n"
        mock_run.side_effect = [
            self._mock_ok(),
            self._mock_ok(),
            MagicMock(returncode=1, stdout=stdout, stderr=""),
        ]
        runner = self._make_runner()
        result = runner.run(tmp_path)
        assert result.passed is False
        assert result.failed_tests == ["tests/test_foo.py::test_bar"]

    @patch("docker.runner.subprocess.run")
    def test_handles_timeout(self, mock_run, tmp_path):
        mock_run.side_effect = [
            self._mock_ok(),
            self._mock_ok(),
            subprocess.TimeoutExpired(cmd="docker run", timeout=30),
        ]
        runner = self._make_runner()
        result = runner.run(tmp_path)
        assert result.passed is False
        assert "타임아웃" in result.summary

    @patch("docker.runner.subprocess.run")
    def test_returns_failed_when_docker_not_running(self, mock_run, tmp_path):
        """Docker 데몬 미실행 시 passed=False RunResult를 반환한다."""
        mock_run.return_value = MagicMock(returncode=1, stderr="Cannot connect")
        runner = self._make_runner()
        result = runner.run(tmp_path)
        assert result.passed is False
        assert "Docker" in result.summary

    @patch("docker.runner.subprocess.run")
    def test_build_image_raises_on_failure(self, mock_run):
        mock_run.side_effect = [
            self._mock_ok(),                              # docker info
            self._mock_fail(stderr="build failed"),       # docker build
        ]
        runner = self._make_runner()
        with pytest.raises(RuntimeError, match="빌드 실패"):
            runner.build_image("python")

    @patch("docker.runner.subprocess.run")
    def test_unsupported_language_returns_error(self, mock_run, tmp_path):
        """매핑 테이블에 없는 언어는 즉시 passed=False를 반환한다."""
        runner = self._make_runner()
        result = runner.run(tmp_path, language="brainfuck")
        assert result.passed is False
        assert "UNSUPPORTED_LANGUAGE" in result.summary
        mock_run.assert_not_called()  # Docker 호출 없어야 함

    @patch("docker.runner.subprocess.run")
    def test_build_image_raises_on_unknown_language(self, mock_run):
        runner = self._make_runner()
        with pytest.raises(RuntimeError, match="지원하지 않는 언어"):
            runner.build_image("cobol")


# ── 언어별 올바른 이미지 선택 검증 ────────────────────────────────────────────


class TestLanguageImageSelection:
    """run() 호출 시 language에 따라 올바른 agent-runner-{lang} 이미지가 선택되는지 검증."""

    def _mock_sequence(self, stdout="3 passed in 0.05s\n"):
        return [
            MagicMock(returncode=0),                                     # docker info
            MagicMock(returncode=0),                                     # image inspect (exists)
            MagicMock(returncode=0, stdout=stdout, stderr=""),           # docker run
        ]

    def _get_docker_run_cmd(self, mock_run):
        """mock_run의 세 번째 호출(docker run) 커맨드 리스트를 반환한다."""
        return mock_run.call_args_list[2][0][0]

    @patch("docker.runner.subprocess.run")
    def test_python_uses_agent_runner_python(self, mock_run, tmp_path):
        mock_run.side_effect = self._mock_sequence()
        DockerTestRunner(timeout=30).run(tmp_path, language="python")
        cmd = self._get_docker_run_cmd(mock_run)
        assert "agent-runner-python" in cmd

    @patch("docker.runner.subprocess.run")
    def test_kotlin_uses_agent_runner_kotlin(self, mock_run, tmp_path):
        mock_run.side_effect = self._mock_sequence()
        DockerTestRunner(timeout=30).run(tmp_path, language="kotlin")
        cmd = self._get_docker_run_cmd(mock_run)
        assert "agent-runner-kotlin" in cmd

    @patch("docker.runner.subprocess.run")
    def test_javascript_uses_agent_runner_javascript(self, mock_run, tmp_path):
        mock_run.side_effect = self._mock_sequence()
        DockerTestRunner(timeout=30).run(tmp_path, language="javascript")
        cmd = self._get_docker_run_cmd(mock_run)
        assert "agent-runner-javascript" in cmd

    @patch("docker.runner.subprocess.run")
    def test_typescript_uses_agent_runner_javascript(self, mock_run, tmp_path):
        mock_run.side_effect = self._mock_sequence()
        DockerTestRunner(timeout=30).run(tmp_path, language="typescript")
        cmd = self._get_docker_run_cmd(mock_run)
        assert "agent-runner-javascript" in cmd

    @patch("docker.runner.subprocess.run")
    def test_go_uses_agent_runner_go(self, mock_run, tmp_path):
        mock_run.side_effect = self._mock_sequence()
        DockerTestRunner(timeout=30).run(tmp_path, language="go")
        cmd = self._get_docker_run_cmd(mock_run)
        assert "agent-runner-go" in cmd

    @patch("docker.runner.subprocess.run")
    def test_c_uses_agent_runner_c(self, mock_run, tmp_path):
        mock_run.side_effect = self._mock_sequence()
        DockerTestRunner(timeout=30).run(tmp_path, language="c")
        cmd = self._get_docker_run_cmd(mock_run)
        assert "agent-runner-c" in cmd

    @patch("docker.runner.subprocess.run")
    def test_cpp_uses_agent_runner_cpp(self, mock_run, tmp_path):
        mock_run.side_effect = self._mock_sequence()
        DockerTestRunner(timeout=30).run(tmp_path, language="cpp")
        cmd = self._get_docker_run_cmd(mock_run)
        assert "agent-runner-cpp" in cmd

    @patch("docker.runner.subprocess.run")
    def test_isolation_options_preserved_for_all_languages(self, mock_run, tmp_path):
        """--network none, --memory 512m, --cpus 1은 모든 언어에 동일하게 적용된다."""
        for lang in ("python", "go", "kotlin", "javascript", "c", "cpp"):
            mock_run.side_effect = [
                MagicMock(returncode=0),
                MagicMock(returncode=0),
                MagicMock(returncode=0, stdout="ok\n", stderr=""),
            ]
            DockerTestRunner(timeout=30).run(tmp_path, language=lang)
            cmd = self._get_docker_run_cmd(mock_run)
            cmd_str = " ".join(cmd)
            assert "--network none" in cmd_str, f"{lang}: --network none 없음"
            assert "--memory 512m" in cmd_str, f"{lang}: --memory 512m 없음"
            assert "--cpus 1" in cmd_str, f"{lang}: --cpus 1 없음"


# ── test_framework 파라미터 전달 검증 ─────────────────────────────────────────


class TestTestFrameworkParameter:
    """test_framework 값이 Docker 명령의 -e TEST_FRAMEWORK=... 로 전달되는지 검증."""

    def _run_with_framework(self, mock_run, tmp_path, framework: str, language: str = "python"):
        mock_run.side_effect = [
            MagicMock(returncode=0),                                          # docker info
            MagicMock(returncode=0),                                          # image inspect
            MagicMock(returncode=0, stdout="3 passed in 0.05s\n", stderr=""), # docker run
        ]
        runner = DockerTestRunner(timeout=30)
        runner.run(tmp_path, test_framework=framework, language=language)
        return mock_run.call_args_list[2][0][0]  # docker run 커맨드 리스트

    @patch("docker.runner.subprocess.run")
    def test_default_framework_is_pytest(self, mock_run, tmp_path):
        cmd = self._run_with_framework(mock_run, tmp_path, "pytest")
        assert "TEST_FRAMEWORK=pytest" in " ".join(cmd)

    @patch("docker.runner.subprocess.run")
    def test_jest_framework_passed(self, mock_run, tmp_path):
        cmd = self._run_with_framework(mock_run, tmp_path, "jest", language="javascript")
        assert "TEST_FRAMEWORK=jest" in " ".join(cmd)

    @patch("docker.runner.subprocess.run")
    def test_go_framework_passed(self, mock_run, tmp_path):
        cmd = self._run_with_framework(mock_run, tmp_path, "go", language="go")
        assert "TEST_FRAMEWORK=go" in " ".join(cmd)

    @patch("docker.runner.subprocess.run")
    def test_gradle_framework_passed(self, mock_run, tmp_path):
        cmd = self._run_with_framework(mock_run, tmp_path, "gradle", language="kotlin")
        assert "TEST_FRAMEWORK=gradle" in " ".join(cmd)

    @patch("docker.runner.subprocess.run")
    def test_c_framework_passed(self, mock_run, tmp_path):
        cmd = self._run_with_framework(mock_run, tmp_path, "c", language="c")
        assert "TEST_FRAMEWORK=c" in " ".join(cmd)

    @patch("docker.runner.subprocess.run")
    def test_cpp_framework_passed(self, mock_run, tmp_path):
        cmd = self._run_with_framework(mock_run, tmp_path, "cpp", language="cpp")
        assert "TEST_FRAMEWORK=cpp" in " ".join(cmd)

    @patch("docker.runner.subprocess.run")
    def test_arbitrary_command_passed(self, mock_run, tmp_path):
        """알 수 없는 프레임워크(직접 명령)도 그대로 전달된다."""
        cmd = self._run_with_framework(mock_run, tmp_path, "cargo test")
        assert "TEST_FRAMEWORK=cargo test" in " ".join(cmd)

    @patch("docker.runner.subprocess.run")
    def test_rspec_framework_passed(self, mock_run, tmp_path):
        cmd = self._run_with_framework(mock_run, tmp_path, "rspec")
        assert "TEST_FRAMEWORK=rspec" in " ".join(cmd)

    @patch("docker.runner.subprocess.run")
    def test_language_map_used_when_framework_omitted(self, mock_run, tmp_path):
        """test_framework 미지정 시 LANGUAGE_TEST_FRAMEWORK_MAP이 자동 적용된다."""
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="ok\n", stderr=""),
        ]
        DockerTestRunner(timeout=30).run(tmp_path, language="kotlin")
        cmd = mock_run.call_args_list[2][0][0]
        assert "TEST_FRAMEWORK=gradle" in " ".join(cmd)


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

    def test_failure_reason_defaults_to_empty(self):
        result = RunResult(passed=True, returncode=0, stdout="", summary="1 passed")
        assert result.failure_reason == ""

    def test_failure_reason_preserved_when_set(self):
        result = RunResult(
            passed=False, returncode=71, stdout="", summary="",
            failure_reason="[NO_TESTS_COLLECTED]",
        )
        assert result.failure_reason == "[NO_TESTS_COLLECTED]"


# ── collect-only 사전 검사 (exit 70 / 71) ────────────────────────────────────


class TestCollectOnlyPrecheck:
    """
    docker-entrypoint.sh 가 `pytest --collect-only` / `go test -list` 로 사전
    검사 후 사용하는 커스텀 exit code 를 runner.py 가 올바르게 failure_reason
    으로 매핑하는지 검증한다.
    """

    def _make_runner(self):
        return DockerTestRunner(timeout=30)

    def _mock_ok(self, stdout="", stderr=""):
        return MagicMock(returncode=0, stdout=stdout, stderr=stderr)

    @patch("docker.runner.subprocess.run")
    def test_collect_only_zero_tests_returns_no_tests_collected(self, mock_run, tmp_path):
        """exit 71 → failure_reason '[NO_TESTS_COLLECTED]' + passed False."""
        mock_run.side_effect = [
            self._mock_ok(),  # docker info
            self._mock_ok(),  # image inspect
            MagicMock(
                returncode=71,
                stdout="---NO_TESTS_COLLECTED---\ncollected 0 items\n",
                stderr="",
            ),
        ]
        result = self._make_runner().run(tmp_path, language="python")
        assert result.passed is False
        assert result.returncode == 71
        assert result.failure_reason == "[NO_TESTS_COLLECTED]"
        assert "No tests were collected" in result.summary

    @patch("docker.runner.subprocess.run")
    def test_collect_only_import_error_returns_collection_error(self, mock_run, tmp_path):
        """exit 70 → failure_reason '[COLLECTION_ERROR]' + passed False."""
        mock_run.side_effect = [
            self._mock_ok(),
            self._mock_ok(),
            MagicMock(
                returncode=70,
                stdout="---COLLECTION_ERROR---\nImportError: No module named 'foo'\n",
                stderr="",
            ),
        ]
        result = self._make_runner().run(tmp_path, language="python")
        assert result.passed is False
        assert result.returncode == 70
        assert result.failure_reason == "[COLLECTION_ERROR]"
        assert "collection failed" in result.summary

    @patch("docker.runner.subprocess.run")
    def test_collect_only_success_proceeds_to_run(self, mock_run, tmp_path):
        """exit 0 → 기존 'N passed' 파싱 유지, failure_reason 빈 문자열."""
        mock_run.side_effect = [
            self._mock_ok(),
            self._mock_ok(),
            MagicMock(
                returncode=0,
                stdout="---COLLECTED: 3---\n3 passed in 0.05s\n",
                stderr="",
            ),
        ]
        result = self._make_runner().run(tmp_path, language="python")
        assert result.passed is True
        assert result.failure_reason == ""
        assert "3 passed" in result.summary

    @patch("docker.runner.subprocess.run")
    def test_jest_no_tests_found_returns_no_tests_collected(self, mock_run, tmp_path):
        """Jest --passWithNoTests=false 가 찍는 'No tests found' 출력 감지."""
        mock_run.side_effect = [
            self._mock_ok(),
            self._mock_ok(),
            MagicMock(
                returncode=1,
                stdout="No tests found, exiting with code 1\n",
                stderr="",
            ),
        ]
        result = self._make_runner().run(
            tmp_path, language="javascript", test_framework="jest",
        )
        assert result.passed is False
        assert result.failure_reason == "[NO_TESTS_COLLECTED]"
        assert "[NO_TESTS_COLLECTED]" in result.summary

    @patch("docker.runner.subprocess.run")
    def test_go_no_tests_returns_no_tests_collected(self, mock_run, tmp_path):
        """Go 의 -list pre-check 결과 exit 71 도 동일하게 매핑된다."""
        mock_run.side_effect = [
            self._mock_ok(),
            self._mock_ok(),
            MagicMock(
                returncode=71,
                stdout="---NO_TESTS_COLLECTED---\nok  \texample.com/pkg\t[no test files]\n",
                stderr="",
            ),
        ]
        result = self._make_runner().run(
            tmp_path, language="go", test_framework="go",
        )
        assert result.passed is False
        assert result.returncode == 71
        assert result.failure_reason == "[NO_TESTS_COLLECTED]"

    @patch("docker.runner.subprocess.run")
    def test_gradle_zero_tests_in_stdout_flags_no_tests_collected(self, mock_run, tmp_path):
        """Gradle 은 0 테스트여도 exit 가 정상이므로 stdout 패턴으로 감지."""
        mock_run.side_effect = [
            self._mock_ok(),
            self._mock_ok(),
            MagicMock(
                returncode=1,
                stdout="BUILD SUCCESSFUL\n> Task :test\n0 tests completed\n",
                stderr="",
            ),
        ]
        result = self._make_runner().run(
            tmp_path, language="kotlin", test_framework="gradle",
        )
        assert result.passed is False
        assert result.failure_reason == "[NO_TESTS_COLLECTED]"
        assert "[NO_TESTS_COLLECTED]" in result.summary

    @patch("docker.runner.subprocess.run")
    def test_gradle_build_successful_with_zero_tests_still_flags(self, mock_run, tmp_path):
        """
        Gradle 의 `BUILD SUCCESSFUL` + `0 tests completed` (exit 0) 를
        '통과' 로 오분류하지 않는다. 리뷰어 지적(P1): Gradle 감지가
        `if not passed:` 아래에 있으면 정상 종료 케이스를 놓친다.
        """
        mock_run.side_effect = [
            self._mock_ok(),
            self._mock_ok(),
            MagicMock(
                returncode=0,   # ← BUILD SUCCESSFUL
                stdout="> Task :test\n0 tests completed\nBUILD SUCCESSFUL in 3s\n",
                stderr="",
            ),
        ]
        result = self._make_runner().run(
            tmp_path, language="kotlin", test_framework="gradle",
        )
        assert result.passed is False, "0 tests completed 는 exit 0 이어도 통과 아님"
        assert result.failure_reason == "[NO_TESTS_COLLECTED]"
        assert "[NO_TESTS_COLLECTED]" in result.summary

    @patch("docker.runner.subprocess.run")
    def test_jest_no_tests_matched_variant_also_detected(self, mock_run, tmp_path):
        """Jest 의 'No tests matched' 변형도 감지 (대소문자/변형 내성)."""
        mock_run.side_effect = [
            self._mock_ok(),
            self._mock_ok(),
            MagicMock(
                returncode=1,
                stdout="No tests matched the regex pattern.\n",
                stderr="",
            ),
        ]
        result = self._make_runner().run(
            tmp_path, language="javascript", test_framework="jest",
        )
        assert result.failure_reason == "[NO_TESTS_COLLECTED]"

    @patch("docker.runner.subprocess.run")
    def test_compensation_does_not_flip_no_tests_collected_case(self, mock_run, tmp_path):
        """
        Jest/Gradle 출력에 우연히 'N passed' 가 포함되어도 failure_reason 이
        설정돼 있으면 passed=True 보정 안 함 (no-tests 판정 유지).
        """
        mock_run.side_effect = [
            self._mock_ok(),
            self._mock_ok(),
            MagicMock(
                returncode=1,
                stdout="No tests found. Previous run summary: 5 passed.\n",
                stderr="",
            ),
        ]
        result = self._make_runner().run(
            tmp_path, language="javascript", test_framework="jest",
        )
        assert result.passed is False  # 보정으로 True 로 뒤집히면 실패
        assert result.failure_reason == "[NO_TESTS_COLLECTED]"

    @patch("docker.runner.subprocess.run")
    def test_existing_ok_n_passed_compensation_still_works(self, mock_run, tmp_path):
        """기존 'OK: N passed' 보정 로직이 neue failure_reason 분기와 충돌하지 않음."""
        mock_run.side_effect = [
            self._mock_ok(),
            self._mock_ok(),
            MagicMock(
                returncode=1,  # exit code 이상
                stdout="OK: 5 passed, 0 failed\n",
                stderr="",
            ),
        ]
        result = self._make_runner().run(tmp_path, language="python")
        assert result.passed is True  # 보정 로직이 살아 있어야 함
        assert result.failure_reason == ""
