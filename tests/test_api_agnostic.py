"""
tests/test_api_agnostic.py — API agnostic 리팩토링 검증 테스트

intervention.py, hotline_tools.py, backend routers, run.py의
BaseLLMClient 주입 방식 동작을 mock으로 검증한다.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest

from llm.base import BaseLLMClient, LLMConfig, LLMResponse, Message


# ── Mock LLMClient ────────────────────────────────────────────────────────────

class MockLLMClient(BaseLLMClient):
    """테스트용 더미 LLM 클라이언트."""

    def __init__(self, config: LLMConfig, response_text: str = "mock response"):
        super().__init__(config)
        self.response_text = response_text
        self.called_messages: list[list[Message]] = []

    def chat(self, messages: list[Message], **kwargs) -> LLMResponse:
        self.called_messages.append(messages)
        block = MagicMock()
        block.type = "text"
        block.text = self.response_text
        return LLMResponse(
            content=[block],
            model=self.config.model,
            stop_reason="end_turn",
        )

    def stream(self, messages: list[Message], **kwargs) -> Generator[str, None, None]:
        for word in self.response_text.split():
            yield word + " "

    def is_available(self) -> bool:
        return True

    def list_models(self) -> list[str]:
        return [self.config.model]


# ── intervention.py 테스트 ────────────────────────────────────────────────────

class TestIntervention:
    def setup_method(self):
        """각 테스트 전에 LLM을 None으로 초기화."""
        import orchestrator.intervention as iv
        iv._analyze_llm = None
        iv._report_llm = None

    def test_set_llm_injects_clients(self):
        from orchestrator.intervention import set_llm
        analyze_llm = MockLLMClient(LLMConfig(model="mock-model"))
        report_llm = MockLLMClient(LLMConfig(model="mock-model"))

        set_llm(analyze_llm, report_llm)

        import orchestrator.intervention as iv
        assert iv._analyze_llm is analyze_llm
        assert iv._report_llm is report_llm

    def test_analyze_returns_retry_on_retry_response(self):
        from orchestrator.intervention import set_llm, analyze
        from orchestrator.task import Task

        analyze_llm = MockLLMClient(LLMConfig(model="mock-model"), "RETRY: 함수명을 fix_login으로 수정하세요")
        report_llm = MockLLMClient(LLMConfig(model="mock-model"))
        set_llm(analyze_llm, report_llm)

        task = Task(id="t1", title="테스트", description="설명",
                    acceptance_criteria=["조건"], target_files=[])
        result = analyze(task, "테스트 실패", 1)

        assert result.should_retry is True
        assert "fix_login" in result.hint

    def test_analyze_returns_giveup_on_giveup_response(self):
        from orchestrator.intervention import set_llm, analyze
        from orchestrator.task import Task

        analyze_llm = MockLLMClient(LLMConfig(model="mock-model"), "GIVE_UP: 스펙이 불명확합니다")
        report_llm = MockLLMClient(LLMConfig(model="mock-model"))
        set_llm(analyze_llm, report_llm)

        task = Task(id="t1", title="테스트", description="설명",
                    acceptance_criteria=["조건"], target_files=[])
        result = analyze(task, "반복 실패", 3)

        assert result.should_retry is False
        assert "스펙" in result.hint

    def test_analyze_returns_giveup_when_llm_not_set(self):
        from orchestrator.intervention import analyze
        from orchestrator.task import Task

        task = Task(id="t1", title="테스트", description="설명",
                    acceptance_criteria=["조건"], target_files=[])
        result = analyze(task, "실패", 1)

        assert result.should_retry is False
        assert "미초기화" in result.hint

    def test_generate_report_uses_report_llm(self):
        from orchestrator.intervention import set_llm, generate_report
        from orchestrator.task import Task

        analyze_llm = MockLLMClient(LLMConfig(model="mock-model"))
        report_llm = MockLLMClient(LLMConfig(model="mock-model"), "## 실패 보고서\n내용입니다")
        set_llm(analyze_llm, report_llm)

        task = Task(id="t1", title="테스트", description="설명",
                    acceptance_criteria=["조건"], target_files=[])
        report = generate_report(task, "최종 실패", 2, ["힌트1", "힌트2"])

        assert "실패 보고서" in report
        assert len(report_llm.called_messages) == 1  # report_llm만 호출됨
        assert len(analyze_llm.called_messages) == 0  # analyze_llm은 호출 안 됨

    def test_analyze_uses_analyze_llm_not_report_llm(self):
        from orchestrator.intervention import set_llm, analyze
        from orchestrator.task import Task

        analyze_llm = MockLLMClient(LLMConfig(model="mock-model"), "RETRY: 힌트")
        report_llm = MockLLMClient(LLMConfig(model="mock-model"))
        set_llm(analyze_llm, report_llm)

        task = Task(id="t1", title="테스트", description="설명",
                    acceptance_criteria=["조건"], target_files=[])
        analyze(task, "실패", 1)

        assert len(analyze_llm.called_messages) == 1
        assert len(report_llm.called_messages) == 0

    def test_create_intervention_llms_uses_correct_system_prompts(self):
        from orchestrator.intervention import (
            create_intervention_llms,
            _ANALYZE_SYSTEM,
            _REPORT_SYSTEM,
        )

        with patch("orchestrator.intervention.create_client") as mock_create:
            mock_create.return_value = MockLLMClient(LLMConfig(model="m"))
            create_intervention_llms("claude", "claude-sonnet-4-6")

        calls = mock_create.call_args_list
        assert len(calls) == 2
        # analyze 클라이언트의 system_prompt
        assert calls[0][0][1].system_prompt == _ANALYZE_SYSTEM
        assert calls[0][0][1].max_tokens == 1024
        # report 클라이언트의 system_prompt
        assert calls[1][0][1].system_prompt == _REPORT_SYSTEM
        assert calls[1][0][1].max_tokens == 4096


# ── hotline_tools.py 테스트 ───────────────────────────────────────────────────

class TestHotlineTools:
    def setup_method(self):
        import tools.hotline_tools as ht
        ht._conv_llm = None
        ht._sum_llm = None

    def test_set_llm_injects_clients(self):
        from tools.hotline_tools import set_llm
        conv_llm = MockLLMClient(LLMConfig(model="mock"))
        sum_llm = MockLLMClient(LLMConfig(model="mock"))

        set_llm(conv_llm, sum_llm)

        import tools.hotline_tools as ht
        assert ht._conv_llm is conv_llm
        assert ht._sum_llm is sum_llm

    def test_orchestrator_reply_uses_conv_llm(self):
        from tools.hotline_tools import set_llm, _orchestrator_reply
        conv_llm = MockLLMClient(LLMConfig(model="mock"), "네, 그렇게 하시면 됩니다.")
        sum_llm = MockLLMClient(LLMConfig(model="mock"))
        set_llm(conv_llm, sum_llm)

        reply = _orchestrator_reply("어떻게 할까요?", [{"role": "user", "content": "도움이 필요해요"}])

        assert "그렇게 하시면 됩니다" in reply
        assert len(conv_llm.called_messages) == 1
        assert len(sum_llm.called_messages) == 0

    def test_orchestrator_reply_returns_error_msg_when_not_set(self):
        from tools.hotline_tools import _orchestrator_reply
        reply = _orchestrator_reply("질문", [])
        assert "미초기화" in reply

    def test_synthesize_answer_uses_sum_llm(self):
        from tools.hotline_tools import set_llm, _synthesize_answer
        conv_llm = MockLLMClient(LLMConfig(model="mock"))
        sum_llm = MockLLMClient(LLMConfig(model="mock"), "최종 결정: A 방식으로 진행")
        set_llm(conv_llm, sum_llm)

        answer = _synthesize_answer(
            "어떤 방식으로 구현?",
            [{"role": "user", "content": "A 방식이 좋겠어요"}, {"role": "assistant", "content": "좋은 선택입니다"}]
        )

        assert "A 방식" in answer
        assert len(sum_llm.called_messages) == 1
        assert len(conv_llm.called_messages) == 0

    def test_synthesize_answer_fallback_when_no_conversation(self):
        from tools.hotline_tools import set_llm, _synthesize_answer
        conv_llm = MockLLMClient(LLMConfig(model="mock"))
        sum_llm = MockLLMClient(LLMConfig(model="mock"))
        set_llm(conv_llm, sum_llm)

        answer = _synthesize_answer("질문", [])

        # 대화 없이 확정 — sum_llm 호출 없이 기본 메시지 반환
        assert "확정" in answer
        assert len(sum_llm.called_messages) == 0

    def test_create_hotline_llms_uses_correct_system_prompts(self):
        from tools.hotline_tools import (
            create_hotline_llms,
            _CONVERSATION_SYSTEM,
            _SUMMARIZE_SYSTEM,
        )

        with patch("tools.hotline_tools.create_client") as mock_create:
            mock_create.return_value = MockLLMClient(LLMConfig(model="m"))
            create_hotline_llms("claude", "claude-sonnet-4-6")

        calls = mock_create.call_args_list
        assert len(calls) == 2
        assert calls[0][0][1].system_prompt == _CONVERSATION_SYSTEM
        assert calls[0][0][1].max_tokens == 1024
        assert calls[1][0][1].system_prompt == _SUMMARIZE_SYSTEM
        assert calls[1][0][1].max_tokens == 512


# ── _extract_text 헬퍼 테스트 ──────────────────────────────────────────────────

class TestExtractText:
    def test_extracts_text_from_dict_block(self):
        from orchestrator.intervention import _extract_text
        response = MagicMock()
        response.content = [{"type": "text", "text": "hello world"}]
        assert _extract_text(response) == "hello world"

    def test_extracts_text_from_object_block(self):
        from orchestrator.intervention import _extract_text
        block = MagicMock()
        block.type = "text"
        block.text = "hello world"
        response = MagicMock()
        response.content = [block]
        assert _extract_text(response) == "hello world"

    def test_returns_empty_string_for_empty_content(self):
        from orchestrator.intervention import _extract_text
        response = MagicMock()
        response.content = []
        assert _extract_text(response) == ""

    def test_skips_non_text_blocks(self):
        from orchestrator.intervention import _extract_text
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "actual text"
        response = MagicMock()
        response.content = [tool_block, text_block]
        assert _extract_text(response) == "actual text"


# ── backend/config.py 테스트 ──────────────────────────────────────────────────

class TestBackendConfig:
    def test_reads_llm_provider_from_env(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")

        import importlib
        import backend.config as cfg
        importlib.reload(cfg)

        assert cfg.LLM_PROVIDER == "openai"

    def test_defaults_to_claude_provider(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.delenv("LLM_PROVIDER", raising=False)

        import importlib
        import backend.config as cfg
        importlib.reload(cfg)

        assert cfg.LLM_PROVIDER == "claude"

    def test_reads_model_names_from_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("LLM_MODEL_FAST", "gpt-4o-mini")
        monkeypatch.setenv("LLM_MODEL_CAPABLE", "gpt-4o")

        import importlib
        import backend.config as cfg
        importlib.reload(cfg)

        assert cfg.LLM_MODEL_FAST == "gpt-4o-mini"
        assert cfg.LLM_MODEL_CAPABLE == "gpt-4o"


# ── backend/routers/chat.py 테스트 ────────────────────────────────────────────

class TestChatRouter:
    def test_resolve_model_uses_fast_model_when_flagged(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        import importlib
        import backend.config as cfg
        importlib.reload(cfg)

        from backend.routers.chat import ChatRequest, _resolve_model
        req = ChatRequest(max_tokens=100, messages=[], use_fast_model=True)

        import backend.routers.chat as chat_mod
        chat_mod.LLM_MODEL_FAST = "fast-model"
        chat_mod.LLM_MODEL_CAPABLE = "capable-model"

        assert _resolve_model(req) == "fast-model"

    def test_resolve_model_uses_capable_model_by_default(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        from backend.routers.chat import ChatRequest, _resolve_model
        import backend.routers.chat as chat_mod
        chat_mod.LLM_MODEL_FAST = "fast-model"
        chat_mod.LLM_MODEL_CAPABLE = "capable-model"

        req = ChatRequest(max_tokens=100, messages=[])
        assert _resolve_model(req) == "capable-model"

    def test_resolve_model_uses_explicit_model_when_provided(self):
        from backend.routers.chat import ChatRequest, _resolve_model
        req = ChatRequest(max_tokens=100, messages=[], model="explicit-model-name")
        assert _resolve_model(req) == "explicit-model-name"

    def test_to_messages_converts_dicts(self):
        from backend.routers.chat import _to_messages
        raw = [
            {"role": "user", "content": "안녕"},
            {"role": "assistant", "content": "반갑습니다"},
        ]
        messages = _to_messages(raw)
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[0].content == "안녕"
        assert messages[1].role == "assistant"

    def test_chat_complete_returns_text(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        from backend.routers.chat import chat_complete, ChatRequest
        import backend.routers.chat as chat_mod

        mock_client = MockLLMClient(LLMConfig(model="mock"), "테스트 응답입니다")

        async def run():
            with patch("backend.routers.chat.create_client", return_value=mock_client):
                req = ChatRequest(max_tokens=100, messages=[{"role": "user", "content": "안녕"}])
                return await chat_complete(req)

        result = asyncio.run(run())
        assert result["text"] == "테스트 응답입니다"

    def test_chat_stream_yields_sse_events(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        from backend.routers.chat import chat_stream, ChatRequest
        from fastapi.responses import StreamingResponse

        mock_client = MockLLMClient(LLMConfig(model="mock"), "hello world")

        # thread 안에서도 patch가 유지되도록 monkeypatch 사용
        monkeypatch.setattr("backend.routers.chat.create_client", lambda *a, **kw: mock_client)

        async def run():
            req = ChatRequest(max_tokens=100, messages=[{"role": "user", "content": "hi"}])
            response = await chat_stream(req)
            assert isinstance(response, StreamingResponse)
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk)
            return "".join(chunks)

        full = asyncio.run(run())
        assert "text_delta" in full
        assert "done" in full


# ── run.py CLI args 테스트 ────────────────────────────────────────────────────

class TestRunPipelineArgs:
    def test_run_pipeline_has_provider_param(self):
        import inspect
        from orchestrator.run import run_pipeline
        sig = inspect.signature(run_pipeline)
        assert "provider" in sig.parameters
        assert "model_fast" in sig.parameters
        assert "model_capable" in sig.parameters

    def test_default_provider_is_claude(self):
        import inspect
        from orchestrator.run import run_pipeline
        sig = inspect.signature(run_pipeline)
        assert sig.parameters["provider"].default == "claude"
        # 3회차 재시도에서 intervention 스켈레톤 주입 → 4회차 실행을 허용하도록 기본값 3
        assert sig.parameters["max_orchestrator_retries"].default == 3
        assert sig.parameters["intervention_auto_split"].default is False

    def test_parse_args_accepts_provider_flag(self):
        from orchestrator.run import _parse_args
        with patch("sys.argv", ["run.py", "--tasks", "t.yaml", "--provider", "openai",
                                "--model-fast", "gpt-4o-mini", "--model-capable", "gpt-4o"]):
            args = _parse_args()
        assert args.provider == "openai"
        assert args.model_fast == "gpt-4o-mini"
        assert args.model_capable == "gpt-4o"

    def test_parse_args_default_provider_is_claude(self):
        from orchestrator.run import _parse_args
        with patch("sys.argv", ["run.py", "--tasks", "t.yaml"]):
            args = _parse_args()
        assert args.provider == "claude"
        assert args.model_fast == "claude-haiku-4-5"
        assert args.model_capable == "claude-sonnet-4-6"

    def test_parse_args_accepts_glm_provider(self):
        from orchestrator.run import _parse_args
        with patch("sys.argv", ["run.py", "--tasks", "t.yaml", "--provider", "glm"]):
            args = _parse_args()
        assert args.provider == "glm"


class TestLLMFactoryLazyImport:
    def test_create_openai_client_does_not_import_ollama_client(self):
        import llm as llm_pkg

        class DummyOpenai:
            def __init__(self, config, **kwargs):
                self.config = config

        with patch.object(llm_pkg, "OpenaiClient", DummyOpenai):
            with patch("builtins.__import__") as mock_import:
                cfg = LLMConfig(model="dummy")
                client = llm_pkg.create_client("openai", cfg)
                assert isinstance(client, DummyOpenai)
                imported = " ".join(str(c.args[0]) for c in mock_import.call_args_list if c.args)
                assert "ollama_client" not in imported
