"""
cli/interrupt.py — ESC 인터럽트 핸들러

loop.run() 실행 중 ESC 키를 감지해 stop_check 플래그를 세운다.
context manager로 사용:

    handler = EscInterruptHandler()
    with handler:
        result = loop.run(..., stop_check=handler.is_interrupted)

    if handler.was_interrupted:
        # "Interrupted · What should Claude do instead?" 흐름 처리
"""

from __future__ import annotations

import select
import sys
import threading

_ESC = "\x1b"


class EscInterruptHandler:
    """
    백그라운드 스레드에서 ESC 입력을 감지한다.

    - `__enter__` / `__exit__`으로 감시 시작·종료를 관리한다.
    - `is_interrupted()` : stop_check 콜백으로 ReactLoop에 전달한다.
    - `was_interrupted`  : 루프 종료 후 인터럽트 발생 여부 확인.
    - `reset()`          : 다음 루프 실행 전 플래그를 초기화한다.

    macOS / Linux 전용. Windows에서는 no-op으로 동작한다.
    """

    def __init__(self) -> None:
        self._interrupted = threading.Event()
        self._stop_listener = threading.Event()
        self._thread: threading.Thread | None = None

    # ── context manager ──────────────────────────────────────────────────────

    def __enter__(self) -> "EscInterruptHandler":
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()

    # ── 공개 인터페이스 ───────────────────────────────────────────────────────

    def start(self) -> None:
        """ESC 감지 스레드를 시작한다."""
        self._interrupted.clear()
        self._stop_listener.clear()
        if sys.platform == "win32":
            return
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """ESC 감지 스레드를 정지하고 합류 대기한다."""
        self._stop_listener.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
            self._thread = None

    def reset(self) -> None:
        """다음 루프 실행 전 인터럽트 플래그를 초기화한다."""
        self._interrupted.clear()

    def is_interrupted(self) -> bool:
        """ReactLoop.stop_check 콜백으로 전달되는 함수."""
        return self._interrupted.is_set()

    @property
    def was_interrupted(self) -> bool:
        return self._interrupted.is_set()

    # ── 내부 리스너 ──────────────────────────────────────────────────────────

    def _listen(self) -> None:
        """백그라운드 스레드: stdin을 raw mode로 전환해 ESC 입력을 감시한다."""
        import tty
        import termios

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            while not self._stop_listener.is_set():
                # 100ms 주기로 폴링 — CPU 점유 최소화
                ready, _, _ = select.select([sys.stdin], [], [], 0.1)
                if ready:
                    ch = sys.stdin.read(1)
                    if ch == _ESC:
                        self._interrupted.set()
                        break
        except Exception:
            pass
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            except Exception:
                pass
