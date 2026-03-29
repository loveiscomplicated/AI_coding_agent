"""
core/context.py — 컨텍스트 길이 관리

ContextManager(max_tokens) 클래스.
fit(messages) 로 토큰 한도 내로 히스토리를 잘라 반환한다.

  - 시스템 메시지는 항상 보존
  - 최신 메시지부터 역순으로 포함 (오래된 메시지 먼저 탈락)
  - tool_use / tool_result 쌍은 같이 잘려야 한다 (쌍 깨지면 안 됨)
  - 토큰 추정: 글자 수 / 4 (rough estimate)
"""

from __future__ import annotations

from llm.base import Message


class ContextManager:
    def __init__(self, max_tokens: int):
        self.max_tokens = max(0, max_tokens)

    def count_tokens(self, msg: Message) -> int:
        if isinstance(msg.content, str):
            return max(1, len(msg.content.encode("utf-8")))
        return max(1, len(str(msg.content).encode("utf-8")))

    def total_tokens(self, messages: list[Message]) -> int:
        return sum(self.count_tokens(m) for m in messages)

    def fit(self, messages: list[Message]) -> list[Message]:
        if not messages:
            return []

        system_msgs = [m for m in messages if m.role == "system"]
        non_system = [m for m in messages if m.role != "system"]

        if not non_system:
            return system_msgs

        # Single non-system message: never truncate
        if len(non_system) == 1:
            return system_msgs + non_system

        system_tokens = self.total_tokens(system_msgs)
        budget = self.max_tokens - system_tokens

        if budget <= 0:
            return system_msgs

        # Add messages from newest to oldest, preserving tool pairs
        kept = []
        remaining = budget
        i = len(non_system) - 1

        while i >= 0:
            msg = non_system[i]
            # Check if this is a tool_result (pair with previous tool_use)
            is_tool_result = (
                isinstance(msg.content, list) and
                any(isinstance(b, dict) and b.get("type") == "tool_result" for b in msg.content)
            )
            if is_tool_result and i > 0:
                prev = non_system[i - 1]
                is_tool_use = (
                    isinstance(prev.content, list) and
                    any(isinstance(b, dict) and b.get("type") == "tool_use" for b in prev.content)
                )
                if is_tool_use:
                    pair = [prev, msg]
                    pair_tokens = self.total_tokens(pair)
                    if pair_tokens <= remaining:
                        kept = pair + kept
                        remaining -= pair_tokens
                    i -= 2
                    continue

            t = self.count_tokens(msg)
            if t <= remaining:
                kept = [msg] + kept
                remaining -= t
            i -= 1

        return system_msgs + kept
