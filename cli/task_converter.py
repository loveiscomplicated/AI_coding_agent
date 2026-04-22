"""
cli/task_converter.py — 자연어 입력을 단일 Task 객체로 변환하는 미니 회의 레이어.

흐름:
  1. 사용자 자연어 + (있으면) PROJECT_STRUCTURE.md + @파일 내용을 조립
  2. LLM과 멀티턴 대화로 의도 정렬
  3. LLM이 충분히 이해하면 구분자로 감싼 Task JSON 출력
  4. JSON 파싱 후 Task 객체로 변환

핵심 원칙:
  - 의도 정렬이 주목적. 태스크 생성은 그 다음의 기계적 단계.
  - 빈 입력(Esc/Ctrl-D) → 즉시 aborted=True, task=None 반환.
  - max_turns(기본 10) 초과 시 ConversionError — 무한 대화 방지 안전장치.
  - ConversionError는 JSON 2연속 파싱 실패 또는 턴 한도 초과 시 발생.

진입점: `TaskConverter.convert(user_input, file_contents=None)` (async)
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, TYPE_CHECKING

from cli import interface as ui
from llm import BaseLLMClient, LLMConfig, Message, create_client
from orchestrator.complexity import compute_complexity, normalize_complexity
from orchestrator.task import LANGUAGE_TEST_FRAMEWORK_MAP, Task
from orchestrator.task_utils import sanitize_task_draft

if TYPE_CHECKING:
    from llm.base import LLMResponse


# ── 모듈 상수 ─────────────────────────────────────────────────────────────────

_DELIMITER_START = "===TASK_JSON_START==="
_DELIMITER_END = "===TASK_JSON_END==="

_DEFAULT_MAX_TURNS = 10

_PROJECT_STRUCTURE_CACHE_SECONDS = 300
_MAX_FILE_BYTES = 500_000

_FILE_REFERENCE_PATTERN = re.compile(r"@(\S+)")
_SYSTEM_PROMPT_PATH = Path(__file__).parent / "prompts" / "instant_task_system.md"

_BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico",
    ".pdf", ".zip", ".tar", ".gz", ".7z",
    ".db", ".sqlite", ".sqlite3",
    ".so", ".dylib", ".dll", ".bin", ".exe",
    ".mp3", ".mp4", ".wav", ".ogg", ".mov",
    ".pyc", ".class",
}


# ── 예외 ──────────────────────────────────────────────────────────────────────

class ConversionError(Exception):
    """태스크 변환 실패 (JSON 파싱 2회 연속 실패 등)."""


# ── 결과 ──────────────────────────────────────────────────────────────────────

@dataclass
class ConversionResult:
    task: Task | None
    conversation_history: list[Message] = field(default_factory=list)
    raw_task_json: str | None = None
    structure_token_count: int = 0
    aborted: bool = False
    warnings: list[str] = field(default_factory=list)
    turns_used: int = 0
    file_refs: dict[str, str] = field(default_factory=dict)


# ── @파일 파싱 ────────────────────────────────────────────────────────────────

def parse_file_references(
    text: str,
    repo_path: Path,
) -> tuple[str, dict[str, str], list[str]]:
    """입력 텍스트에서 @path 참조를 추출해 파일 내용을 읽는다.

    Args:
        text: 사용자 자연어 입력
        repo_path: 레포 루트 (상대 경로 해석 기준)

    Returns:
        (cleaned_text, file_refs, warnings)
        - cleaned_text: 실패한 @path 토큰을 제거한 텍스트. 성공한 토큰은 보존.
        - file_refs: {상대경로: 파일내용}
        - warnings: 실패한 @path에 대한 설명

    규칙:
      - 존재 X → warning, 토큰은 원문 유지 (LLM이 직접 다룰 수 있도록)
      - 디렉토리 → warning, 토큰 제거 (지원 안 함)
      - 바이너리 확장자 or UnicodeDecodeError → warning, 토큰 제거
      - 크기 초과 → warning, 토큰 제거
    """
    file_refs: dict[str, str] = {}
    warnings: list[str] = []
    tokens_to_remove: list[str] = []

    for match in _FILE_REFERENCE_PATTERN.finditer(text):
        raw_path = match.group(1)
        full_token = match.group(0)  # "@path"

        try:
            candidate = (repo_path / raw_path).expanduser()
        except Exception:
            candidate = Path(raw_path).expanduser()

        if not candidate.exists():
            warnings.append(f"@{raw_path} — 파일을 찾을 수 없음 (LLM이 직접 해석)")
            continue  # 원문 유지

        if candidate.is_dir():
            warnings.append(f"@{raw_path} — 디렉토리는 지원하지 않음 (건너뜀)")
            tokens_to_remove.append(full_token)
            continue

        if candidate.suffix.lower() in _BINARY_EXTENSIONS:
            warnings.append(f"@{raw_path} — 바이너리 파일 건너뜀")
            tokens_to_remove.append(full_token)
            continue

        try:
            size = candidate.stat().st_size
        except OSError as e:
            warnings.append(f"@{raw_path} — 파일 정보 조회 실패: {e}")
            tokens_to_remove.append(full_token)
            continue

        if size > _MAX_FILE_BYTES:
            warnings.append(f"@{raw_path} — {size // 1024}KB, 크기 초과로 건너뜀")
            tokens_to_remove.append(full_token)
            continue

        try:
            content = candidate.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            warnings.append(f"@{raw_path} — 바이너리 파일 건너뜀 (디코딩 실패)")
            tokens_to_remove.append(full_token)
            continue
        except OSError as e:
            warnings.append(f"@{raw_path} — 읽기 실패: {e}")
            tokens_to_remove.append(full_token)
            continue

        file_refs[raw_path] = content

    cleaned = text
    for token in tokens_to_remove:
        cleaned = cleaned.replace(token, "")
    # 연속 공백 정리
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned, file_refs, warnings


# ── 응답 파싱 ─────────────────────────────────────────────────────────────────

def _extract_delimited_json(text: str) -> tuple[str | None, str]:
    """응답에서 구분자 사이의 JSON을 추출.

    Returns:
        (json_str_or_none, preamble_text)
        - json_str_or_none: 구분자 사이 내용. 없으면 None.
        - preamble_text: 구분자 앞에 있던 요약 텍스트 (있을 때만).
    """
    start_idx = text.find(_DELIMITER_START)
    if start_idx < 0:
        return None, ""

    end_idx = text.find(_DELIMITER_END, start_idx + len(_DELIMITER_START))
    if end_idx < 0:
        return None, ""

    preamble = text[:start_idx].strip()
    inner = text[start_idx + len(_DELIMITER_START):end_idx].strip()
    return inner, preamble


def _response_to_text(resp: "LLMResponse") -> str:
    """LLMResponse.content 블록 리스트에서 text 타입을 병합한다.

    `backend/routers/tasks.py`의 파싱 패턴과 동일.
    """
    parts: list[str] = []
    for block in resp.content:
        if isinstance(block, dict):
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif "text" in block:
                parts.append(block["text"])
        elif hasattr(block, "type") and getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", ""))
        elif hasattr(block, "text"):
            parts.append(getattr(block, "text", ""))
        elif isinstance(block, str):
            parts.append(block)
    return "".join(parts)


def _strip_code_fences(raw: str) -> str:
    """```json 코드펜스를 벗겨낸다 (backend와 동일 패턴)."""
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


# ── Task 후처리 ───────────────────────────────────────────────────────────────

def _post_process(
    raw_json_str: str,
    default_language: str = "python",
) -> tuple[Task, str, list[str]]:
    """LLM이 생성한 raw JSON을 Task 객체로 변환한다.

    Raises:
        json.JSONDecodeError: JSON 파싱 실패

    Returns:
        (task, cleaned_json_str, warnings)
    """
    cleaned = _strip_code_fences(raw_json_str)
    data = json.loads(cleaned)

    # LLM이 {"task": {...}} 또는 {"tasks": [...]}로 감쌀 가능성 대비
    if isinstance(data, dict) and "task" in data and isinstance(data["task"], dict):
        data = data["task"]
    elif isinstance(data, dict) and "tasks" in data and isinstance(data["tasks"], list) \
            and data["tasks"]:
        data = data["tasks"][0]

    if not isinstance(data, dict):
        raise json.JSONDecodeError("Task JSON이 객체가 아닙니다", cleaned, 0)

    # 강제 재설정 (LLM 값 덮어쓰기)
    data["id"] = f"instant-{int(time.time())}"
    data["status"] = "pending"
    data["depends_on"] = []

    # 기본값
    data.setdefault("task_type", "backend")
    data.setdefault("language", default_language)
    data.setdefault("target_files", [])
    data.setdefault("description", "")
    data.setdefault("acceptance_criteria", [])
    data.setdefault("title", "(제목 없음)")
    data.setdefault(
        "test_framework",
        LANGUAGE_TEST_FRAMEWORK_MAP.get(data["language"], "pytest"),
    )

    warnings: list[str] = []
    sanitize_task_draft(data, warnings)  # target_files 정규화 (반드시 complexity 전에)

    # complexity — sanitize 이후에 계산해야 target_files 최종값을 본다
    cx = data.get("complexity")
    if cx in ("standard", "complex"):
        data["complexity"] = normalize_complexity(cx)  # → "non-simple"
    elif cx not in ("simple", "non-simple"):
        data["complexity"] = compute_complexity(data)

    try:
        task = Task.from_dict(data)
    except (KeyError, ValueError, TypeError) as e:
        raise json.JSONDecodeError(
            f"Task.from_dict 실패: {e}", cleaned, 0,
        ) from e

    return task, cleaned, warnings


# ── PROJECT_STRUCTURE.md 로드 ─────────────────────────────────────────────────

def _load_project_structure(
    repo_path: Path,
    max_age_seconds: int = _PROJECT_STRUCTURE_CACHE_SECONDS,
) -> str:
    """PROJECT_STRUCTURE.md를 로드한다. mtime이 오래되면 재생성.

    캐시 정책: 파일 존재 + mtime이 max_age 이내면 재사용, 아니면 update() 호출.
    """
    target = repo_path / "PROJECT_STRUCTURE.md"
    needs_regen = (
        not target.exists()
        or (time.time() - target.stat().st_mtime) >= max_age_seconds
    )
    if needs_regen:
        # import를 함수 내부에서 — tree-sitter 의존성이 무거움
        from structure.updater import update as structure_update
        try:
            structure_update(root=str(repo_path), output="PROJECT_STRUCTURE.md")
        except Exception:
            # 생성 실패해도 기존 파일이 있으면 그대로 사용. 둘 다 없으면 빈 문자열.
            if not target.exists():
                return ""
    if not target.exists():
        return ""
    return target.read_text(encoding="utf-8", errors="replace")


# ── 초기 사용자 메시지 빌드 ───────────────────────────────────────────────────

def _build_initial_user_message(
    structure_md: str,
    user_text: str,
    file_refs: dict[str, str],
) -> str:
    parts: list[str] = []

    if structure_md.strip():
        parts.append("# 프로젝트 구조\n")
        parts.append(structure_md.rstrip())
        parts.append("")

    if file_refs:
        parts.append("# 참조 파일\n")
        for path, content in file_refs.items():
            ext = Path(path).suffix.lstrip(".") or "text"
            parts.append(f"## {path}")
            parts.append(f"```{ext}")
            parts.append(content)
            parts.append("```")
            parts.append("")

    parts.append("# 사용자 요청\n")
    parts.append(user_text)

    return "\n".join(parts)


# ── TaskConverter ─────────────────────────────────────────────────────────────

class TaskConverter:
    """미니 회의를 통한 자연어 → 단일 Task 객체 변환기.

    메인 CLI 세션의 provider/model을 재사용하지만, system_prompt는
    미니 회의용으로 교체한다.

    Args:
        repo_path: 프로젝트 루트 (PROJECT_STRUCTURE.md 위치 + @path 해석 기준)
        llm_config: 메인 CLI 세션의 LLMConfig (model/temperature/max_tokens/extra 재사용)
        provider: LLM provider 문자열 (claude/openai/ollama/glm/gemini)
        client: 사전 빌드된 LLM 클라이언트 (주로 테스트용 DI). None이면 첫 convert 시 생성.
        max_turns: 최대 회의 턴 (기본 10). 초과 시 ConversionError.
        input_fn: 사용자 입력 함수 (테스트용 DI). 기본: ui.get_input.
        output_fn: LLM 응답 출력 함수. 기본: ui.print_answer.
    """

    def __init__(
        self,
        repo_path: str,
        llm_config: LLMConfig,
        provider: str,
        *,
        client: BaseLLMClient | None = None,
        max_turns: int = _DEFAULT_MAX_TURNS,
        input_fn: Callable[[str], str] | None = None,
        output_fn: Callable[[str], None] | None = None,
    ) -> None:
        self.repo_path = Path(repo_path).resolve()
        self.max_turns = max_turns
        self._input_fn = input_fn or (lambda prompt: ui.get_input(prompt))
        self._output_fn = output_fn or (lambda text: ui.print_answer(text))

        system_text = _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
        self._config = LLMConfig(
            model=llm_config.model,
            temperature=llm_config.temperature,
            max_tokens=llm_config.max_tokens,
            system_prompt=system_text,
            extra=dict(llm_config.extra),
        )
        self._provider = provider
        self._client = client

    def _get_client(self) -> BaseLLMClient:
        if self._client is None:
            self._client = create_client(self._provider, self._config)
        return self._client

    async def convert(
        self,
        user_input: str,
        file_contents: dict[str, str] | None = None,
    ) -> ConversionResult:
        """미니 회의를 실행하고 Task 객체를 반환한다.

        Args:
            user_input: 사용자 자연어 입력 (@파일 참조 포함 가능)
            file_contents: 외부에서 주입하는 파일 내용. None이면 user_input에서 @파싱.

        Returns:
            ConversionResult — task가 None이면 aborted=True 또는 예외.

        Raises:
            ConversionError: JSON 파싱 2회 연속 실패 (LLM이 깨진 경우)
        """
        structure_md = _load_project_structure(
            self.repo_path, _PROJECT_STRUCTURE_CACHE_SECONDS,
        )

        if file_contents is None:
            cleaned_input, file_refs, fwarnings = parse_file_references(
                user_input, self.repo_path,
            )
        else:
            cleaned_input = user_input
            file_refs = dict(file_contents)
            fwarnings = []

        warnings: list[str] = list(fwarnings)
        structure_token_count = len(structure_md) // 4

        first_msg = _build_initial_user_message(structure_md, cleaned_input, file_refs)
        messages: list[Message] = [Message(role="user", content=first_msg)]

        def _build_history() -> list[Message]:
            """conversation_history = [system, *messages] 형태로 반환."""
            return [
                Message(role="system", content=self._config.system_prompt),
                *messages,
            ]

        def _abort(turn: int, reason: str) -> ConversionResult:
            return ConversionResult(
                task=None,
                conversation_history=_build_history(),
                raw_task_json=None,
                structure_token_count=structure_token_count,
                aborted=True,
                warnings=[*warnings, reason],
                turns_used=turn,
                file_refs=file_refs,
            )

        for turn in range(1, self.max_turns + 1):
            resp = await asyncio.to_thread(self._get_client().chat, messages)
            text = _response_to_text(resp)
            raw_json, preamble = _extract_delimited_json(text)

            if raw_json is None:
                # 질문 턴 — 사용자 답변을 받는다. 빈 입력(Esc/Ctrl-D) → 즉시 abort.
                self._output_fn(text)
                messages.append(Message(role="assistant", content=text))

                reply = self._input_fn("instant")
                if not reply.strip():
                    return _abort(turn, "사용자가 빈 입력으로 중단")

                messages.append(Message(role="user", content=reply))
                continue

            # 구분자 있음 — 파싱 시도 (1회 재시도)
            messages.append(Message(role="assistant", content=text))
            try:
                task, cleaned_json, parse_warnings = _post_process(raw_json)
            except json.JSONDecodeError:
                retry_prompt = (
                    f"JSON 파싱에 실패했습니다. {_DELIMITER_START} 와 "
                    f"{_DELIMITER_END} 사이에 유효한 Task JSON만 다시 생성해주세요."
                )
                messages.append(Message(role="user", content=retry_prompt))
                retry_resp = await asyncio.to_thread(self._get_client().chat, messages)
                retry_text = _response_to_text(retry_resp)
                retry_raw, _ = _extract_delimited_json(retry_text)
                messages.append(Message(role="assistant", content=retry_text))

                if retry_raw is None:
                    raise ConversionError(
                        "LLM이 재시도에서 JSON 구분자를 생성하지 못했습니다."
                    )
                try:
                    task, cleaned_json, parse_warnings = _post_process(retry_raw)
                except json.JSONDecodeError as e:
                    raise ConversionError(f"JSON 파싱 2회 연속 실패: {e}") from e

            warnings.extend(parse_warnings)
            if preamble:
                self._output_fn(preamble)

            return ConversionResult(
                task=task,
                conversation_history=_build_history(),
                raw_task_json=cleaned_json,
                structure_token_count=structure_token_count,
                aborted=False,
                warnings=warnings,
                turns_used=turn,
                file_refs=file_refs,
            )

        raise ConversionError(
            f"미니 회의가 최대 {self.max_turns}턴을 초과했습니다."
        )
