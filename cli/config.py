"""
cli/config.py — CLI 전용 설정 (agent.toml 로더)

기존 core/config.py의 AgentConfig와는 이름이 같지만 필드가 완전히 다르다.
(core 쪽: ReactLoop 전용 / 여기: TDD 파이프라인 + CLI 모드)
import 경로로 구분해서 사용한다.

우선순위 (높음 → 낮음):
  1. 환경변수   (LLM_PROVIDER, LLM_MODEL_FAST, LLM_MODEL_CAPABLE;
                 기존 LLM_TITLE_MODEL / LLM_DEFAULT_MODEL 은 fallback)
  2. {repo_path}/agent.toml
  3. ~/.config/agent/config.toml
  4. 하드코딩 기본값
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AgentConfig:
    # LLM
    provider: str = "claude"
    model_fast: str = "claude-haiku-4-5-20251001"
    model_capable: str = "claude-opus-4-6"
    provider_fast: str | None = None
    provider_capable: str | None = None

    # 프로젝트 기본값
    language: str = "python"
    test_framework: str = "pytest"
    base_branch: str = "main"

    # 동작
    default_mode: str = "normal"   # "normal" | "tdd"
    auto_push: bool = False


def find_repo_root(start: str | None = None) -> str | None:
    """`.git` 디렉토리 기준 상위 탐색. 찾지 못하면 None."""
    cur = Path(start or os.getcwd()).resolve()
    for p in [cur, *cur.parents]:
        if (p / ".git").exists():
            return str(p)
    return None


def _load_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        print(
            f"경고: 설정 파일 파싱 실패 ({path}): {exc}. 기본값으로 계속합니다.",
            file=sys.stderr,
        )
        return {}
    except OSError as exc:
        print(
            f"경고: 설정 파일을 읽지 못했습니다 ({path}): {exc}. 기본값으로 계속합니다.",
            file=sys.stderr,
        )
        return {}
    except Exception as exc:
        print(
            f"경고: 설정 파일 로드 실패 ({path}): {exc}. 기본값으로 계속합니다.",
            file=sys.stderr,
        )
        return {}


_LLM_KEYS = ("provider", "model_fast", "model_capable",
             "provider_fast", "provider_capable")
_PROJECT_KEYS = ("language", "test_framework", "base_branch")
_BEHAVIOR_KEYS = ("default_mode", "auto_push")


def _apply_toml(cfg: AgentConfig, data: dict) -> None:
    """TOML의 [llm], [project], [behavior] 테이블을 cfg에 반영."""
    for key in _LLM_KEYS:
        v = data.get("llm", {}).get(key)
        if v is not None:
            setattr(cfg, key, v)
    for key in _PROJECT_KEYS:
        v = data.get("project", {}).get(key)
        if v is not None:
            setattr(cfg, key, v)
    for key in _BEHAVIOR_KEYS:
        v = data.get("behavior", {}).get(key)
        if v is not None:
            setattr(cfg, key, v)


def load_config(repo_path: str | None = None) -> AgentConfig:
    """AgentConfig를 환경변수 > repo agent.toml > 글로벌 > 기본값 순으로 구성."""
    cfg = AgentConfig()

    # 3) 글로벌
    _apply_toml(cfg, _load_toml(Path.home() / ".config" / "agent" / "config.toml"))

    # 2) 프로젝트
    if repo_path:
        _apply_toml(cfg, _load_toml(Path(repo_path) / "agent.toml"))

    # 1) 환경변수 (새 이름 우선, 기존 이름 fallback)
    if v := os.getenv("LLM_PROVIDER"):
        cfg.provider = v
    if v := (os.getenv("LLM_MODEL_FAST") or os.getenv("LLM_TITLE_MODEL")):
        cfg.model_fast = v
    if v := (os.getenv("LLM_MODEL_CAPABLE") or os.getenv("LLM_DEFAULT_MODEL")):
        cfg.model_capable = v
    if v := os.getenv("LLM_PROVIDER_FAST"):
        cfg.provider_fast = v
    if v := os.getenv("LLM_PROVIDER_CAPABLE"):
        cfg.provider_capable = v

    return cfg
