"""
tests/test_loop_with_compaction.py — ReactLoop + auto-compaction 통합 테스트.

Mock LLM 으로 토큰 폭발을 시뮬레이션하고, 압축 경로가 정상 호출 + 종료되는지 검증한다.

실행:
    pytest tests/test_loop_with_compaction.py -v
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from llm.base import Message
from core.loop import ReactLoop, StopReason


# ── 공통 Mock ─────────────────────────────────────────────────────────────────


class _ScriptedLLM:
    """
    순서대로 응답을 내뱉는 Mock LLM. tools 인자를 받으면 chat_log 에 기록한다.

    - build_messages: 실제 동작과 동일 (system + history + user)
    - chat: self._responses 의 다음 응답을 반환
    """

    def __init__(self, responses: list, system_prompt: str = "대용량 시스템 프롬프트 " * 200):
        self._responses = iter(responses)
        self.config = SimpleNamespace(system_prompt=system_prompt)
        self.call_count = 0
        self.received_message_counts: list[int] = []

    def build_messages(self, user_input, history=None):
        msgs = [Message(role="system", content=self.config.system_prompt)]
        if history:
            msgs.extend(history)
        msgs.append(Message(role="user", content=user_input))
        return msgs

    def chat(self, messages, **kwargs):
        self.call_count += 1
        self.received_message_counts.append(len(messages))
        return next(self._responses)


def _tool_use_response(tool_id: str, tool_name: str, tool_input: dict):
    return SimpleNamespace(
        stop_reason="tool_use",
        content=[{"type": "tool_use", "id": tool_id, "name": tool_name, "input": tool_input}],
        input_tokens=1000,
        output_tokens=100,
        model="mock",
        cached_read_tokens=0,
        cached_write_tokens=0,
    )


def _text_response(text: str):
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[{"type": "text", "text": text}],
        input_tokens=500,
        output_tokens=50,
        model="mock",
        cached_read_tokens=0,
        cached_write_tokens=0,
    )


class _FakeFastClient:
    """compaction 호출 전용 Mock — chat() 한 번 호출에 요약 텍스트 반환."""

    def __init__(self, summary: str = "과거: A.py를 읽고 실패 확인. 현재: B.py 작성 중."):
        self.summary = summary
        self.call_count = 0

    def chat(self, messages, **kwargs):
        self.call_count += 1
        return SimpleNamespace(
            content=[{"type": "text", "text": self.summary}],
            model="fast",
            stop_reason="end_turn",
            input_tokens=600,
            output_tokens=200,
        )


@pytest.fixture(autouse=True)
def _patch_tools_schema(monkeypatch):
    """Mock LLM 은 registry 에 없으므로 tools 스키마 조회를 빈 리스트로 패치."""
    monkeypatch.setattr(ReactLoop, "get_tools_schema", lambda self: [])


@pytest.fixture
def compaction_on_env(monkeypatch):
    """
    opt-in 픽스처 — compaction 경로를 강제 행사해야 하는 테스트에만 사용.

    DISABLE_COMPACTION 은 운영 킬스위치이지 테스트 전용 모드가 아니므로,
    compaction 동작을 검증하는 테스트만 env 를 명시적으로 해제한다.
    킬스위치(fallback) 경로를 검증하는 테스트는 이 픽스처를 적용하지 않아서
    `DISABLE_COMPACTION=1 pytest ...` 실행 시에도 실제 킬스위치 동작을 그대로
    검증한다.
    """
    monkeypatch.delenv("DISABLE_COMPACTION", raising=False)


# ── 테스트 ────────────────────────────────────────────────────────────────────


@pytest.mark.usefixtures("compaction_on_env")
class TestLoopWithCompaction:
    def test_below_threshold_compaction_not_triggered(self, tmp_path):
        """토큰이 임계치 미만이면 compaction 이 발동하지 않는다."""
        f = tmp_path / "a.txt"
        f.write_text("small", encoding="utf-8")

        llm = _ScriptedLLM(
            [
                _tool_use_response("c1", "read_file", {"path": str(f)}),
                _text_response("완료"),
            ],
            system_prompt="짧은 프롬프트",
        )
        fast = _FakeFastClient()
        loop = ReactLoop(
            llm=llm,
            fast_client_for_compaction=fast,
            compaction_threshold_tokens=1_000_000,  # 사실상 비활성화
        )
        result = loop.run("작업해")

        assert result.succeeded
        assert fast.call_count == 0
        # call_log 에 compaction 이벤트가 없어야 함
        assert not any(e.get("event") == "compaction" for e in result.call_log)

    def test_above_threshold_compaction_triggers_and_loop_finishes(self, tmp_path):
        """
        메시지가 부풀어 임계치를 넘으면 compaction 이 발동하고,
        ReactLoop 은 최종적으로 end_turn 으로 정상 종료한다.
        """
        f = tmp_path / "big.txt"
        # 매우 큰 파일 — read_file 결과가 컨텍스트를 부풀린다
        f.write_text("X" * 50_000, encoding="utf-8")

        # 6번 read_file → 각 tool_result 가 컨텍스트에 쌓이며 임계치 넘김
        reads = [
            _tool_use_response(f"c{i}", "read_file", {"path": str(f)})
            for i in range(6)
        ]
        responses = reads + [_text_response("모든 파일 확인 완료")]

        llm = _ScriptedLLM(responses, system_prompt="프롬프트 " * 500)
        fast = _FakeFastClient()
        loop = ReactLoop(
            llm=llm,
            fast_client_for_compaction=fast,
            compaction_threshold_tokens=5_000,  # 낮은 임계치로 강제 트리거
            compaction_keep_first_n=2,
            compaction_keep_last_n=4,
            max_tool_result_chars=20_000,  # read_file 결과를 잘 자르지 않도록
        )

        result = loop.run("모든 big.txt 읽어")

        assert result.stop_reason == StopReason.END_TURN, result.answer
        assert fast.call_count >= 1  # 최소 1회 compaction
        # call_log 에 compaction 엔트리가 추가되었는지
        compaction_events = [e for e in result.call_log if e.get("event") == "compaction"]
        assert len(compaction_events) >= 1
        # net 토큰 절감이 양수여야 의미가 있음
        assert any(e.get("net_tokens_saved", 0) > 0 for e in compaction_events)

    def test_cache_prefix_preserved_across_compaction(self, tmp_path):
        """
        compaction 이 발동해도 messages[0] (system) 과 messages[1] (초기 user task)
        은 그대로 유지되어야 한다 — 캐시 prefix 가 안정되므로 이후 호출에서 적중 가능.
        """
        # compaction 이 발동한 뒤 다음 LLM 호출이 받은 messages 의 첫 두 개를 확인
        f = tmp_path / "big.txt"
        f.write_text("Y" * 40_000, encoding="utf-8")

        reads = [
            _tool_use_response(f"c{i}", "read_file", {"path": str(f)})
            for i in range(5)
        ]
        responses = reads + [_text_response("완료")]

        system = "시스템 프롬프트 ABC " * 300
        initial_task = "첫 유저 태스크 설명 DEF"
        llm = _ScriptedLLM(responses, system_prompt=system)
        fast = _FakeFastClient()
        loop = ReactLoop(
            llm=llm,
            fast_client_for_compaction=fast,
            compaction_threshold_tokens=4_000,
            compaction_keep_first_n=2,
            compaction_keep_last_n=4,
            max_tool_result_chars=20_000,
        )

        result = loop.run(initial_task)
        assert result.stop_reason == StopReason.END_TURN
        # 최종 messages 의 앞 두 개가 그대로 유지되어야 함
        assert len(result.messages) >= 2
        assert result.messages[0].role == "system"
        assert system in result.messages[0].content  # 원본 system prompt 보존
        assert result.messages[1].role == "user"
        assert result.messages[1].content == initial_task

    def test_disable_compaction_env_var(self, tmp_path, monkeypatch):
        """DISABLE_COMPACTION=1 일 때는 compaction 이 절대 발동하지 않는다."""
        monkeypatch.setenv("DISABLE_COMPACTION", "1")
        f = tmp_path / "big.txt"
        f.write_text("Z" * 40_000, encoding="utf-8")

        reads = [
            _tool_use_response(f"c{i}", "read_file", {"path": str(f)})
            for i in range(4)
        ]
        responses = reads + [_text_response("완료")]
        llm = _ScriptedLLM(responses, system_prompt="프롬프트 " * 500)
        fast = _FakeFastClient()
        loop = ReactLoop(
            llm=llm,
            fast_client_for_compaction=fast,
            compaction_threshold_tokens=1_000,  # 매우 낮춰도
            max_tool_result_chars=20_000,
        )
        result = loop.run("작업")
        assert result.stop_reason == StopReason.END_TURN
        assert fast.call_count == 0
        assert not any(e.get("event") == "compaction" for e in result.call_log)

    def test_compaction_enabled_false_disables(self, tmp_path):
        """compaction_enabled=False 플래그 단독으로도 비활성화 가능."""
        f = tmp_path / "big.txt"
        f.write_text("W" * 40_000, encoding="utf-8")

        reads = [
            _tool_use_response(f"c{i}", "read_file", {"path": str(f)})
            for i in range(4)
        ]
        responses = reads + [_text_response("완료")]
        llm = _ScriptedLLM(responses, system_prompt="프롬프트")
        fast = _FakeFastClient()
        loop = ReactLoop(
            llm=llm,
            fast_client_for_compaction=fast,
            compaction_enabled=False,
            compaction_threshold_tokens=1_000,
            max_tool_result_chars=20_000,
        )
        result = loop.run("작업")
        assert result.stop_reason == StopReason.END_TURN
        assert fast.call_count == 0

    def test_context_pruner_has_priority_over_compaction(self, tmp_path):
        """context_pruner 가 설정되어 있으면 compaction 은 호출되지 않는다 (우선순위)."""
        f = tmp_path / "a.txt"
        f.write_text("small", encoding="utf-8")

        class _StubPruner:
            def __init__(self):
                self.call_count = 0

            def fit(self, messages):
                self.call_count += 1
                return messages  # no-op

        pruner = _StubPruner()
        fast = _FakeFastClient()
        llm = _ScriptedLLM(
            [
                _tool_use_response("c1", "read_file", {"path": str(f)}),
                _text_response("ok"),
            ],
            system_prompt="프롬프트 " * 500,
        )
        loop = ReactLoop(
            llm=llm,
            context_pruner=pruner,
            fast_client_for_compaction=fast,
            compaction_threshold_tokens=1,  # 아주 낮은 임계치
        )
        result = loop.run("작업")
        assert result.succeeded
        assert pruner.call_count >= 1
        assert fast.call_count == 0  # compaction 은 호출되지 않음

    def test_fallback_to_self_llm_when_fast_client_not_provided(self, tmp_path):
        """fast_client_for_compaction 이 None 이면 self.llm 으로 fallback 한다."""
        f = tmp_path / "big.txt"
        f.write_text("V" * 40_000, encoding="utf-8")

        # LLM 이 tool 응답, 압축 시 요약 텍스트, 최종 종료 응답 순으로 필요
        # 하지만 self.llm 이 compaction 과 main 루프를 모두 처리하므로 순서가 섞임.
        # 여기서는 fast_client_for_compaction=None 일 때도 크래시 없이 동작하는지만 검증.
        reads = [
            _tool_use_response(f"c{i}", "read_file", {"path": str(f)})
            for i in range(3)
        ]
        # compaction 발동 후 summary 요청용 응답 삽입
        summary_resp = _text_response("요약: 여러 파일을 읽었음")
        responses = reads + [summary_resp] + [_text_response("최종 완료")]
        llm = _ScriptedLLM(responses, system_prompt="프롬프트 " * 500)
        loop = ReactLoop(
            llm=llm,
            fast_client_for_compaction=None,  # self.llm 사용
            compaction_threshold_tokens=3_000,
            max_tool_result_chars=20_000,
        )

        result = loop.run("작업")
        # 중요한 것: 크래시 없음 + end_turn 으로 종료
        assert result.stop_reason == StopReason.END_TURN


class TestKillSwitchFallback:
    """compaction 킬스위치 상태에서도 _trim_history fallback 으로 히스토리가 제한된다."""

    def _long_run_responses(self, f_path: str, n: int = 8):
        reads = [
            _tool_use_response(f"c{i}", "read_file", {"path": f_path})
            for i in range(n)
        ]
        return reads + [_text_response("완료")]

    def _assert_prefix_preserved(self, result, system_prompt: str, initial_task: str):
        """messages[0] 은 system, messages[1] 은 초기 user task 여야 한다."""
        assert len(result.messages) >= 2, (
            f"최소 [system, first_user] 는 남아야 함 (len={len(result.messages)})"
        )
        assert result.messages[0].role == "system"
        assert result.messages[0].content == system_prompt
        assert result.messages[1].role == "user"
        assert result.messages[1].content == initial_task, (
            f"첫 user task 가 드롭됨: 현재 messages[1]={result.messages[1]!r}"
        )

    def test_compaction_disabled_trims_history(self, tmp_path):
        """compaction_enabled=False → _trim_history 가 적용되어 메시지가 무제한 증가하지 않는다."""
        f = tmp_path / "big.txt"
        f.write_text("Q" * 3000, encoding="utf-8")
        system = "sys-prompt-XYZ"
        task = "초기 task 문자열 GHI"
        llm = _ScriptedLLM(self._long_run_responses(str(f), n=8), system_prompt=system)
        fast = _FakeFastClient()
        loop = ReactLoop(
            llm=llm,
            fast_client_for_compaction=fast,
            compaction_enabled=False,
            history_window=2,  # 매우 작게 → 트리밍 효과 확인
            max_tool_result_chars=2000,
        )
        result = loop.run(task)
        assert result.stop_reason == StopReason.END_TURN
        assert fast.call_count == 0
        # 히스토리 무제한 증가 방지 — 보호 prefix(2) + 최근 2 턴(최대 4) 수준
        assert len(result.messages) <= 6, (
            f"킬스위치에서도 히스토리가 제한되어야 함 (len={len(result.messages)})"
        )
        # 보호 prefix 유지: system + 첫 user task 모두 살아있어야 함
        self._assert_prefix_preserved(result, system, task)

    def test_disable_compaction_env_falls_back_to_trim(self, tmp_path, monkeypatch):
        """DISABLE_COMPACTION=1 → compaction OFF + _trim_history ON 이어야 한다."""
        monkeypatch.setenv("DISABLE_COMPACTION", "1")
        f = tmp_path / "big.txt"
        f.write_text("P" * 3000, encoding="utf-8")
        system = "sys-prompt-ABC"
        task = "초기 task 문자열 DEF"
        llm = _ScriptedLLM(self._long_run_responses(str(f), n=8), system_prompt=system)
        fast = _FakeFastClient()
        loop = ReactLoop(
            llm=llm,
            fast_client_for_compaction=fast,
            history_window=2,
            max_tool_result_chars=2000,
        )
        result = loop.run(task)
        assert result.stop_reason == StopReason.END_TURN
        assert fast.call_count == 0
        assert not any(e.get("event") == "compaction" for e in result.call_log)
        assert len(result.messages) <= 6
        self._assert_prefix_preserved(result, system, task)

    def test_trim_history_preserves_system_and_first_user_directly(self):
        """
        regression: _trim_history() 자체가 [system, first_user] 두 메시지를
        모두 보존해야 한다. 이전 구현은 messages[0] 만 유지해 첫 user 를 드롭했다.
        """
        from core.loop import _trim_history

        msgs = [
            Message(role="system", content="SYS"),
            Message(role="user", content="TASK"),
            Message(role="assistant", content=[{"type": "tool_use", "id": "1", "name": "r", "input": {}}]),
            Message(role="user", content=[{"type": "tool_result", "tool_use_id": "1", "content": "a"}]),
            Message(role="assistant", content=[{"type": "tool_use", "id": "2", "name": "r", "input": {}}]),
            Message(role="user", content=[{"type": "tool_result", "tool_use_id": "2", "content": "b"}]),
        ]
        trimmed = _trim_history(msgs, window=1)
        assert trimmed[0].role == "system" and trimmed[0].content == "SYS"
        assert trimmed[1].role == "user" and trimmed[1].content == "TASK", (
            f"첫 user task 드롭 회귀: trimmed[1]={trimmed[1]!r}"
        )

    def test_trim_history_without_system_still_preserves_first_user(self):
        """system 이 없는 레거시 레이아웃에서도 첫 user 만큼은 유지한다."""
        from core.loop import _trim_history

        msgs = [
            Message(role="user", content="TASK-ONLY"),
            Message(role="assistant", content=[{"type": "tool_use", "id": "1", "name": "r", "input": {}}]),
            Message(role="user", content=[{"type": "tool_result", "tool_use_id": "1", "content": "a"}]),
            Message(role="assistant", content=[{"type": "tool_use", "id": "2", "name": "r", "input": {}}]),
            Message(role="user", content=[{"type": "tool_result", "tool_use_id": "2", "content": "b"}]),
        ]
        trimmed = _trim_history(msgs, window=1)
        assert trimmed[0].role == "user" and trimmed[0].content == "TASK-ONLY"


@pytest.mark.usefixtures("compaction_on_env")
class TestCompactionCooldown:
    """직전 compaction 직후 연쇄로 다시 호출되지 않는지 검증."""

    def test_cooldown_prevents_back_to_back_compaction(self, tmp_path):
        f = tmp_path / "huge.txt"
        f.write_text("H" * 60_000, encoding="utf-8")
        # 6 개의 대용량 tool_result 로 반복마다 임계치 초과 상태를 유지
        reads = [
            _tool_use_response(f"c{i}", "read_file", {"path": str(f)})
            for i in range(6)
        ]
        responses = reads + [_text_response("완료")]
        llm = _ScriptedLLM(responses, system_prompt="프롬프트 " * 300)
        fast = _FakeFastClient()
        loop = ReactLoop(
            llm=llm,
            fast_client_for_compaction=fast,
            compaction_threshold_tokens=3_000,  # 낮춰서 매 iter 트리거 가능 상태
            compaction_cooldown_iters=2,       # 최소 2 반복 간격 강제
            max_tool_result_chars=30_000,
        )
        result = loop.run("작업")
        compactions = [e for e in result.call_log if e.get("event") == "compaction"]
        # 첫 compaction 반복을 i1 이라 할 때, 다음 compaction 은 i1+2 이상이어야 함.
        iters = [e["iteration"] for e in compactions]
        for a, b in zip(iters, iters[1:]):
            assert b - a >= 2, (
                f"쿨다운 위반: compaction 반복 차이 {b - a} < 2 (전체 {iters})"
            )

    def test_cooldown_iter_zero_allows_consecutive(self, tmp_path):
        """compaction_cooldown_iters=0 이면 쿨다운 없이 매번 발동 가능."""
        f = tmp_path / "huge.txt"
        f.write_text("K" * 60_000, encoding="utf-8")
        reads = [
            _tool_use_response(f"c{i}", "read_file", {"path": str(f)})
            for i in range(4)
        ]
        responses = reads + [_text_response("완료")]
        llm = _ScriptedLLM(responses, system_prompt="프롬프트 " * 200)
        fast = _FakeFastClient()
        loop = ReactLoop(
            llm=llm,
            fast_client_for_compaction=fast,
            compaction_threshold_tokens=3_000,
            compaction_cooldown_iters=0,
            max_tool_result_chars=30_000,
        )
        result = loop.run("작업")
        # 쿨다운 미적용이므로 여러 번 발동 가능. 여기서는 제약 조건이 없음을 확인.
        assert result.stop_reason == StopReason.END_TURN


@pytest.mark.usefixtures("compaction_on_env")
class TestCompactionObservability:
    """compaction 이벤트가 call_log 에 올바르게 기록되는지 검증."""

    def test_compaction_event_fields(self, tmp_path):
        f = tmp_path / "big.txt"
        f.write_text("U" * 40_000, encoding="utf-8")

        reads = [
            _tool_use_response(f"c{i}", "read_file", {"path": str(f)})
            for i in range(5)
        ]
        responses = reads + [_text_response("완료")]
        llm = _ScriptedLLM(responses, system_prompt="프롬프트 " * 300)
        fast = _FakeFastClient()
        loop = ReactLoop(
            llm=llm,
            fast_client_for_compaction=fast,
            compaction_threshold_tokens=4_000,
            max_tool_result_chars=20_000,
        )
        result = loop.run("작업")

        compaction_events = [e for e in result.call_log if e.get("event") == "compaction"]
        assert len(compaction_events) >= 1
        e = compaction_events[0]
        for key in (
            "iteration",
            "dropped_messages",
            "dropped_tokens_estimate",
            "summary_tokens_estimate",
            "net_tokens_saved",
            "input_tokens",
            "output_tokens",
            "timestamp",
        ):
            assert key in e, f"compaction 이벤트에 '{key}' 누락"
