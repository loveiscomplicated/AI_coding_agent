"""
tests/test_shell_tools.py

core/loop.py 단위 테스트.
외부 의존성 없음 — tmp_path fixture로 실제 파일 I/O 검증.

실행:
    pytest tests/test_shell_tools.py -v

테스트 케이스:

1. 가상 디렉토리 만들기

tests/shell_tools_test_folder/
- .env.example
- happy.py
- folder_/

2. echo "hello"

3. python 파일 실행

4. 오류 케이스-subprocess.CalledProcessError

5. 오류 케이스-subprocess.TimeoutExpired

6. 오류 케이스-FileNotFoundError

7. 오류 케이스-Exception
"""

import os
import sys
import subprocess
from unittest.mock import patch
import pytest

par_dir = os.path.join(os.path.dirname(__file__), "..")
par_dir = os.path.abspath(par_dir)

sys.path.insert(0, par_dir)
from tools.shell_tools import execute_command


class TestExecuteCommand:
    # 1 & 2. 가상 디렉토리 생성 및 echo(또는 ls) 확인
    def test_setup_directory_and_echo(self, tmp_path):

        # echo "hello" 테스트
        result_echo = execute_command(["echo", "hello"])
        assert "hello" in result_echo.output

        # 디렉토리 구조 확인
        test_dir = tmp_path / "data"
        test_dir.mkdir()

        f = test_dir / "hello.txt"
        f.write_text("hello world", encoding="utf-8")

        f = test_dir / "happy.py"
        f.write_text("hello world", encoding="utf-8")

        f = test_dir / "config.json"
        f.write_text('{"key": "value"}')

        folder = test_dir / "temp_folder"
        folder.mkdir()

        result = execute_command(["ls", str(test_dir)])

        intended_output = "config.json\nhappy.py\nhello.txt\ntemp_folder\n"

        assert result.success is True
        assert result.output == intended_output
        assert result.error is None

    # 3. python 파일 실행
    def test_python_execution(self, tmp_path):
        py_file = tmp_path / "test_script.py"
        py_file.write_text("import sys; print('python_ok')", encoding="utf-8")

        result = execute_command(["python3", str(py_file)])
        assert result.success is True
        assert "python_ok" in result.output

    # 4. 오류 케이스 - subprocess.CalledProcessError (명령어는 존재하나 실행 실패)
    def test_calledprocess_error(self):
        result = execute_command([])

        assert result.success is False
        assert result.error is not None

    # 5. 오류 케이스 - subprocess.TimeoutExpired
    def test_timout_error(self):
        result = execute_command(["sleep", "3s"], timeout=1.0)

        assert result.success is False
        assert result.error is not None

    # 6. 오류 케이스 - FileNotFoundError (명령어 자체가 없음)
    def test_file_not_found_error(self):
        # 'abcde123' 이라는 명령어는 시스템에 존재하지 않음
        result = execute_command(["abcde123_invalid_command"])
        assert result.success is False
        # 에러 메시지나 타입에 FileNotFoundError 관련 내용이 있는지 확인
        assert "FileNotFoundError" in str(result.error) or "No such file" in str(
            result.error
        )

    # 7. 오류 케이스 - Exception (기타 예외)
    def test_generic_exception(self):
        # 'Unexpected Generic Error'라는 메시지를 가진 예외를 강제로 발생시킴
        error_msg = "Unexpected Generic Error"
        with patch("subprocess.run", side_effect=Exception(error_msg)):
            result = execute_command(["echo", "test"])

            assert result.success is False
            # 에러 메시지 내용이 그대로 들어있는지 확인
            assert error_msg in str(result.error)
