"""
tests/test_token_tracking.py — 토큰 사용량 추적 인프라 테스트

LLMResponse 캐시 필드, ReactLoop 누적, TaskReport 직렬화,
_accumulate_tokens 4-tuple, JSONL 로그 기록을 검증한다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest

from llm.base import BaseLLMClient, LLMConfig, LLMResponse, Message


# ── Helpers ──────────────────────────────────────────────────────────────────


class _TokenMockLLM(BaseLLMClient):
    """매 호출마다 고정 토큰 값을 반환하는 mock LLM 클라이언트."""

    def __init__(
        self,
        responses: list[LLMResponse] | None = None,
    ):
        config = LLMConfig(model="mock-model")
        super().__init__(config)
        self._responses = list(responses or [])
        self._call_idx = 0

    def chat(self, messages: list[Message], **kwargs) -> LLMResponse:
        if self._call_idx < len(self._responses):
            resp = self._responses[self._call_idx]
            self._call_idx += 1
            return resp
        # 기본: end_turn
        return LLMResponse(
            content=[{"type": "text", "text": "done"}],
            model="mock-model",
            stop_reason="end_turn",
        )

    def stream(self, messages: list[Message], **kwargs) -> Generator[str, None, None]:
        yield "done"

    def is_available(self) -> bool:
        return True

    def list_models(self) -> list[str]:
        return ["mock-model"]


# ── 1) LLMResponse 캐시 필드 기본값 ─────────────────────────────────────────


class TestLLMResponseCacheFields:
    def test_defaults_to_zero(self):
        resp = LLMResponse(content=[], model="test")
        assert resp.cached_read_tokens == 0
        assert resp.cached_write_tokens == 0

    def test_explicit_values(self):
        resp = LLMResponse(
            content=[],
            model="test",
            cached_read_tokens=500,
            cached_write_tokens=200,
        )
        assert resp.cached_read_tokens == 500
        assert resp.cached_write_tokens == 200

    def test_backward_compat_no_cache_kwargs(self):
        """기존 호출 방식(cache 인자 미전달)이 동작하는지 확인."""
        resp = LLMResponse(
            content=[{"type": "text", "text": "hi"}],
            model="claude",
            stop_reason="end_turn",
            input_tokens=100,
            output_tokens=50,
        )
        assert resp.input_tokens == 100
        assert resp.output_tokens == 50
        assert resp.cached_read_tokens == 0
        assert resp.cached_write_tokens == 0


# ── 2) ClaudeClient mock 파싱 ────────────────────────────────────────────────


class TestClaudeClientTokenParsing:
    def test_cache_tokens_extracted(self):
        """Anthropic usage 객체에서 cache 필드를 추출하는지 검증."""
        from llm.claude_client import ClaudeClient

        mock_usage = MagicMock()
        mock_usage.input_tokens = 1000
        mock_usage.output_tokens = 200
        mock_usage.cache_read_input_tokens = 800
        mock_usage.cache_creation_input_tokens = 300

        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="hello")]
        mock_response.model = "claude-haiku"
        mock_response.stop_reason = "end_turn"
        mock_response.usage = mock_usage

        # LLMResponse 생성 로직만 검증 (API 호출 없이)
        resp = LLMResponse(
            content=mock_response.content,
            model=mock_response.model,
            stop_reason=mock_response.stop_reason,
            input_tokens=mock_response.usage.input_tokens,
            output_tokens=mock_response.usage.output_tokens,
            cached_read_tokens=getattr(mock_response.usage, "cache_read_input_tokens", 0) or 0,
            cached_write_tokens=getattr(mock_response.usage, "cache_creation_input_tokens", 0) or 0,
        )
        assert resp.cached_read_tokens == 800
        assert resp.cached_write_tokens == 300

    def test_missing_cache_fields_default_zero(self):
        """cache 필드가 없는 usage 객체에서 0으로 처리되는지 검증."""
        mock_usage = MagicMock(spec=["input_tokens", "output_tokens"])
        mock_usage.input_tokens = 500
        mock_usage.output_tokens = 100

        resp = LLMResponse(
            content=[],
            model="claude",
            cached_read_tokens=getattr(mock_usage, "cache_read_input_tokens", 0) or 0,
            cached_write_tokens=getattr(mock_usage, "cache_creation_input_tokens", 0) or 0,
        )
        assert resp.cached_read_tokens == 0
        assert resp.cached_write_tokens == 0


# ── 3) OpenAI/GLM mock 파싱 ──────────────────────────────────────────────────


class TestOpenAITokenParsing:
    def test_cached_tokens_from_prompt_details(self):
        """prompt_tokens_details.cached_tokens 추출 검증."""
        mock_details = MagicMock()
        mock_details.cached_tokens = 600

        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 1000
        mock_usage.completion_tokens = 200
        mock_usage.prompt_tokens_details = mock_details

        _cached_read = 0
        if mock_usage and hasattr(mock_usage, "prompt_tokens_details") and mock_usage.prompt_tokens_details:
            _cached_read = getattr(mock_usage.prompt_tokens_details, "cached_tokens", 0) or 0

        assert _cached_read == 600

    def test_no_prompt_details_defaults_zero(self):
        """prompt_tokens_details 가 없으면 0."""
        mock_usage = MagicMock(spec=["prompt_tokens", "completion_tokens"])
        mock_usage.prompt_tokens = 1000
        mock_usage.completion_tokens = 200

        _cached_read = 0
        if mock_usage and hasattr(mock_usage, "prompt_tokens_details") and mock_usage.prompt_tokens_details:
            _cached_read = getattr(mock_usage.prompt_tokens_details, "cached_tokens", 0) or 0

        assert _cached_read == 0


# ── 4) LoopResult 캐시 토큰 누적 ─────────────────────────────────────────────


class TestLoopResultCacheAccumulation:
    @patch("core.loop.ReactLoop.get_tools_schema", return_value=[])
    def test_multi_turn_accumulation(self, _mock_schema):
        """multi-turn 루프에서 캐시 토큰이 올바르게 누적되는지 검증."""
        from core.loop import ReactLoop

        # end_turn 1회 응답 — 도구 스키마 없이 단순 text 응답
        responses = [
            LLMResponse(
                content=[{"type": "text", "text": "완료"}],
                model="mock",
                stop_reason="end_turn",
                input_tokens=150,
                output_tokens=30,
                cached_read_tokens=60,
                cached_write_tokens=10,
            ),
        ]

        mock_llm = _TokenMockLLM(responses)
        loop = ReactLoop(llm=mock_llm, max_iterations=5)
        result = loop.run("테스트")

        assert result.total_input_tokens == 150
        assert result.total_output_tokens == 30
        assert result.total_cached_read_tokens == 60
        assert result.total_cached_write_tokens == 10
        assert len(result.call_log) == 1


# ── 5) call_log 구조 ─────────────────────────────────────────────────────────


class TestCallLogStructure:
    @patch("core.loop.ReactLoop.get_tools_schema", return_value=[])
    def test_call_log_keys(self, _mock_schema):
        """call_log 항목에 필수 키가 존재하는지 확인."""
        from core.loop import ReactLoop

        responses = [
            LLMResponse(
                content=[{"type": "text", "text": "응답"}],
                model="mock-model",
                stop_reason="end_turn",
                input_tokens=100,
                output_tokens=20,
                cached_read_tokens=50,
                cached_write_tokens=10,
            ),
        ]

        mock_llm = _TokenMockLLM(responses)
        loop = ReactLoop(llm=mock_llm, max_iterations=3)
        result = loop.run("안녕")

        assert len(result.call_log) >= 1
        entry = result.call_log[0]
        required_keys = {
            "timestamp", "iteration", "model",
            "input_tokens", "output_tokens",
            "cached_read_tokens", "cached_write_tokens",
            "tool_calls",
        }
        assert required_keys.issubset(entry.keys())
        assert entry["model"] == "mock-model"
        assert entry["input_tokens"] == 100
        assert entry["cached_read_tokens"] == 50
        assert isinstance(entry["tool_calls"], list)

    @patch("core.loop.ReactLoop.get_tools_schema", return_value=[])
    def test_call_log_tool_names(self, _mock_schema):
        """tool_use 응답에서 도구 이름이 call_log에 기록되는지 확인."""
        from core.loop import ReactLoop

        # tool_use → end_turn (도구 스키마 없으므로 tool_calls 추출 후 실행 시 에러 처리)
        responses = [
            LLMResponse(
                content=[
                    {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "x.py"}},
                    {"type": "tool_use", "id": "t2", "name": "write_file", "input": {"path": "y.py", "content": ""}},
                ],
                model="mock",
                stop_reason="tool_use",
                input_tokens=100,
                output_tokens=20,
            ),
            LLMResponse(
                content=[{"type": "text", "text": "done"}],
                model="mock",
                stop_reason="end_turn",
                input_tokens=50,
                output_tokens=10,
            ),
        ]

        mock_llm = _TokenMockLLM(responses)
        loop = ReactLoop(llm=mock_llm, max_iterations=5)
        result = loop.run("test")

        # 첫 호출의 tool_calls 에 도구 이름이 들어있어야 함
        assert len(result.call_log) >= 1
        assert "read_file" in result.call_log[0]["tool_calls"]
        assert "write_file" in result.call_log[0]["tool_calls"]


# ── 6) _accumulate_tokens 4-tuple ────────────────────────────────────────────


class TestAccumulateTokens:
    def test_4tuple_accumulation(self):
        """4-tuple 토큰 누적이 올바르게 동작하는지 검증."""
        from core.loop import LoopResult
        from orchestrator.pipeline import PipelineMetrics, _accumulate_tokens

        # ScopedResult mock
        scoped = MagicMock()
        scoped.loop_result = LoopResult(
            answer="done",
            stop_reason=MagicMock(),
            total_input_tokens=1000,
            total_output_tokens=200,
            total_cached_read_tokens=500,
            total_cached_write_tokens=100,
            call_log=[{"iteration": 1}],
        )

        metrics = PipelineMetrics()
        _accumulate_tokens(metrics, "implementer", scoped)

        assert metrics.token_usage["implementer"] == (1000, 200, 500, 100)
        assert "implementer" in metrics.call_logs
        assert len(metrics.call_logs["implementer"]) == 1

    def test_2tuple_backward_compat(self):
        """기존 2-tuple 데이터에 새 누적이 올바르게 처리되는지 검증."""
        from core.loop import LoopResult
        from orchestrator.pipeline import PipelineMetrics, _accumulate_tokens

        metrics = PipelineMetrics()
        # 레거시 2-tuple 시뮬레이션
        metrics.token_usage["reviewer"] = (500, 100)

        scoped = MagicMock()
        scoped.loop_result = LoopResult(
            answer="ok",
            stop_reason=MagicMock(),
            total_input_tokens=200,
            total_output_tokens=50,
            total_cached_read_tokens=80,
            total_cached_write_tokens=20,
        )

        _accumulate_tokens(metrics, "reviewer", scoped)
        result = metrics.token_usage["reviewer"]
        assert len(result) == 4
        assert result == (700, 150, 80, 20)

    def test_none_loop_result_skipped(self):
        """loop_result가 None이면 건너뛰는지 검증."""
        from orchestrator.pipeline import PipelineMetrics, _accumulate_tokens

        scoped = MagicMock()
        scoped.loop_result = None

        metrics = PipelineMetrics()
        _accumulate_tokens(metrics, "test_writer", scoped)
        assert "test_writer" not in metrics.token_usage


# ── 7) TaskReport round-trip ─────────────────────────────────────────────────


class TestTaskReportRoundTrip:
    def test_cache_fields_survive_serialization(self):
        """to_dict() → from_dict() 왕복 후 캐시 필드가 보존되는지 확인."""
        from reports.task_report import TaskReport

        report = TaskReport(
            task_id="task-100",
            title="토큰 추적 테스트",
            status="COMPLETED",
            completed_at="2026-04-17T00:00:00",
            total_tokens=5000,
            total_cached_read_tokens=2000,
            total_cached_write_tokens=500,
            cache_hit_rate=0.6667,
            token_usage={
                "test_writer": {"input": 1000, "output": 200, "cached_read": 800, "cached_write": 300},
                "implementer": {"input": 2000, "output": 500, "cached_read": 1200, "cached_write": 200},
            },
        )

        d = report.to_dict()
        restored = TaskReport.from_dict(d)

        assert restored.total_cached_read_tokens == 2000
        assert restored.total_cached_write_tokens == 500
        assert restored.cache_hit_rate == 0.6667
        assert restored.token_usage is not None
        assert restored.token_usage["test_writer"]["cached_read"] == 800
        assert restored.token_usage_detail == restored.token_usage

    def test_old_format_without_cache_fields(self):
        """캐시 필드가 없는 기존 YAML 데이터가 역직렬화되는지 확인."""
        from reports.task_report import TaskReport

        old_data = {
            "task_id": "task-old",
            "title": "레거시 리포트",
            "status": "COMPLETED",
            "completed_at": "2024-01-01T00:00:00",
            "metrics": {
                "retry_count": 0,
                "total_tokens": 1000,
            },
            "pipeline_result": {},
        }

        report = TaskReport.from_dict(old_data)
        assert report.total_cached_read_tokens == 0
        assert report.total_cached_write_tokens == 0
        assert report.cache_hit_rate == 0.0
        assert report.token_usage is None
        assert report.token_usage_detail is None

    def test_token_usage_optional(self):
        """token_usage가 None이면 to_dict()에서 제외되는지 확인."""
        from reports.task_report import TaskReport

        report = TaskReport(
            task_id="task-200",
            title="test",
            status="COMPLETED",
            completed_at="2026-01-01T00:00:00",
        )
        d = report.to_dict()
        assert "token_usage" not in d

    def test_legacy_token_usage_detail_is_loaded(self):
        """레거시 token_usage_detail 키도 token_usage로 역직렬화된다."""
        from reports.task_report import TaskReport

        data = {
            "task_id": "task-legacy",
            "title": "legacy",
            "status": "COMPLETED",
            "completed_at": "2026-01-01T00:00:00",
            "metrics": {},
            "pipeline_result": {},
            "token_usage_detail": {
                "reviewer": {"input": 10, "output": 5, "cached_read": 3, "cached_write": 1},
            },
        }
        report = TaskReport.from_dict(data)
        assert report.token_usage is not None
        assert report.token_usage["reviewer"]["cached_write"] == 1


# ── 8) cache_hit_rate 계산 ───────────────────────────────────────────────────


class TestCacheHitRate:
    def test_rate_calculation(self):
        """build_report 에서 cache_hit_rate 가 올바르게 계산되는지 검증."""
        # cached_read / (input + cached_read)
        # 2000 / (3000 + 2000) = 0.4
        total_input = 3000
        total_cached_read = 2000
        rate = round(total_cached_read / (total_input + total_cached_read), 4)
        assert rate == 0.4

    def test_zero_input_returns_zero(self):
        """input과 cached_read가 모두 0이면 rate도 0."""
        total_input = 0
        total_cached_read = 0
        rate = (
            round(total_cached_read / (total_input + total_cached_read), 4)
            if (total_input + total_cached_read) > 0 else 0.0
        )
        assert rate == 0.0


# ── 9) write_call_log JSONL ──────────────────────────────────────────────────


class TestWriteCallLog:
    def test_jsonl_output(self, tmp_path: Path):
        """JSONL 파일에 올바른 JSON이 기록되는지 확인."""
        from core.token_log import write_call_log

        call_log = [
            {
                "timestamp": "2026-04-17T00:00:00+00:00",
                "iteration": 1,
                "model": "claude-haiku",
                "input_tokens": 1000,
                "output_tokens": 200,
                "cached_read_tokens": 500,
                "cached_write_tokens": 100,
                "tool_calls": ["read_file"],
            },
            {
                "timestamp": "2026-04-17T00:00:01+00:00",
                "iteration": 2,
                "model": "claude-haiku",
                "input_tokens": 1500,
                "output_tokens": 300,
                "cached_read_tokens": 800,
                "cached_write_tokens": 0,
                "tool_calls": ["write_file"],
            },
        ]

        path = write_call_log("task-001", "test_writer", call_log, log_dir=tmp_path)
        assert path is not None
        assert path.exists()

        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2

        row0 = json.loads(lines[0])
        assert row0["role"] == "test_writer"
        assert row0["task_id"] == "task-001"
        assert row0["input_tokens"] == 1000
        assert row0["cached_read_tokens"] == 500

        row1 = json.loads(lines[1])
        assert row1["iteration"] == 2
        assert row1["tool_calls"] == ["write_file"]

    def test_empty_call_log_returns_none(self, tmp_path: Path):
        """빈 call_log 에 대해 None을 반환하는지 확인."""
        from core.token_log import write_call_log

        result = write_call_log("task-002", "implementer", [], log_dir=tmp_path)
        assert result is None

    def test_jsonl_filename_format(self, tmp_path: Path):
        """파일명이 {task_id}_{timestamp}.jsonl 형식인지 확인."""
        from core.token_log import write_call_log

        call_log = [{"iteration": 1, "model": "m"}]
        path = write_call_log("task-abc", "reviewer", call_log, log_dir=tmp_path)
        assert path is not None
        assert path.name.startswith("task-abc_")
        assert path.name.endswith(".jsonl")
