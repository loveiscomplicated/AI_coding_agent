"""
docker/runner.py — Docker 기반 격리 테스트 러너

workspace 디렉토리를 읽기 전용으로 컨테이너에 마운트하고
pytest를 실행한 뒤 결과를 RunResult로 반환한다.

사용 예:
    runner = DockerTestRunner()
    result = runner.run(Path("/tmp/agent_workspaces/task-001"))
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


IMAGE_NAME = "ai-coding-agent-test-runner"
DOCKERFILE_DIR = Path(__file__).parent


@dataclass
class RunResult:
    passed: bool
    returncode: int
    stdout: str                          # pytest 전체 출력
    summary: str                         # "5 passed, 2 failed in 0.12s" 마지막 줄
    failed_tests: list[str] = field(default_factory=list)  # 실패한 테스트 이름


class DockerTestRunner:
    """
    Docker 컨테이너 안에서 pytest를 실행한다.

    - workspace_dir 를 /workspace 로 읽기 전용 마운트
    - 컨테이너는 실행 후 자동 삭제 (--rm)
    - Docker 데몬 미실행이나 이미지 미빌드 시 명확한 오류 반환
    """

    def __init__(self, image: str = IMAGE_NAME, timeout: int = 120):
        self.image = image
        self.timeout = timeout

    # ── 공개 인터페이스 ───────────────────────────────────────────────────────

    def build_image(self) -> None:
        """
        Dockerfile.test 로 이미지를 빌드한다.
        이미 존재하면 캐시를 활용해 빠르게 완료된다.
        """
        _check_docker_available()
        project_root = DOCKERFILE_DIR.parent
        result = subprocess.run(
            [
                "docker", "build",
                "-f", str(DOCKERFILE_DIR / "Dockerfile.test"),
                "-t", self.image,
                str(project_root),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"이미지 빌드 실패:\n{result.stderr}")

    def run(self, workspace_dir: Path, target_files: list[str] | None = None) -> RunResult:
        """
        workspace_dir 를 마운트해 테스트를 실행하고 RunResult 를 반환한다.

        target_files 의 확장자에서 런타임을 자동 감지한다:
          .js/.ts/… → "node"  (프레임워크 없는 JS 테스트)
          .py/기타   → "python" (프레임워크 없는 Python 테스트)

        Args:
            workspace_dir:  테스트가 포함된 로컬 디렉토리 (절대 경로)
            target_files:   task.target_files (런타임 감지에 사용)

        Returns:
            RunResult — 이미지 미빌드 시 passed=False, returncode=-1
        """
        _check_docker_available()

        workspace_dir = workspace_dir.resolve()
        if not workspace_dir.exists():
            return RunResult(
                passed=False,
                returncode=-1,
                stdout="",
                summary=f"workspace 디렉토리 없음: {workspace_dir}",
            )

        if not self._image_exists():
            try:
                self.build_image()
            except RuntimeError as e:
                return RunResult(
                    passed=False,
                    returncode=-1,
                    stdout="",
                    summary=f"Docker 이미지 빌드 실패: {e}",
                )

        runtime = _detect_runtime(target_files or [])

        try:
            result = subprocess.run(
                [
                    "docker", "run", "--rm",
                    "--network", "none",            # 네트워크 차단
                    "--memory", "512m",             # 메모리 제한
                    "--cpus", "1",
                    "-v", f"{workspace_dir}:/workspace:ro",
                    "-e", "PYTHONPATH=/workspace/src:/workspace",  # src/ 모듈 직접 import 허용
                    "-e", f"TEST_FRAMEWORK={runtime}",
                    self.image,
                ],
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

        return RunResult(
            passed=passed,
            returncode=result.returncode,
            stdout=stdout,
            summary=summary,
            failed_tests=failed_tests,
        )

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────────────

    def _image_exists(self) -> bool:
        result = subprocess.run(
            ["docker", "image", "inspect", self.image],
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
    return failed
