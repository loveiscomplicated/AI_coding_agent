"""
tests/test_config.py

설정 파일 기능 테스트.

설계:
  core/config.py 에 AgentConfig 구현.

  - 기본 위치: ~/.config/ai_coding_agent/config.toml
  - 필드: provider, model, max_iterations, max_tokens, auto_approve
  - load_config(path) / save_config(config, path)
  - 없으면 기본값 반환
  - 잘못된 값은 기본값으로 fallback

아직 구현되지 않음 — 처음엔 실패한다.

실행:
    pytest tests/test_config.py -v
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.config import AgentConfig, load_config, save_config   # 아직 없음


# ── 기본값 ────────────────────────────────────────────────────────────────────


class TestAgentConfigDefaults:
    def test_default_provider_is_claude(self):
        cfg = AgentConfig()
        assert cfg.provider == "claude"

    def test_default_model_is_sonnet(self):
        cfg = AgentConfig()
        assert "sonnet" in cfg.model or "claude" in cfg.model

    def test_default_max_iterations_positive(self):
        cfg = AgentConfig()
        assert cfg.max_iterations > 0

    def test_default_max_tokens_positive(self):
        cfg = AgentConfig()
        assert cfg.max_tokens > 0

    def test_default_auto_approve_false(self):
        cfg = AgentConfig()
        assert cfg.auto_approve is False

    def test_all_fields_present(self):
        cfg = AgentConfig()
        assert hasattr(cfg, "provider")
        assert hasattr(cfg, "model")
        assert hasattr(cfg, "max_iterations")
        assert hasattr(cfg, "max_tokens")
        assert hasattr(cfg, "auto_approve")


# ── load_config ────────────────────────────────────────────────────────────────


class TestLoadConfig:
    def test_missing_file_returns_defaults(self, tmp_path):
        """파일이 없으면 기본값 AgentConfig 를 반환해야 한다."""
        path = tmp_path / "nonexistent.toml"
        cfg = load_config(str(path))
        assert isinstance(cfg, AgentConfig)
        assert cfg.provider == AgentConfig().provider

    def test_loads_provider(self, tmp_path):
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text('provider = "ollama"\n', encoding="utf-8")
        cfg = load_config(str(cfg_file))
        assert cfg.provider == "ollama"

    def test_loads_model(self, tmp_path):
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text('model = "devstral:24b"\n', encoding="utf-8")
        cfg = load_config(str(cfg_file))
        assert cfg.model == "devstral:24b"

    def test_loads_max_iterations(self, tmp_path):
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text("max_iterations = 20\n", encoding="utf-8")
        cfg = load_config(str(cfg_file))
        assert cfg.max_iterations == 20

    def test_loads_auto_approve_true(self, tmp_path):
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text("auto_approve = true\n", encoding="utf-8")
        cfg = load_config(str(cfg_file))
        assert cfg.auto_approve is True

    def test_partial_config_uses_defaults_for_missing(self, tmp_path):
        """일부 필드만 있으면 나머지는 기본값으로 채워야 한다."""
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text('provider = "openai"\n', encoding="utf-8")
        cfg = load_config(str(cfg_file))
        assert cfg.provider == "openai"
        assert cfg.max_iterations == AgentConfig().max_iterations

    def test_invalid_provider_falls_back_to_default(self, tmp_path):
        """잘못된 provider 값은 기본값으로 fallback."""
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text('provider = "invalid_provider_xyz"\n', encoding="utf-8")
        cfg = load_config(str(cfg_file))
        assert cfg.provider in ("claude", "openai", "ollama")

    def test_invalid_toml_returns_defaults(self, tmp_path):
        """파싱 불가 파일이면 기본값 반환."""
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text("this is not valid toml :::???!!!\n", encoding="utf-8")
        cfg = load_config(str(cfg_file))
        assert isinstance(cfg, AgentConfig)

    def test_negative_max_iterations_falls_back(self, tmp_path):
        """음수 max_iterations 는 기본값으로 fallback."""
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text("max_iterations = -5\n", encoding="utf-8")
        cfg = load_config(str(cfg_file))
        assert cfg.max_iterations > 0

    def test_zero_max_tokens_falls_back(self, tmp_path):
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text("max_tokens = 0\n", encoding="utf-8")
        cfg = load_config(str(cfg_file))
        assert cfg.max_tokens > 0


# ── save_config ────────────────────────────────────────────────────────────────


class TestSaveConfig:
    def test_saves_and_reloads(self, tmp_path):
        cfg_file = tmp_path / "config.toml"
        cfg = AgentConfig(provider="ollama", model="devstral:24b", max_iterations=20)
        save_config(cfg, str(cfg_file))

        loaded = load_config(str(cfg_file))
        assert loaded.provider == "ollama"
        assert loaded.model == "devstral:24b"
        assert loaded.max_iterations == 20

    def test_creates_parent_directories(self, tmp_path):
        path = tmp_path / "a" / "b" / "config.toml"
        save_config(AgentConfig(), str(path))
        assert path.exists()

    def test_overwrites_existing_file(self, tmp_path):
        cfg_file = tmp_path / "config.toml"
        save_config(AgentConfig(provider="claude"), str(cfg_file))
        save_config(AgentConfig(provider="openai"), str(cfg_file))

        loaded = load_config(str(cfg_file))
        assert loaded.provider == "openai"

    def test_saved_file_is_valid_toml(self, tmp_path):
        """저장된 파일은 TOML 형식이어야 한다."""
        import tomllib  # Python 3.11+
        cfg_file = tmp_path / "config.toml"
        save_config(AgentConfig(), str(cfg_file))
        with open(cfg_file, "rb") as f:
            data = tomllib.load(f)
        assert isinstance(data, dict)

    def test_auto_approve_persisted(self, tmp_path):
        cfg_file = tmp_path / "config.toml"
        save_config(AgentConfig(auto_approve=True), str(cfg_file))
        loaded = load_config(str(cfg_file))
        assert loaded.auto_approve is True


# ── AgentConfig 유효성 ─────────────────────────────────────────────────────────


class TestAgentConfigValidation:
    def test_invalid_provider_raises(self):
        with pytest.raises((ValueError, TypeError)):
            AgentConfig(provider="grok")

    def test_negative_max_iterations_raises(self):
        with pytest.raises((ValueError, TypeError)):
            AgentConfig(max_iterations=-1)

    def test_zero_max_iterations_raises(self):
        with pytest.raises((ValueError, TypeError)):
            AgentConfig(max_iterations=0)

    def test_valid_providers_accepted(self):
        for p in ("claude", "openai", "ollama"):
            cfg = AgentConfig(provider=p)
            assert cfg.provider == p

    def test_equality(self):
        cfg1 = AgentConfig(provider="claude", model="sonnet")
        cfg2 = AgentConfig(provider="claude", model="sonnet")
        assert cfg1 == cfg2

    def test_inequality(self):
        cfg1 = AgentConfig(provider="claude")
        cfg2 = AgentConfig(provider="openai")
        assert cfg1 != cfg2
