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
import threading
from datetime import datetime
from pathlib import Path

from llm import BaseLLMClient, LLMConfig, Message, create_client

logger = logging.getLogger(__name__)

_conv_llm: BaseLLMClient | None = None
_sum_llm: BaseLLMClient | None = None
_llm_lock = threading.Lock()


def set_llm(conv_llm: BaseLLMClient, sum_llm: BaseLLMClient) -> None:
    """
    대화용(conv_llm)과 요약용(sum_llm) LLM 클라이언트를 주입한다.
    파이프라인 시작 시 run.py에서 호출.
    """
    global _conv_llm, _sum_llm
    with _llm_lock:
        _conv_llm = conv_llm
        _sum_llm = sum_llm


def create_hotline_llms(provider: str, model: str) -> tuple[BaseLLMClient, BaseLLMClient]:
    """
    hotline 모듈에서 사용할 LLM 클라이언트 쌍을 생성한다.

    Returns:
        (conv_llm, sum_llm) — 각각 올바른 시스템 프롬프트로 설정됨
    """
    conv_llm = create_client(
        provider, LLMConfig(model=model, system_prompt=_CONVERSATION_SYSTEM, max_tokens=1024)
    )
    sum_llm = create_client(
        provider, LLMConfig(model=model, system_prompt=_SUMMARIZE_SYSTEM, max_tokens=512)
    )
    return conv_llm, sum_llm

_POLL_CHUNK = 60  # 폴링 단위 (초)
_CONFIRM_KEYWORDS = {"확정", "결정", "confirm", "done", "완료"}
_SKIP_KEYWORDS   = {"알아서 해", "알아서해", "skip", "건너뛰기", "패스", "pass"}

# 파이프라인 시작 시 run.py가 주입하는 DiscordNotifier (없으면 stdin 폴백)
_notifier = None
_notifier_lock = threading.Lock()

# 결정 사항 기록용 레포 경로 (run.py가 주입)
_repo_path: Path | None = None
_repo_path_lock = threading.Lock()

_CONVERSATION_SYSTEM = """\
당신은 AI 코딩 에이전트 파이프라인의 중앙 오케스트레이터입니다.
하위 에이전트가 구현 중 모호한 사항을 발견해 사용자에게 질문했습니다.

사용자가 결정을 내릴 수 있도록 대화 파트너로 참여하세요.
- 사용자의 생각을 명확히 하는 데 도움을 주세요.
- 선택지의 트레이드오프를 설명하세요.
- 결정이 섰다고 판단되면 "확정을 입력하면 에이전트에게 전달하겠습니다." 라고 안내하세요.
- 짧고 핵심적으로 답하세요. 장황하게 설명하지 마세요.
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


def set_repo_path(repo_path: str | Path | None) -> None:
    """
    decisions.md를 기록할 레포 경로를 주입한다. 파이프라인 시작 시 run.py에서 호출.
    None이면 decisions.md 기록을 건너뛴다.
    """
    global _repo_path
    with _repo_path_lock:
        _repo_path = Path(repo_path).resolve() if repo_path else None


def _append_decision(question: str, answer: str, method: str) -> None:
    """
    data/context/decisions.md에 결정 사항을 기록한다.

    Args:
        question: 에이전트가 물어본 원래 질문
        answer:   최종 답변 (요약 또는 자율 판단 내용)
        method:   "사용자 확정" | "에이전트 자율 판단"
    """
    with _repo_path_lock:
        repo = _repo_path
    if repo is None:
        return
    decisions_path = repo / "data" / "context" / "decisions.md"
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


def ask_user(question: str) -> str:
    """
    에이전트가 사용자에게 직접 질문한다.
    사용자가 오케스트레이터와 자유롭게 대화한 뒤 "확정"을 입력하면 종료된다.

    사용 원칙 (시스템 프롬프트에 명시됨):
      1. context/ 문서를 먼저 확인한다.
      2. 그래도 불명확하면 이 도구를 호출한다.

    Args:
        question: 사용자에게 보낼 질문 (구체적일수록 좋음)

    Returns:
        대화에서 확정된 답변 문자열.
    """
    question = question.strip()
    if not question:
        return "질문 내용이 비어 있습니다. 질문을 구체적으로 작성하세요."

    with _notifier_lock:
        notifier = _notifier

    if notifier is not None:
        return _ask_via_discord(notifier, question)
    else:
        return _ask_via_stdin(question)


# ── 내부 구현 ──────────────────────────────────────────────────────────────────


def _ask_via_discord(notifier, question: str) -> str:
    """
    Discord에서 사용자와 멀티턴 대화를 진행한다.
    오케스트레이터 LLM이 대화 파트너로 참여하고, "확정" 입력 시 종료한다.
    """
    try:
        opening = (
            f"❓ **에이전트 질문**\n\n"
            f"{question}\n\n"
            f"_자유롭게 대화하세요. 결정이 되면 `확정`, 에이전트에게 맡기려면 `알아서 해`를 입력해주세요._"
        )
        last_bot_message_id = notifier.send(opening)
        logger.info("[ask_user] Discord 질문 전송 — 대화 대기 중")
    except Exception as e:
        logger.warning("[ask_user] Discord 전송 실패, stdin 폴백: %s", e)
        return _ask_via_stdin(question)

    conversation: list[dict] = []  # {"role": "user"|"assistant", "content": str}

    while True:
        # 사용자 메시지 올 때까지 무한 대기
        user_msg = _poll_forever(notifier, last_bot_message_id)

        if user_msg.strip().lower() in _SKIP_KEYWORDS:
            logger.info("[ask_user] skip 수신 — 에이전트 자율 판단으로 진행")
            skip_answer = "사용자가 답변을 건너뛰었습니다. 컨텍스트 문서와 일반적인 관례를 바탕으로 최선의 판단으로 진행하세요."
            _append_decision(question, skip_answer, "에이전트 자율 판단")
            try:
                notifier.send("⏭ **건너뜀** — 에이전트가 최선의 판단으로 진행합니다.")
            except Exception:
                pass
            return skip_answer

        if user_msg.strip().lower() in _CONFIRM_KEYWORDS:
            # 대화 내용을 요약해 에이전트에게 전달할 단일 답변 생성
            final_answer = _synthesize_answer(question, conversation)
            logger.info("[ask_user] 확정 수신 — 최종 답변: %r", final_answer[:80])
            _append_decision(question, final_answer, "사용자 확정")
            try:
                notifier.send(
                    f"✅ **확정 완료**\n\n"
                    f"에이전트에게 전달할 답변:\n>>> {final_answer}"
                )
            except Exception:
                pass
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


def _poll_forever(notifier, after_message_id: str) -> str:
    """사용자 메시지가 올 때까지 chunk 단위로 폴링을 반복한다."""
    current_after = after_message_id
    while True:
        reply = notifier.wait_for_reply(
            after_message_id=current_after,
            timeout=_POLL_CHUNK,
        )
        if reply is not None:
            return reply
        logger.debug("[ask_user] 아직 답변 없음 — 계속 대기 중")


def _extract_text(response) -> str:
    """LLMResponse에서 텍스트를 추출한다."""
    for block in response.content:
        if isinstance(block, dict) and block.get("type") == "text":
            return block["text"]
        if hasattr(block, "type") and block.type == "text":
            return block.text
    return ""


def _orchestrator_reply(question: str, conversation: list[dict]) -> str:
    """오케스트레이터 LLM이 대화에 참여해 응답을 생성한다.

    multi-turn API 구조 대신 대화 내용을 단일 user 메시지에 인라인으로 넣어 전달한다.
    GLM 계열 API가 multi-turn에서 1213 오류를 발생시키는 문제를 원천 회피한다.
    """
    with _llm_lock:
        llm = _conv_llm
    if llm is None:
        return "(오케스트레이터 LLM 미초기화)"

    # 대화 히스토리를 텍스트로 직렬화
    history_lines: list[str] = []
    for m in conversation[:-1]:  # 마지막 user 메시지는 별도로 처리
        role_label = "사용자" if m["role"] == "user" else "봇"
        history_lines.append(f"{role_label}: {m['content']}")

    last_user_msg = conversation[-1]["content"] if conversation else ""

    parts = [f"## 에이전트의 원래 질문\n\n{question}"]
    if history_lines:
        parts.append("## 지금까지의 대화\n\n" + "\n\n".join(history_lines))
    parts.append(f"## 사용자의 새 메시지\n\n{last_user_msg}")

    single_user_content = "\n\n---\n\n".join(parts)

    try:
        response = llm.chat([Message(role="user", content=single_user_content)])
        return _extract_text(response) or "(응답 없음)"
    except Exception as e:
        logger.error("[ask_user] 오케스트레이터 LLM 호출 실패: %s", e)
        raise RuntimeError("오케스트레이터 LLM 응답 생성 실패") from e


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

        if user_input.lower() in _SKIP_KEYWORDS:
            skip_answer = "사용자가 답변을 건너뛰었습니다. 컨텍스트 문서와 일반적인 관례를 바탕으로 최선의 판단으로 진행하세요."
            _append_decision(question, skip_answer, "에이전트 자율 판단")
            return skip_answer

        if user_input.lower() in _CONFIRM_KEYWORDS:
            answer = _synthesize_answer(question, conversation)
            _append_decision(question, answer, "사용자 확정")
            print(f"\n[에이전트에 전달할 답변]\n{answer}\n")
            return answer

        conversation.append({"role": "user", "content": user_input})
        response = _orchestrator_reply(question, conversation)
        conversation.append({"role": "assistant", "content": response})
        print(f"\n오케스트레이터: {response}\n")

    return "사용자가 입력을 중단했습니다. 최선의 판단으로 진행하세요."
