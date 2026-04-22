"""
tests/test_task_converter.py

`cli/task_converter.py` 단위 테스트.

- 실제 LLM 호출 없이 FakeClient로 scripted 응답을 주입.
- PROJECT_STRUCTURE.md는 tmp_path에 pre-seed하여 structure.updater를 우회.
- 사용자 입력은 input_fn DI로 mock.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest

# backend.config가 import 체인에 들어가지 않지만, 혹시 모를 케이스를 위해 더미 키 세팅.
if not os.environ.get("ANTHROPIC_API_KEY"):
    os.environ["ANTHROPIC_API_KEY"] = "test-key"

from cli.task_converter import (  # noqa: E402
    ConversionError,
    ConversionResult,
    TaskConverter,
    _extract_delimited_json,
    _post_process,
    _strip_code_fences,
    parse_file_references,
)
from llm import LLMConfig, Message  # noqa: E402


# ── FakeClient ────────────────────────────────────────────────────────────────


@dataclass
class _FakeLLMResponse:
    content: list = field(default_factory=list)
    stop_reason: str = "end_turn"
    input_tokens: int = 0
    output_tokens: int = 0
    cached_read_tokens: int = 0
    cached_write_tokens: int = 0
    model: str = "fake"

    @classmethod
    def of(cls, text: str) -> "_FakeLLMResponse":
        return cls(content=[{"type": "text", "text": text}])


class FakeClient:
    """Scripted LLM responses + KeyboardInterrupt 주입 지원."""

    def __init__(self, scripted: list):
        # 각 원소: str 또는 KeyboardInterrupt 인스턴스 (raise)
        self._scripted = list(scripted)
        self.calls: list[list[Message]] = []

    def chat(self, messages, **kw):
        # 메시지 복사 (리스트 mutation 방지)
        self.calls.append([Message(m.role, m.content) for m in messages])
        if not self._scripted:
            raise AssertionError(
                f"FakeClient: scripted 응답이 소진됨 (호출 {len(self.calls)}회)"
            )
        item = self._scripted.pop(0)
        if isinstance(item, BaseException):
            raise item
        return _FakeLLMResponse.of(item)

    def is_available(self) -> bool:
        return True


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

_VALID_JSON = """{
  "title": "샘플 태스크",
  "task_type": "backend",
  "language": "python",
  "description": "### 목적과 배경\\n샘플 목적\\n### 기술 요구사항\\n샘플 요구\\n### 인접 컨텍스트\\n없음\\n### 비고려 항목\\n명시적 비범위 없음.",
  "acceptance_criteria": ["c1", "c2"],
  "target_files": ["sample.py"],
  "test_framework": "pytest"
}"""


def _make_task_response(inner_json: str = _VALID_JSON, preamble: str = "알겠어요.") -> str:
    return (
        f"{preamble}\n\n"
        f"===TASK_JSON_START===\n"
        f"{inner_json}\n"
        f"===TASK_JSON_END===\n"
    )


def _make_converter(
    tmp_path: Path,
    scripted_llm: list,
    input_replies: list[str] | None = None,
    *,
    structure_content: str = "# PROJECT_STRUCTURE\n\n(test fixture)",
    max_turns: int = 10,
) -> tuple[TaskConverter, FakeClient, list[str]]:
    """Converter 생성. (converter, client, outputs) 반환."""
    (tmp_path / "PROJECT_STRUCTURE.md").write_text(structure_content, encoding="utf-8")

    replies_iter = iter(input_replies or [])
    outputs: list[str] = []

    def _input(prompt: str) -> str:
        try:
            v = next(replies_iter)
        except StopIteration:
            raise AssertionError(
                "input_fn: 예상보다 많은 입력 요청 (scripted replies 소진)"
            )
        if isinstance(v, BaseException):
            raise v
        return v

    client = FakeClient(scripted_llm)
    config = LLMConfig(model="fake")
    conv = TaskConverter(
        repo_path=str(tmp_path),
        llm_config=config,
        provider="claude",
        client=client,
        max_turns=max_turns,
        input_fn=_input,
        output_fn=lambda text: outputs.append(text),
    )
    return conv, client, outputs


def _run(coro):
    return asyncio.run(coro)


# ═════════════════════════════════════════════════════════════════════════════
# 미니 회의 흐름 (8)
# ═════════════════════════════════════════════════════════════════════════════


def test_single_turn_clear_request(tmp_path):
    """1턴 JSON → turns_used==1, no input prompt."""
    conv, client, outputs = _make_converter(
        tmp_path,
        scripted_llm=[_make_task_response()],
        input_replies=[],
    )

    result = _run(conv.convert("샘플 태스크 만들어줘"))

    assert result.aborted is False
    assert result.task is not None
    assert result.task.id.startswith("instant-")
    assert result.turns_used == 1
    assert len(client.calls) == 1
    # preamble이 있으면 output으로 출력됨
    assert any("알겠어요" in o for o in outputs)


def test_multi_turn_ambiguous_request(tmp_path):
    """질문 → 사용자 답변 → JSON. turns_used==2."""
    conv, client, outputs = _make_converter(
        tmp_path,
        scripted_llm=[
            "중복 발견 시 예외 raise할까요, bool 반환할까요?",
            _make_task_response(),
        ],
        input_replies=["예외 raise"],
    )

    result = _run(conv.convert("이메일 중복 검사 추가"))

    assert result.aborted is False
    assert result.task is not None
    assert result.turns_used == 2
    assert len(client.calls) == 2
    # 두 번째 호출 시점에 messages에 사용자 답변이 포함됨
    second_call = client.calls[1]
    assert any(m.role == "user" and "예외 raise" in m.content for m in second_call)


def test_user_abort_esc(tmp_path):
    """빈 입력(Esc/Ctrl-D) → 즉시 aborted=True, task is None."""
    conv, client, outputs = _make_converter(
        tmp_path,
        scripted_llm=["어떻게 구현할까요?"],
        input_replies=[""],
    )

    result = _run(conv.convert("기능 추가"))

    assert result.aborted is True
    assert result.task is None
    assert result.turns_used == 1


def test_user_abort_whitespace_only(tmp_path):
    """공백만 있는 입력도 빈 입력과 동일하게 abort."""
    conv, client, outputs = _make_converter(
        tmp_path,
        scripted_llm=["질문?"],
        input_replies=["   \t\n"],
    )

    result = _run(conv.convert("요청"))

    assert result.aborted is True
    assert result.task is None


def test_max_turns_exceeded(tmp_path):
    """LLM이 max_turns 동안 질문만 → ConversionError."""
    # max_turns=3으로 낮춰서 빠르게 테스트
    scripted = ["질문1", "질문2", "질문3"]
    replies = ["답1", "답2", "답3"]
    conv, client, outputs = _make_converter(
        tmp_path,
        scripted_llm=scripted,
        input_replies=replies,
        max_turns=3,
    )

    with pytest.raises(ConversionError) as exc_info:
        _run(conv.convert("요청"))
    assert "3턴" in str(exc_info.value)


def test_default_max_turns_is_10():
    """기본 max_turns는 10 (무한 대화 방지 안전장치)."""
    from cli.task_converter import _DEFAULT_MAX_TURNS
    assert _DEFAULT_MAX_TURNS == 10


def test_project_structure_injected(tmp_path):
    """PROJECT_STRUCTURE.md 내용이 첫 user 메시지에 주입됨."""
    sentinel = "SENTINEL_STRUCTURE_TOKEN_XYZ"
    conv, client, outputs = _make_converter(
        tmp_path,
        scripted_llm=[_make_task_response()],
        input_replies=[],
        structure_content=f"# PROJECT_STRUCTURE\n\n{sentinel}\n",
    )

    _run(conv.convert("요청"))

    first_msg = client.calls[0][0]
    assert sentinel in first_msg.content


def test_conversation_history_includes_system(tmp_path):
    """conversation_history[0]은 system 역할이고 미니 회의 시스템 프롬프트 내용을 담는다."""
    conv, client, outputs = _make_converter(
        tmp_path,
        scripted_llm=[_make_task_response()],
        input_replies=[],
    )

    result = _run(conv.convert("요청"))

    assert len(result.conversation_history) >= 2
    assert result.conversation_history[0].role == "system"
    assert result.conversation_history[1].role == "user"
    # 시스템 프롬프트에는 지적 파트너 역할이 언급되어 있어야 함
    assert "지적 파트너" in result.conversation_history[0].content \
        or "역할" in result.conversation_history[0].content


# ═════════════════════════════════════════════════════════════════════════════
# Task 파싱 (9)
# ═════════════════════════════════════════════════════════════════════════════


def test_task_json_extraction():
    """구분자 주변 prose 제거, JSON 파싱 성공."""
    text = (
        "합의된 내용입니다.\n\n"
        "===TASK_JSON_START===\n"
        "{\"title\": \"t\"}\n"
        "===TASK_JSON_END===\n"
        "후기 코멘트"
    )
    inner, preamble = _extract_delimited_json(text)
    assert inner == '{"title": "t"}'
    assert preamble == "합의된 내용입니다."


def test_task_json_with_code_fence():
    """구분자 안에 ```json 코드펜스가 있어도 파싱 성공."""
    raw = "```json\n" + _VALID_JSON + "\n```"
    task, cleaned, warnings = _post_process(raw)
    assert task is not None
    assert not cleaned.startswith("```")
    assert not cleaned.endswith("```")


def test_json_parse_failure_retry(tmp_path):
    """1차 깨진 JSON → 재시도 → 성공. 사용자 입력 호출 없음 (LLM 내부 재시도)."""
    broken_with_delim = (
        "===TASK_JSON_START===\n"
        "{this is not json}\n"
        "===TASK_JSON_END===\n"
    )
    conv, client, outputs = _make_converter(
        tmp_path,
        scripted_llm=[broken_with_delim, _make_task_response()],
        input_replies=[],  # 재시도는 내부적이라 사용자 입력 필요 없음
    )

    result = _run(conv.convert("요청"))

    assert result.aborted is False
    assert result.task is not None
    assert len(client.calls) == 2  # 원본 + 재시도


def test_json_parse_failure_gives_up(tmp_path):
    """깨진 JSON 2연속 → ConversionError."""
    broken1 = "===TASK_JSON_START===\n{bad1}\n===TASK_JSON_END===\n"
    broken2 = "===TASK_JSON_START===\n{bad2}\n===TASK_JSON_END===\n"
    conv, client, outputs = _make_converter(
        tmp_path,
        scripted_llm=[broken1, broken2],
        input_replies=[],
    )

    with pytest.raises(ConversionError):
        _run(conv.convert("요청"))


def test_sanitize_target_files():
    """src/models/user.py → models/user.py."""
    raw = (
        '{"title": "t", "language": "python", "target_files": '
        '["src/models/user.py"], "description": "### 목적과 배경\\nx\\n### 기술 요구사항\\n'
        'x\\n### 인접 컨텍스트\\nx\\n### 비고려 항목\\nx", '
        '"acceptance_criteria": ["c1"]}'
    )
    task, _, _ = _post_process(raw)
    assert task.target_files == ["models/user.py"]


def test_id_is_auto_generated():
    """LLM의 id는 무시되고 instant-{timestamp}로 재설정."""
    raw = '{"title": "t", "id": "llm-chosen-id", "language": "python", "description": "x", "acceptance_criteria": ["c"], "target_files": ["a.py"]}'
    task, _, _ = _post_process(raw)
    assert task.id != "llm-chosen-id"
    assert task.id.startswith("instant-")
    # 타임스탬프가 숫자여야 함
    suffix = task.id[len("instant-"):]
    assert suffix.isdigit()


def test_missing_fields_get_defaults():
    """test_framework, task_type 누락 → 언어 기반 기본값."""
    raw = '{"title": "t", "language": "python", "description": "x", "acceptance_criteria": ["c"], "target_files": ["a.py"]}'
    task, _, _ = _post_process(raw)
    assert task.test_framework == "pytest"
    assert task.task_type == "backend"


def test_missing_fields_kotlin_defaults():
    """kotlin → gradle."""
    raw = '{"title": "t", "language": "kotlin", "description": "x", "acceptance_criteria": ["c"], "target_files": ["Foo.kt"]}'
    task, _, _ = _post_process(raw)
    assert task.test_framework == "gradle"


def test_complexity_auto_computed_simple():
    """complexity 누락 + 단일 파일 + 짧은 description → simple."""
    raw = '{"title": "t", "language": "python", "description": "x" * 50, "acceptance_criteria": ["c"], "target_files": ["a.py"]}'
    # description을 짧게
    data = {
        "title": "t", "language": "python", "description": "짧다",
        "acceptance_criteria": ["c1"], "target_files": ["a.py"],
    }
    task, _, _ = _post_process(json.dumps(data))
    assert task.complexity == "simple"


def test_complexity_auto_computed_non_simple_multi_file():
    """멀티 파일 → non-simple."""
    data = {
        "title": "t", "language": "python", "description": "짧다",
        "acceptance_criteria": ["c1"], "target_files": ["a.py", "b.py"],
    }
    task, _, _ = _post_process(json.dumps(data))
    assert task.complexity == "non-simple"


def test_complexity_legacy_standard_normalized():
    """'standard' → 'non-simple'."""
    data = {
        "title": "t", "language": "python", "description": "x",
        "acceptance_criteria": ["c"], "target_files": ["a.py"],
        "complexity": "standard",
    }
    task, _, _ = _post_process(json.dumps(data))
    assert task.complexity == "non-simple"


def test_complexity_legacy_complex_normalized():
    """'complex' → 'non-simple'."""
    data = {
        "title": "t", "language": "python", "description": "x",
        "acceptance_criteria": ["c"], "target_files": ["a.py"],
        "complexity": "complex",
    }
    task, _, _ = _post_process(json.dumps(data))
    assert task.complexity == "non-simple"


# ═════════════════════════════════════════════════════════════════════════════
# @파일 참조 (4)
# ═════════════════════════════════════════════════════════════════════════════


def test_file_reference_extracted(tmp_path):
    """@src/utils.py → file_refs에 포함."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "utils.py").write_text("def foo(): pass\n", encoding="utf-8")

    cleaned, refs, warnings = parse_file_references(
        "@src/utils.py 리팩토링 부탁", tmp_path,
    )

    assert "src/utils.py" in refs
    assert "def foo()" in refs["src/utils.py"]
    assert warnings == []


def test_multiple_file_references(tmp_path):
    """여러 @파일 동시 추출."""
    (tmp_path / "a.py").write_text("a\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("b\n", encoding="utf-8")

    cleaned, refs, warnings = parse_file_references(
        "@a.py 와 @b.py 통합", tmp_path,
    )

    assert set(refs.keys()) == {"a.py", "b.py"}
    assert refs["a.py"].strip() == "a"
    assert refs["b.py"].strip() == "b"


def test_file_not_found_warns_not_errors(tmp_path):
    """존재하지 않는 @경로 → warning, 예외 없음."""
    cleaned, refs, warnings = parse_file_references(
        "@nonexistent.py 수정", tmp_path,
    )

    assert refs == {}
    assert any("찾을 수 없" in w for w in warnings)
    # 토큰은 원문 유지
    assert "@nonexistent.py" in cleaned


def test_binary_file_skipped(tmp_path):
    """@image.png → skip + warning."""
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0d")

    cleaned, refs, warnings = parse_file_references(
        "@image.png 봐줘", tmp_path,
    )

    assert "image.png" not in refs
    assert any("바이너리" in w for w in warnings)


# ═════════════════════════════════════════════════════════════════════════════
# 추가 유틸 테스트 (보너스)
# ═════════════════════════════════════════════════════════════════════════════


def test_strip_code_fences_json():
    """```json ... ``` 제거."""
    assert _strip_code_fences('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_strip_code_fences_plain():
    """```...``` 제거."""
    assert _strip_code_fences('```\n{"a": 1}\n```') == '{"a": 1}'


def test_extract_delimited_json_missing():
    """구분자 없으면 None."""
    inner, preamble = _extract_delimited_json("그냥 질문입니다.")
    assert inner is None
    assert preamble == ""
