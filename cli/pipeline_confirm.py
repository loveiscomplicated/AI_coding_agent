"""
cli/pipeline_confirm.py — 파이프라인 단계별 사용자 확인 매니저

방향키 인라인 선택기로 진행/취소를 묻고, 일부 확인 종류는 "이 세션에서
항상 허용" 선택지를 제공한다. 항상 허용 상태는 메모리에만 저장되고 프로세스
종료 시 사라진다.
"""

from __future__ import annotations

from enum import Enum

from cli.selector import SelectOption, inline_select


class ConfirmType(Enum):
    # 스킵 가능 (진행 / 이 세션에서 항상 허용 / 취소)
    TASK_REVIEW = "task_review"
    COMMIT_APPROVED = "commit_approved"

    # 스킵 불가능 (진행 / 취소)
    COMMIT_CHANGES_REQUESTED = "commit_changes_requested"
    EXISTING_TEST_BROKEN = "existing_test_broken"
    OUT_OF_SCOPE_FILE = "out_of_scope_file"
    FILE_DELETION = "file_deletion"
    TASK_TOO_LARGE = "task_too_large"


_SKIPPABLE: set[ConfirmType] = {
    ConfirmType.TASK_REVIEW,
    ConfirmType.COMMIT_APPROVED,
}

_SKIPPABLE_OPTIONS: list[SelectOption] = [
    SelectOption(label="진행", value="proceed"),
    SelectOption(label="이 세션에서 항상 허용", value="always"),
    SelectOption(label="취소", value="cancel"),
]

_NON_SKIPPABLE_OPTIONS: list[SelectOption] = [
    SelectOption(label="진행", value="proceed"),
    SelectOption(label="취소", value="cancel"),
]


class PipelineConfirmManager:
    """파이프라인 단계별 확인을 관리한다."""

    def __init__(self) -> None:
        self._always_allowed: set[ConfirmType] = set()

    def confirm(
        self,
        confirm_type: ConfirmType,
        message: str,
        detail: str | None = None,
    ) -> bool:
        """사용자에게 확인을 요청한다. True=진행, False=중단."""
        if confirm_type in self._always_allowed:
            return True

        is_skippable = confirm_type in _SKIPPABLE
        options = _SKIPPABLE_OPTIONS if is_skippable else _NON_SKIPPABLE_OPTIONS

        selected = inline_select(options, message=message, detail=detail)

        if selected == "always" and is_skippable:
            self._always_allowed.add(confirm_type)
            return True
        if selected == "proceed":
            return True
        return False

    def is_always_allowed(self, confirm_type: ConfirmType) -> bool:
        return confirm_type in self._always_allowed

    def reset(self) -> None:
        self._always_allowed.clear()
