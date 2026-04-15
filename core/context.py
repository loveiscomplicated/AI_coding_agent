"""
core/context.py — 컨텍스트 길이 관리

두 가지 컨텍스트 관리자를 제공한다:

ContextManager(max_tokens)
    - 토큰 예산 기반의 단순 pruning (기존 구현, 변경 없음)
    - 시스템 메시지 항상 보존
    - 최신 메시지부터 역순으로 포함 (오래된 메시지 먼저 탈락)
    - tool_use / tool_result 쌍 보존

SemanticContextPruner(max_tokens, task_description, ...)
    - 3-계층 TF-IDF 기반 시맨틱 pruning (신규)
    - Tier 1 (recent_turns):  최근 N 쌍 → 전문 보존
    - Tier 2 (middle_turns):  중간 M 쌍 → 1줄 요약으로 압축
    - Tier 3 (나머지):         드롭
    - 외부 LLM 호출 없음, O(total_chars) 복잡도
"""

from __future__ import annotations

import math
import re
from collections import Counter

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


# ── SemanticContextPruner ─────────────────────────────────────────────────────

# 한국어·영어 불용어
_STOP_WORDS: frozenset[str] = frozenset({
    # 영어
    "the", "a", "an", "is", "in", "of", "to", "and", "or", "for",
    "be", "with", "that", "this", "it", "on", "at", "by", "from",
    "are", "was", "were", "been", "have", "has", "had", "do", "does",
    "did", "will", "would", "could", "should", "may", "might", "shall",
    "not", "no", "but", "if", "as", "so", "than", "then", "when",
    # 한국어 조사·어미 (2글자 이하는 이미 필터됨)
    "있습", "없습", "합니", "입니", "습니", "이다", "하다", "되다",
    "이고", "이며", "이나", "이라", "에서", "으로", "까지", "부터",
})


class SemanticContextPruner:
    """
    3-계층 TF-IDF 기반 시맨틱 컨텍스트 관리자.

    외부 LLM 호출 없음. 모든 연산은 O(total_chars) 이내.

    Tier 1 (recent_turns 개 쌍):    전문(full text) 보존
    Tier 2 (middle_turns 개 쌍):    1줄 요약으로 압축 (추출적 요약)
    Tier 3 (그 이전):               드롭

    시스템 메시지와 첫 번째 사용자 메시지(초기 태스크)는 항상 Tier 1 으로 취급된다.

    Parameters
    ----------
    max_tokens        : 토큰 하드 캡 (byte-length 기준 rough estimate)
    task_description  : 현재 태스크 텍스트 — TF-IDF 쿼리 문서로 사용
    recent_turns      : Tier 1 turn 쌍 수
    middle_turns      : Tier 2 turn 쌍 수 (요약 압축)
    """

    def __init__(
        self,
        max_tokens: int,
        task_description: str = "",
        recent_turns: int = 4,
        middle_turns: int = 4,
    ):
        self.max_tokens = max(0, max_tokens)
        self.recent_turns = recent_turns
        self.middle_turns = middle_turns
        self._task_tokens: Counter = self._tokenize(task_description)

    # ── 공개 인터페이스 ────────────────────────────────────────────────────────

    def fit(self, messages: list[Message]) -> list[Message]:
        """
        ContextManager.fit() 의 drop-in 대체재.

        항상 시스템 메시지 + 첫 user 메시지(초기 태스크)를 보존하고,
        나머지를 tier 별로 선별한다.

        Args:
            messages: 전체 메시지 목록

        Returns:
            pruned 메시지 목록
        """
        if not messages:
            return []

        system_msgs = [m for m in messages if m.role == "system"]
        non_system = [m for m in messages if m.role != "system"]

        if not non_system:
            return system_msgs

        # 첫 번째 메시지(초기 태스크)는 항상 보존
        anchor = non_system[:1]
        rest = non_system[1:]

        # (assistant, user) 쌍으로 묶기
        turns = self._pair_turns(rest)

        total = len(turns)
        tier1_count = self.recent_turns
        tier2_count = self.middle_turns

        # 최신 순으로 tier 배정
        tier1_turns = turns[max(0, total - tier1_count):]           # 전문 보존
        tier2_turns = turns[max(0, total - tier1_count - tier2_count):max(0, total - tier1_count)]  # 요약
        # tier3 는 드롭 (사용 안 함)

        kept: list[Message] = []

        # Tier 2: 요약 압축
        for turn in tier2_turns:
            for msg in turn:
                kept.append(self._summarize(msg))

        # Tier 1: 전문 보존
        for turn in tier1_turns:
            kept.extend(turn)

        result = system_msgs + anchor + kept

        # 최종 토큰 예산 초과 시 ContextManager 로 fallback 트리밍
        total_tok = sum(self._count_tokens(m) for m in result)
        if self.max_tokens > 0 and total_tok > self.max_tokens:
            cm = ContextManager(self.max_tokens)
            result = cm.fit(result)

        return result

    def update_task(self, task_description: str) -> None:
        """
        현재 태스크가 변경되었을 때 TF-IDF 쿼리 문서를 갱신한다.

        파이프라인 단계 전환(예: TestWriter → Implementer) 시 호출하면
        새 단계에 맞게 관련성 점수가 갱신된다.
        """
        self._task_tokens = self._tokenize(task_description)

    def score(self, msg: Message) -> float:
        """
        메시지와 현재 태스크 간의 TF-IDF 코사인 유사도를 반환한다.

        task_description 이 비어있으면 0.0 반환.
        Score 범위: [0.0, 1.0]
        """
        return self._score(msg)

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────────────

    def _tokenize(self, text: str) -> Counter:
        """소문자 단어 토크나이저. 2글자 이하 단어 및 불용어 제거."""
        words = re.findall(r"[a-zA-Z가-힣]{2,}", text.lower())
        return Counter(w for w in words if w not in _STOP_WORDS)

    def _score(self, msg: Message) -> float:
        """TF-IDF 스타일 코사인 유사도 (task vs message)."""
        if not self._task_tokens:
            return 0.0
        content_str = msg.content if isinstance(msg.content, str) else str(msg.content)
        msg_tokens = self._tokenize(content_str)
        if not msg_tokens:
            return 0.0
        dot = sum(self._task_tokens[k] * msg_tokens[k] for k in self._task_tokens)
        mag_q = math.sqrt(sum(v * v for v in self._task_tokens.values()))
        mag_d = math.sqrt(sum(v * v for v in msg_tokens.values()))
        if mag_q * mag_d == 0:
            return 0.0
        return dot / (mag_q * mag_d)

    def _summarize(self, msg: Message) -> Message:
        """
        추출적 요약: 첫 의미있는 문장(text) 또는 구조화 1-liner(tool_use/result).

        원본 메시지를 변경하지 않고 새 Message 객체를 반환한다.
        """
        content = msg.content

        # 순수 텍스트
        if isinstance(content, str):
            # 첫 비어있지 않은 문장 추출
            sentences = [s.strip() for s in re.split(r"[.!?\n]", content) if s.strip()]
            first = sentences[0] if sentences else content.strip()
            summary = f"[SUMMARY] {first[:120]}" if first else "[SUMMARY] (empty)"
            return Message(role=msg.role, content=summary)

        # 구조화 콘텐츠 (tool_use / tool_result 블록 리스트)
        if isinstance(content, list) and content:
            block = content[0]
            btype = (
                block.get("type") if isinstance(block, dict)
                else getattr(block, "type", "")
            )
            if btype == "tool_use":
                name = (
                    block.get("name", "?") if isinstance(block, dict)
                    else getattr(block, "name", "?")
                )
                inp = (
                    block.get("input", {}) if isinstance(block, dict)
                    else getattr(block, "input", {})
                )
                inp_preview = repr(inp)[:80]
                return Message(role=msg.role,
                               content=f"[SUMMARY] tool_use: {name}({inp_preview})")
            if btype == "tool_result":
                raw = (
                    block.get("content", "") if isinstance(block, dict)
                    else getattr(block, "content", "")
                )
                is_err = (
                    block.get("is_error", False) if isinstance(block, dict)
                    else getattr(block, "is_error", False)
                )
                tool_use_id = (
                    block.get("tool_use_id", "") if isinstance(block, dict)
                    else getattr(block, "tool_use_id", "")
                )
                prefix = "ERROR" if is_err else "OK"
                # tool_use_id를 보존해야 OpenAI/GLM API가 tool_call ↔ tool_result
                # 페어링을 검증할 때 오류가 발생하지 않는다.
                return Message(role=msg.role, content=[{
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": f"[SUMMARY] tool_result({prefix}): {str(raw)[:100]}",
                    "is_error": is_err,
                }])

        return Message(role=msg.role, content="[SUMMARY] (structured)")

    def _count_tokens(self, msg: Message) -> int:
        content = msg.content
        if isinstance(content, str):
            return max(1, len(content.encode("utf-8")))
        return max(1, len(str(content).encode("utf-8")))

    def _pair_turns(self, messages: list[Message]) -> list[list[Message]]:
        """
        메시지를 (assistant, user) 쌍으로 묶는다.

        _trim_history 와 동일한 쌍 묶기 로직 — tool_use/tool_result 쌍이
        분리되지 않도록 atomic 단위로 취급한다.
        """
        pairs: list[list[Message]] = []
        i = 0
        while i < len(messages):
            if messages[i].role == "assistant":
                if i + 1 < len(messages) and messages[i + 1].role == "user":
                    pairs.append([messages[i], messages[i + 1]])
                    i += 2
                else:
                    pairs.append([messages[i]])
                    i += 1
            else:
                # 고립된 user 메시지 (힐 프롬프트 등)
                pairs.append([messages[i]])
                i += 1
        return pairs
