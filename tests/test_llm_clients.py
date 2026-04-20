"""
tests/test_llm_clients.py

Reviewer 의 prompt cache prefix 안정성과 각 LLM 클라이언트 메시지 직렬화의
결정성(회귀 방지)을 검증한다.

이 파일은 결정성 관련 기존 테스트와 역할이 다르다:
  - `tests/test_openai_message_layout.py` / `tests/test_glm_message_layout.py`
    → `_to_openai_messages()` 내부 결정성 (키 순서, canonical arguments)
  - 본 파일 (`test_llm_clients.py`)
    → 호출 경계(payload/전체 messages)에서의 cross-task / 반복 호출 안정성
      + Reviewer system prompt 불변성 + Gemini `_to_gemini_contents` 결정성

핵심 불변식은 "prefix 가 byte-identical 이다" 이므로, 본 파일은 단순 `==`
대신 **canonical serialization → sha256 digest** 비교와 실패 시 **unified
diff** 를 출력하는 헬퍼(`_assert_byte_identical`)로 검증한다. drift 가 발생하면
테스트 실패 메시지가 어느 문자에서 깨졌는지 설명해준다.
"""
from __future__ import annotations

import copy
import difflib
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from agents.roles import REVIEWER
from docker.runner import RunResult
from llm.base import Message
from llm.gemini_client import _to_gemini_contents
from llm.glm_client import _to_openai_messages as _to_glm_messages
from llm.openai_client import _to_openai_messages as _to_openai_messages
from orchestrator.pipeline import _build_reviewer_prompt
from orchestrator.task import Task
from orchestrator.workspace import WorkspaceManager


# ── byte-identical helper ────────────────────────────────────────────────────


def _canonical_bytes(payload) -> bytes:
    """
    payload 가 '실제로 배출하는 바이트' 를 그대로 hash 한다. dict 키 정규화는
    금지 — 삽입 순서 자체가 prompt caching prefix 의 일부이므로, key-order
    drift 를 여기서 의미적 동등성으로 축소하면 회귀를 놓친다.

    - str          → UTF-8 그대로
    - list/dict    → json.dumps(sort_keys=False, ensure_ascii=False) UTF-8
                     (삽입 순서 보존 — {"a":1,"b":2} ≠ {"b":2,"a":1})
    - 그 외        → str(payload) UTF-8
    """
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8")
    if isinstance(payload, (list, dict)):
        return json.dumps(
            payload, sort_keys=False, ensure_ascii=False
        ).encode("utf-8")
    return str(payload).encode("utf-8")


def _digest(payload) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _assert_byte_identical(a, b, label: str = "payload") -> None:
    """
    두 payload 의 canonical byte digest 가 같은지 검증.
    다르면 sha256 두 개 + unified diff 를 포함한 AssertionError 로 실패시킨다.
    """
    da, db = _digest(a), _digest(b)
    if da == db:
        return
    ba = _canonical_bytes(a).decode("utf-8", errors="replace")
    bb = _canonical_bytes(b).decode("utf-8", errors="replace")
    diff = "\n".join(difflib.unified_diff(
        ba.splitlines(), bb.splitlines(),
        fromfile=f"{label}@a", tofile=f"{label}@b",
        lineterm="",
    ))
    raise AssertionError(
        f"{label}: byte drift detected\n"
        f"  sha256(a) = {da}\n"
        f"  sha256(b) = {db}\n"
        f"  len(a)    = {len(ba)}\n"
        f"  len(b)    = {len(bb)}\n"
        f"  diff:\n{diff}"
    )


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _make_task(task_id: str, title: str) -> Task:
    return Task(
        id=task_id,
        title=title,
        description="테스트용 설명",
        acceptance_criteria=["조건1", "조건2"],
        target_files=["src/example.py"],
    )


def _make_workspace(tmp_path: Path, task: Task) -> WorkspaceManager:
    ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
    ws.create()
    return ws


def _pass_run() -> RunResult:
    return RunResult(passed=True, returncode=0, stdout="", summary="2 passed in 0.1s")


# ── 1) Reviewer system prompt: cross-task byte-identical ──────────────────────


class TestReviewerSystemPromptStability:
    """
    Reviewer system prompt (REVIEWER.render(lang, framework)) 가 task-specific
    인자에 대해 byte-identical 한지 확인한다.

    reviewer.md 에는 {language}/{test_framework}/{build_instructions} 플레이스홀더가
    **없다**. 따라서 render 는 사실상 no-op 이며, 어떤 (lang, framework) 조합에
    대해서도 결과 시스템 프롬프트는 byte-identical 해야 한다. 이 성질이 깨지면
    Anthropic 의 cache_control 블록이 cross-task 공유되지 못한다.
    """

    def test_render_is_byte_identical_across_language_framework(self):
        a = REVIEWER.render("python", "pytest").system_prompt
        b = REVIEWER.render("javascript", "jest").system_prompt
        c = REVIEWER.render("kotlin", "gradle").system_prompt
        _assert_byte_identical(a, b, "reviewer_system[python-vs-js]")
        _assert_byte_identical(a, c, "reviewer_system[python-vs-kotlin]")

    def test_render_digest_is_stable_across_repeated_calls(self):
        """같은 인자로 여러 번 호출해도 digest 가 동일 — deterministic rendering."""
        digests = {_digest(REVIEWER.render("python", "pytest").system_prompt)
                   for _ in range(5)}
        assert len(digests) == 1, f"render 결과가 매 호출마다 다름: {digests}"

    def test_render_preserves_core_sections(self):
        s = REVIEWER.render("python", "pytest").system_prompt
        assert "VERDICT" in s
        assert "APPROVED" in s and "CHANGES_REQUESTED" in s
        assert "Quality Gate 통과 항목" in s

    def test_render_contains_no_leftover_placeholder(self):
        s = REVIEWER.render("python", "pytest").system_prompt
        for forbidden in ("{language}", "{test_framework}", "{build_instructions}"):
            assert forbidden not in s, (
                f"reviewer.md 에 치환되지 않은 placeholder {forbidden} 가 남아 있습니다."
            )


# ── 2) Reviewer user message: 동일 task 반복 호출 결정성 ─────────────────────


class TestReviewerUserMessageDeterminism:
    """
    `_build_reviewer_prompt(task, workspace, run_result)` 가 동일 입력에 대해
    반복 호출 시 byte-identical 한 문자열을 반환하는지 확인한다. 이것이 깨지면
    같은 태스크 내 loop iteration 에서도 prefix 가 달라져 캐시 히트 불가.
    """

    def test_same_task_same_run_result_yields_byte_identical_prompt(self, tmp_path):
        task = _make_task("task-001", "제목 A")
        ws = _make_workspace(tmp_path, task)
        rr = _pass_run()
        p1 = _build_reviewer_prompt(task, ws, rr)
        p2 = _build_reviewer_prompt(task, ws, rr)
        _assert_byte_identical(p1, p2, "reviewer_user[repeat]")

    def test_deepcopy_input_yields_byte_identical_prompt(self, tmp_path):
        task = _make_task("task-001", "제목 A")
        ws = _make_workspace(tmp_path, task)
        rr = _pass_run()
        p1 = _build_reviewer_prompt(task, ws, rr)
        p2 = _build_reviewer_prompt(
            copy.deepcopy(task), ws, copy.deepcopy(rr)
        )
        _assert_byte_identical(p1, p2, "reviewer_user[deepcopy]")


# ── 3) Gemini `_to_gemini_contents` 결정성 ────────────────────────────────────


class TestGeminiContentsDeterminism:
    """
    Gemini 는 explicit cached_content API 대신 implicit caching 을 쓴다.
    _to_gemini_contents 가 동일 입력에 대해 반복 byte-identical 이어야
    prefix 가 안정적으로 유지된다.
    """

    def _sample(self) -> list[Message]:
        return [
            Message(role="system", content="SYS"),
            Message(role="user", content="first task"),
            Message(
                role="assistant",
                content=[
                    {"type": "text", "text": "plan"},
                    {
                        "type": "tool_use",
                        "id": "g_1",
                        "name": "read_file",
                        "input": {"path": "x.py", "offset": 0},
                    },
                ],
            ),
            Message(
                role="user",
                content=[
                    {
                        "type": "tool_result",
                        "tool_use_id": "g_1",
                        "content": "result",
                        "is_error": False,
                    }
                ],
            ),
        ]

    def test_repeated_calls_byte_identical(self):
        a = _to_gemini_contents(self._sample())
        b = _to_gemini_contents(self._sample())
        _assert_byte_identical(a, b, "gemini_contents[repeat]")

    def test_deepcopy_input_byte_identical(self):
        msgs = self._sample()
        a = _to_gemini_contents(msgs)
        b = _to_gemini_contents(copy.deepcopy(msgs))
        _assert_byte_identical(a, b, "gemini_contents[deepcopy]")

    def test_function_call_args_preserves_original_dict(self):
        """function_call.args 는 원본 dict 를 그대로 전달 (normalization 없음).
        Python 3.7+ 삽입 순서 보존 원리로 동일 입력이면 동일 바이트."""
        msgs = [
            Message(
                role="assistant",
                content=[
                    {
                        "type": "tool_use",
                        "id": "g_x",
                        "name": "write",
                        "input": {"path": "a.py", "content": "X"},
                    }
                ],
            )
        ]
        out = _to_gemini_contents(msgs)
        fc = out[0]["parts"][0]["function_call"]
        assert fc["args"] == {"path": "a.py", "content": "X"}
        out2 = _to_gemini_contents(copy.deepcopy(msgs))
        _assert_byte_identical(out, out2, "gemini_contents[function_call]")

    def test_system_message_is_skipped(self):
        msgs = [
            Message(role="system", content="SYS"),
            Message(role="user", content="u"),
        ]
        out = _to_gemini_contents(msgs)
        assert len(out) == 1
        assert out[0]["role"] == "user"


# ── 4) Reviewer payload 의 system 부분 cross-task byte-identical ──────────────


class TestReviewerPayloadSystemAcrossTasks:
    """
    Reviewer 호출 시 최종 payload 에서 system 부분이 서로 다른 task 간에도
    byte-identical 해야 cross-task prompt caching 이 가능하다.

    OpenAI/GLM 이 chat() 에서 실제로 구성하는 패턴
    `[{system: ...}, *_to_openai_messages(history)]` 을 재현해서 system
    entry 만 비교한다. 실제 네트워크 호출은 하지 않는다.
    """

    def _payload_openai(self, reviewer_system: str, user_msg: str) -> list[dict]:
        history = [Message(role="user", content=user_msg)]
        return [{"role": "system", "content": reviewer_system}] + \
            _to_openai_messages(history)

    def _payload_glm(self, reviewer_system: str, user_msg: str) -> list[dict]:
        history = [Message(role="user", content=user_msg)]
        return [{"role": "system", "content": reviewer_system}] + \
            _to_glm_messages(history)

    def test_openai_payload_system_byte_identical_cross_task(self, tmp_path):
        sys_prompt = REVIEWER.render("python", "pytest").system_prompt
        task1 = _make_task("task-001", "제목 A")
        ws1 = _make_workspace(tmp_path / "t1", task1)
        user1 = _build_reviewer_prompt(task1, ws1, _pass_run())

        task2 = _make_task("task-042", "제목 B — 전혀 다른 태스크")
        ws2 = _make_workspace(tmp_path / "t2", task2)
        rr2 = RunResult(passed=True, returncode=0, stdout="", summary="5 passed in 0.3s")
        user2 = _build_reviewer_prompt(task2, ws2, rr2)

        p1 = self._payload_openai(sys_prompt, user1)
        p2 = self._payload_openai(sys_prompt, user2)

        _assert_byte_identical(p1[0], p2[0], "openai_payload_system_entry")
        # user 는 태스크마다 달라야 정상 (cache miss 기대 지점)
        assert p1[1]["content"] != p2[1]["content"]

    def test_glm_payload_system_byte_identical_cross_task(self, tmp_path):
        sys_prompt = REVIEWER.render("python", "pytest").system_prompt
        task1 = _make_task("task-001", "제목 A")
        ws1 = _make_workspace(tmp_path / "t1", task1)
        user1 = _build_reviewer_prompt(task1, ws1, _pass_run())

        task2 = _make_task("task-042", "제목 B — 전혀 다른 태스크")
        ws2 = _make_workspace(tmp_path / "t2", task2)
        user2 = _build_reviewer_prompt(task2, ws2, _pass_run())

        p1 = self._payload_glm(sys_prompt, user1)
        p2 = self._payload_glm(sys_prompt, user2)

        _assert_byte_identical(p1[0], p2[0], "glm_payload_system_entry")
        assert p1[1]["content"] != p2[1]["content"]


# ── 5) Claude 시스템 프롬프트 cache_control 래핑 스냅샷 ──────────────────────


class TestClaudeCacheControlWrapping:
    """
    Claude 클라이언트의 `_build_system()` 이 시스템 프롬프트에 `cache_control`
    블록을 붙이는지, 동일 프롬프트에 대해 반복 호출 시 결정적인지 확인한다.
    실제 API 키는 필요 없으므로 ClaudeClient 를 생성하지 않고 unbound 메서드
    호출로 구조만 검증한다.
    """

    def test_build_system_wraps_with_ephemeral_cache_control(self):
        from llm.base import LLMConfig
        from llm.claude_client import ClaudeClient

        fake = ClaudeClient.__new__(ClaudeClient)
        fake.config = LLMConfig(
            model="claude-test",
            system_prompt=REVIEWER.render("python", "pytest").system_prompt,
        )
        a = fake._build_system()
        b = fake._build_system()
        assert isinstance(a, list) and len(a) == 1
        assert a[0]["cache_control"] == {"type": "ephemeral"}
        _assert_byte_identical(a, b, "claude_build_system[repeat]")

    def test_build_system_is_byte_identical_across_tasks(self):
        """Reviewer system prompt 는 cross-task 동일하므로 _build_system()
        결과도 cross-task byte-identical 해야 한다."""
        from llm.base import LLMConfig
        from llm.claude_client import ClaudeClient

        def _sys(lang, fw):
            fake = ClaudeClient.__new__(ClaudeClient)
            fake.config = LLMConfig(
                model="claude-test",
                system_prompt=REVIEWER.render(lang, fw).system_prompt,
            )
            return fake._build_system()

        _assert_byte_identical(
            _sys("python", "pytest"), _sys("javascript", "jest"),
            "claude_build_system[cross-task]",
        )

    def test_build_system_empty_prompt_returns_empty_string(self):
        from llm.base import LLMConfig
        from llm.claude_client import ClaudeClient

        fake = ClaudeClient.__new__(ClaudeClient)
        fake.config = LLMConfig(model="claude-test", system_prompt="")
        assert fake._build_system() == ""


# ── 6) Helper 자체의 동작 확인 ────────────────────────────────────────────────


class TestByteIdenticalHelper:
    """`_assert_byte_identical` 가 실제로 drift 를 잡고, diff 를 포함한
    메시지를 만드는지 검증. 이 헬퍼가 깨지면 다른 모든 테스트의 신뢰성이 깨진다.

    특히 중요: dict 삽입 순서 drift 는 prompt caching prefix 를 깨므로,
    helper 는 의미적 동등성이 아니라 **실제 배출 바이트**를 비교해야 한다.
    """

    def test_string_identical_passes(self):
        _assert_byte_identical("abc", "abc")

    def test_dict_same_insertion_order_passes(self):
        _assert_byte_identical({"a": 1, "b": 2}, {"a": 1, "b": 2})

    def test_dict_key_order_drift_is_detected(self):
        """키 순서가 다르면 byte drift 로 잡아야 한다 — 이게 통과되면 prompt
        caching prefix 회귀를 놓친다 (이전 helper 의 실패 모드)."""
        with pytest.raises(AssertionError) as excinfo:
            _assert_byte_identical({"a": 1, "b": 2}, {"b": 2, "a": 1}, "key_order")
        msg = str(excinfo.value)
        assert "key_order" in msg
        assert "sha256(a)" in msg and "sha256(b)" in msg

    def test_nested_dict_key_order_drift_is_detected(self):
        """중첩된 dict 의 키 순서 drift 도 잡아야 한다."""
        a = {"outer": {"x": 1, "y": 2}}
        b = {"outer": {"y": 2, "x": 1}}
        with pytest.raises(AssertionError):
            _assert_byte_identical(a, b)

    def test_list_order_drift_is_detected(self):
        with pytest.raises(AssertionError):
            _assert_byte_identical([1, 2, 3], [1, 3, 2])

    def test_drift_fails_with_digests_and_diff(self):
        with pytest.raises(AssertionError) as excinfo:
            _assert_byte_identical("abc", "abd", "label_x")
        msg = str(excinfo.value)
        assert "label_x" in msg
        assert "sha256(a)" in msg and "sha256(b)" in msg
        assert "@a" in msg and "@b" in msg
        assert "-abc" in msg or "+abd" in msg

    def test_digest_is_stable_and_64_hex(self):
        d1 = _digest({"k": [1, 2, 3]})
        d2 = _digest({"k": [1, 2, 3]})
        assert d1 == d2
        assert len(d1) == 64 and all(c in "0123456789abcdef" for c in d1)

    def test_digest_differs_for_dict_key_order(self):
        """sort_keys=False 를 썼을 때 기대되는 동작: 키 순서 차이는 다른 digest."""
        d1 = _digest({"a": 1, "b": 2})
        d2 = _digest({"b": 2, "a": 1})
        assert d1 != d2
