"""tests/test_pipeline_confirm.py — PipelineConfirmManager 단위 테스트.

inline_select를 mock해서 사용자 선택 결과를 직접 주입한다.
"""

from __future__ import annotations

import pytest

from cli.pipeline_confirm import (
    ConfirmType,
    PipelineConfirmManager,
)


def _patch_select(monkeypatch, return_values, captured=None):
    """inline_select를 시퀀스 또는 단일 값으로 대체."""
    if isinstance(return_values, str) or return_values is None:
        values = iter([return_values])
    else:
        values = iter(return_values)

    def fake_select(options, message=None, detail=None, default_index=0):
        if captured is not None:
            captured.append({
                "options": list(options),
                "message": message,
                "detail": detail,
            })
        return next(values)

    monkeypatch.setattr("cli.pipeline_confirm.inline_select", fake_select)


def test_skippable_proceed(monkeypatch):
    _patch_select(monkeypatch, "proceed")
    mgr = PipelineConfirmManager()
    assert mgr.confirm(ConfirmType.TASK_REVIEW, "검토하시겠습니까?") is True
    assert mgr.is_always_allowed(ConfirmType.TASK_REVIEW) is False


def test_skippable_always(monkeypatch):
    captured: list[dict] = []
    _patch_select(monkeypatch, ["always", "proceed"], captured=captured)
    mgr = PipelineConfirmManager()

    # 첫 호출: always 선택 → True + 등록
    assert mgr.confirm(ConfirmType.TASK_REVIEW, "msg") is True
    assert mgr.is_always_allowed(ConfirmType.TASK_REVIEW) is True

    # 두 번째 호출: 등록되어 있으므로 inline_select 호출 없이 즉시 True
    assert mgr.confirm(ConfirmType.TASK_REVIEW, "msg") is True
    assert len(captured) == 1, "두 번째 호출에서는 inline_select가 호출되지 않아야 한다"


def test_skippable_cancel(monkeypatch):
    _patch_select(monkeypatch, "cancel")
    mgr = PipelineConfirmManager()
    assert mgr.confirm(ConfirmType.TASK_REVIEW, "msg") is False


def test_skippable_escape(monkeypatch):
    _patch_select(monkeypatch, None)  # Esc → None
    mgr = PipelineConfirmManager()
    assert mgr.confirm(ConfirmType.TASK_REVIEW, "msg") is False


def test_non_skippable_has_no_always(monkeypatch):
    captured: list[dict] = []
    _patch_select(monkeypatch, "proceed", captured=captured)
    mgr = PipelineConfirmManager()

    mgr.confirm(ConfirmType.OUT_OF_SCOPE_FILE, "msg")
    assert len(captured) == 1
    values = {opt.value for opt in captured[0]["options"]}
    assert "always" not in values
    assert values == {"proceed", "cancel"}


def test_non_skippable_always_returns_false(monkeypatch):
    # non-skippable 확인에서 selector가 somehow "always"를 돌려주면 False(무효)로 처리해야 한다.
    _patch_select(monkeypatch, "always")  # 비정상 반환
    mgr = PipelineConfirmManager()

    result = mgr.confirm(ConfirmType.FILE_DELETION, "정말 삭제할까요?")
    assert result is False  # "always"는 non-skippable에서 무효 → False
    assert mgr.is_always_allowed(ConfirmType.FILE_DELETION) is False  # 등록 안 됨


def test_always_per_type(monkeypatch):
    captured: list[dict] = []
    _patch_select(monkeypatch, ["always", "proceed"], captured=captured)
    mgr = PipelineConfirmManager()

    # TASK_REVIEW에 항상 허용 — COMMIT_APPROVED는 영향 없음
    mgr.confirm(ConfirmType.TASK_REVIEW, "first")
    assert mgr.is_always_allowed(ConfirmType.TASK_REVIEW) is True
    assert mgr.is_always_allowed(ConfirmType.COMMIT_APPROVED) is False

    # COMMIT_APPROVED는 여전히 inline_select 호출됨
    mgr.confirm(ConfirmType.COMMIT_APPROVED, "second")
    assert len(captured) == 2


def test_reset_clears_always(monkeypatch):
    _patch_select(monkeypatch, ["always", "proceed"])
    mgr = PipelineConfirmManager()
    mgr.confirm(ConfirmType.TASK_REVIEW, "msg")
    assert mgr.is_always_allowed(ConfirmType.TASK_REVIEW) is True

    mgr.reset()
    assert mgr.is_always_allowed(ConfirmType.TASK_REVIEW) is False
