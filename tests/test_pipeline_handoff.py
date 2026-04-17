"""
tests/test_pipeline_handoff.py

TestWriter → Implementer 핸드오프 시 `context/test_design_notes.md` 가
Implementer 프롬프트에 올바르게 주입되는지 검증한다.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from orchestrator.pipeline import _build_implementer_prompt
from orchestrator.task import Task
from orchestrator.workspace import WorkspaceManager


@pytest.fixture
def task():
    return Task(
        id="handoff-001",
        title="덧셈 계산기",
        description="두 정수를 더하는 함수를 구현한다.",
        acceptance_criteria=["양수 덧셈", "음수 덧셈"],
        target_files=["src/calc.py"],
    )


@pytest.fixture
def workspace(tmp_path, task):
    ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
    ws.create()
    return ws


class TestDesignNotesInjection:
    def test_context_dir_created_by_workspace(self, workspace):
        assert (workspace.path / "context").is_dir()

    def test_prompt_includes_design_notes_when_present(self, task, workspace):
        notes = (
            "# Test Design Notes\n\n"
            "## 핵심 의도\n계산기 add 함수를 검증한다.\n\n"
            "## 주요 테스트 케이스 설명\n"
            "- `test_add_positive`: 1 + 2 == 3\n"
            "- `test_add_negative`: -1 + -2 == -3\n"
        )
        (workspace.path / "context" / "test_design_notes.md").write_text(
            notes, encoding="utf-8"
        )

        prompt = _build_implementer_prompt(task, workspace)

        assert "테스트 설계 노트" in prompt
        assert "test_add_positive" in prompt
        assert "계산기 add 함수를 검증" in prompt

    def test_prompt_omits_section_when_notes_missing(self, task, workspace):
        notes_path = workspace.path / "context" / "test_design_notes.md"
        assert not notes_path.exists()

        prompt = _build_implementer_prompt(task, workspace)

        assert "테스트 설계 노트" not in prompt
        assert task.title in prompt
        assert "수락 기준" in prompt

    def test_long_notes_truncated_at_4000_chars(self, task, workspace):
        marker = "UNIQUE_TAIL_MARKER_5678"
        # 5000자 노트 — 4000자에서 잘려야 함
        long_body = "가" * 4500 + marker
        (workspace.path / "context" / "test_design_notes.md").write_text(
            long_body, encoding="utf-8"
        )

        prompt = _build_implementer_prompt(task, workspace)

        assert "...(truncated)" in prompt
        # 뒤쪽 marker 는 잘려서 프롬프트에 등장하지 않아야 한다
        assert marker not in prompt

    def test_notes_under_4000_chars_not_truncated(self, task, workspace):
        body = "짧은 노트 내용입니다." * 10
        (workspace.path / "context" / "test_design_notes.md").write_text(
            body, encoding="utf-8"
        )

        prompt = _build_implementer_prompt(task, workspace)

        assert "...(truncated)" not in prompt
        assert "짧은 노트 내용입니다." in prompt

    def test_design_notes_after_target_files(self, task, workspace):
        """design_notes 는 target_files 섹션 뒤, 재시도 섹션 앞에 온다."""
        (workspace.path / "context" / "test_design_notes.md").write_text(
            "# Test Design Notes\n\n## 핵심 의도\n테스트 의도.",
            encoding="utf-8",
        )

        prompt = _build_implementer_prompt(task, workspace)

        idx_target = prompt.index("생성할 파일 목록")
        idx_notes = prompt.index("테스트 설계 노트")
        assert idx_target < idx_notes

    def test_reviewer_feedback_appears_after_notes(self, task, workspace):
        (workspace.path / "context" / "test_design_notes.md").write_text(
            "# Test Design Notes\n\n## 핵심 의도\n의도.",
            encoding="utf-8",
        )

        prompt = _build_implementer_prompt(
            task, workspace, reviewer_feedback="보안 이슈 수정 필요"
        )

        idx_notes = prompt.index("테스트 설계 노트")
        idx_feedback = prompt.index("Reviewer 피드백")
        assert idx_notes < idx_feedback
        assert "보안 이슈 수정 필요" in prompt
