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

    def test_security_and_structure_violations_trigger_changes_requested(self, content):
        """보안 취약점·모듈 구조·target_files 스코프 위반은 반드시
        CHANGES_REQUESTED 대상임이 명시돼 있어야 한다. 이게 APPROVED_WITH_SUGGESTIONS
        로 흘러가면 보안/구조 결함이 비-블로킹 제안으로 내려가는 회귀가 생긴다."""
        # CHANGES_REQUESTED 섹션 안에 세 키워드가 나열되어 있는지
        assert "CHANGES_REQUESTED" in content
        # 보안 취약점은 반려 대상
        assert (
            "SQL injection" in content
            or "path traversal" in content
            or "command injection" in content
        )
        # target_files 위반은 반려 대상
        assert "target_files" in content
        # __init__.py 신규 생성, 순환 import 는 자동 반려
        assert "__init__.py" in content
        assert "순환 import" in content

    def test_approved_with_suggestions_is_style_only(self, content):
        """APPROVED_WITH_SUGGESTIONS 범위가 '스타일/가독성/관용' 로 한정돼 있는지.
        '방어성' 처럼 보안·견고성을 suggestions 로 떠넘길 수 있는 용어는 없어야 한다."""
        # APPROVED_WITH_SUGGESTIONS 가 정의된 본문에 '방어성' 단어가 없는지
        # (wording 충돌 방지 — 보안·견고성은 CHANGES_REQUESTED 전용)
        assert "방어성" not in content
        # suggestions 범위 예시는 여전히 스타일 항목 위주여야 함
        assert "스타일" in content or "가독성" in content
