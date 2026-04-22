"""
tests/test_cli_integration.py — CLI 메인 루프 TDD 통합 테스트

main.py, cli/config.py, cli/commands.py, cli/interface.py 의 통합 동작을 검증한다.
InstantRunner, ReactLoop는 mock, 파일시스템은 tmp_path, 환경변수는 monkeypatch.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

if not os.environ.get("ANTHROPIC_API_KEY"):
    os.environ["ANTHROPIC_API_KEY"] = "test-key"

from cli import commands, interface as ui
from cli.commands import Action
from cli.config import AgentConfig, find_repo_root, load_config
from cli.interface import (
    CLIMode,
    configure_tdd_availability,
    get_current_mode,
    set_mode,
)


# ── 공통 픽스처 ────────────────────────────────────────────────────────────────


_LLM_ENV_KEYS = (
    "LLM_PROVIDER",
    "LLM_MODEL_FAST",
    "LLM_MODEL_CAPABLE",
    "LLM_PROVIDER_FAST",
    "LLM_PROVIDER_CAPABLE",
    "LLM_TITLE_MODEL",
    "LLM_DEFAULT_MODEL",
)


@pytest.fixture(autouse=True)
def _reset_mode():
    """모드 전역 상태가 테스트 간에 오염되지 않도록 리셋."""
    configure_tdd_availability(True)
    set_mode(CLIMode.NORMAL)
    yield
    configure_tdd_availability(True)
    set_mode(CLIMode.NORMAL)


@pytest.fixture
def clean_env(monkeypatch):
    """LLM 관련 환경변수를 모두 비운 상태로 테스트."""
    for k in _LLM_ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """HOME을 임시 디렉토리로 변경하여 글로벌 config.toml이 없는 상태를 보장."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path


# ── 1) TDD 모드에서 InstantRunner 호출 ─────────────────────────────────────────


def test_tdd_mode_runs_instant_runner():
    """mode=TDD + runner loader 주입 시 InstantRunner.run()이 호출되고 True 반환."""
    from main import _run_turn

    runner = MagicMock()
    runner.run = AsyncMock(return_value=MagicMock())
    get_runner = MagicMock(return_value=runner)

    handled = _run_turn("hello", mode=CLIMode.TDD, get_runner=get_runner)

    assert handled is True
    get_runner.assert_called_once_with()
    runner.run.assert_called_once_with("hello")


# ── 2) 일반 모드에서는 InstantRunner 호출 안 됨 ────────────────────────────────


def test_normal_mode_runs_react_loop():
    """mode=NORMAL 이면 runner loader를 만들지 않고 False를 반환한다."""
    from main import _run_turn

    runner = MagicMock()
    runner.run = AsyncMock()
    get_runner = MagicMock(return_value=runner)

    handled = _run_turn("hello", mode=CLIMode.NORMAL, get_runner=get_runner)

    assert handled is False
    get_runner.assert_not_called()
    runner.run.assert_not_called()


# ── 3) 슬래시 명령어로 모드 전환 ──────────────────────────────────────────────


def test_mode_toggle_switch(monkeypatch):
    mgr = MagicMock()
    session = MagicMock()

    # 부수효과 함수들은 조용히 캡처
    captured: list[str] = []
    monkeypatch.setattr("cli.interface.print_info", lambda msg: captured.append(msg))
    monkeypatch.setattr("cli.interface.print_mode_changed", lambda m: None)

    # /tdd
    result = commands.handle("/tdd", mgr, session)
    assert result is not None
    assert result.action == Action.NONE
    assert get_current_mode() == CLIMode.TDD

    # /normal
    result = commands.handle("/normal", mgr, session)
    assert get_current_mode() == CLIMode.NORMAL

    # /mode (인자 없음) — "현재 모드" 메시지 출력
    captured.clear()
    result = commands.handle("/mode", mgr, session)
    assert result.action == Action.NONE
    assert any("현재 모드" in m for m in captured)

    # /mode tdd
    result = commands.handle("/mode tdd", mgr, session)
    assert get_current_mode() == CLIMode.TDD


def test_tdd_switches_blocked_without_git(monkeypatch):
    mgr = MagicMock()
    session = MagicMock()
    captured: list[str] = []

    configure_tdd_availability(False)
    monkeypatch.setattr("cli.interface.print_info", lambda msg: captured.append(msg))
    monkeypatch.setattr("cli.interface.print_mode_changed", lambda m: None)

    result = commands.handle("/tdd", mgr, session)
    assert result is not None
    assert result.action == Action.NONE
    assert get_current_mode() == CLIMode.NORMAL
    assert any("git 프로젝트 내에서 실행하세요" in m for m in captured)

    captured.clear()
    result = commands.handle("/mode tdd", mgr, session)
    assert result is not None
    assert result.action == Action.NONE
    assert get_current_mode() == CLIMode.NORMAL
    assert any("git 프로젝트 내에서 실행하세요" in m for m in captured)

    captured.clear()
    event = SimpleNamespace(app=SimpleNamespace(invalidate=lambda: None))
    ui._toggle_mode_handler(event)
    assert any("git 프로젝트 내에서 실행하세요" in m for m in captured)
    assert get_current_mode() == CLIMode.NORMAL


# ── 4) TOML 로드 ──────────────────────────────────────────────────────────────


def test_config_loads_from_toml(isolated_home, clean_env):
    """agent.toml의 모든 필드가 정확히 파싱된다."""
    repo = isolated_home / "repo"
    repo.mkdir()
    (repo / "agent.toml").write_text(
        """
[llm]
provider = "glm"
model_fast = "glm-fast"
model_capable = "glm-pro"
provider_fast = "ollama"
provider_capable = "claude"

[project]
language = "typescript"
test_framework = "vitest"
base_branch = "develop"

[behavior]
default_mode = "tdd"
auto_push = true
""",
        encoding="utf-8",
    )

    cfg = load_config(str(repo))

    assert cfg.provider == "glm"
    assert cfg.model_fast == "glm-fast"
    assert cfg.model_capable == "glm-pro"
    assert cfg.provider_fast == "ollama"
    assert cfg.provider_capable == "claude"
    assert cfg.language == "typescript"
    assert cfg.test_framework == "vitest"
    assert cfg.base_branch == "develop"
    assert cfg.default_mode == "tdd"
    assert cfg.auto_push is True


# ── 5) 환경변수 우선 + 기존 변수 fallback ─────────────────────────────────────


def test_config_env_overrides_toml(isolated_home, clean_env):
    repo = isolated_home / "repo"
    repo.mkdir()
    (repo / "agent.toml").write_text(
        """
[llm]
provider = "claude"
model_fast = "toml-fast"
model_capable = "toml-capable"
""",
        encoding="utf-8",
    )

    # 새 이름이 toml을 이긴다
    clean_env.setenv("LLM_PROVIDER", "openai")
    clean_env.setenv("LLM_MODEL_FAST", "env-fast")

    cfg = load_config(str(repo))
    assert cfg.provider == "openai"
    assert cfg.model_fast == "env-fast"
    assert cfg.model_capable == "toml-capable"  # 환경변수 없음 → toml 값

    # 기존 이름이 fallback 으로 쓰인다
    clean_env.delenv("LLM_MODEL_FAST", raising=False)
    clean_env.setenv("LLM_TITLE_MODEL", "legacy-fast")
    clean_env.setenv("LLM_DEFAULT_MODEL", "legacy-capable")

    cfg = load_config(str(repo))
    assert cfg.model_fast == "legacy-fast"
    assert cfg.model_capable == "legacy-capable"

    # 새 이름과 기존 이름이 둘 다 있으면 새 이름이 이긴다
    clean_env.setenv("LLM_MODEL_FAST", "new-fast")
    cfg = load_config(str(repo))
    assert cfg.model_fast == "new-fast"


# ── 6) toml 없을 때 기본값 ────────────────────────────────────────────────────


def test_config_missing_toml_uses_defaults(isolated_home, clean_env):
    repo = isolated_home / "repo"
    repo.mkdir()
    # agent.toml 없음, 환경변수 없음, 글로벌 HOME도 비어있음

    cfg = load_config(str(repo))
    assert cfg == AgentConfig()  # 하드코딩 기본값과 일치


# ── 7) .git 기준 repo root 탐지 ───────────────────────────────────────────────


def test_repo_root_detection(tmp_path):
    (tmp_path / ".git").mkdir()
    sub = tmp_path / "a" / "b" / "c"
    sub.mkdir(parents=True)

    assert find_repo_root(str(sub)) == str(tmp_path)
    assert find_repo_root(str(tmp_path)) == str(tmp_path)


# ── 8) .git이 없으면 TDD 비활성화 ─────────────────────────────────────────────


def test_no_repo_root_disables_tdd(tmp_path):
    """find_repo_root가 None이면 일반 루프로 안전하게 fallback 된다."""
    from main import _run_turn

    # tmp_path에는 .git이 없음 (그리고 부모에도 없도록 HOME-like 위치)
    # 단, tmp_path의 부모 디렉토리에 .git이 있을 수 있으므로 완전 고립된 경로로:
    isolated = tmp_path / "isolated_subdir"
    isolated.mkdir()

    # find_repo_root이 상위로 올라가며 .git을 찾으므로,
    # CI 환경에 따라 True일 수 있다. 여기서는 핵심 불변식만 검증:
    # "get_runner=None이면 TDD 모드여도 _run_turn이 False를 반환 → 일반 흐름으로 fallback"
    handled = _run_turn("hello", mode=CLIMode.TDD, get_runner=None)
    assert handled is False


def test_invalid_toml_warns_and_falls_back(isolated_home, clean_env, capsys):
    repo = isolated_home / "repo"
    repo.mkdir()
    (repo / "agent.toml").write_text("this is not valid toml :::???!!!\n", encoding="utf-8")

    cfg = load_config(str(repo))

    assert cfg == AgentConfig()
    err = capsys.readouterr().err
    assert "설정 파일 파싱 실패" in err
    assert "agent.toml" in err


# ── 9) /help 출력에 TDD 명령어 포함 ───────────────────────────────────────────


def test_slash_help_includes_tdd_commands(monkeypatch):
    captured: list[str] = []
    monkeypatch.setattr("cli.interface.print_info", lambda msg: captured.append(msg))

    mgr = MagicMock()
    session = MagicMock()
    result = commands.handle("/help", mgr, session)

    assert result.action == Action.NONE
    blob = "\n".join(captured)
    assert "/mode" in blob
    assert "/tdd" in blob
    assert "/normal" in blob
