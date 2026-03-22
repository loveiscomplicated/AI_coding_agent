import os
import subprocess
from typing import Optional
from .schemas import ToolResult


def execute_command(command: list[str], input_: Optional[str] = None):
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            input=input_,
            check=True,
        )
        return ToolResult(success=True, output=str(result))
    except subprocess.CalledProcessError as e:
        return ToolResult(success=False, output="", error=str(e))
