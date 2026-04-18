"""
tests/test_prompts.py

agents/prompts/*.md 프롬프트 파일의 핵심 섹션/키워드 존재 여부 회귀 테스트.
프롬프트가 에이전트 행동 규약이므로, verdict 정의나 출력 형식이 실수로
삭제되지 않도록 방어한다.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "agents" / "prompts"


def _load(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


class TestReviewerPromptVerdictSection:
    """reviewer.md 에 네 가지 verdict 가 모두 정의돼 있어야 한다."""

    @pytest.fixture(scope="class")
    def content(self) -> str:
        return _load("reviewer.md")

    def test_file_exists(self):
        assert (PROMPTS_DIR / "reviewer.md").exists()

    def test_has_verdict_section(self, content):
        assert "## Verdict" in content or "## 판정 규칙" in content

    def test_lists_all_four_verdicts(self, content):
        for verdict in (
            "APPROVED",
            "APPROVED_WITH_SUGGESTIONS",
            "CHANGES_REQUESTED",
            "ERROR",
        ):
            assert verdict in content, f"reviewer.md 에 {verdict} 정의 누락"

    def test_defines_approved_with_suggestions_semantics(self, content):
        # non-blocking 제안 용도임을 명시해야 한다
        assert "APPROVED_WITH_SUGGESTIONS" in content
        assert "non-blocking" in content or "비-블로킹" in content

    def test_documents_output_format(self, content):
        # VERDICT / SUMMARY / DETAILS 출력 규약 유지
        assert "VERDICT:" in content
        assert "SUMMARY:" in content
        assert "DETAILS:" in content
