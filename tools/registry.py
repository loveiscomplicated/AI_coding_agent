"""
tools/registry.py — 도구 등록 & 관리

TOOL_REGISTRY  : 도구 함수 + 스키마 정의 (단일 진실 공급원)
TOOLS_SCHEMA   : LLM API에 넘기는 tools 배열 (자동 생성)
call_tool()    : 이름으로 도구 호출
"""

from __future__ import annotations

from tools.file_tools import (
    append_to_file,
    edit_file,
    list_directory,
    read_file,
    read_file_lines,
    search_files,
    search_in_file,
    write_file,
)

# ── 타입 별칭 ─────────────────────────────────────────────────────────────────
# 각 파라미터 항목: (type, description, required, default)
#   type     : JSON Schema 타입 문자열  "string" | "integer" | "boolean" | "number"
#   required : True이면 LLM이 반드시 채워야 함
#   default  : required=False일 때 함수 기본값을 LLM에게 알려주는 힌트 (description에 삽입됨)
_Param = tuple[str, str, bool, object]  # (type, description, required, default)


# ── 단일 진실 공급원 ──────────────────────────────────────────────────────────
TOOL_REGISTRY: dict[str, dict] = {
    "read_file": {
        "fn": read_file,
        "description": "파일 전체 내용 읽기",
        "params": {
            "path": ("string", "읽을 파일 경로", True, None),
        },
    },
    "read_file_lines": {
        "fn": read_file_lines,
        "description": "파일의 특정 줄 범위만 읽기 (대용량 파일 대응, 1-indexed)",
        "params": {
            "path": ("string", "읽을 파일 경로", True, None),
            "start": ("integer", "시작 줄 번호 (1-indexed)", True, None),
            "end": ("integer", "끝 줄 번호 (포함, 1-indexed)", True, None),
        },
    },
    "list_directory": {
        "fn": list_directory,
        "description": "디렉토리 내 파일 및 하위 폴더 목록 출력",
        "params": {
            "path": ("string", "조회할 디렉토리 경로", False, "."),
            "recursive": ("boolean", "하위 디렉토리까지 재귀 탐색 여부", False, False),
        },
    },
    "search_in_file": {
        "fn": search_in_file,
        "description": "단일 파일 내에서 정규식 패턴과 일치하는 줄 검색 (grep 역할)",
        "params": {
            "path": ("string", "검색할 파일 경로", True, None),
            "pattern": ("string", "검색할 정규식 패턴", True, None),
        },
    },
    "search_files": {
        "fn": search_files,
        "description": "디렉토리 전체를 재귀 탐색하며 정규식 패턴과 일치하는 줄 검색",
        "params": {
            "directory": ("string", "탐색할 루트 디렉토리 경로", True, None),
            "pattern": ("string", "검색할 정규식 패턴", True, None),
            "file_ext": (
                "string",
                "필터링할 파일 확장자 (예: '.py'). 기본값: '' → 전체",
                False,
                "",
            ),
        },
    },
    "write_file": {
        "fn": write_file,
        "description": (
            "파일 생성 또는 전체 내용 덮어쓰기 (중간 디렉토리 자동 생성). "
            "새 파일을 만들 때만 사용하세요. 기존 파일 수정은 edit_file을 쓰세요."
        ),
        "params": {
            "path": ("string", "작성할 파일 경로", True, None),
            "content": ("string", "저장할 전체 내용", True, None),
        },
    },
    "edit_file": {
        "fn": edit_file,
        "description": (
            "파일의 특정 문자열만 교체 (전체 덮어쓰기 방지). "
            "old_str은 파일 내에서 정확히 1회만 등장해야 합니다. "
            "기존 파일을 수정할 때 write_file 대신 이 도구를 사용하세요."
        ),
        "params": {
            "path": ("string", "수정할 파일 경로", True, None),
            "old_str": (
                "string",
                "교체할 원본 문자열 (파일 내 정확히 1회 등장해야 함)",
                True,
                None,
            ),
            "new_str": ("string", "새 문자열", True, None),
        },
    },
    "append_to_file": {
        "fn": append_to_file,
        "description": "파일 끝에 내용 추가 (기존 내용 유지)",
        "params": {
            "path": ("string", "대상 파일 경로", True, None),
            "content": ("string", "추가할 내용", True, None),
        },
    },
}


# ── TOOLS_SCHEMA 자동 생성 ────────────────────────────────────────────────────


def _build_tools_schema(registry: dict, provider: str = "anthropic") -> list[dict]:
    """
    TOOL_REGISTRY → Anthropic / OpenAI / Ollama tool_use 형식의 TOOLS_SCHEMA 자동 생성.

    Anthropic 형식:
    {
        "name": "read_file",
        "description": "...",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "..."}},
            "required": ["path"]
        }
    }

    OpenAI 형식:
    {
        "type": "function",
        "name": "read_file",
        "description": "...",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "..."}},
            "required": ["path"]
        }
    Ollama 형식
    {
        "type": "function",
        "function": {                    # ← OpenAI와 차이점: "function" 키로 한 번 더 감쌈
            "name": "read_file",
            "description": "...",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "..."}},
                "required": ["path"]
            }
        }
    }
    }
    """
    schema: list[dict] = []

    for tool_name, meta in registry.items():
        properties: dict[str, dict] = {}
        required: list[str] = []

        for param_name, (p_type, p_desc, p_required, p_default) in meta[
            "params"
        ].items():
            prop: dict[str, str] = {
                "type": p_type,
                "description": (
                    p_desc if p_required else f"{p_desc} (기본값: {p_default!r})"
                ),
            }
            properties[param_name] = prop

            if p_required:
                required.append(param_name)

        params_schema = {
            "type": "object",
            "properties": properties,
            **({"required": required} if required else {}),
        }
        if provider == "anthropic":
            schema.append(
                {
                    "name": tool_name,
                    "description": meta["description"],
                    "input_schema": params_schema,
                }
            )
        elif provider == "openai":
            schema.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": meta["description"],
                        "parameters": params_schema,
                    },
                }
            )
        elif provider == "ollama":
            schema.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": meta["description"],
                        "parameters": params_schema,
                    },
                }
            )
        else:
            raise ValueError(
                f"지원하지 않는 provider: {provider!r} (anthropic | openai | ollama)"
            )
    return schema


TOOLS_SCHEMA_ANTHROPIC: list[dict] = _build_tools_schema(TOOL_REGISTRY, "anthropic")
TOOLS_SCHEMA_OPENAI: list[dict] = _build_tools_schema(TOOL_REGISTRY, "openai")
TOOLS_SCHEMA_OLLAMA: list[dict] = _build_tools_schema(TOOL_REGISTRY, "ollama")


# ── 호출 인터페이스 ───────────────────────────────────────────────────────────


def call_tool(name: str, **kwargs):
    """
    이름으로 도구를 호출합니다.

    Args:
        name:   TOOL_REGISTRY에 등록된 도구 이름
        **kwargs: 도구 함수에 전달할 인자

    Returns:
        ToolResult (tools/file_tools.py 정의)

    Raises:
        ValueError: 등록되지 않은 도구 이름
    """
    if name not in TOOL_REGISTRY:
        raise ValueError(
            f"알 수 없는 도구: '{name}'. "
            f"사용 가능한 도구: {list(TOOL_REGISTRY.keys())}"
        )
    return TOOL_REGISTRY[name]["fn"](**kwargs)
