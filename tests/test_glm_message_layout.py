"""
tests/test_glm_message_layout.py

GLM context caching 용 _to_openai_messages() 결정성 검증.

GLM 은 OpenAI 와 거의 동일한 결정성 규칙(키 순서/canonical arguments)을 따른다.
현재 glm_client 는 assistant 메시지에서 tool_calls 가 있으면 content 키를
생략하는데, 이것이 Z.ai API 의 필수 제약인지는 공식 스키마로 확인되지 않았다.
이 파일은 "현재 동작"을 확인하는 선에서 테스트하며 특정 shape 을 인바리언트로
고정하지 않는다 (자세한 내용은 llm/glm_client.py 모듈 docstring 참조).
"""
from __future__ import annotations

import copy
import json

import pytest

from llm.base import Message
from llm.glm_client import _to_openai_messages


def _sample_history() -> list[Message]:
    return [
        Message(role="system", content="GLM 코딩 에이전트 system prompt"),
        Message(role="user", content="첫 번째 태스크 설명"),
        Message(
            role="assistant",
            content=[
                {"type": "text", "text": "이 텍스트는 GLM 에서 누락됨 (tool_calls 존재)"},
                {
                    "type": "tool_use",
                    "id": "g_1",
                    "name": "read_file",
                    "input": {"path": "x.py"},
                },
            ],
        ),
        Message(
            role="user",
            content=[
                {
                    "type": "tool_result",
                    "tool_use_id": "g_1",
                    "content": "result payload",
                    "is_error": False,
                }
            ],
        ),
        Message(role="assistant", content=[{"type": "text", "text": "완료"}]),
    ]


def test_repeated_calls_are_byte_identical():
    out1 = _to_openai_messages(_sample_history())
    out2 = _to_openai_messages(_sample_history())
    assert json.dumps(out1, sort_keys=False, ensure_ascii=False) == \
           json.dumps(out2, sort_keys=False, ensure_ascii=False)


def test_deepcopy_input_yields_identical_output():
    msgs = _sample_history()
    clone = copy.deepcopy(msgs)
    out_a = _to_openai_messages(msgs)
    out_b = _to_openai_messages(clone)
    assert json.dumps(out_a, sort_keys=False, ensure_ascii=False) == \
           json.dumps(out_b, sort_keys=False, ensure_ascii=False)


def test_string_content_preserved_verbatim():
    raw = "\t leading + trailing \n"
    out = _to_openai_messages([Message(role="user", content=raw)])
    assert out[0]["content"] == raw


def test_assistant_with_tool_calls_current_client_behavior():
    """
    현재 glm_client 는 tool_calls 가 있는 assistant 메시지에서 content 를 생략한다.
    이것이 Z.ai API 의 필수 제약인지는 공식 스키마로 재확인이 필요하다
    (글로벌 인바리언트로 locked-in 하지 않기 위해 "현재 동작" 임을 테스트명에 명시).

    핵심 불변식은 shape 자체가 아니라, 동일 입력에 대해 결정적으로 같은 shape
    이 나오는가이다 — prompt caching prefix 안정성은 그것으로 충분하다.
    """
    msgs = [
        Message(
            role="assistant",
            content=[
                {"type": "text", "text": "plan"},
                {
                    "type": "tool_use",
                    "id": "g_x",
                    "name": "noop",
                    "input": {"k": 1},
                },
            ],
        )
    ]
    out = _to_openai_messages(msgs)[0]

    # 현재 동작: role → tool_calls, content 없음. (glm_client.py 주석 참조)
    # 만약 미래에 공식 스키마 확인 후 content 포함으로 바꾸면 이 테스트도 함께 변경.
    assert out["role"] == "assistant"
    assert "tool_calls" in out
    # 결정성: shape 은 입력에 대해 deterministic 이어야 함 (두 번 호출해도 동일 keys)
    out_twice = _to_openai_messages(msgs)[0]
    assert list(out.keys()) == list(out_twice.keys())


def test_assistant_without_tool_calls_key_order():
    """tool_calls 없으면 role → content 순."""
    msgs = [Message(role="assistant", content=[{"type": "text", "text": "done"}])]
    out = _to_openai_messages(msgs)[0]
    assert list(out.keys()) == ["role", "content"]


def test_tool_result_key_order():
    """role → content → tool_call_id."""
    msgs = [
        Message(
            role="user",
            content=[
                {
                    "type": "tool_result",
                    "tool_use_id": "g_42",
                    "content": "payload",
                    "is_error": False,
                }
            ],
        )
    ]
    out = _to_openai_messages(msgs)[0]
    assert list(out.keys()) == ["role", "content", "tool_call_id"]


def test_tool_call_inner_key_order():
    msgs = [
        Message(
            role="assistant",
            content=[
                {
                    "type": "tool_use",
                    "id": "g_z",
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
    의미상 동일하지만 dict 삽입 순서가 다른 input 이 같은 arguments 문자열로
    직렬화되어야 한다 (sort_keys=True canonicalization).
    """
    def _build(input_dict):
        return [Message(role="assistant", content=[{
            "type": "tool_use", "id": "g_perm", "name": "write_file",
            "input": input_dict,
        }])]

    out_ab = _to_openai_messages(_build({"path": "x.py", "content": "a"}))
    out_ba = _to_openai_messages(_build({"content": "a", "path": "x.py"}))

    args_ab = out_ab[0]["tool_calls"][0]["function"]["arguments"]
    args_ba = out_ba[0]["tool_calls"][0]["function"]["arguments"]
    assert args_ab == args_ba

    s_ab = json.dumps(out_ab, sort_keys=False, ensure_ascii=False)
    s_ba = json.dumps(out_ba, sort_keys=False, ensure_ascii=False)
    assert s_ab == s_ba


def test_system_message_skipped():
    msgs = [
        Message(role="system", content="sys"),
        Message(role="user", content="u"),
    ]
    out = _to_openai_messages(msgs)
    assert len(out) == 1
    assert out[0]["role"] == "user"


@pytest.mark.parametrize("run_count", [2, 3, 5])
def test_multiple_serializations_bytewise_stable(run_count: int):
    msgs = _sample_history()
    serialized = [
        json.dumps(_to_openai_messages(msgs), sort_keys=False, ensure_ascii=False)
        for _ in range(run_count)
    ]
    assert len(set(serialized)) == 1
