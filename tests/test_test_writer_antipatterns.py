"""
tests/test_test_writer_antipatterns.py

TestWriter 가 task-008 에서 관찰된 방어적 패턴(동적 import + skipif,
try/except TypeError, hasattr 우회) 을 다시 생성하지 못하도록 만든 가드의
end-to-end 회귀 테스트.

세 가지 계층을 모두 검증한다:
  1. 프롬프트: 금지 패턴과 `ask_user` / `pytest.skip` / `dependency_artifacts`
     대안이 명시돼 있는가.
  2. 정적 품질 게이트(`_detect_task008_antipatterns` + `_validate_python_test`):
     LLM 이 금지 패턴으로 write_file 을 했을 때 pipeline 수준에서 거부되는가.
  3. ScopedReactLoop 도구 권한: TestWriter 가 `ask_user` 를 호출할 때
     `[역할 제약]` 으로 차단되지 않고 실제 툴로 dispatch 되는가.

(3) 의 "end-to-end" 성격을 위해 mock LLM 이 실제 tool_use 블록을 반환해
`ScopedReactLoop.run()` 경로를 통과시킨다. 프롬프트 문자열 매칭만으로는
잡히지 않는 회귀(예: roles.py 에서 ask_user 가 다시 삭제) 를 포착한다.
"""

from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from agents.roles import TEST_WRITER
from agents.scoped_loop import ScopedReactLoop
from core.loop import ToolCall
from llm.base import LLMResponse, Message
from orchestrator.pipeline import (
    _detect_task008_antipatterns,
    _validate_python_test,
    _validate_tests_static,
)
from orchestrator.workspace import WorkspaceManager
from orchestrator.task import Task
from tools.schemas import ToolResult


# ────────────────────────────────────────────────────────────────────────────
# 1. 프롬프트 지시가 task-008 금지 패턴을 명시적으로 다루는가
# ────────────────────────────────────────────────────────────────────────────


class TestPromptMentionsTask008Patterns:
    def test_all_banned_pattern_keywords_mentioned(self):
        prompt = TEST_WRITER.system_prompt
        # 4가지 금지 패턴 키워드
        for snippet in ("skipif", "생성자 파라미터", "플레이스홀더", "hasattr"):
            assert snippet in prompt, f"금지 패턴 키워드 누락: {snippet!r}"

    def test_prompt_offers_three_alternatives(self):
        prompt = TEST_WRITER.system_prompt
        assert "ask_user" in prompt
        assert "pytest.skip" in prompt
        assert "dependency_artifacts.md" in prompt

    def test_ask_user_has_usage_conditions(self):
        prompt = TEST_WRITER.system_prompt
        assert "ask_user 사용 조건" in prompt


# ────────────────────────────────────────────────────────────────────────────
# 2. 정적 품질 게이트가 task-008 재현 코드를 감지하는가
# ────────────────────────────────────────────────────────────────────────────


TASK_008_BAD_SAMPLES: dict[str, str] = {
    "dynamic_import_skipif": textwrap.dedent("""
        import pytest
        try:
            from src.mymodule import MyClass
        except ImportError:
            MyClass = None

        @pytest.mark.skipif(MyClass is None, reason="class not found")
        def test_something():
            assert MyClass is not None
    """).strip(),
    "try_except_constructor_guess": textwrap.dedent("""
        from src.mymodule import MyClass

        def test_ctor():
            try:
                obj = MyClass(in_dim=10, out_dim=20)
            except TypeError:
                obj = MyClass(10, 20)
            assert obj is not None
    """).strip(),
    "hasattr_bypass": textwrap.dedent("""
        from src.mymodule import MyClass

        def test_method():
            obj = MyClass()
            if hasattr(obj, "some_method"):
                result = obj.some_method()
            else:
                result = None
            assert result is None
    """).strip(),
}


class TestTask008StaticGuard:
    """`_detect_task008_antipatterns` 가 모든 task-008 재현 샘플을 잡아야 한다."""

    @pytest.mark.parametrize("name,src", list(TASK_008_BAD_SAMPLES.items()))
    def test_sample_is_flagged(self, name: str, src: str):
        import ast
        tree = ast.parse(src)
        issues = _detect_task008_antipatterns(tree, f"tests/test_{name}.py")
        assert issues, f"{name!r} 샘플에 대해 감지된 이슈 없음"
        # 이슈 메시지가 금지 패턴 네이밍을 재사용해 reviewer/agent 가 원인을 알 수 있어야 한다
        joined = "\n".join(issues)
        if name == "dynamic_import_skipif":
            assert "동적 import" in joined or "skipif" in joined
        if name == "try_except_constructor_guess":
            assert "except TypeError" in joined
        if name == "hasattr_bypass":
            assert "hasattr" in joined

    def test_clean_test_not_flagged(self):
        good = textwrap.dedent("""
            import pytest
            from src.mymodule import MyClass

            def test_add():
                calc = MyClass()
                assert calc.add(2, 3) == 5

            def test_unknown_format():
                pytest.skip(reason="직렬화 포맷 미정 — dependency_artifacts 추가 예정")

            def test_raises_type_error_is_allowed():
                with pytest.raises(TypeError):
                    MyClass(wrong=True)
        """).strip()
        assert _validate_python_test(good, "tests/test_good.py") == []

    def test_reraise_in_except_type_error_is_allowed(self):
        """except TypeError: raise 만 있는 경우는 스펙 검증으로 간주 (false positive 방지)."""
        src = textwrap.dedent("""
            from src.mymodule import MyClass

            def test_wraps():
                try:
                    MyClass(bad=1)
                except TypeError:
                    raise
        """).strip()
        issues = _validate_python_test(src, "tests/test_wrap.py")
        # hasattr/skipif 는 없고, 우리 task-008 가드는 re-raise 만 있는 handler 는 무시해야 한다
        joined = "\n".join(issues)
        assert "except TypeError" not in joined

    # ── 적법한(정상) 패턴 허용 계약 — 리뷰어 지적 반영 ──────────────────────
    #
    #   새 품질 게이트가 "금지 패턴" 예시보다 넓어져서 정당한 테스트까지
    #   거절하면 실제 태스크에서 필요한 방어 코드를 못 쓰게 된다.
    #   아래 테스트는 허용돼야 하는 패턴들을 계약으로 고정한다.

    def test_assert_hasattr_is_allowed_for_contract_tests(self):
        """`assert hasattr(obj, "version")` 같은 속성 계약 검증은 통과."""
        src = textwrap.dedent("""
            from src.mymodule import User

            def test_user_has_version_attr():
                u = User()
                assert hasattr(u, "version")
        """).strip()
        issues = _validate_python_test(src, "tests/test_contract.py")
        assert issues == [], (
            f"속성 존재 계약 검증(assert hasattr) 이 false-positive 로 걸림: {issues}"
        )

    def test_except_type_error_message_assertion_is_allowed(self):
        """`except TypeError as e: assert '...' in str(e)` 같은 예외 메시지 검증 허용."""
        src = textwrap.dedent("""
            import pytest
            from src.mymodule import MyClass

            def test_invalid_kwarg_raises_descriptive_error():
                try:
                    MyClass(bad=True)
                except TypeError as e:
                    assert "unexpected keyword" in str(e)
                else:
                    pytest.fail("did not raise")
        """).strip()
        issues = _validate_python_test(src, "tests/test_msg.py")
        assert issues == [], (
            f"예외 메시지 검증 패턴이 false-positive 로 걸림: {issues}"
        )

    def test_ternary_hasattr_gate_is_flagged(self):
        """`x = obj.m() if hasattr(obj, 'm') else None` — 삼항 게이트도 우회 패턴."""
        src = textwrap.dedent("""
            from src.mymodule import Foo

            def test_ternary_gate():
                obj = Foo()
                result = obj.bar() if hasattr(obj, "bar") else None
                assert result is None
        """).strip()
        issues = _validate_python_test(src, "tests/test_tern.py")
        assert any("hasattr" in i for i in issues), (
            f"삼항 hasattr 게이트가 감지되지 않음: {issues}"
        )

    def test_except_type_error_without_recall_is_allowed(self):
        """try 와 except 에서 **다른 callable** 을 부르면 fallback 아님 → 허용."""
        src = textwrap.dedent("""
            from src.mymodule import MyClass
            from src.logging import log_error

            def test_error_is_logged():
                try:
                    MyClass(unknown=1)
                except TypeError as e:
                    log_error(e)
                    assert True  # 검증은 별도 reporting 채널에서 — 로깅 경로만 확인
        """).strip()
        issues = _validate_python_test(src, "tests/test_err_log.py")
        # 우리 task-008 guard 는 이 경우를 통과시켜야 한다 (try: MyClass(...),
        # except: log_error(...) — 서로 다른 callable 이므로 fallback 아님).
        # (assert True 플레이스홀더 가드는 별도 이슈이므로 여기서는 무시)
        joined = "\n".join(issues)
        assert "fallback 패턴" not in joined

    def test_validate_tests_static_integrates_with_workspace(self, tmp_path: Path):
        """파이프라인에서 실제로 호출되는 `_validate_tests_static` 이 금지 패턴을 놓치지 않는다."""
        task = Task(
            id="task-mock",
            title="t",
            description="",
            acceptance_criteria=[],
            target_files=["src/mymodule.py"],
        )
        ws = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "ws")
        ws.create()
        bad_path = ws.tests_dir / "test_bad.py"
        bad_path.write_text(TASK_008_BAD_SAMPLES["dynamic_import_skipif"], encoding="utf-8")

        rel = str(bad_path.relative_to(ws.path))
        issues = _validate_tests_static([rel], ws)
        assert issues, "워크스페이스 통합 경로에서도 금지 패턴이 감지돼야 한다"


# ────────────────────────────────────────────────────────────────────────────
# 3. ScopedReactLoop 이 TestWriter 의 ask_user / write_file 을 end-to-end 로
#    처리하는가 (mock LLM 주입)
# ────────────────────────────────────────────────────────────────────────────


def _make_mock_llm(responses: list[LLMResponse]):
    """LLMResponse 시퀀스를 차례로 반환하는 mock LLM.

    ScopedReactLoop.run 경로에서 필요한 최소 인터페이스만 구현한다:
      - config.system_prompt  (run 에서 교체/복원)
      - build_messages(user_input, history)
      - chat(messages, tools)
    """
    it = iter(responses)
    llm = MagicMock()
    llm.config = MagicMock()
    llm.config.system_prompt = "original"
    type(llm).__name__ = "ClaudeClient"
    llm.build_messages = lambda user_input, history=None: (
        ([Message(role="system", content=llm.config.system_prompt)] if llm.config.system_prompt else [])
        + (list(history) if history else [])
        + [Message(role="user", content=user_input)]
    )

    def _chat(messages, tools=None, **kw):
        return next(it)
    llm.chat = _chat
    return llm


def _tool_use(block_id: str, name: str, input_: dict) -> dict:
    return {"type": "tool_use", "id": block_id, "name": name, "input": input_}


def _text(msg: str) -> dict:
    return {"type": "text", "text": msg}


GOOD_TEST_BODY = textwrap.dedent("""
    import pytest
    from src.mymodule import MyClass

    def test_add_known_signature():
        calc = MyClass()
        assert calc.add(2, 3) == 5

    def test_unknown_serialization_format():
        pytest.skip(reason="직렬화 포맷은 ask_user 회신 이후 작성 예정")
""").strip()

BAD_TEST_BODY = TASK_008_BAD_SAMPLES["dynamic_import_skipif"]


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "tests").mkdir()
    (ws / "context").mkdir()
    (ws / "src").mkdir()
    return ws


class TestScopedLoopEndToEnd:
    """mock LLM 으로 ScopedReactLoop 전체 경로를 실행해 회귀를 검증한다."""

    def test_test_writer_can_invoke_ask_user_and_write_clean_file(self, workspace: Path):
        """정상 경로: LLM 이 dependency_artifacts 를 읽고 ask_user 로 확인한 뒤
        금지 패턴 없는 테스트 파일을 작성 → ScopedReactLoop 이 차단 없이 완료한다.

        회귀 가드: TEST_WRITER.allowed_tools 에서 ask_user 가 빠지면 첫 iteration
        의 ask_user 호출이 `[역할 제약]` 에러로 바뀌어 test_file 이 안 생긴다.
        """
        responses = [
            LLMResponse(
                content=[_tool_use("c1", "read_file", {"path": "context/dependency_artifacts.md"})],
                model="mock", stop_reason="tool_use",
            ),
            LLMResponse(
                content=[_tool_use("c2", "ask_user", {"question": "MyClass.add 인자 순서?"})],
                model="mock", stop_reason="tool_use",
            ),
            LLMResponse(
                content=[_tool_use("c3", "write_file", {
                    "path": "tests/test_mymodule.py",
                    "content": GOOD_TEST_BODY,
                })],
                model="mock", stop_reason="tool_use",
            ),
            LLMResponse(
                content=[_text("테스트 작성 완료.")],
                model="mock", stop_reason="end_turn",
            ),
        ]
        # context/dependency_artifacts.md 는 존재만 하면 된다
        (workspace / "context" / "dependency_artifacts.md").write_text(
            "# Dependency Artifacts\nMyClass.add(self, x: int, y: int) -> int\n",
            encoding="utf-8",
        )

        loop = ScopedReactLoop(
            llm=_make_mock_llm(responses),
            role=TEST_WRITER,
            workspace_dir=workspace,
            max_iterations=6,
        )

        # ask_user 는 Discord/stdin 에 의존하므로 call_tool 레벨에서 스텁
        def _fake_call_tool(name, **kwargs):
            if name == "ask_user":
                return ToolResult(success=True, output="Signature: add(self, x: int, y: int) -> int")
            from tools.registry import call_tool as real_call
            return real_call(name, **kwargs)

        with patch("core.loop.call_tool", side_effect=_fake_call_tool):
            result = loop.run("MyClass.add 의 덧셈 동작을 검증하는 pytest 작성")

        # 루프 자체가 성공
        assert result.succeeded, f"scoped loop 실패: {result.answer}"
        # 파일이 실제로 생성됨
        produced = workspace / "tests" / "test_mymodule.py"
        assert produced.exists(), "write_file 이 차단됐거나 호출되지 않았다"
        body = produced.read_text(encoding="utf-8")
        # 금지 패턴이 없고 ask_user 후 생성된 "clean" 코드다
        assert _validate_python_test(body, "tests/test_mymodule.py") == []
        # ask_user 호출이 실제로 dispatch 되어 역할 제약 에러가 아니다
        tool_names = [
            tc.name
            for it in result.loop_result.iterations
            for tc in it.tool_calls
        ]
        assert "ask_user" in tool_names
        # scoped loop 이 ask_user 결과를 에러로 내리지 않음
        for it in result.loop_result.iterations:
            for tr in it.tool_results:
                if any(tc.id == tr.tool_use_id and tc.name == "ask_user" for tc in it.tool_calls):
                    assert not tr.is_error, f"ask_user 가 거절됨: {tr.content}"

    def test_banned_pattern_write_is_caught_by_quality_gate(self, tmp_path: Path):
        """회귀 시나리오: LLM 이 task-008 스타일의 방어 코드를 write_file 로 저장해도
        pipeline 수준 정적 게이트(`_validate_tests_static`) 가 이를 거절한다.

        ScopedReactLoop 자체는 write_file 을 허용(프롬프트 위반 책임은 LLM 에 있음)
        하지만, 그 다음 단계의 품질 게이트가 fail-closed 로 동작해야 한다.
        """
        task = Task(
            id="task-x", title="t", description="",
            acceptance_criteria=[], target_files=["src/mymodule.py"],
        )
        ws_mgr = WorkspaceManager(task, tmp_path, base_dir=tmp_path / "wsbase")
        ws_mgr.create()

        responses = [
            LLMResponse(
                content=[_tool_use("b1", "write_file", {
                    "path": "tests/test_bad.py",
                    "content": BAD_TEST_BODY,
                })],
                model="mock", stop_reason="tool_use",
            ),
            LLMResponse(
                content=[_text("end.")],
                model="mock", stop_reason="end_turn",
            ),
        ]
        loop = ScopedReactLoop(
            llm=_make_mock_llm(responses),
            role=TEST_WRITER,
            workspace_dir=ws_mgr.path,
            max_iterations=4,
        )
        result = loop.run("MyClass 에 대한 테스트 작성")

        # write 자체는 허용된다 (loop 은 프롬프트 위반을 판정하지 않는다)
        bad_path = ws_mgr.path / "tests" / "test_bad.py"
        assert bad_path.exists()
        assert result.succeeded

        # 그러나 품질 게이트는 반드시 이걸 잡아야 한다
        issues = _validate_tests_static([str(bad_path.relative_to(ws_mgr.path))], ws_mgr)
        assert issues, "task-008 재현 파일이 품질 게이트를 통과했다 — 회귀 발생"
        joined = "\n".join(issues)
        assert ("동적 import" in joined) or ("skipif" in joined)

    def test_ask_user_still_blocked_if_removed_from_allowed_tools(self, workspace: Path):
        """음성 가드: roles.py 에서 ask_user 를 다시 제거하면 첫 호출이 즉시 역할
        제약 에러로 차단돼야 한다. 이 테스트는 '현재 설정에서는 허용됨' + '제거되면
        즉시 감지됨' 두 가정을 동시에 고정한다.
        """
        from dataclasses import replace

        responses = [
            LLMResponse(
                content=[_tool_use("q1", "ask_user", {"question": "시그니처?"})],
                model="mock", stop_reason="tool_use",
            ),
            LLMResponse(
                content=[_text("stop.")],
                model="mock", stop_reason="end_turn",
            ),
        ]
        # 가설적으로 ask_user 가 제거된 역할
        reduced_role = replace(
            TEST_WRITER,
            allowed_tools=tuple(t for t in TEST_WRITER.allowed_tools if t != "ask_user"),
        )
        loop = ScopedReactLoop(
            llm=_make_mock_llm(responses),
            role=reduced_role,
            workspace_dir=workspace,
            max_iterations=3,
        )
        result = loop.run("임의 질문")

        # ask_user 가 차단돼 에러 결과가 기록된다
        errors = [
            tr
            for it in result.loop_result.iterations
            for tr in it.tool_results
            if tr.is_error and "역할 제약" in tr.content
        ]
        assert errors, (
            "ask_user 가 허용 목록에서 빠졌는데도 loop 이 차단하지 않았다 — "
            "가드가 깨짐"
        )
