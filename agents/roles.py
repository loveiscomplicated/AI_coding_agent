"""
agents/roles.py — 에이전트 역할 정의

각 역할(RoleConfig)은 시스템 프롬프트와 허용 도구 목록을 갖는다.
ScopedReactLoop 생성 시 role 을 전달하면 해당 제약이 자동 적용된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# ── 도구 그룹 정의 ────────────────────────────────────────────────────────────

# 읽기 전용 도구 (모든 역할에서 사용 가능)
READ_TOOLS: list[str] = [
    "read_file",
    "read_file_lines",
    "list_directory",
    "search_files",
    "search_in_file",
    "get_outline",
    "get_function_src",
    "get_imports",
]

# 파일 쓰기 도구 (쓰기 권한이 있는 역할만)
WRITE_TOOLS: list[str] = [
    "write_file",
    "edit_file",
    "append_to_file",
]


# ── 역할 데이터 클래스 ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RoleConfig:
    name: str
    system_prompt: str
    allowed_tools: tuple[str, ...]

    def allows(self, tool_name: str) -> bool:
        return tool_name in self.allowed_tools

    def can_write(self) -> bool:
        return any(t in self.allowed_tools for t in WRITE_TOOLS)


def _load_prompt(filename: str) -> str:
    path = _PROMPTS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"프롬프트 파일 없음: {path}")
    return path.read_text(encoding="utf-8")


# ── 역할 상수 ─────────────────────────────────────────────────────────────────

TEST_WRITER = RoleConfig(
    name="test_writer",
    system_prompt=_load_prompt("test_writer.md"),
    # 테스트 파일 생성만 허용 (edit_file/append 불필요)
    allowed_tools=tuple(READ_TOOLS + ["write_file"]),
)

IMPLEMENTER = RoleConfig(
    name="implementer",
    system_prompt=_load_prompt("implementer.md"),
    # 구현 파일 생성 및 수정 모두 허용
    allowed_tools=tuple(READ_TOOLS + WRITE_TOOLS),
)

REVIEWER = RoleConfig(
    name="reviewer",
    system_prompt=_load_prompt("reviewer.md"),
    # 읽기 전용
    allowed_tools=tuple(READ_TOOLS),
)
