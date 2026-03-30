"""
backend/config.py — 환경 변수 설정

.env 파일 또는 환경 변수에서 API 키/토큰을 읽는다.
프론트엔드에 시크릿을 노출하지 않기 위해 백엔드에서만 관리한다.
"""

import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")

if not ANTHROPIC_API_KEY:
    raise RuntimeError(
        "ANTHROPIC_API_KEY 환경 변수가 설정되지 않았습니다.\n"
        "프로젝트 루트의 .env 파일에 ANTHROPIC_API_KEY=sk-ant-... 를 추가하세요."
    )

# Discord 핫라인 (Step 5). 미설정 시 Discord 기능 비활성화.
DISCORD_BOT_TOKEN: str = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_CHANNEL_ID: int | None = (
    int(os.environ["DISCORD_CHANNEL_ID"])
    if os.environ.get("DISCORD_CHANNEL_ID")
    else None
)
