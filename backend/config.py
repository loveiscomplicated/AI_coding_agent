"""
backend/config.py — 환경 변수 설정

.env 파일 또는 환경 변수에서 API 키/토큰을 읽는다.
프론트엔드에 시크릿을 노출하지 않기 위해 백엔드에서만 관리한다.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# LLM 프로바이더 설정 (기본값: claude)
LLM_PROVIDER: str = os.environ.get("LLM_PROVIDER", "claude")
LLM_MODEL_FAST: str = os.environ.get("LLM_MODEL_FAST", "claude-haiku-4-5-20251001")
LLM_MODEL_CAPABLE: str = os.environ.get("LLM_MODEL_CAPABLE", "claude-opus-4-6")

# API 키 (provider에 따라 필요 여부 다름)
ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
ZAI_API_KEY: str = os.environ.get("ZAI_API_KEY", "")
GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")

# provider별 API 키 필수 확인
if LLM_PROVIDER == "claude" and not ANTHROPIC_API_KEY:
    raise RuntimeError(
        "ANTHROPIC_API_KEY 환경 변수가 설정되지 않았습니다.\n"
        "프로젝트 루트의 .env 파일에 ANTHROPIC_API_KEY=sk-ant-... 를 추가하세요.\n"
        "다른 프로바이더를 사용하려면 LLM_PROVIDER 환경 변수를 설정하세요."
    )
if LLM_PROVIDER == "openai" and not OPENAI_API_KEY:
    raise RuntimeError(
        "OPENAI_API_KEY 환경 변수가 설정되지 않았습니다.\n"
        "프로젝트 루트의 .env 파일에 OPENAI_API_KEY=sk-... 를 추가하세요."
    )
if LLM_PROVIDER == "glm" and not ZAI_API_KEY:
    raise RuntimeError(
        "ZAI_API_KEY 환경 변수가 설정되지 않았습니다.\n"
        "프로젝트 루트의 .env 파일에 ZAI_API_KEY=... 를 추가하세요."
    )
if LLM_PROVIDER == "gemini" and not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY 환경 변수가 설정되지 않았습니다.\n"
        "프로젝트 루트의 .env 파일에 GEMINI_API_KEY=... 를 추가하세요."
    )

# Discord 핫라인 (Step 5). 미설정 시 Discord 기능 비활성화.
DISCORD_BOT_TOKEN: str = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_GUILD_ID: int | None = (
    int(os.environ["DISCORD_GUILD_ID"])
    if os.environ.get("DISCORD_GUILD_ID")
    else None
)
