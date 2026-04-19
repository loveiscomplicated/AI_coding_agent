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

    def test_quality_gate_skip_list_documented(self, content):
        """Reviewer 가 QG 통과 항목을 재검사하지 않는다는 섹션이 있어야 한다.
        syntax / assertion / test_* / import 4 항목이 모두 명시돼야 함."""
        assert "Quality Gate 통과 항목" in content
        assert "재검사하지 않는다" in content
        assert "syntax" in content
        assert "assertion" in content
        assert "test_*" in content
        assert "import" in content

    def test_quality_gate_skip_list_includes_placeholder(self, content):
        """리뷰 피드백 #3 회귀 가드: placeholder/skeleton 감지는 QG 담당.
        Reviewer 가 placeholder 재검사로 `assert True` 같은 걸 기반으로
        CHANGES_REQUESTED 를 내는 역할 겹침을 막는다.
        """
        # Quality Gate 통과 항목 섹션 안에 placeholder / skeleton 언급
        section_start = content.index("Quality Gate 통과 항목")
        # 다음 섹션(## ...) 전까지가 QG 섹션
        next_section = content.find("\n## ", section_start + 1)
        qg_section = content[section_start:next_section] if next_section > 0 else content[section_start:]
        assert "placeholder" in qg_section.lower() or "스켈레톤" in qg_section or "skeleton" in qg_section.lower(), (
            "Reviewer 프롬프트의 QG 통과 항목 섹션에 placeholder/skeleton 제외 지시가 없습니다."
        )

    def test_reviewer_scope_is_semantic_not_formal(self, content):
        """Reviewer 의 '테스트 품질' 검토는 형식이 아닌 의미론에 한정돼야 한다.
        'placeholder' 탐지 같은 형식 게이트는 QG 영역이라는 안내가 명시적이어야 한다.
        """
        # "테스트 품질" 또는 "테스트 의미 품질" 항목이 acceptance_criteria 나
        # 외부 동작 검증에 초점을 맞추고 있는지
        assert "외부 동작" in content or "acceptance_criteria" in content
        # 또한 QG 가 placeholder 를 담당한다는 명시 문구
        assert "QG" in content or "Quality Gate" in content


class TestTestWriterPromptAntiPatterns:
    """test_writer.md 에 금지 패턴 섹션과 ask_user 사용 조건이 있어야 한다.

    정보 부족 상황에서 TestWriter가 방어적 코드(동적 import, try/except로 시그니처 추측,
    빈 테스트, hasattr 우회)를 쓰는 대신 ask_user / pytest.skip / dependency_artifacts
    조회 중 하나를 택하도록 유도하는 지시가 필요하다.
    """

    @pytest.fixture(scope="class")
    def content(self) -> str:
        return _load("test_writer.md")

    def test_file_exists(self):
        assert (PROMPTS_DIR / "test_writer.md").exists()

    def test_test_writer_prompt_includes_antipatterns(self, content):
        # 섹션 제목
        assert "금지 패턴" in content
        # 4개 금지 패턴이 모두 언급되어야 함
        assert "동적 import" in content
        assert "skipif" in content
        assert "try/except" in content or "try: / except" in content
        assert "빈 테스트" in content or "플레이스홀더" in content
        assert "hasattr" in content

    def test_prompt_references_dependency_artifacts(self, content):
        assert "dependency_artifacts.md" in content

    def test_prompt_suggests_ask_user_or_skip(self, content):
        # 불확실할 때 대안으로 ask_user 와 pytest.skip 이 모두 제시되어야 함
        assert "ask_user" in content
        assert "pytest.skip" in content

    def test_ask_user_usage_conditions_present(self, content):
        # ask_user 남용 방지를 위한 조건 섹션 존재
        assert "ask_user 사용 조건" in content

    def test_workflow_is_strict_priority_and_mentions_dep_artifacts(self, content):
        # "작업 절차" 섹션에 엄격한 우선순위 선언이 있어야 하고
        # dependency_artifacts 가 그 안에서 명시적으로 언급돼야 한다.
        assert "작업 절차" in content
        assert "엄격한 우선순위" in content or "고정" in content
        idx_workflow = content.index("작업 절차")
        idx_dep = content.index("dependency_artifacts.md", idx_workflow)
        assert idx_dep > idx_workflow

    def test_workflow_clarifies_conflicting_first_directives(self, content):
        """다른 섹션에서 '가장 먼저' 라고 나와도 '작업 절차' 의 1번이 최우선이어야 한다.

        회귀 가드: 과거 '가장 먼저' 가 dependency_artifacts 와 PROJECT_STRUCTURE.md
        양쪽에 모두 찍혀 있어 우선순위가 모호했다. 우선순위를 명시적으로 고정한다.
        """
        # 엄격한 우선순위를 선언하는 meta-directive 가 반드시 있어야 한다.
        assert "이 절차의 1번이 항상 최우선" in content or "1번을 항상 우선" in content
        # "행동 원칙" 의 PROJECT_STRUCTURE.md 지시는 보조 탐색(4단계 이하) 으로
        # 격하돼야 하며, "가장 먼저" 로 다시 충돌시키면 안 된다.
        # (섹션 헤더 '## 행동 원칙' 기준 — 작업 절차 안의 인용 표현 제외)
        idx_principle = content.index("## 행동 원칙")
        principle_block = content[idx_principle:idx_principle + 800]
        assert "PROJECT_STRUCTURE.md" in principle_block
        # 원칙 블록 안에서 "가장 먼저" 단어는 더이상 나오면 안 됨
        assert "가장 먼저" not in principle_block, (
            "'행동 원칙' 블록에 '가장 먼저' 가 남아 있으면 '작업 절차'의 우선순위와 충돌합니다."
        )
