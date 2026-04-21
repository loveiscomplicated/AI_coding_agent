"""
tools/hotline_tools.py — 에이전트 → 사용자 대화 도구

에이전트가 컨텍스트 문서로도 해결할 수 없는 모호한 사항을 발견했을 때
Discord(또는 stdin)를 통해 사용자와 대화한다.

흐름:
  1. 에이전트가 ask_user(question=...) 호출
  2. Discord 채널에 질문 전송
  3. 사용자가 자유롭게 대화 (오케스트레이터 LLM이 응답 파트너로 참여)
  4. 사용자가 "확정" 입력 → 대화 내용을 요약해 에이전트에게 단일 답변 반환
  5. 에이전트 계속 진행

사용 전 초기화 (run.py에서 한 번 호출):
    from tools.hotline_tools import set_notifier
    set_notifier(notifier)   # DiscordNotifier 인스턴스
    set_notifier(None)       # Discord 없음 → stdin 폴백
"""

from __future__ import annotations

import logging
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from llm import BaseLLMClient, LLMConfig, Message, create_client
from tools.schemas import ToolResult as _ToolResult

logger = logging.getLogger(__name__)

_conv_llm: BaseLLMClient | None = None
_sum_llm: BaseLLMClient | None = None
_llm_lock = threading.Lock()

# 현재 사용 중인 provider/model 기록 (런타임 변경 지원용)
_hotline_provider: str = "glm"
_hotline_conv_model: str = "glm-5.1"

# 태스크 재설계용 LLM 설정 (런타임 변경 지원용)
_redesign_provider: str = "glm"
_redesign_model: str = "glm-5.1"

# 태스크 초안 생성용 LLM 설정 (런타임 변경 지원용)
_task_draft_provider: str = "glm"
_task_draft_model: str = "glm-5.1"

# Critique LLM 설정 (런타임 변경 지원용)
_critique_provider: str = "glm"
_critique_model: str = "glm-5.1"


def set_llm(conv_llm: BaseLLMClient, sum_llm: BaseLLMClient) -> None:
    """
    대화용(conv_llm)과 요약용(sum_llm) LLM 클라이언트를 주입한다.
    파이프라인 시작 시 run.py에서 호출.
    """
    global _conv_llm, _sum_llm
    with _llm_lock:
        _conv_llm = conv_llm
        _sum_llm = sum_llm


def get_conv_model() -> dict:
    """현재 Discord 대화용 LLM provider/model을 반환한다."""
    return {"provider": _hotline_provider, "model": _hotline_conv_model}


def set_conv_model(model: str, provider: str | None = None) -> None:
    """Discord 대화용 LLM 모델을 런타임에 변경한다."""
    global _conv_llm, _hotline_conv_model, _hotline_provider
    target_provider = provider or _hotline_provider
    with _llm_lock:
        new_conv = create_client(
            target_provider,
            LLMConfig(model=model, system_prompt=_CONVERSATION_SYSTEM, max_tokens=1024),
        )
        _conv_llm = new_conv
        _hotline_conv_model = model
        _hotline_provider = target_provider


def get_redesign_model() -> dict:
    """현재 태스크 재설계용 LLM provider/model을 반환한다."""
    return {"provider": _redesign_provider, "model": _redesign_model}


def set_redesign_model(model: str, provider: str) -> None:
    """태스크 재설계용 LLM 모델을 런타임에 변경한다."""
    global _redesign_provider, _redesign_model
    _redesign_provider = provider
    _redesign_model = model


def get_task_draft_model() -> dict:
    """현재 태스크 초안 생성용 LLM provider/model을 반환한다."""
    return {"provider": _task_draft_provider, "model": _task_draft_model}


def set_task_draft_model(model: str, provider: str) -> None:
    """태스크 초안 생성용 LLM 모델을 런타임에 변경한다."""
    global _task_draft_provider, _task_draft_model
    _task_draft_provider = provider
    _task_draft_model = model


def get_critique_model() -> dict:
    """현재 Critique LLM provider/model을 반환한다."""
    return {"provider": _critique_provider, "model": _critique_model}


def set_critique_model(model: str, provider: str) -> None:
    """Critique LLM 모델을 런타임에 변경한다."""
    global _critique_provider, _critique_model
    _critique_provider = provider
    _critique_model = model


def create_hotline_llms(provider: str, model: str) -> tuple[BaseLLMClient, BaseLLMClient]:
    """
    hotline 모듈에서 사용할 LLM 클라이언트 쌍을 생성한다.

    Returns:
        (conv_llm, sum_llm) — 각각 올바른 시스템 프롬프트로 설정됨
    """
    global _hotline_provider, _hotline_conv_model
    _hotline_provider = provider
    _hotline_conv_model = model
    conv_llm = create_client(
        provider, LLMConfig(model=model, system_prompt=_CONVERSATION_SYSTEM, max_tokens=1024)
    )
    sum_llm = create_client(
        provider, LLMConfig(model=model, system_prompt=_SUMMARIZE_SYSTEM, max_tokens=512)
    )
    return conv_llm, sum_llm

_POLL_CHUNK = 5  # 폴링 단위 (초)
_CONFIRM_KEYWORDS = {"확정", "결정", "confirm", "done", "완료"}
_SKIP_KEYWORDS   = {"알아서 해", "알아서해", "skip", "건너뛰기", "패스", "pass"}
_STOP_KEYWORDS   = {"중단", "stop", "종료", "abort"}

# run.py가 주입하는 PauseController (중단 명령 전달용)
_pause_controller = None
_pause_controller_lock = threading.Lock()


def _is_confirm(msg: str) -> bool:
    """확정 키워드가 포함된 메시지인지 확인한다. 변형('확정!', '확정확정' 등)도 인식한다."""
    lower = msg.strip().lower()
    return any(kw in lower for kw in _CONFIRM_KEYWORDS)


def _is_skip(msg: str) -> bool:
    """건너뜀 키워드가 포함된 메시지인지 확인한다."""
    lower = msg.strip().lower()
    return any(kw in lower for kw in _SKIP_KEYWORDS)


def _is_stop(msg: str) -> bool:
    """중단 키워드와 정확히 일치하는지 확인한다.
    핫라인 대화 맥락에서 '시스템 종료 오류' 같은 문장이 오탐을 일으키지 않도록 exact match 사용.
    리스너 경로(urgent_callback)는 PauseController.handle_command가 별도로 처리한다."""
    lower = msg.strip().lower()
    return lower in _STOP_KEYWORDS

# 파이프라인 시작 시 run.py가 주입하는 DiscordNotifier (없으면 stdin 폴백)
_notifier = None
_notifier_lock = threading.Lock()

# 핫라인 대화 활성 여부 — True면 listen_for_commands가 메시지를 무시
_hotline_active = False
_hotline_active_lock = threading.Lock()


def is_hotline_active() -> bool:
    """핫라인 대화가 진행 중인지 반환한다. listen_for_commands에서 참조."""
    with _hotline_active_lock:
        return _hotline_active

# 결정 사항 기록용 레포 경로 (run.py가 주입)
_repo_path: Path | None = None
_repo_path_lock = threading.Lock()

# 태스크 파일 경로 (run.py가 주입) — 오케스트레이터 컨텍스트 로드용
_tasks_path: Path | None = None
_tasks_path_lock = threading.Lock()

# 현재 활성 workspace context 디렉토리들 (pipeline.py가 주입) — decisions.md 동기화용
# {task_id: workspace_context_dir} 형태
_workspace_context_dirs: dict[str, Path] = {}
_workspace_context_lock = threading.Lock()


def register_workspace_context_dir(task_id: str, context_dir: str | Path) -> None:
    """pipeline.py가 workspace 생성 후 호출. decisions.md 동기화 대상으로 등록한다."""
    with _workspace_context_lock:
        _workspace_context_dirs[task_id] = Path(context_dir)


def unregister_workspace_context_dir(task_id: str) -> None:
    """pipeline.py가 workspace 정리 후 호출."""
    with _workspace_context_lock:
        _workspace_context_dirs.pop(task_id, None)

_CONVERSATION_SYSTEM = """\
당신은 AI 코딩 에이전트 파이프라인의 중앙 오케스트레이터입니다.
하위 에이전트가 구현 중 모호한 사항을 발견해 사용자에게 질문했습니다.

## 핵심 임무
에이전트의 질문에 대한 답변을 사용자와 함께 확정하고, 에이전트에게 전달한다.

## 행동 원칙
1. **먼저 컨텍스트를 탐색하라**: tasks.yaml, PROJECT_STRUCTURE.md 에 이미 답이 있으면 먼저 요약해 제시하라. 사용자에게 물어보기 전에 알고 있는 정보를 공유하라.
2. **사용자 답변을 즉시 수용하라**: 사용자가 명확히 답했으면 같은 질문을 다시 하지 마라. 그 답변을 바탕으로 정리하고 "확정을 입력해 주세요"로 안내하라.
3. **반복 금지**: 동일한 선택지나 질문을 두 번 이상 반복하지 마라. 사용자가 이미 답한 내용은 기정사실로 취급하라.
4. **짧고 직접적으로**: 불필요한 배경 설명 없이 핵심만 전달하라.
5. **확정 안내**: 결정이 섰다고 판단되면 반드시 `확정을 입력해 주세요`라고 안내하라.
"""

_SUMMARIZE_SYSTEM = """\
당신은 AI 코딩 에이전트 파이프라인의 중앙 오케스트레이터입니다.
사용자와의 대화를 바탕으로 에이전트에게 전달할 최종 답변을 한 문단으로 정리하세요.

- 결정된 내용만 포함하세요. 대화 과정의 고민은 제외하세요.
- 에이전트가 구현에 바로 사용할 수 있을 만큼 구체적이어야 합니다.
- 한국어로 작성하세요.
"""


def set_notifier(notifier) -> None:
    """
    DiscordNotifier 인스턴스를 주입한다. 파이프라인 시작 시 run.py에서 호출.
    None을 넘기면 stdin 폴백 모드로 동작한다.
    """
    global _notifier
    with _notifier_lock:
        _notifier = notifier


def set_pause_controller(ctrl) -> None:
    """
    PauseController 인스턴스를 주입한다. 파이프라인 시작 시 run.py에서 호출.
    핫라인 대화 중 "중단" 메시지가 오면 PauseController에 전달한다.
    """
    global _pause_controller
    with _pause_controller_lock:
        _pause_controller = ctrl


def set_repo_path(repo_path: str | Path | None) -> None:
    """
    decisions.md를 기록할 레포 경로를 주입한다. 파이프라인 시작 시 run.py에서 호출.
    None이면 decisions.md 기록을 건너뛴다.
    """
    global _repo_path
    with _repo_path_lock:
        _repo_path = Path(repo_path).resolve() if repo_path else None


def set_tasks_path(tasks_path: str | Path | None) -> None:
    """
    tasks.yaml 경로를 주입한다. 파이프라인 시작 시 run.py에서 호출.
    오케스트레이터가 사용자 대화 중 태스크 내용을 참조할 수 있게 한다.
    """
    global _tasks_path
    with _tasks_path_lock:
        _tasks_path = Path(tasks_path).resolve() if tasks_path else None


def _load_orchestrator_context() -> str:
    """
    오케스트레이터 대화에 주입할 정적 프로젝트 컨텍스트를 로드한다.

    포함 항목:
    - PROJECT_STRUCTURE.md (코드베이스 전체 구조 요약)
    - tasks.yaml 전체 내용
    - agent-data/context/*.md 파일들

    반환값은 LLM 프롬프트에 직접 삽입되는 문자열이다.
    특정 파일 내용이 필요하면 오케스트레이터가 read_file 도구를 호출한다.
    """
    parts: list[str] = []

    with _repo_path_lock:
        rp = _repo_path

    # PROJECT_STRUCTURE.md (최대 8000자)
    if rp:
        ps_file = rp / "PROJECT_STRUCTURE.md"
        if ps_file.exists():
            try:
                text = ps_file.read_text(encoding="utf-8")
                if len(text) > 8000:
                    text = text[:8000] + f"\n... [{len(text)-8000}자 생략]"
                parts.append(f"## PROJECT_STRUCTURE.md\n\n{text.strip()}")
            except Exception as e:
                logger.warning("[hotline] PROJECT_STRUCTURE.md 로드 실패: %s", e)

    # tasks.yaml
    with _tasks_path_lock:
        tp = _tasks_path
    if tp and tp.exists():
        try:
            content = tp.read_text(encoding="utf-8")
            parts.append(f"## tasks.yaml\n\n```yaml\n{content}\n```")
        except Exception as e:
            logger.warning("[hotline] tasks.yaml 로드 실패: %s", e)

    # agent-data/context/*.md
    if rp:
        ctx_dir = rp / "agent-data" / "context"
        if ctx_dir.is_dir():
            for md_file in sorted(ctx_dir.glob("*.md")):
                try:
                    text = md_file.read_text(encoding="utf-8")
                    if text.strip():
                        parts.append(f"## {md_file.name}\n\n{text.strip()}")
                except Exception as e:
                    logger.warning("[hotline] %s 로드 실패: %s", md_file.name, e)

    if not parts:
        return ""
    return "# 프로젝트 컨텍스트\n\n" + "\n\n---\n\n".join(parts)


# ── 오케스트레이터 코드베이스 접근 도구 ──────────────────────────────────────


def _safe_repo_path(rel: str) -> Path | None:
    """rel 이 _repo_path 밖으로 탈출하지 않는지 검증하고 절대 경로를 반환한다."""
    with _repo_path_lock:
        repo = _repo_path
    if repo is None:
        return None
    resolved = (repo / rel).resolve()
    # path traversal 방지
    if not str(resolved).startswith(str(repo)):
        return None
    return resolved


def _hotline_read_file(path: str) -> str:
    """레포 내 파일을 읽어 반환한다 (최대 6000자)."""
    p = _safe_repo_path(path)
    if p is None:
        return f"오류: 접근 불가 경로 ({path})"
    if not p.exists():
        return f"오류: 파일 없음 — {path}"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
        if len(text) > 6000:
            text = text[:6000] + f"\n... [{len(text) - 6000}자 생략]"
        return text
    except Exception as e:
        return f"오류: 읽기 실패 — {e}"


def _hotline_list_dir(path: str = ".") -> str:
    """레포 내 디렉토리 목록을 반환한다."""
    p = _safe_repo_path(path)
    if p is None:
        return f"오류: 접근 불가 경로 ({path})"
    if not p.exists():
        return f"오류: 디렉토리 없음 — {path}"
    try:
        entries = []
        for item in sorted(p.iterdir()):
            suffix = "/" if item.is_dir() else f"  ({item.stat().st_size:,}B)"
            entries.append(f"{item.name}{suffix}")
        return "\n".join(entries) if entries else "(비어 있음)"
    except Exception as e:
        return f"오류: 목록 조회 실패 — {e}"


def _hotline_search_code(pattern: str, path: str = ".") -> str:
    """레포 내 코드를 정규식으로 검색한다 (grep, 최대 50줄)."""
    p = _safe_repo_path(path)
    if p is None:
        return f"오류: 접근 불가 경로 ({path})"
    try:
        result = subprocess.run(
            ["grep", "-rn", "-m", "50", "--include=*.py",
             "--include=*.ts", "--include=*.tsx", "--include=*.js",
             "--include=*.go", "--include=*.java", "--include=*.rs",
             "--include=*.yaml", "--include=*.yml", "--include=*.md",
             pattern, str(p)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = result.stdout[:4000]
        return output if output else f"'{pattern}' 검색 결과 없음"
    except subprocess.TimeoutExpired:
        return "오류: 검색 타임아웃"
    except Exception as e:
        return f"오류: 검색 실패 — {e}"


def _exec_hotline_tool(name: str, args: dict) -> str:
    """hotline 도구 이름과 인자로 실제 실행 결과를 반환한다."""
    if name == "read_file":
        return _hotline_read_file(args.get("path", ""))
    if name == "list_dir":
        return _hotline_list_dir(args.get("path", "."))
    if name == "search_code":
        return _hotline_search_code(
            args.get("pattern", ""),
            args.get("path", "."),
        )
    return f"알 수 없는 도구: {name}"


def _build_hotline_tools(provider: str) -> list[dict]:
    """LLM provider 에 맞는 도구 스키마를 반환한다."""
    tools_def = [
        {
            "name": "read_file",
            "description": "레포지토리의 소스 파일 내용을 읽는다. 코드·설정·문서 확인에 사용한다.",
            "params": {
                "path": {
                    "type": "string",
                    "description": "repo 루트 기준 상대 경로 (예: src/game/state.py)",
                },
            },
            "required": ["path"],
        },
        {
            "name": "list_dir",
            "description": "레포지토리의 디렉토리 내 파일·폴더 목록을 확인한다.",
            "params": {
                "path": {
                    "type": "string",
                    "description": "repo 루트 기준 상대 경로 (기본값: . — 루트)",
                },
            },
            "required": [],
        },
        {
            "name": "search_code",
            "description": "레포지토리 전체(또는 특정 경로)에서 코드·텍스트를 정규식으로 검색한다.",
            "params": {
                "pattern": {
                    "type": "string",
                    "description": "검색할 문자열 또는 정규식",
                },
                "path": {
                    "type": "string",
                    "description": "검색 범위 상대 경로 (기본값: . — 전체)",
                },
            },
            "required": ["pattern"],
        },
    ]

    if provider == "anthropic":
        return [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": {
                    "type": "object",
                    "properties": t["params"],
                    "required": t["required"],
                },
            }
            for t in tools_def
        ]
    else:  # openai / glm / ollama
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": {
                        "type": "object",
                        "properties": t["params"],
                        "required": t["required"],
                    },
                },
            }
            for t in tools_def
        ]


def _append_decision(question: str, answer: str, method: str) -> None:
    """
    agent-data/context/decisions.md에 결정 사항을 기록한다.

    Args:
        question: 에이전트가 물어본 원래 질문
        answer:   최종 답변 (요약 또는 자율 판단 내용)
        method:   "사용자 확정" | "에이전트 자율 판단"
    """
    with _repo_path_lock:
        repo = _repo_path
    if repo is None:
        return
    decisions_path = repo / "agent-data" / "context" / "decisions.md"
    decisions_path.parent.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = (
        f"\n---\n\n"
        f"**날짜:** {date_str}  \n"
        f"**방법:** {method}  \n\n"
        f"**질문**\n\n{question}\n\n"
        f"**결정**\n\n{answer}\n"
    )
    needs_header = not decisions_path.exists() or decisions_path.stat().st_size == 0
    with open(decisions_path, "a", encoding="utf-8") as f:
        if needs_header:
            f.write("# 에이전트 결정 사항\n\n에이전트가 구현 중 내린 결정들을 자동으로 기록합니다.\n")
        f.write(entry)
    logger.info("[decisions] 결정 사항 기록 완료: %s", decisions_path)

    # 활성 workspace context 디렉토리에도 동기화 (stale 방지)
    with _workspace_context_lock:
        ws_dirs = list(_workspace_context_dirs.values())
    for ws_ctx in ws_dirs:
        try:
            ws_ctx.mkdir(parents=True, exist_ok=True)
            ws_decisions = ws_ctx / "decisions.md"
            ws_needs_header = not ws_decisions.exists() or ws_decisions.stat().st_size == 0
            with open(ws_decisions, "a", encoding="utf-8") as f:
                if ws_needs_header:
                    f.write("# 에이전트 결정 사항\n\n에이전트가 구현 중 내린 결정들을 자동으로 기록합니다.\n")
                f.write(entry)
            logger.debug("[decisions] workspace 동기화 완료: %s", ws_decisions)
        except Exception as e:
            logger.warning("[decisions] workspace 동기화 실패 (%s): %s", ws_ctx, e)


def ask_user(question: str) -> _ToolResult:
    """
    에이전트가 사용자에게 직접 질문한다.
    사용자가 오케스트레이터와 자유롭게 대화한 뒤 "확정"을 입력하면 종료된다.

    사용 원칙 (시스템 프롬프트에 명시됨):
      1. context/ 문서를 먼저 확인한다.
      2. 그래도 불명확하면 이 도구를 호출한다.

    Args:
        question: 사용자에게 보낼 질문 (구체적일수록 좋음)

    Returns:
        ToolResult — 대화에서 확정된 답변.
    """
    question = question.strip()
    if not question:
        return _ToolResult(success=False, output="", error="질문 내용이 비어 있습니다. 질문을 구체적으로 작성하세요.")

    with _notifier_lock:
        notifier = _notifier

    if notifier is not None:
        answer = _ask_via_discord(notifier, question)
    else:
        answer = _ask_via_stdin(question)
    return _ToolResult(success=True, output=answer)


# ── 내부 구현 ──────────────────────────────────────────────────────────────────


def _ask_via_discord(notifier, question: str) -> str:
    """
    Discord에서 사용자와 멀티턴 대화를 진행한다.
    오케스트레이터 LLM이 대화 파트너로 참여하고, "확정" 입력 시 종료한다.
    """
    global _hotline_active
    with _hotline_active_lock:
        _hotline_active = True
    try:
        opening = (
            f"❓ **에이전트 질문**\n\n"
            f"{question}\n\n"
            f"_자유롭게 대화하세요. 결정이 되면 `확정`, 에이전트에게 맡기려면 `알아서 해`를 입력해주세요._"
        )
        last_bot_message_id = notifier.send(opening)
        logger.info("[ask_user] Discord 질문 전송 — 대화 대기 중")
    except Exception as e:
        with _hotline_active_lock:
            _hotline_active = False
        logger.warning("[ask_user] Discord 전송 실패, stdin 폴백: %s", e)
        return _ask_via_stdin(question)

    conversation: list[dict] = []  # {"role": "user"|"assistant", "content": str}

    try:
        while True:
            # urgent_callback이 이미 중단을 처리했으면 즉시 탈출
            with _pause_controller_lock:
                ctrl = _pause_controller
            if ctrl is not None and ctrl.is_stopped:
                logger.info("[ask_user] PauseController 중단 상태 감지 — 핫라인 종료")
                return "사용자가 파이프라인 중단을 요청했습니다."

            # 사용자 메시지 올 때까지 무한 대기
            user_msg = _poll_forever(notifier, last_bot_message_id)

            # 빈 메시지 방어 (notifier 필터링 후에도 혹시 남는 경우 대비)
            if not user_msg.strip():
                logger.debug("[ask_user] 빈 메시지 수신 — 무시하고 계속 대기")
                continue

            # 중단 명령 → PauseController에 전달하고 대화 종료
            if _is_stop(user_msg):
                logger.info("[ask_user] 중단 명령 수신 — PauseController에 전달")
                with _pause_controller_lock:
                    ctrl = _pause_controller
                # urgent_callback이 먼저 처리했으면 이미 stopped=True 상태
                # 그 경우엔 알림 중복 전송을 막기 위해 send를 건너뜀
                already_stopped = ctrl is not None and ctrl.is_stopped
                if ctrl is not None:
                    ctrl.handle_command(user_msg)
                if not already_stopped:
                    try:
                        notifier.send("🛑 중단 요청 확인! 현재 태스크 완료 후 파이프라인을 종료합니다.")
                    except Exception:
                        pass
                return "사용자가 파이프라인 중단을 요청했습니다."

            if _is_skip(user_msg):
                logger.info("[ask_user] skip 수신 — 에이전트 자율 판단으로 진행")
                try:
                    notifier.send("⏭ 입력 확인! 에이전트가 알아서 진행합니다.")
                except Exception as e:
                    logger.warning("[ask_user] skip 확인 메시지 전송 실패: %s", e)
                skip_answer = "사용자가 답변을 건너뛰었습니다. 컨텍스트 문서와 일반적인 관례를 바탕으로 최선의 판단으로 진행하세요."
                _append_decision(question, skip_answer, "에이전트 자율 판단")
                return skip_answer

            if _is_confirm(user_msg):
                try:
                    notifier.send("✅ 확정 입력 확인! 대화 내용 정리 중이에요…")
                except Exception as e:
                    logger.warning("[ask_user] 확정 확인 메시지 전송 실패: %s", e)
                # 대화 내용을 요약해 에이전트에게 전달할 단일 답변 생성
                final_answer = _synthesize_answer(question, conversation)
                logger.info("[ask_user] 확정 수신 — 최종 답변: %r", final_answer[:80])
                _append_decision(question, final_answer, "사용자 확정")
                try:
                    notifier.send(
                        f"📋 **에이전트에게 전달할 답변**\n>>> {final_answer}"
                    )
                except Exception as e:
                    logger.warning("[ask_user] 최종 답변 전송 실패: %s", e)
                return final_answer

            # 일반 대화 메시지 → 오케스트레이터 LLM이 응답
            conversation.append({"role": "user", "content": user_msg})
            try:
                response = _orchestrator_reply(question, conversation)
                conversation.append({"role": "assistant", "content": response})
            except Exception as e:
                logger.error("[ask_user] 오케스트레이터 응답 실패, conversation에 미추가: %s", e)
                response = "⚠️ 일시적으로 응답을 생성할 수 없습니다. 계속 대화하시거나 `확정`/`알아서 해`를 입력해주세요."
                # 실패한 응답은 conversation 히스토리에 추가하지 않음

            try:
                last_bot_message_id = notifier.send(response)
            except Exception as e:
                logger.warning("[ask_user] Discord 응답 전송 실패: %s", e)

    except Exception as e:
        # 대화 루프 자체에서 예상치 못한 예외 → 사용자에게 알리고 자율 판단으로 전환
        logger.error("[ask_user] 대화 루프 예외 발생 — 자율 판단으로 전환: %s", e, exc_info=True)
        try:
            notifier.send(
                f"⚠️ 대화 처리 중 오류가 발생했습니다. 에이전트가 자율 판단으로 계속 진행합니다.\n"
                f"오류: `{type(e).__name__}: {e}`"
            )
        except Exception:
            pass
        fallback = "대화 처리 오류로 자율 판단으로 진행합니다. 컨텍스트 문서와 일반적인 관례를 바탕으로 최선의 판단으로 진행하세요."
        _append_decision(question, fallback, "에이전트 자율 판단 (오류 복구)")
        return fallback
    finally:
        with _hotline_active_lock:
            _hotline_active = False


def _poll_forever(notifier, after_message_id: str) -> str:
    """사용자 메시지가 올 때까지 chunk 단위로 폴링을 반복한다."""
    current_after = after_message_id
    while True:
        try:
            reply, current_after = notifier.wait_for_reply(
                after_message_id=current_after,
                timeout=_POLL_CHUNK,
            )
        except Exception as e:
            logger.warning("[ask_user] 폴링 중 예외 발생, 재시도: %s", e)
            continue
        if reply is not None:
            return reply
        logger.debug("[ask_user] 아직 답변 없음 (after=%s) — 계속 대기 중", current_after)


def _infer_provider(llm) -> str:
    """LLM 클라이언트 타입에서 provider 이름을 추론한다."""
    mapping = {
        "ClaudeClient":  "anthropic",
        "OpenaiClient":  "openai",
        "GlmClient":     "openai",
        "OllamaClient":  "ollama",
    }
    return mapping.get(type(llm).__name__, "anthropic")


def _orchestrator_reply(question: str, conversation: list[dict]) -> str:
    """
    오케스트레이터 LLM이 대화에 참여해 응답을 생성한다.

    미니 ReAct 루프로 동작한다:
    - read_file / list_dir / search_code 도구를 호출해 코드베이스를 탐색한 뒤 답변한다.
    - 도구 호출 없이 end_turn이면 즉시 텍스트를 반환한다.
    - 최대 _MAX_TOOL_ROUNDS 회 도구 호출 후에는 그 시점의 텍스트를 반환한다.

    대화 히스토리는 GLM 호환성을 위해 단일 user 메시지로 직렬화하여 전달한다.
    """
    _MAX_TOOL_ROUNDS = 2

    with _llm_lock:
        llm = _conv_llm
    if llm is None:
        return "(오케스트레이터 LLM 미초기화)"

    # ── 초기 user 메시지 구성 ────────────────────────────────────────────────
    # 컨텍스트 초과 방지: 최근 6턴(3왕복)만 히스토리에 포함
    _HISTORY_LIMIT = 6
    trimmed = conversation[:-1]
    if len(trimmed) > _HISTORY_LIMIT:
        trimmed = trimmed[-_HISTORY_LIMIT:]
    history_lines = [
        f"{'사용자' if m['role'] == 'user' else '봇'}: {m['content']}"
        for m in trimmed
    ]
    last_user_msg = conversation[-1]["content"] if conversation else ""
    project_ctx = _load_orchestrator_context()

    parts: list[str] = []
    if project_ctx:
        parts.append(project_ctx)
    parts.append(f"## 에이전트의 원래 질문\n\n{question}")
    if history_lines:
        parts.append("## 지금까지의 대화\n\n" + "\n\n".join(history_lines))
    parts.append(f"## 사용자의 새 메시지\n\n{last_user_msg}")

    provider = _infer_provider(llm)
    tools = _build_hotline_tools(provider)
    messages: list[Message] = [Message(role="user", content="\n\n---\n\n".join(parts))]

    # 순환 import 방지를 위해 함수 내에서 지연 import
    from core.loop import _extract_tool_calls, _extract_text as _loop_extract_text  # noqa: PLC0415

    # ── 미니 ReAct 루프 ───────────────────────────────────────────────────────
    last_text = ""
    for _round in range(_MAX_TOOL_ROUNDS + 1):
        try:
            response = llm.chat(messages=messages, tools=tools)
        except Exception as e:
            logger.error("[ask_user] 오케스트레이터 LLM 호출 실패: %s", e)
            raise RuntimeError("오케스트레이터 LLM 응답 생성 실패") from e

        last_text = _loop_extract_text(response.content) or ""

        # 종료 조건: 도구 호출 없음
        tool_calls = _extract_tool_calls(response.content)
        if not tool_calls or _round == _MAX_TOOL_ROUNDS:
            return last_text or "(응답 없음)"

        # 도구 실행 후 결과를 다음 메시지로 추가
        messages.append(Message(role="assistant", content=response.content))
        tool_results = [
            {
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": _exec_hotline_tool(tc.name, tc.input),
            }
            for tc in tool_calls
        ]
        messages.append(Message(role="user", content=tool_results))
        logger.debug(
            "[ask_user] 도구 호출 %d회: %s",
            _round + 1,
            [tc.name for tc in tool_calls],
        )

    return last_text or "(응답 없음)"


def _synthesize_answer(question: str, conversation: list[dict]) -> str:
    """대화 내용을 바탕으로 에이전트에게 전달할 단일 답변을 생성한다."""
    if not conversation:
        return "사용자가 대화 없이 확정을 입력했습니다. 최선의 판단으로 진행하세요."

    with _llm_lock:
        llm = _sum_llm
    if llm is None:
        last_user = next(
            (m["content"] for m in reversed(conversation) if m["role"] == "user"),
            "답변 없음",
        )
        return last_user

    conversation_text = "\n".join(
        f"{'사용자' if m['role'] == 'user' else '오케스트레이터'}: {m['content']}"
        for m in conversation
    )
    user_msg = (
        f"## 에이전트의 원래 질문\n\n{question}\n\n"
        f"## 사용자와의 대화\n\n{conversation_text}\n\n"
        f"위 대화에서 결정된 내용을 에이전트에게 전달할 답변으로 정리하세요."
    )
    try:
        response = llm.chat([Message(role="user", content=user_msg)])
        text = _extract_text(response)
        return text.strip() if text else "(요약 실패)"
    except Exception as e:
        logger.error("[ask_user] 답변 요약 LLM 호출 실패: %s", e)
        last_user = next(
            (m["content"] for m in reversed(conversation) if m["role"] == "user"),
            "답변 없음",
        )
        return last_user


def _ask_via_stdin(question: str) -> str:
    """Discord 없을 때 터미널 stdin으로 멀티턴 대화를 진행한다."""
    print(f"\n{'='*60}")
    print("[에이전트 질문]")
    print(question)
    print(f"{'='*60}")
    print("자유롭게 입력하세요. 결정이 되면 '확정'을 입력하세요.\n")

    conversation: list[dict] = []
    while True:
        try:
            user_input = input(">>> ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input:
            continue

        if _is_skip(user_input):
            skip_answer = "사용자가 답변을 건너뛰었습니다. 컨텍스트 문서와 일반적인 관례를 바탕으로 최선의 판단으로 진행하세요."
            _append_decision(question, skip_answer, "에이전트 자율 판단")
            return skip_answer

        if _is_confirm(user_input):
            answer = _synthesize_answer(question, conversation)
            _append_decision(question, answer, "사용자 확정")
            print(f"\n[에이전트에 전달할 답변]\n{answer}\n")
            return answer

        conversation.append({"role": "user", "content": user_input})
        response = _orchestrator_reply(question, conversation)
        conversation.append({"role": "assistant", "content": response})
        print(f"\n오케스트레이터: {response}\n")

    return "사용자가 입력을 중단했습니다. 최선의 판단으로 진행하세요."
