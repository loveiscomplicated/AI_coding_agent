"""
cli/retry_prompt.py — 자동 재시도 소진 후 사용자 선택지 프롬프트

방향키 인라인 선택기로 재시도/중단/(상황별) 힌트·무시 옵션을 제공한다.
파이프라인 자동 재시도가 모두 끝난 뒤 호출된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from cli.selector import SelectOption, inline_select


RetryAction = Literal["retry", "retry_with_hint", "ignore", "quit"]


@dataclass
class RetryDecision:
    action: RetryAction
    hint: str | None = None


_TEST_FAILURE_OPTIONS: list[SelectOption] = [
    SelectOption(label="재시도", value="retry"),
    SelectOption(label="힌트 추가해서 재시도", value="retry_with_hint"),
    SelectOption(label="중단", value="quit"),
]

_REVIEW_REJECTED_OPTIONS: list[SelectOption] = [
    SelectOption(label="피드백 반영해서 재시도", value="retry"),
    SelectOption(label="무시하고 진행", value="ignore"),
    SelectOption(label="중단", value="quit"),
]

_PIPELINE_ERROR_OPTIONS: list[SelectOption] = [
    SelectOption(label="재시도", value="retry"),
    SelectOption(label="중단", value="quit"),
]


class RetryPrompt:
    """자동 재시도가 모두 소진된 후 사용자에게 선택지를 제공한다."""

    def ask_on_test_failure(
        self,
        failure_summary: str,
        auto_retry_count: int = 0,
    ) -> RetryDecision:
        message = f"❌ 테스트 실패 (자동 재시도 {auto_retry_count}회 소진)"
        selected = inline_select(
            _TEST_FAILURE_OPTIONS,
            message=message,
            detail=failure_summary,
        )

        if selected == "retry_with_hint":
            hint = self._get_hint()
            if hint is None:
                return RetryDecision(action="quit")
            return RetryDecision(action="retry_with_hint", hint=hint)
        if selected == "retry":
            return RetryDecision(action="retry")
        return RetryDecision(action="quit")

    async def ask_on_test_failure_async(
        self,
        failure_summary: str,
        auto_retry_count: int = 0,
    ) -> RetryDecision:
        from cli.selector import inline_select_async

        message = f"❌ 테스트 실패 (자동 재시도 {auto_retry_count}회 소진)"
        selected = await inline_select_async(
            _TEST_FAILURE_OPTIONS,
            message=message,
            detail=failure_summary,
        )

        if selected == "retry_with_hint":
            hint = await self._get_hint_async()
            if hint is None:
                return RetryDecision(action="quit")
            return RetryDecision(action="retry_with_hint", hint=hint)
        if selected == "retry":
            return RetryDecision(action="retry")
        return RetryDecision(action="quit")

    def ask_on_review_rejected(self, reviewer_feedback: str) -> RetryDecision:
        selected = inline_select(
            _REVIEW_REJECTED_OPTIONS,
            message="⚠️  Reviewer: CHANGES_REQUESTED",
            detail=reviewer_feedback,
        )

        if selected == "retry":
            return RetryDecision(action="retry")
        if selected == "ignore":
            return RetryDecision(action="ignore")
        return RetryDecision(action="quit")

    async def ask_on_review_rejected_async(self, reviewer_feedback: str) -> RetryDecision:
        from cli.selector import inline_select_async

        selected = await inline_select_async(
            _REVIEW_REJECTED_OPTIONS,
            message="⚠️  Reviewer: CHANGES_REQUESTED",
            detail=reviewer_feedback,
        )

        if selected == "retry":
            return RetryDecision(action="retry")
        if selected == "ignore":
            return RetryDecision(action="ignore")
        return RetryDecision(action="quit")

    def ask_on_pipeline_error(self, error_message: str) -> RetryDecision:
        selected = inline_select(
            _PIPELINE_ERROR_OPTIONS,
            message="⛔ 파이프라인 오류",
            detail=error_message,
        )

        if selected == "retry":
            return RetryDecision(action="retry")
        return RetryDecision(action="quit")

    async def ask_on_pipeline_error_async(self, error_message: str) -> RetryDecision:
        from cli.selector import inline_select_async

        selected = await inline_select_async(
            _PIPELINE_ERROR_OPTIONS,
            message="⛔ 파이프라인 오류",
            detail=error_message,
        )

        if selected == "retry":
            return RetryDecision(action="retry")
        return RetryDecision(action="quit")

    def _get_hint(self) -> str | None:
        from cli.interface import console, prompt_with_stdin_pause
        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit.key_binding import KeyBindings

        hint_kb = KeyBindings()

        @hint_kb.add("escape")
        def _esc(event):
            # Esc는 빈 입력이 아니라 즉시 취소로 처리
            event.app.exit(exception=EOFError())

        for _ in range(3):
            try:
                hint = prompt_with_stdin_pause(
                    HTML("<ansiyellow><b>  힌트 ❯ </b></ansiyellow>"),
                    key_bindings=hint_kb,
                ).strip()
                if hint:
                    return hint
                console.print("[dim]  힌트를 입력해주세요.[/dim]")
            except (KeyboardInterrupt, EOFError):
                return None
        return None

    async def _get_hint_async(self) -> str | None:
        from cli.interface import console, prompt_with_stdin_pause_async
        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit.key_binding import KeyBindings

        hint_kb = KeyBindings()

        @hint_kb.add("escape")
        def _esc(event):
            event.app.exit(exception=EOFError())

        for _ in range(3):
            try:
                hint = (
                    await prompt_with_stdin_pause_async(
                        HTML("<ansiyellow><b>  힌트 ❯ </b></ansiyellow>"),
                        key_bindings=hint_kb,
                    )
                ).strip()
                if hint:
                    return hint
                console.print("[dim]  힌트를 입력해주세요.[/dim]")
            except (KeyboardInterrupt, EOFError):
                return None
        return None
