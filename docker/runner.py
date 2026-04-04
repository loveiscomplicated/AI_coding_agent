"""
docker/runner.py — Docker 기반 격리 테스트 러너

workspace 디렉토리를 읽기 전용으로 컨테이너에 마운트하고
언어별 이미지에서 테스트를 실행한 뒤 결과를 RunResult로 반환한다.

이미지 이름 규칙: agent-runner-{language}
  agent-runner-python      python:3.12-slim 기반
  agent-runner-go          golang:1.22-alpine 기반
  agent-runner-kotlin      eclipse-temurin:21-jdk-alpine + Gradle 기반
  agent-runner-javascript  node:20-alpine 기반
  agent-runner-c           gcc:14-bookworm + make + check/cmocka 기반
  agent-runner-cpp         gcc:14-bookworm + cmake + googletest 기반

사용 예:
    runner = DockerTestRunner()
    result = runner.run(Path("/tmp/agent_workspaces/task-001"), language="python")
    if result.passed:
        print("테스트 통과!")
    else:
        print(result.stdout)
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.task import LANGUAGE_TEST_FRAMEWORK_MAP


DOCKERFILE_DIR = Path(__file__).parent

# ── 언어 → 이미지 이름 ────────────────────────────────────────────────────────

_LANGUAGE_IMAGE: dict[str, str] = {
    "python":     "agent-runner-python",
    "javascript": "agent-runner-javascript",
    "typescript": "agent-runner-javascript",
    "go":         "agent-runner-go",
    "kotlin":     "agent-runner-kotlin",
    "java":       "agent-runner-kotlin",
    "ruby":       "agent-runner-python",   # python 이미지에 rspec 포함 예정 시 분리
    "c":          "agent-runner-c",
    "cpp":        "agent-runner-cpp",
}

# ── 언어 → Dockerfile 파일명 ──────────────────────────────────────────────────

_LANGUAGE_DOCKERFILE: dict[str, str] = {
    "python":     "Dockerfile.python",
    "javascript": "Dockerfile.javascript",
    "typescript": "Dockerfile.javascript",
    "go":         "Dockerfile.go",
    "kotlin":     "Dockerfile.kotlin",
    "java":       "Dockerfile.kotlin",
    "ruby":       "Dockerfile.python",
    "c":          "Dockerfile.c",
    "cpp":        "Dockerfile.cpp",
}


@dataclass
class RunResult:
    passed: bool
    returncode: int
    stdout: str                          # 테스트 전체 출력
    summary: str                         # "5 passed, 2 failed in 0.12s" 마지막 줄
    failed_tests: list[str] = field(default_factory=list)  # 실패한 테스트 이름


class DockerTestRunner:
    """
    언어별 Docker 컨테이너 안에서 테스트를 실행한다.

    - workspace_dir 를 /workspace 로 읽기 전용 마운트
    - 컨테이너는 실행 후 자동 삭제 (--rm)
    - 네트워크 차단 (--network none) 및 리소스 제한 유지
    - 언어별 이미지(agent-runner-{language})를 자동 선택
    - 이미지가 없으면 Dockerfile.{language}로 자동 빌드
    """

    def __init__(self, timeout: int = 120):
        self.timeout = timeout

    # ── 공개 인터페이스 ───────────────────────────────────────────────────────

    def build_image(self, language: str) -> None:
        """
        언어에 해당하는 이미지를 Dockerfile.{language}로 빌드한다.
        이미 존재하면 캐시를 활용해 빠르게 완료된다.
        """
        image = _LANGUAGE_IMAGE.get(language)
        dockerfile = _LANGUAGE_DOCKERFILE.get(language)
        if not image or not dockerfile:
            raise RuntimeError(
                f"지원하지 않는 언어: {language!r}\n"
                f"지원 목록: {sorted(_LANGUAGE_IMAGE.keys())}"
            )

        _check_docker_available()
        project_root = DOCKERFILE_DIR.parent
        result = subprocess.run(
            [
                "docker", "build",
                "-f", str(DOCKERFILE_DIR / dockerfile),
                "-t", image,
                str(project_root),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"[{language}] 이미지 빌드 실패 ({dockerfile}):\n{result.stderr}"
            )

    def run(
        self,
        workspace_dir: Path,
        target_files: list[str] | None = None,
        test_framework: str | None = None,
        language: str = "python",
        test_files: list[str] | None = None,
    ) -> RunResult:
        """
        language에 해당하는 Docker 이미지를 선택해 테스트를 실행한다.

        TEST_FRAMEWORK 결정 우선순위:
          1. test_framework 인자 (명시적 지정)
          2. LANGUAGE_TEST_FRAMEWORK_MAP[language]
          3. target_files 확장자 자동 감지 (_detect_runtime)

        Args:
            workspace_dir:  테스트가 포함된 로컬 디렉토리 (절대 경로)
            target_files:   task.target_files (런타임 감지 fallback에 사용)
            test_framework: 테스트 프레임워크 명시적 지정 (없으면 자동 결정)
            language:       태스크 구현 언어 — 이미지 선택에 사용
            test_files:     특정 테스트 파일만 실행 (TEST_FILES 환경변수로 전달)

        Returns:
            RunResult — 이미지 미빌드/언어 미지원 시 passed=False, returncode=-1
        """
        image = _LANGUAGE_IMAGE.get(language)
        if not image:
            return RunResult(
                passed=False,
                returncode=-1,
                stdout="",
                summary=(
                    f"[UNSUPPORTED_LANGUAGE] '{language}'에 대한 이미지가 없습니다. "
                    f"지원 언어: {sorted(_LANGUAGE_IMAGE.keys())}"
                ),
                failed_tests=["unsupported_language"],
            )

        try:
            _check_docker_available()
        except RuntimeError as e:
            return RunResult(
                passed=False, returncode=-1, stdout="", summary=str(e),
            )

        workspace_dir = workspace_dir.resolve()
        if not workspace_dir.exists():
            return RunResult(
                passed=False,
                returncode=-1,
                stdout="",
                summary=f"workspace 디렉토리 없음: {workspace_dir}",
            )

        if not self._image_exists(image):
            try:
                self.build_image(language)
            except RuntimeError as e:
                return RunResult(
                    passed=False,
                    returncode=-1,
                    stdout="",
                    summary=f"Docker 이미지 빌드 실패: {e}",
                )

        runtime = (
            test_framework
            or LANGUAGE_TEST_FRAMEWORK_MAP.get(language)
            or _detect_runtime(target_files or [])
        )

        # Docker 실행 커맨드 조립
        docker_cmd = [
            "docker", "run", "--rm",
            "--network", "none",            # 네트워크 차단
            "--memory", "512m",             # 메모리 제한
            "--cpus", "1",
            "-v", f"{workspace_dir}:/workspace:ro",
            "-e", "PYTHONPATH=/workspace/src:/workspace",
            "-e", f"TEST_FRAMEWORK={runtime}",
        ]
        if test_files:
            docker_cmd += ["-e", f"TEST_FILES={' '.join(test_files)}"]
        docker_cmd.append(image)

        try:
            result = subprocess.run(
                docker_cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return RunResult(
                passed=False,
                returncode=-1,
                stdout="",
                summary=f"테스트 타임아웃 ({self.timeout}초 초과)",
            )

        stdout = result.stdout + result.stderr
        passed = result.returncode == 0
        summary = _parse_summary(stdout)
        failed_tests = _parse_failed_tests(stdout)

        # 실제로 전부 통과했는데 exit code만 비정상인 경우 보정.
        # 원인 예시: LLM 생성 테스트의 잘못된 sys.exit(), pytest INTERNALERROR/SystemExit
        if not passed and summary:
            if re.match(r"^OK:", summary):
                passed = True
            elif (re.search(r"\d+ passed", summary)
                  and not re.search(r"[1-9]\d* failed", summary)
                  and not re.search(r"[1-9]\d* error", summary)):
                passed = True

        return RunResult(
            passed=passed,
            returncode=result.returncode,
            stdout=stdout,
            summary=summary,
            failed_tests=failed_tests,
        )

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────────────

    def _image_exists(self, image: str) -> bool:
        result = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0


# ── 모듈 수준 헬퍼 ────────────────────────────────────────────────────────────


_JS_EXTS = {'.js', '.ts', '.jsx', '.tsx', '.mjs', '.cjs'}


def _detect_runtime(target_files: list[str]) -> str:
    """
    target_files 확장자에서 테스트 런타임을 결정한다.

    JS/TS 파일이 하나라도 있으면 "node",
    그 외(Python, HTML, CSS 등)는 "python".
    """
    exts = {Path(f).suffix.lower() for f in target_files}
    if exts & _JS_EXTS:
        return "node"
    return "python"


def _check_docker_available() -> None:
    """Docker 데몬이 실행 중인지 확인한다. 아니면 RuntimeError."""
    result = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Docker 데몬이 실행되지 않고 있습니다. Docker Desktop을 먼저 시작하세요."
        )


def _parse_summary(stdout: str) -> str:
    """
    여러 테스트 프레임워크 출력에서 요약 줄을 추출한다.

    convention: "OK: N passed, 0 failed" / "FAIL: N passed, M failed"
    pytest:   "5 passed, 2 failed in 0.12s"
    jest:     "Tests: 2 failed, 3 passed, 5 total"
    go test:  "FAIL\tmodule/pkg\t0.123s" / "ok\tmodule/pkg\t0.123s"
    rspec:    "5 examples, 2 failures"
    ctest:    "N% tests passed, M tests failed out of N"
    """
    lines = stdout.splitlines()

    # 출력 규약: "OK: N passed, M failed" / "FAIL: N passed, M failed"
    for line in reversed(lines):
        if re.match(r"^(OK|FAIL):\s+\d+\s+passed", line.strip()):
            return line.strip()

    # jest / vitest: "Tests:  N failed, N passed, N total"
    for line in lines:
        if re.match(r"\s*Tests:\s+", line):
            return line.strip()

    # ctest: "N% tests passed, M tests failed out of N"
    for line in reversed(lines):
        if re.search(r"tests? passed", line) and re.search(r"out of \d+", line):
            return line.strip()

    # go test: "ok" 또는 "FAIL" 로 시작하는 결과 줄
    for line in reversed(lines):
        if re.match(r"^(ok|FAIL)\s+\S+\s+[\d.]+s", line.strip()):
            return line.strip()

    # rspec: "N examples, N failures"
    for line in reversed(lines):
        if re.search(r"\d+ example", line):
            return line.strip()

    # pytest: "= N passed ... in Xs ="
    for line in reversed(lines):
        line = line.strip()
        if re.search(r"\d+ (passed|failed|error)", line):
            return re.sub(r"=+\s*", "", line).strip()

    for line in reversed(lines):
        if line.strip():
            return line.strip()
    return "(출력 없음)"


def _parse_failed_tests(stdout: str) -> list[str]:
    """
    여러 테스트 프레임워크 출력에서 실패한 테스트 이름 목록을 추출한다.

    convention: "- test_name: reason" (FAIL: 줄 이후)
    pytest:   "FAILED tests/test_foo.py::TestBar::test_baz - ..."
    jest:     "  ✕ test name (5ms)"
    go test:  "--- FAIL: TestFuncName (0.00s)"
    rspec:    "  1) ClassName#method description"
    ctest:    "N - TestName (Failed)"
    """
    failed: list[str] = []
    in_convention_block = False
    for line in stdout.splitlines():
        # 출력 규약: "FAIL: ..." 줄 이후에 나오는 "- test_name: reason"
        if re.match(r"^FAIL:\s+\d+\s+passed", line.strip()):
            in_convention_block = True
            continue
        if in_convention_block:
            m = re.match(r"^\s*-\s+(.+)$", line)
            if m:
                failed.append(m.group(1).strip())
            continue
        # pytest
        m = re.match(r"^FAILED\s+([\w/.:_-]+)", line.strip())
        if m:
            failed.append(m.group(1))
            continue
        # jest / vitest (✕ 또는 × 기호)
        m = re.match(r"^\s*[✕×]\s+(.+?)(?:\s+\(\d+ms\))?$", line)
        if m:
            failed.append(m.group(1).strip())
            continue
        # go test
        m = re.match(r"^--- FAIL:\s+(\S+)", line.strip())
        if m:
            failed.append(m.group(1))
            continue
        # rspec (번호 목록)
        m = re.match(r"^\s+\d+\)\s+(.+)$", line)
        if m:
            failed.append(m.group(1).strip())
            continue
        # ctest: "N - TestName (Failed)"
        m = re.match(r"^\s*\d+\s+-\s+(\S+)\s+\(Failed\)", line)
        if m:
            failed.append(m.group(1))
    return failed
