"""Per-call token usage JSONL writer.

태스크 완료 시 각 LLM 호출의 토큰 사용 내역을 JSONL 파일로 기록한다.
파일 경로: agent-data/logs/{task_id}_{timestamp}.jsonl
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_LOG_DIR = Path("agent-data/logs")


def write_call_log(
    task_id: str,
    role: str,
    call_log: list[dict],
    log_dir: Path | None = None,
) -> Path | None:
    """call_log 항목들을 JSONL 파일에 기록한다.

    Args:
        task_id: 태스크 식별자
        role: 에이전트 역할 (test_writer/implementer/reviewer/intervention 등)
        call_log: ReactLoop.call_log 항목 리스트
        log_dir: 로그 디렉터리 (테스트용 오버라이드)

    Returns:
        기록된 파일 경로. 실패 시 None.
    """
    if not call_log:
        return None

    dest = log_dir or _LOG_DIR
    dest.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = dest / f"{task_id}_{ts}.jsonl"

    try:
        with open(path, "a", encoding="utf-8") as f:
            for entry in call_log:
                row = {**entry, "role": role, "task_id": task_id}
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return path
    except Exception as e:
        logger.warning("Failed to write call log: %s", e)
        return None
