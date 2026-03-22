"""
tools/schemas.py — 데이터클래스 정리

ToolResult  : 도구 사용 후 결과 저장 클래스
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ToolResult:
    success: bool
    output: str
    error: Optional[str] = None
