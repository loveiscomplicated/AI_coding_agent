"""
tests/test_registry.py

tools/registry.py 단위 테스트.
TOOLS_SCHEMA 구조 검증 및 call_tool() 라우팅 검증.

실행:
    pytest tests/test_registry.py -v
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from tools.registry import TOOL_REGISTRY, TOOLS_SCHEMA, call_tool


# ── TOOLS_SCHEMA 구조 ──────────────────────────────────────────────────────


class TestToolsSchema:
    def test_schema_is_list(self):
        assert isinstance(TOOLS_SCHEMA, list)
        assert len(TOOLS_SCHEMA) > 0

    def test_each_entry_has_required_keys(self):
        for entry in TOOLS_SCHEMA:
            assert "name" in entry, f"{entry} 에 name 없음"
            assert "description" in entry, f"{entry} 에 description 없음"
            assert "input_schema" in entry, f"{entry} 에 input_schema 없음"

    def test_input_schema_is_object_type(self):
        for entry in TOOLS_SCHEMA:
            schema = entry["input_schema"]
            assert schema["type"] == "object"
            assert "properties" in schema

    def test_required_params_listed(self):
        """required=True로 정의된 파라미터가 input_schema.required에 포함되어야 함"""
        for tool_name, meta in TOOL_REGISTRY.items():
            schema_entry = next(e for e in TOOLS_SCHEMA if e["name"] == tool_name)
            required_in_schema = schema_entry["input_schema"].get("required", [])
            for param, (_, _, is_required, _) in meta["params"].items():
                if is_required:
                    assert (
                        param in required_in_schema
                    ), f"{tool_name}.{param} 은 required=True 인데 schema에 없음"

    def test_optional_params_have_default_hint_in_description(self):
        """required=False 파라미터는 description에 기본값 힌트가 포함되어야 함"""
        for tool_name, meta in TOOL_REGISTRY.items():
            schema_entry = next(e for e in TOOLS_SCHEMA if e["name"] == tool_name)
            props = schema_entry["input_schema"]["properties"]
            for param, (_, _, is_required, default) in meta["params"].items():
                if not is_required:
                    desc = props[param]["description"]
                    assert "기본값" in desc, f"{tool_name}.{param} 에 기본값 힌트 없음"

    def test_registry_and_schema_have_same_tools(self):
        schema_names = {e["name"] for e in TOOLS_SCHEMA}
        registry_names = set(TOOL_REGISTRY.keys())
        assert schema_names == registry_names

    @pytest.mark.parametrize(
        "tool_name",
        [
            "read_file",
            "write_file",
            "edit_file",
            "append_to_file",
            "list_directory",
            "search_in_file",
            "search_files",
            "read_file_lines",
        ],
    )
    def test_expected_tools_present(self, tool_name):
        names = [e["name"] for e in TOOLS_SCHEMA]
        assert tool_name in names


# ── call_tool ──────────────────────────────────────────────────────────────


class TestCallTool:
    def test_calls_read_file_successfully(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("content", encoding="utf-8")

        result = call_tool("read_file", path=str(f))

        assert result.success is True
        assert result.output == "content"

    def test_calls_write_file_successfully(self, tmp_path):
        path = str(tmp_path / "out.txt")

        result = call_tool("write_file", path=path, content="written")

        assert result.success is True
        assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "written"

    def test_unknown_tool_raises_value_error(self):
        with pytest.raises(ValueError, match="알 수 없는 도구"):
            call_tool("nonexistent_tool")

    def test_wrong_kwargs_raises_type_error(self):
        """등록된 도구에 잘못된 인자 전달 → TypeError"""
        with pytest.raises(TypeError):
            call_tool("read_file", wrong_param="value")

    def test_all_registry_tools_are_callable(self, tmp_path):
        """모든 등록 도구가 함수로 호출 가능한지 확인 (실제 실행 X, fn이 callable인지만)"""
        for name, meta in TOOL_REGISTRY.items():
            assert callable(meta["fn"]), f"{name} 의 fn이 callable이 아님"
