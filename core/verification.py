"""
core/verification.py — 검증 기반 커밋 게이트 (Verification-Driven Commit)

git_commit 도구 호출을 인터셉트하여, 설정된 검증 커맨드(pytest, flake8 등)가
모두 통과한 경우에만 실제 커밋을 허용한다.

사용 예:
    gate = VerificationGate(verification_commands=["pytest -q", "flake8 src/"])
    loop = ReactLoop(llm=..., verification_gate=gate)

테스트에서는 duck-typing 프로토콜로 mock 가능:
    class _AlwaysPassGate:
        def check(self, repo_path: str) -> GateResult:
            return GateResult(all_passed=True)
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ── 결과 데이터 클래스 ────────────────────────────────────────────────────────

@dataclass
class VerificationResult:
    """단일 검증 커맨드 실행 결과."""

    passed: bool
    command: str        # 실행된 커맨드 문자열 (로깅·리포트용)
    output: str         # stdout
    error: str = ""     # stderr
    returncode: int = 0


@dataclass
class GateResult:
    """모든 검증 커맨드의 집계 결과."""

    all_passed: bool
    results: list[VerificationResult] = field(default_factory=list)

    @property
    def failure_summary(self) -> str:
        """
        LLM에게 반환할 구조화된 실패 메시지.

        [VERIFICATION GATE FAILED] 헤더 + 실패 커맨드별 출력.
        """
        failed = [r for r in self.results if not r.passed]
        if not failed:
            return "[VERIFICATION GATE] 모든 검증을 통과했습니다."

        lines: list[str] = [
            "[VERIFICATION GATE FAILED] 다음 검증이 통과되지 않았습니다.\n",
            "커밋하기 전에 아래 문제를 모두 수정하세요.\n",
        ]
        for r in failed:
            lines.append(f"── 커맨드: {r.command}")
            lines.append(f"   Exit code: {r.returncode}")
            if r.output.strip():
                # 너무 긴 출력은 처음 1000자만 포함
                out = r.output.strip()
                if len(out) > 1000:
                    out = out[:997] + "...[생략]"
                lines.append(f"   Output:\n{_indent(out, '     ')}")
            if r.error.strip():
                err = r.error.strip()
                if len(err) > 500:
                    err = err[:497] + "...[생략]"
                lines.append(f"   Stderr:\n{_indent(err, '     ')}")
            lines.append("")
        lines.append("위 오류를 수정한 뒤 다시 커밋을 시도하세요.")
        return "\n".join(lines)


def _indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.splitlines())


# ── 검증 게이트 ───────────────────────────────────────────────────────────────

class VerificationGate:
    """
    git_commit 도구 인터셉터.

    Parameters
    ----------
    verification_commands : list[str]
        실행할 검증 커맨드 목록. 각 항목은 공백으로 분리된 토큰 문자열
        (예: ``["pytest -q", "flake8 src/"]``).
        shell=False 로 실행되므로 쉘 인젝션이 방지됩니다.
    verification_timeout : float
        커맨드당 타임아웃 초. 초과 시 FAILED 처리.
    working_dir : str | None
        검증 커맨드를 실행할 디렉토리.
        None 이면 git_commit 호출의 repo_path 를 사용.
    enabled : bool
        False 이면 check() 가 즉시 GateResult(all_passed=True) 를 반환.
        (테스트·개발 환경에서 게이트를 일시적으로 끌 때 사용)
    fail_fast : bool
        True(기본)이면 첫 번째 실패 커맨드에서 중단.
        False 이면 모든 커맨드를 실행하고 결과를 취합.
    """

    def __init__(
        self,
        verification_commands: list[str] | None = None,
        verification_timeout: float = 60.0,
        working_dir: str | None = None,
        enabled: bool = True,
        fail_fast: bool = True,
    ):
        self.verification_commands: list[str] = verification_commands or [
            "pytest -q",
            "flake8 .",
        ]
        self.verification_timeout = verification_timeout
        self.working_dir = working_dir
        self.enabled = enabled
        self.fail_fast = fail_fast

    def check(self, repo_path: str) -> GateResult:
        """
        모든 검증 커맨드를 실행하고 GateResult 를 반환한다.

        Args:
            repo_path: git_commit 도구의 repo_path 인수.
                       working_dir 가 None 인 경우 cwd 로 사용.

        Returns:
            GateResult — all_passed=True 이면 커밋 진행 허용.
        """
        if not self.enabled:
            logger.debug("VerificationGate 비활성화 — 검증 스킵")
            return GateResult(all_passed=True)

        cwd = self.working_dir or repo_path
        results: list[VerificationResult] = []

        for cmd_str in self.verification_commands:
            logger.info("검증 실행: %s (cwd=%s)", cmd_str, cwd)
            vr = self._run_command(cmd_str.split(), cwd)
            results.append(vr)

            if not vr.passed:
                logger.warning("검증 실패: %s (exit=%d)", cmd_str, vr.returncode)
                if self.fail_fast:
                    return GateResult(all_passed=False, results=results)
            else:
                logger.info("검증 통과: %s", cmd_str)

        all_passed = all(r.passed for r in results)
        return GateResult(all_passed=all_passed, results=results)

    def _run_command(self, tokens: list[str], cwd: str) -> VerificationResult:
        """
        서브프로세스로 커맨드를 실행한다.

        오버라이드 포인트 — 테스트에서 이 메서드를 교체해 외부 프로세스 없이
        동작을 검증할 수 있다.

        Args:
            tokens: 커맨드 토큰 목록 (shell=False 안전)
            cwd:    작업 디렉토리

        Returns:
            VerificationResult
        """
        cmd_str = " ".join(tokens)
        try:
            proc = subprocess.run(
                tokens,
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=self.verification_timeout,
            )
            return VerificationResult(
                passed=(proc.returncode == 0),
                command=cmd_str,
                output=proc.stdout,
                error=proc.stderr,
                returncode=proc.returncode,
            )
        except subprocess.TimeoutExpired:
            logger.error("검증 타임아웃: %s (%.1fs)", cmd_str, self.verification_timeout)
            return VerificationResult(
                passed=False,
                command=cmd_str,
                output="",
                error=f"커맨드가 {self.verification_timeout}초 타임아웃을 초과했습니다.",
                returncode=-1,
            )
        except FileNotFoundError:
            logger.error("검증 커맨드를 찾을 수 없음: %s", tokens[0])
            return VerificationResult(
                passed=False,
                command=cmd_str,
                output="",
                error=f"커맨드를 찾을 수 없습니다: '{tokens[0]}'. PATH를 확인하세요.",
                returncode=-1,
            )
        except Exception as exc:
            logger.error("검증 실행 중 예외: %s — %s", cmd_str, exc)
            return VerificationResult(
                passed=False,
                command=cmd_str,
                output="",
                error=f"실행 중 예외 발생: {type(exc).__name__}: {exc}",
                returncode=-1,
            )
