"""tests/test_selector.py — inline_select 단위 테스트.

prompt_toolkit Application은 stdin/터미널이 필요하므로 Application 클래스를
가짜로 대체하고 등록된 키 바인딩 핸들러를 직접 호출해 시뮬레이션한다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from cli.selector import SelectOption, inline_select


# prompt_toolkit이 'enter' → Keys.ControlM(value='c-m')으로 정규화하므로
# 테스트 시퀀스의 'enter' 문자열도 같은 변환을 거친다.
_KEY_NORMALIZE = {
    "enter": "c-m",
}


class _FakeEvent:
    def __init__(self, app):
        self.app = app


class _FakeApp:
    test_sequence: list[str] = []

    def __init__(
        self,
        layout=None,
        key_bindings=None,
        full_screen=False,
        erase_when_done=True,
        **kwargs,
    ):
        self.layout = layout
        self.key_bindings = key_bindings
        self._exited = False

    def run(self):
        handlers: dict[str, object] = {}
        for binding in self.key_bindings.bindings:
            for key in binding.keys:
                key_value = key.value if hasattr(key, "value") else str(key)
                handlers[key_value] = binding.handler

        for key in type(self).test_sequence:
            if self._exited:
                break
            normalized = _KEY_NORMALIZE.get(key, key)
            handler = handlers.get(normalized)
            if handler is None:
                raise KeyError(f"등록되지 않은 키: {key} (정규화: {normalized})")
            handler(_FakeEvent(self))

    def exit(self):
        self._exited = True

    def invalidate(self):
        pass


@pytest.fixture
def options():
    return [
        SelectOption(label="첫째", value="a"),
        SelectOption(label="둘째", value="b"),
        SelectOption(label="셋째", value="c"),
    ]


def _run_with_keys(monkeypatch, options, keys, **kwargs):
    _FakeApp.test_sequence = list(keys)
    monkeypatch.setattr("cli.selector.Application", _FakeApp)
    return inline_select(options, **kwargs)


def test_select_first_option(monkeypatch, options):
    assert _run_with_keys(monkeypatch, options, ["enter"]) == "a"


def test_select_second_option(monkeypatch, options):
    assert _run_with_keys(monkeypatch, options, ["down", "enter"]) == "b"


def test_select_last_option(monkeypatch, options):
    assert _run_with_keys(monkeypatch, options, ["down", "down", "enter"]) == "c"


def test_escape_returns_none(monkeypatch, options):
    assert _run_with_keys(monkeypatch, options, ["escape"]) is None


def test_up_at_top_stays(monkeypatch, options):
    # index 0에서 up을 두 번 눌러도 0에 머물러 첫째가 선택되어야 한다.
    assert _run_with_keys(monkeypatch, options, ["up", "up", "enter"]) == "a"


def test_down_at_bottom_stays(monkeypatch, options):
    # 끝까지 내려간 뒤 추가 down을 눌러도 마지막에 머물러 셋째가 선택되어야 한다.
    assert _run_with_keys(
        monkeypatch, options, ["down", "down", "down", "down", "enter"]
    ) == "c"
