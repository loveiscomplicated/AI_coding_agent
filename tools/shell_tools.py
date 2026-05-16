"""
tools/shell_tools.py — shell command 명령 실행 도구

execute_command  :  shell 명령어 실행
"""

import subprocess
from typing import Optional

from .schemas import ToolResult

_READ_ONLY_COMMANDS = frozenset({
    "ls",
    "find",
    "rg",
    "cat",
    "head",
    "tail",
    "wc",
    "git",
})
_READ_ONLY_GIT_SUBCOMMANDS = frozenset({"status", "diff", "log"})
_READ_ONLY_BLOCKED_TOKENS = frozenset({
    ">",
    ">>",
    "<",
    "|",
    "&&",
    "||",
    ";",
})


def _format_process_error(exc: Exception) -> str:
    """subprocess 예외에서 비어 있지 않은 진단 메시지를 만든다."""
    stderr = getattr(exc, "stderr", None)
    stdout = getattr(exc, "output", None)

    if isinstance(stderr, bytes):
        stderr = stderr.decode(errors="replace")
    if isinstance(stdout, bytes):
        stdout = stdout.decode(errors="replace")

    stderr = (stderr or "").strip()
    stdout = (stdout or "").strip()
    message = str(exc).strip()

    if stderr:
        return stderr
    if stdout:
        return stdout
    if message:
        return message
    return repr(exc)


def execute_command(
    command: list[str],
    input_: Optional[str] = None,
    timeout: Optional[float] = None,
):
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            input=input_,
            timeout=timeout,
            check=True,
        )
        return ToolResult(success=True, output=str(result.stdout))

    except subprocess.CalledProcessError as e:
        # 명령어는 실행됐으나 로직상 에러 (예: 파일 없음, 권한 없음)
        return ToolResult(success=False, output="", error=_format_process_error(e))

    except subprocess.TimeoutExpired as e:
        # 설정한 시간을 넘긴 경우
        return ToolResult(success=False, output="", error=_format_process_error(e))

    except Exception as e:
        # 기타 예상치 못한 모든 에러
        return ToolResult(success=False, output="", error=str(e))


def _validate_readonly_command(command: list[str]) -> str | None:
    if not command:
        return "빈 명령어는 허용되지 않습니다."

    if any(token in _READ_ONLY_BLOCKED_TOKENS for token in command):
        return "셸 제어 토큰은 read-only 명령에서 허용되지 않습니다."

    program = command[0]
    if program not in _READ_ONLY_COMMANDS:
        return f"'{program}' 명령은 read-only 정책에서 허용되지 않습니다."

    if program == "git":
        if len(command) < 2:
            return "git read-only 명령은 subcommand가 필요합니다."
        if command[1] not in _READ_ONLY_GIT_SUBCOMMANDS:
            return f"'git {command[1]}'은 read-only 정책에서 허용되지 않습니다."

    return None


def execute_readonly_command(
    command: list[str],
    input_: Optional[str] = None,
    timeout: Optional[float] = None,
):
    violation = _validate_readonly_command(command)
    if violation:
        return ToolResult(
            success=False,
            output="",
            error=f"READ_ONLY_POLICY_VIOLATION: {violation}",
        )
    return execute_command(command=command, input_=input_, timeout=timeout)


if __name__ == "__main__":
    a = execute_command(["ls", "tests/asdf"])
    print(a)
    """try:
        subprocess.run("exit 1", shell=True, check=True)
    except Exception as e:
        print(e)"""
