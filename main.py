"""
main.py — AI Coding Agent 진입점

실행:
    python main.py                        # claude (기본값)
    python main.py -p ollama -m qwen2.5-coder:14b
    python main.py -p openai  -m gpt-4o
    python main.py -p claude  -s <세션-ID-앞자리>  # 세션 이어하기
"""

from __future__ import annotations

import argparse
import logging
import sys

from cli import interface as ui
from cli.commands import Action, handle
from core.loop import ReactLoop
from llm import LLMConfig, create_client
from memory import SessionManager


# ── 인자 파싱 ─────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="agent",
        description="로컬 AI 코딩 에이전트",
    )
    parser.add_argument(
        "-p", "--provider",
        default="claude",
        choices=["ollama", "openai", "claude"],
        help="LLM provider (기본값: claude)",
    )
    parser.add_argument(
        "-m", "--model",
        default=None,
        help="모델 이름 (미지정 시 provider 기본값 사용)",
    )
    parser.add_argument(
        "-s", "--session",
        default=None,
        metavar="ID",
        help="이어할 세션 ID (앞자리만 입력 가능)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="DEBUG 로그 출력",
    )
    return parser.parse_args()


# ── provider별 기본 모델 ──────────────────────────────────────────────────────


_DEFAULT_MODELS: dict[str, str] = {
    "ollama": "qwen2.5-coder:14b",
    "openai": "gpt-4o",
    "claude": "claude-sonnet-4-6",
}

_OLLAMA_KWARGS: dict = {"native_tool_role": True}


# ── 메인 REPL ────────────────────────────────────────────────────────────────


def main() -> None:
    args = _parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    # ── LLM 클라이언트 초기화 ────────────────────────────────────────────────
    model = args.model or _DEFAULT_MODELS[args.provider]
    config = LLMConfig(model=model)
    extra  = _OLLAMA_KWARGS if args.provider == "ollama" else {}

    try:
        client = create_client(provider=args.provider, config=config, **extra)
    except Exception as e:
        ui.print_error(f"LLM 클라이언트 초기화 실패: {e}")
        sys.exit(1)

    if not client.is_available():
        ui.print_error(f"모델 '{model}'에 연결할 수 없습니다. 설정을 확인하세요.")
        sys.exit(1)

    # ── ReAct 루프 설정 ───────────────────────────────────────────────────────
    loop = ReactLoop(
        llm=client,
        max_iterations=15,
        on_tool_call=ui.print_tool_call,
        on_tool_result=ui.print_tool_result,
    )

    # ── 세션 초기화 ───────────────────────────────────────────────────────────
    mgr = SessionManager()

    if args.session:
        summaries = mgr.list_all()
        matches = [s for s in summaries if s.session_id.startswith(args.session)]
        if not matches:
            ui.print_error(f"세션 '{args.session}'을 찾을 수 없습니다.")
            sys.exit(1)
        session = mgr.load(matches[0].session_id)
        ui.print_info(
            f"세션 불러옴: [{session.session_id[:8]}] "
            f"{session.title or '(제목 없음)'} ({len(session.messages)}개 메시지)"
        )
    else:
        session = mgr.new(model=model)

    # ── 배너 & REPL ───────────────────────────────────────────────────────────
    ui.print_banner()
    ui.print_info(f"provider={args.provider}  model={model}  session=[{session.session_id[:8]}]")

    while True:
        try:
            raw = ui.get_input(session.session_id[:8])
        except KeyboardInterrupt:
            ui.print_info("\n종료합니다.")
            break

        if not raw.strip():
            continue

        # ── 슬래시 명령어 처리 ────────────────────────────────────────────────
        result = handle(raw, mgr, session)
        if result is not None:
            if result.action == Action.EXIT:
                ui.print_info("종료합니다.")
                break
            if result.action in (Action.NEW_SESSION, Action.LOAD_SESSION):
                session = result.session
            continue

        # ── 일반 대화 → ReAct 루프 실행 ──────────────────────────────────────
        history = mgr.get_history(session.session_id)

        try:
            loop_result = loop.run(raw, history=history)
        except Exception as e:
            ui.print_error(f"루프 실행 중 오류: {e}")
            continue

        # 이번 턴에 새로 추가된 메시지만 저장
        # result.messages = [system, *history, user_input, *new_turns]
        new_messages = loop_result.messages[1 + len(history):]
        mgr.append_many(session.session_id, new_messages)

        ui.print_answer(loop_result.answer)

        if not loop_result.succeeded:
            ui.print_info(f"[중단 이유: {loop_result.stop_reason.value}]")


if __name__ == "__main__":
    main()
