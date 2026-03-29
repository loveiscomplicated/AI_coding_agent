"""
core/undo.py — 파일 변경 undo/rollback

ChangeTracker:
  - 파일 수정 전 원본 내용을 스택에 기록
  - undo_last() 로 마지막 변경 복구
  - undo_all() 로 전체 복구
  - 새 파일 생성의 경우 undo 시 파일 삭제
"""

from __future__ import annotations

from pathlib import Path


class ChangeTracker:
    def __init__(self):
        self._stack: list[tuple[str, str | None]] = []

    def record(self, path: str) -> None:
        """파일 수정 전 원본 내용을 스택에 기록합니다. 파일이 없으면 None 으로 기록."""
        p = Path(path)
        content = p.read_text(encoding="utf-8") if p.exists() else None
        self._stack.append((path, content))

    def undo_last(self) -> tuple[str, bool]:
        """마지막 변경을 복구합니다. (path, success) 를 반환합니다."""
        if not self._stack:
            return "", False
        path, original = self._stack.pop()
        try:
            p = Path(path)
            if original is None:
                # 파일이 없던 상태 → undo 시 파일 삭제
                if p.exists():
                    p.unlink()
            else:
                p.write_text(original, encoding="utf-8")
            return path, True
        except Exception:
            return path, False

    def undo_all(self) -> list[tuple[str, bool]]:
        """모든 변경을 복구합니다. 각 (path, success) 리스트를 반환합니다."""
        results = []
        while self._stack:
            results.append(self.undo_last())
        return results

    @property
    def stack_size(self) -> int:
        """현재 스택 크기를 반환합니다."""
        return len(self._stack)
