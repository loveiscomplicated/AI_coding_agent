"""
tools/shell_tools.py — shell command 명령 실행 도구

execute_command  :  shell 명령어 실행
"""

import subprocess
from typing import Optional

from .schemas import ToolResult


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


if __name__ == "__main__":
    a = execute_command(["ls", "tests/asdf"])
    print(a)
    """try:
        subprocess.run("exit 1", shell=True, check=True)
    except Exception as e:
        print(e)"""
