"""
tests/test_openai_message_layout.py

OpenAI prompt caching 용 _to_openai_messages() 결정성 검증.

핵심 불변식:
  1. 동일 입력 → json.dumps(sort_keys=False) 결과가 byte-identical (반복 호출, 복제).
  2. dict 키 삽입 순서 고정: role → content → tool_calls → tool_call_id.
  3. content 는 원본 그대로 (strip/normalize 금지).
"""
from __future__ import annotations

import copy
import json

import pytest

from llm.base import Message
from llm.openai_client import _to_openai_messages


# ── 고정 입력 샘플 ────────────────────────────────────────────────────────────


def _sample_history() -> list[Message]:
    """system + user + (assistant with tool_use) + (user with tool_result) + assistant text."""
    return [
        Message(role="system", content="  당신은 코딩 에이전트입니다.\n  "),
        Message(role="user", content="  hello world with leading space  "),
        Message(
            role="assistant",
            content=[
                {"type": "text", "text": "읽겠습니다."},
                {
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "read_file",
                    "input": {"path": "/tmp/a.py", "offset": 0},
                },
            ],
        ),
        Message(
            role="user",
            content=[
                {
                    "type": "tool_result",
                    "tool_use_id": "call_1",
                    "content": "file contents here",
                    "is_error": False,
                }
            ],
        ),
        Message(role="assistant", content=[{"type": "text", "text": "끝."}]),
    ]


# ── byte-identical 결정성 ────────────────────────────────────────────────────


def test_repeated_calls_are_byte_identical():
    """동일 Message 리스트로 두 번 변환해도 JSON 결과가 byte-identical."""
    msgs1 = _sample_history()
    msgs2 = _sample_history()

    out1 = _to_openai_messages(msgs1)
    out2 = _to_openai_messages(msgs2)

    s1 = json.dumps(out1, sort_keys=False, ensure_ascii=False)
    s2 = json.dumps(out2, sort_keys=False, ensure_ascii=False)

    assert s1 == s2


def test_deepcopy_input_yields_identical_output():
    """deepcopy된 입력에 대해서도 동일한 직렬화 결과."""
    msgs = _sample_history()
    clone = copy.deepcopy(msgs)

    out_a = _to_openai_messages(msgs)
    out_b = _to_openai_messages(clone)

    assert json.dumps(out_a, sort_keys=False, ensure_ascii=False) == \
           json.dumps(out_b, sort_keys=False, ensure_ascii=False)


# ── content 전처리 금지 ──────────────────────────────────────────────────────


def test_string_content_preserved_verbatim():
    """str content 는 strip/normalize 되지 않고 원본 그대로 전달."""
    raw = "  leading + trailing whitespace \n\n"
    out = _to_openai_messages([Message(role="user", content=raw)])
    assert out == [{"role": "user", "content": raw}]
    # 공백이 정확히 보존되는지 재확인
    assert out[0]["content"] == raw


# ── 키 순서: role → content → tool_calls ────────────────────────────────────


def test_assistant_with_tool_calls_key_order():
    """assistant with tool_calls: role → content → tool_calls 순."""
    msgs = [
        Message(
            role="assistant",
            content=[
                {"type": "text", "text": "plan"},
                {
                    "type": "tool_use",
                    "id": "call_x",
                    "name": "noop",
                    "input": {"k": 1},
                },
            ],
        )
    ]
    out = _to_openai_messages(msgs)[0]
    assert list(out.keys()) == ["role", "content", "tool_calls"]


def test_assistant_without_tool_calls_key_order():
    """tool_calls 없으면 role → content 만."""
    msgs = [Message(role="assistant", content=[{"type": "text", "text": "done"}])]
    out = _to_openai_messages(msgs)[0]
    assert list(out.keys()) == ["role", "content"]


# ── 키 순서: role → content → tool_call_id (tool 메시지) ─────────────────────


def test_tool_result_key_order():
    """tool 메시지: role → content → tool_call_id 순."""
    msgs = [
        Message(
            role="user",
            content=[
                {
                    "type": "tool_result",
                    "tool_use_id": "call_42",
                    "content": "result text",
                    "is_error": False,
                }
            ],
        )
    ]
    out = _to_openai_messages(msgs)[0]
    assert list(out.keys()) == ["role", "content", "tool_call_id"]
    assert out["role"] == "tool"
    assert out["content"] == "result text"
    assert out["tool_call_id"] == "call_42"


# ── tool_call 내부 구조: id → type → function ────────────────────────────────


def test_tool_call_inner_key_order():
    """tool_calls 배열 내부 dict: id → type → function 순서 고정."""
    msgs = [
        Message(
            role="assistant",
            content=[
                {
                    "type": "tool_use",
                    "id": "call_z",
                    "name": "search",
                    "input": {"q": "foo"},
                }
            ],
        )
    ]
    tc = _to_openai_messages(msgs)[0]["tool_calls"][0]
    assert list(tc.keys()) == ["id", "type", "function"]
    assert list(tc["function"].keys()) == ["name", "arguments"]


def test_tool_call_arguments_canonical_under_key_permutation():
    """
    의미상 동일하지만 dict 삽입 순서가 다른 tool_call input 이 동일한
    arguments 문자열로 직렬화되어야 한다 (sort_keys=True canonicalization).
    이게 깨지면 prompt caching prefix 가 무의미하게 달라진다.
    """
    def _build(input_dict):
        return [
            Message(
                role="assistant",
                content=[
                    {
                        "type": "tool_use",
                        "id": "call_perm",
                        "name": "write_file",
                        "input": input_dict,
                    }
                ],
            )
        ]

    out_ab = _to_openai_messages(_build({"path": "x.py", "content": "a"}))
    out_ba = _to_openai_messages(_build({"content": "a", "path": "x.py"}))

    # tool_call arguments 가 삽입 순서에 상관없이 byte-identical
    args_ab = out_ab[0]["tool_calls"][0]["function"]["arguments"]
    args_ba = out_ba[0]["tool_calls"][0]["function"]["arguments"]
    assert args_ab == args_ba

    # 전체 메시지 JSON 도 byte-identical
    s_ab = json.dumps(out_ab, sort_keys=False, ensure_ascii=False)
    s_ba = json.dumps(out_ba, sort_keys=False, ensure_ascii=False)
    assert s_ab == s_ba


def test_tool_call_arguments_nested_dict_canonical():
    """중첩된 dict 도 재귀적으로 정렬되어 byte-identical."""
    def _build(input_dict):
        return [Message(role="assistant", content=[{
            "type": "tool_use", "id": "c", "name": "n", "input": input_dict,
        }])]

    out1 = _to_openai_messages(_build({"a": {"y": 2, "x": 1}, "b": 0}))
    out2 = _to_openai_messages(_build({"b": 0, "a": {"x": 1, "y": 2}}))
    args1 = out1[0]["tool_calls"][0]["function"]["arguments"]
    args2 = out2[0]["tool_calls"][0]["function"]["arguments"]
    assert args1 == args2


# ── system 메시지는 건너뜀 ───────────────────────────────────────────────────


def test_system_message_skipped():
    """_to_openai_messages() 는 system 을 건너뛴다 (chat()에서 별도 추가)."""
    msgs = [
        Message(role="system", content="sys"),
        Message(role="user", content="u"),
    ]
    out = _to_openai_messages(msgs)
    assert len(out) == 1
    assert out[0]["role"] == "user"


# ── 동일 shape vs 다른 shape ─────────────────────────────────────────────────


@pytest.mark.parametrize("run_count", [2, 3, 5])
def test_multiple_serializations_bytewise_stable(run_count: int):
    """N번 반복해도 직렬화 결과가 모두 동일."""
    msgs = _sample_history()
    serialized = [
        json.dumps(_to_openai_messages(msgs), sort_keys=False, ensure_ascii=False)
        for _ in range(run_count)
    ]
    assert len(set(serialized)) == 1
