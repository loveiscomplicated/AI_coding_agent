"""
tests/test_undo.py

파일 변경 undo/rollback 기능 테스트.

설계:
  core/undo.py 에 ChangeTracker 클래스 구현.

  - 파일 수정 전 원본 내용을 스택에 기록
  - undo_last() 로 마지막 변경 복구
  - undo_all() 로 전체 복구
  - /undo 슬래시 명령어에서 호출됨
  - 새 파일 생성의 경우 undo 시 파일 삭제

아직 구현되지 않음 — 처음엔 실패한다.

실행:
    pytest tests/test_undo.py -v
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.undo import ChangeTracker   # 아직 없음


# ── 기본 동작 ──────────────────────────────────────────────────────────────────


class TestChangeTrackerBasic:
    def test_record_and_undo_edit(self, tmp_path):
        """수정 전 내용을 기록하고 undo 하면 원상복구된다."""
        f = tmp_path / "a.py"
        original = "def foo(): pass\n"
        f.write_text(original, encoding="utf-8")

        tracker = ChangeTracker()
        tracker.record(str(f))          # 수정 전 기록
        f.write_text("def bar(): pass\n", encoding="utf-8")  # 수정

        path, ok = tracker.undo_last()

        assert ok is True
        assert path == str(f)
        assert f.read_text(encoding="utf-8") == original

    def test_undo_new_file_deletes_it(self, tmp_path):
        """새로 생성된 파일을 undo 하면 파일이 삭제된다."""
        f = tmp_path / "new.py"
        tracker = ChangeTracker()
        tracker.record(str(f))   # 파일 없는 상태 기록

        f.write_text("new content\n", encoding="utf-8")  # 파일 생성

        path, ok = tracker.undo_last()

        assert ok is True
        assert not f.exists()

    def test_undo_empty_stack_returns_false(self):
        """스택이 비어 있으면 undo 실패를 반환해야 한다."""
        tracker = ChangeTracker()
        path, ok = tracker.undo_last()

        assert ok is False
        assert path == ""

    def test_multiple_undos_in_lifo_order(self, tmp_path):
        """여러 번 기록 후 LIFO 순서로 복구된다."""
        f1 = tmp_path / "f1.py"
        f2 = tmp_path / "f2.py"
        f1.write_text("v1\n", encoding="utf-8")
        f2.write_text("v2\n", encoding="utf-8")

        tracker = ChangeTracker()
        tracker.record(str(f1))
        tracker.record(str(f2))

        f1.write_text("changed1\n", encoding="utf-8")
        f2.write_text("changed2\n", encoding="utf-8")

        # 첫 번째 undo → f2 복구 (나중에 기록된 것 먼저)
        path1, ok1 = tracker.undo_last()
        assert ok1 is True
        assert path1 == str(f2)
        assert f2.read_text(encoding="utf-8") == "v2\n"

        # 두 번째 undo → f1 복구
        path2, ok2 = tracker.undo_last()
        assert ok2 is True
        assert path2 == str(f1)
        assert f1.read_text(encoding="utf-8") == "v1\n"

    def test_undo_all_restores_everything(self, tmp_path):
        """undo_all() 은 모든 변경을 한꺼번에 복구한다."""
        files = []
        originals = []
        tracker = ChangeTracker()

        for i in range(3):
            f = tmp_path / f"f{i}.py"
            orig = f"original{i}\n"
            f.write_text(orig, encoding="utf-8")
            files.append(f)
            originals.append(orig)
            tracker.record(str(f))
            f.write_text(f"modified{i}\n", encoding="utf-8")

        results = tracker.undo_all()

        assert all(ok for _, ok in results)
        for f, orig in zip(files, originals):
            assert f.read_text(encoding="utf-8") == orig


# ── 오류 케이스 ────────────────────────────────────────────────────────────────


class TestChangeTrackerErrors:
    def test_record_same_file_twice_stacks_both(self, tmp_path):
        """같은 파일을 두 번 기록하면 스택에 2개가 쌓여야 한다."""
        f = tmp_path / "a.py"
        f.write_text("v1\n", encoding="utf-8")
        tracker = ChangeTracker()

        tracker.record(str(f))          # v1 기록
        f.write_text("v2\n", encoding="utf-8")
        tracker.record(str(f))          # v2 기록
        f.write_text("v3\n", encoding="utf-8")

        # 첫 undo → v2 복구
        tracker.undo_last()
        assert f.read_text(encoding="utf-8") == "v2\n"

        # 두 번째 undo → v1 복구
        tracker.undo_last()
        assert f.read_text(encoding="utf-8") == "v1\n"

    def test_undo_when_file_externally_deleted(self, tmp_path):
        """기록 후 파일이 외부에서 삭제돼도 undo 가 크래시 없이 처리된다."""
        f = tmp_path / "b.py"
        f.write_text("original\n", encoding="utf-8")
        tracker = ChangeTracker()
        tracker.record(str(f))

        f.unlink()  # 외부에서 삭제

        path, ok = tracker.undo_last()
        # 성공(원본 복구) 또는 실패 모두 허용, 단 크래시 없어야 함
        assert isinstance(ok, bool)

    def test_undo_readonly_file_returns_false(self, tmp_path):
        """읽기 전용 파일 복구 실패 시 ok=False 반환."""
        f = tmp_path / "ro.py"
        f.write_text("original\n", encoding="utf-8")
        tracker = ChangeTracker()
        tracker.record(str(f))
        f.write_text("modified\n", encoding="utf-8")
        f.chmod(0o444)  # 읽기 전용

        try:
            path, ok = tracker.undo_last()
            if not ok:
                assert path == str(f)
        finally:
            f.chmod(0o644)  # 정리

    def test_undo_all_empty_returns_empty_list(self):
        """스택이 비어있을 때 undo_all() 은 빈 리스트를 반환해야 한다."""
        tracker = ChangeTracker()
        results = tracker.undo_all()
        assert results == []

    def test_stack_size_property(self, tmp_path):
        """현재 스택 크기를 확인할 수 있어야 한다."""
        f = tmp_path / "x.py"
        f.write_text("x\n", encoding="utf-8")
        tracker = ChangeTracker()

        assert tracker.stack_size == 0
        tracker.record(str(f))
        assert tracker.stack_size == 1
        tracker.undo_last()
        assert tracker.stack_size == 0

    def test_unicode_file_content_preserved(self, tmp_path):
        """한국어 등 유니코드 내용도 정확히 복구된다."""
        f = tmp_path / "kor.py"
        original = "# 한국어 주석\nprint('안녕')\n"
        f.write_text(original, encoding="utf-8")
        tracker = ChangeTracker()
        tracker.record(str(f))
        f.write_text("# changed\n", encoding="utf-8")

        tracker.undo_last()
        assert f.read_text(encoding="utf-8") == original

    def test_large_file_undo(self, tmp_path):
        """큰 파일도 크래시 없이 복구된다."""
        f = tmp_path / "big.py"
        original = "x = 1\n" * 10_000
        f.write_text(original, encoding="utf-8")
        tracker = ChangeTracker()
        tracker.record(str(f))
        f.write_text("changed\n", encoding="utf-8")

        path, ok = tracker.undo_last()
        assert ok is True
        assert f.read_text(encoding="utf-8") == original
