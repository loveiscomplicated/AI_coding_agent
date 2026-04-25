"""
core/undo.py — 파일 변경 undo/rollback

ChangeTracker:
  - 파일 수정 전 원본 내용을 스택에 기록
  - undo_last() 로 마지막 변경 복구
  - undo_all() 로 전체 복구
  - 새 파일 생성의 경우 undo 시 파일 삭제
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


_TREE_SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".pytest_cache"}


@dataclass
class _ChangeEntry:
    kind: str  # "file" | "tree"
    path: str
    original: str | None = None
    tree_snapshot: dict[str, str | None] | None = None


class ChangeTracker:
    def __init__(self):
        self._stack: list[_ChangeEntry] = []

    def _snapshot_tree(self, root: str) -> dict[str, str | None]:
        root_path = Path(root)
        if not root_path.exists():
            return {}
        if root_path.is_file():
            return {str(root_path): root_path.read_text(encoding="utf-8")}

        snapshot: dict[str, str | None] = {}
        for current_root, dirs, files in os.walk(root_path):
            dirs[:] = [d for d in dirs if d not in _TREE_SKIP_DIRS]
            current_path = Path(current_root)
            for filename in files:
                path = current_path / filename
                if path.is_symlink():
                    continue
                snapshot[str(path)] = path.read_text(encoding="utf-8")
        return snapshot

    def _restore_file(self, path: str, original: str | None) -> None:
        p = Path(path)
        if original is None:
            if p.exists():
                p.unlink()
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(original, encoding="utf-8")

    def record(self, path: str) -> None:
        """파일 수정 전 원본 내용을 스택에 기록합니다. 파일이 없으면 None 으로 기록."""
        p = Path(path)
        content = p.read_text(encoding="utf-8") if p.exists() else None
        self._stack.append(_ChangeEntry(kind="file", path=path, original=content))

    def record_tree(self, root: str) -> None:
        """디렉토리 스냅샷을 기록해 명령 실행 후 전체 파일 변경을 되돌릴 수 있게 한다."""
        self._stack.append(
            _ChangeEntry(kind="tree", path=root, tree_snapshot=self._snapshot_tree(root))
        )

    def undo_last(self) -> tuple[str, bool]:
        """마지막 변경을 복구합니다. (path, success) 를 반환합니다."""
        if not self._stack:
            return "", False
        entry = self._stack.pop()
        try:
            if entry.kind == "file":
                self._restore_file(entry.path, entry.original)
                return entry.path, True

            snapshot = entry.tree_snapshot or {}
            current = self._snapshot_tree(entry.path)
            for path in current.keys() - snapshot.keys():
                Path(path).unlink()
            for path, original in snapshot.items():
                self._restore_file(path, original)
            return entry.path, True
        except Exception:
            return entry.path, False

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
